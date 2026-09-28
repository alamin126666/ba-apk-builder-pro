from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
import zipfile
import logging
from pathlib import Path
from queue import Empty, Queue
from typing import Callable

from PIL import Image

from app.config import Settings
from app.project_files import UploadValidationError, copy_allowed_tree, stage_web_project
from scripts.prepare_android import prepare
from scripts.protect_web import create_container

Progress = Callable[[str, int], None]
MAX_BUILD_LOG_BYTES = 10 * 1024 * 1024
logger = logging.getLogger("apk_builder")
_DIAGNOSTIC_MARKERS = (
    "FAILURE:", "What went wrong:", "Execution failed for task", "Could not find",
    "Could not resolve", "No matching variant", "SDK location", "NDK at",
    "CMake Error", "ninja: error:", "error:", "FAILED",
    "Unsupported class file", "Compilation error",
)
_PRIVATE_DIAGNOSTIC = re.compile(
    r"(?i)\b(password|passphrase|token|secret|credential|keystore|private key|api[_ -]?key)\b"
)
_UNIX_PATH = re.compile(r"(?<![\w:])/(?:opt|tmp|root|home|app|usr)/[^\s:'\"`()]+")
_WINDOWS_PATH = re.compile(r"(?<!\w)[A-Za-z]:\\[^\s<>:\"|?*]+")
_KOTLIN_DIAGNOSTIC = re.compile(r"(?:^|\s)e:\s", re.IGNORECASE)


class BuildFailure(Exception):
    def __init__(self, safe_message: str):
        super().__init__(safe_message)
        self.safe_message = safe_message


class BuildCancelled(Exception):
    pass


def _check_cancel(cancel_event: threading.Event) -> None:
    if cancel_event.is_set():
        raise BuildCancelled()


class BoundedBuildLog:
    def __init__(self, path: Path):
        self.path = path
        self.file = path.open("w", encoding="utf-8", errors="replace")
        self.written = 0
        self.truncated = False
        try:
            path.chmod(0o600)
        except OSError:
            pass

    def write(self, text: str) -> None:
        if self.truncated:
            return
        encoded = text.encode("utf-8", errors="replace")
        remaining = MAX_BUILD_LOG_BYTES - self.written
        if len(encoded) <= remaining:
            self.file.write(text)
            self.written += len(encoded)
            return
        if remaining > 0:
            self.file.write(encoded[:remaining].decode("utf-8", errors="replace"))
            self.written = MAX_BUILD_LOG_BYTES
        self.file.write("\n[Build diagnostics capped at 10 MiB.]\n")
        self.truncated = True

    def flush(self) -> None:
        self.file.flush()

    def close(self) -> None:
        self.file.close()


def execute_build(
    settings: Settings,
    build_id: str,
    app_name: str,
    package_name: str,
    project_upload: Path,
    project_filename: str,
    logo_upload: Path | None,
    output_apk: Path,
    log_path: Path,
    progress: Progress,
    cancel_event: threading.Event,
) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    build_home = settings.build_root / build_id
    work = build_home / "work"
    web_source = work / "web-project"
    normalized = work / "normalized-web"
    android_project = work / "android"
    gradle_log = BoundedBuildLog(log_path)
    try:
        progress("Preparing project", 5)
        _check_cancel(cancel_event)
        work.mkdir(parents=True, exist_ok=True)

        progress("Validating files", 12)
        try:
            web_root = stage_web_project(project_upload, project_filename, web_source, settings.max_upload_mb * 1024 * 1024)
        except UploadValidationError as exc:
            raise BuildFailure(str(exc)) from exc
        _remove_if_incoming(project_upload, build_home)
        if logo_upload is not None:
            _validate_logo(logo_upload)
        _check_cancel(cancel_event)

        progress("Protecting web assets", 25)
        files = copy_allowed_tree(web_root, normalized)
        container, key = create_container(files)
        key_hex = key.hex()
        _check_cancel(cancel_event)
        shutil.rmtree(web_source, ignore_errors=True)
        shutil.rmtree(normalized, ignore_errors=True)

        progress("Preparing Android project", 34)
        prepare(settings.project_root / "android-template", android_project, app_name, package_name, logo_upload, key_hex)
        if logo_upload is not None:
            _remove_if_incoming(logo_upload, build_home)
        asset_dir = android_project / "app/src/main/assets"
        asset_dir.mkdir(parents=True, exist_ok=True)
        (asset_dir / "app.dat").write_bytes(container)
        del container
        del key, key_hex
        _check_cancel(cancel_event)

        process_env = os.environ.copy()
        process_env.update({
            "ANDROID_SDK_ROOT": os.getenv("ANDROID_SDK_ROOT", "/opt/android-sdk"),
            "ANDROID_HOME": os.getenv("ANDROID_HOME", os.getenv("ANDROID_SDK_ROOT", "/opt/android-sdk")),
            "ANDROID_NDK_HOME": os.getenv("ANDROID_NDK_HOME", "/opt/android-sdk/ndk/27.2.12479018"),
            "GRADLE_OPTS": "-Dorg.gradle.daemon=false",
            "CMAKE_BUILD_PARALLEL_LEVEL": "1",
        })
        progress("Running Gradle", 50)
        _run_gradle(android_project, process_env, gradle_log, progress, cancel_event, settings.build_timeout_seconds)
        _check_cancel(cancel_event)

        progress("Creating APK", 92)
        apk = android_project / "app/build/outputs/apk/debug/app-debug.apk"
        if not apk.is_file() or apk.stat().st_size < 4096:
            raise BuildFailure("Gradle completed without producing a valid debug APK.")
        _audit_apk(apk)
        _verify_apk_package(apk, package_name, process_env, gradle_log)
        _verify_apk_signature(apk, process_env, gradle_log)
        output_apk.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(apk, output_apk)
        try:
            output_apk.chmod(0o600)
        except OSError:
            pass
        progress("Finalizing", 98)
    except BuildCancelled:
        raise
    except BuildFailure:
        raise
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
        raise BuildFailure("The Android build failed. Review the server-side build log and try again.") from exc
    finally:
        gradle_log.close()


def _validate_logo(logo: Path) -> None:
    try:
        with Image.open(logo) as image:
            if image.format not in {"PNG", "JPEG", "WEBP"}:
                raise BuildFailure("Use a PNG, JPG, or WEBP app icon.")
            if image.width * image.height > 40_000_000:
                raise BuildFailure("The app icon dimensions are too large.")
            image.verify()
    except (OSError, Image.DecompressionBombError) as exc:
        raise BuildFailure("The uploaded app icon is not a valid PNG, JPG, or WEBP image.") from exc


def _remove_if_incoming(path: Path, build_home: Path) -> None:
    """Delete request-local plaintext after it has been validated and staged."""
    try:
        if path.resolve().parent == (build_home / "incoming").resolve():
            path.unlink(missing_ok=True)
    except OSError:
        pass


def _run_gradle(
    project: Path,
    env: dict[str, str],
    log,
    progress: Progress,
    cancel_event: threading.Event,
    timeout_seconds: int = 1200,
) -> None:
    executable = project / ("gradlew.bat" if os.name == "nt" else "gradlew")
    if os.name != "nt" and not os.access(executable, os.X_OK):
        executable.chmod(0o755)
    command = [str(executable), "--no-daemon", "--console=plain", "--stacktrace", "assembleDebug"]
    try:
        process = subprocess.Popen(
            command,
            cwd=project,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            shell=False,
        )
    except OSError as exc:
        raise BuildFailure("The Android build tools are unavailable in this server image.") from exc
    lines: Queue[str | None] = Queue(maxsize=200)

    def read_output() -> None:
        try:
            assert process.stdout is not None
            for line in process.stdout:
                lines.put(line)
        finally:
            lines.put(None)

    reader = threading.Thread(target=read_output, name="gradle-output-reader", daemon=True)
    reader.start()
    stream_done = False
    started_at = time.monotonic()
    next_heartbeat = started_at + 60
    last_task = "waiting for Gradle task output"
    try:
        while process.poll() is None or not stream_done:
            now = time.monotonic()
            elapsed = now - started_at
            if elapsed >= timeout_seconds:
                try:
                    process.terminate()
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                raise BuildFailure(
                    f"The Android build exceeded its {timeout_seconds}-second time limit and was stopped. "
                    "Check Railway memory and outbound dependency access, then retry."
                )
            if now >= next_heartbeat:
                logger.info("Android Gradle build still running after %s seconds; last task: %s", int(elapsed), last_task)
                next_heartbeat = now + 60
            if cancel_event.wait(0.1):
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                raise BuildCancelled()
            try:
                line = lines.get(timeout=0.2)
            except Empty:
                continue
            if line is None:
                stream_done = True
                continue
            log.write(line)
            log.flush()
            task_match = re.match(r"^> Task ([A-Za-z0-9_:.-]+)", line.strip())
            if task_match:
                last_task = task_match.group(1)
                if "externalNativeBuildDebug" in last_task:
                    progress("Running Gradle", 68)
                elif "externalNativeBuild" in last_task or "configureCMake" in last_task or "buildCMake" in last_task:
                    progress("Building native library", 58)
                elif "packageDebug" in last_task or "assembleDebug" in last_task:
                    progress("Creating APK", 88)
        return_code = process.wait()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        while reader.is_alive():
            try:
                lines.get(timeout=0.05)
            except Empty:
                pass
        reader.join(timeout=1)
        if process.stdout is not None:
            process.stdout.close()
    if return_code != 0:
        detail = _gradle_failure_detail(log.path)
        if detail:
            raise BuildFailure(f"The Android debug build failed: {detail}")
        raise BuildFailure("The Android debug build failed. Review the private server-side build log and try again.")


def _gradle_failure_detail(log_path: Path) -> str | None:
    """Extract short Gradle failure context while hiding credentials and server paths."""
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    details: list[str] = []

    def add_line(value: str, *, force: bool = False) -> None:
        value = value.strip()
        if not value or _PRIVATE_DIAGNOSTIC.search(value):
            return
        matched = any(marker.casefold() in value.casefold() for marker in _DIAGNOSTIC_MARKERS)
        if not force and not matched and not _KOTLIN_DIAGNOSTIC.search(value):
            return
        value = _UNIX_PATH.sub("<path>", value)
        value = _WINDOWS_PATH.sub("<path>", value)
        value = value[:240]
        if value not in details and len(details) < 5:
            details.append(value)

    for index, line in enumerate(lines):
        add_line(line)
        if "What went wrong:" in line:
            for context in lines[index + 1:index + 5]:
                if context.strip().startswith("*"):
                    break
                add_line(context, force=True)
    return " | ".join(details)[:900] or None


def _audit_apk(apk: Path) -> None:
    with zipfile.ZipFile(apk) as archive:
        names = set(archive.namelist())
        if "assets/app.dat" not in names:
            raise BuildFailure("The APK is missing its protected web package.")
        if any(name.startswith("assets/") and name not in {"assets/", "assets/app.dat"} for name in names):
            raise BuildFailure("The APK contains an unprotected web resource and was rejected.")
        if not any(name.startswith("lib/") and name.endswith("/libprotected_loader.so") for name in names):
            raise BuildFailure("The APK is missing its native protected-content loader.")


def _verify_apk_signature(apk: Path, env: dict[str, str], log) -> None:
    sdk = Path(env["ANDROID_SDK_ROOT"])
    candidates = [sdk / "build-tools/35.0.0/apksigner", sdk / "build-tools/35.0.0/apksigner.bat"]
    signer = next((path for path in candidates if path.is_file()), None)
    if signer is None:
        from shutil import which

        found = which("apksigner")
        signer = Path(found) if found else None
    if signer is None:
        raise BuildFailure("The Android APK signature verifier is unavailable.")
    result = subprocess.run([str(signer), "verify", "--verbose", str(apk)], cwd=apk.parent, env=env,
                            capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    log.write(result.stdout)
    log.write(result.stderr)
    if result.returncode != 0:
        raise BuildFailure("The APK signature could not be verified.")


def _verify_apk_package(apk: Path, package_name: str, env: dict[str, str], log) -> None:
    sdk = Path(env["ANDROID_SDK_ROOT"])
    candidates = [sdk / "build-tools/35.0.0/aapt", sdk / "build-tools/35.0.0/aapt.exe"]
    aapt = next((path for path in candidates if path.is_file()), None)
    if aapt is None:
        from shutil import which

        found = which("aapt")
        aapt = Path(found) if found else None
    if aapt is None:
        raise BuildFailure("The Android APK metadata verifier is unavailable.")
    result = subprocess.run([str(aapt), "dump", "badging", str(apk)], cwd=apk.parent, env=env,
                            capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    log.write(result.stdout)
    log.write(result.stderr)
    expected = f"package: name='{package_name}'"
    if result.returncode != 0 or expected not in result.stdout:
        raise BuildFailure("The APK does not contain the requested Android package name.")
