from __future__ import annotations

import argparse
import logging
import shutil
import threading
import uuid
from pathlib import Path

from app.builder import BuildFailure, execute_build
from app.config import Settings
from app.security import validate_app_name, validate_package_name


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the same signed APK pipeline used by the private dashboard.")
    parser.add_argument("--project", type=Path, required=True, help="An HTML file or ZIP project")
    parser.add_argument("--app-name", required=True)
    parser.add_argument("--package-name", required=True)
    parser.add_argument("--logo", type=Path)
    parser.add_argument("--output", type=Path, required=True, help="Destination for the signed APK")
    parser.add_argument("--log", type=Path, help="Private server-side diagnostic log")
    args = parser.parse_args()
    settings = Settings.from_env()
    name = validate_app_name(args.app_name)
    package = validate_package_name(args.package_name)
    build_id = uuid.uuid4().hex
    args.output.parent.mkdir(parents=True, exist_ok=True)
    log_path = args.log or args.output.with_suffix(".build.log")
    work_root = settings.build_root / build_id
    try:
        args.output.resolve().relative_to(work_root.resolve())
    except ValueError:
        pass
    else:
        parser.error("--output must be outside the temporary build workspace")

    def progress(stage: str, percent: int) -> None:
        print(f"{percent:3d}%  {stage}", flush=True)

    try:
        execute_build(settings, build_id, name, package, args.project, args.project.name,
                      args.logo, args.output, log_path, progress, threading.Event())
    except (BuildFailure, ValueError) as exc:
        logging.error("Build failed: %s", exc.safe_message if isinstance(exc, BuildFailure) else str(exc))
        return 1
    except Exception:
        logging.error("Build failed. Review the private diagnostic log at %s", log_path)
        return 1
    finally:
        shutil.rmtree(work_root / "work", ignore_errors=True)
        try:
            work_root.rmdir()
        except OSError:
            pass
    print(f"Release APK created at {args.output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
