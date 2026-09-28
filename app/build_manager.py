from __future__ import annotations

import asyncio
import logging
import shutil
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from app.models import BuildJob, utc_now

from app.builder import BuildCancelled, BuildFailure, execute_build
from app.config import Settings
from app.security import valid_build_id

logger = logging.getLogger("apk_builder")
_PHASES = [
    "Preparing project",
    "Validating files",
    "Protecting web assets",
    "Preparing Android project",
    "Building native library",
    "Running Gradle",
    "Creating APK",
    "Finalizing",
]


class BuildManager:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.root = settings.build_root
        self.jobs: dict[str, BuildJob] = {}
        self.queue: asyncio.Queue[str] | None = None
        self.workers: list[asyncio.Task] = []
        self.cleanup_task: asyncio.Task | None = None

    def start(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            self.root.chmod(0o700)
        except OSError:
            pass
        with self._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS builds (
                id TEXT PRIMARY KEY, app_name TEXT NOT NULL, package_name TEXT NOT NULL,
                status TEXT NOT NULL, stage TEXT NOT NULL, progress INTEGER NOT NULL,
                error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )""")
            # In-memory queues and active subprocesses do not survive a process restart.
            db.execute("UPDATE builds SET status='failed', stage='Interrupted', error=? WHERE status IN ('receiving','queued','building')",
                       ("The server restarted before this build finished.",))
            rows = db.execute("SELECT id, app_name, package_name, status, stage, progress, error, created_at, updated_at FROM builds ORDER BY created_at DESC LIMIT 100").fetchall()
            for row in rows:
                job_id, _name, _package, status, _stage, _progress, _error, _created, _updated = row
                if status == "success" and not (self.root / job_id / "result.apk").is_file():
                    db.execute("UPDATE builds SET status='failed', stage='APK expired', error=?, updated_at=? WHERE id=?",
                               ("The stored APK is no longer available. Start a new build.", utc_now(), job_id))
            rows = db.execute("SELECT id, app_name, package_name, status, stage, progress, error, created_at, updated_at FROM builds ORDER BY created_at DESC LIMIT 100").fetchall()
        for row in rows:
            job_id, name, package, status, stage, progress, error, created_at, updated_at = row
            self.jobs[job_id] = BuildJob(
                id=job_id, app_name=name, package_name=package,
                project_upload=self.root / job_id / "incoming/project.upload",
                project_filename="index.html", logo_upload=None, status=status, stage=stage,
                progress=progress, error=error, created_at=created_at, updated_at=updated_at,
            )
        self.queue = asyncio.Queue(maxsize=self.settings.queue_size)
        self.workers = [asyncio.create_task(self._worker(), name=f"apk-build-worker-{i + 1}")
                        for i in range(self.settings.max_concurrent_builds)]
        self.cleanup_task = asyncio.create_task(self._cleanup_loop(), name="apk-build-cleanup")

    async def close(self) -> None:
        for job in self.jobs.values():
            if job.status in {"receiving", "queued"}:
                job.status = "cancelled"
                job.stage = "Cancelled"
                job.error = "Build cancelled because the server is shutting down."
                self._touch(job)
                self._persist(job)
            elif job.status == "building":
                job.cancel_event.set()
        if self.cleanup_task:
            self.cleanup_task.cancel()
            await asyncio.gather(self.cleanup_task, return_exceptions=True)
        if self.queue is not None:
            await self.queue.join()
        for task in self.workers:
            task.cancel()
        if self.workers:
            await asyncio.gather(*self.workers, return_exceptions=True)

    def reserve(self, app_name: str, package_name: str, project_filename: str) -> BuildJob:
        job_id = uuid.uuid4().hex
        workspace = self.root / job_id
        (workspace / "incoming").mkdir(parents=True, exist_ok=False)
        try:
            workspace.chmod(0o700)
            (workspace / "incoming").chmod(0o700)
        except OSError:
            pass
        job = BuildJob(
            id=job_id,
            app_name=app_name,
            package_name=package_name,
            project_upload=workspace / "incoming" / "project.upload",
            project_filename=project_filename,
            logo_upload=workspace / "incoming" / "logo.upload",
        )
        self.jobs[job.id] = job
        self._persist(job)
        return job

    def enqueue(self, job: BuildJob) -> bool:
        if self.queue is None:
            raise RuntimeError("The build queue is not running.")
        try:
            self.queue.put_nowait(job.id)
        except asyncio.QueueFull:
            return False
        job.status = "queued"
        job.stage = "Waiting for a build slot"
        job.progress = 0
        self._touch(job)
        self._persist(job)
        return True

    def discard(self, job: BuildJob) -> None:
        self.jobs.pop(job.id, None)
        with self._db() as db:
            db.execute("DELETE FROM builds WHERE id=?", (job.id,))
        self._remove_workspace(job.id)

    def get(self, build_id: str) -> BuildJob | None:
        if not valid_build_id(build_id):
            return None
        return self.jobs.get(build_id)

    def history(self, limit: int = 50) -> list[dict]:
        rows = sorted(self.jobs.values(), key=lambda item: item.created_at, reverse=True)[:limit]
        return [self.public(job) for job in rows]

    def public(self, job: BuildJob) -> dict:
        result_apk = self.root / job.id / "result.apk"
        steps = []
        active_index = next((i for i, label in enumerate(_PHASES) if label == job.stage), None)
        for i, label in enumerate(_PHASES):
            if job.status == "success" or (active_index is not None and i < active_index):
                state = "done"
            elif job.status in {"failed", "cancelled"} and active_index == i:
                state = "failed" if job.status == "failed" else "pending"
            elif active_index == i or (job.status == "building" and label == "Running Gradle" and "Gradle" in job.stage):
                state = "active"
            else:
                state = "pending"
            steps.append({"label": label, "state": state})
        if job.status == "queued":
            steps = [{"label": label, "state": "pending"} for label in _PHASES]
        return {
            "id": job.id,
            "app_name": job.app_name,
            "package_name": job.package_name,
            "status": job.status,
            "stage": job.stage,
            "progress": job.progress,
            "steps": steps,
            "error": job.error,
            "created_at": job.created_at,
            "updated_at": job.updated_at,
            "download_url": f"/api/build/{job.id}/download" if job.status == "success" and result_apk.is_file() else None,
        }

    def cancel(self, job: BuildJob) -> bool:
        if job.status in {"success", "failed", "cancelled"}:
            return False
        job.cancel_event.set()
        if job.status in {"receiving", "queued"}:
            job.status = "cancelled"
            job.stage = "Cancelled"
            job.error = "Build cancelled."
        else:
            job.stage = "Cancelling build"
        self._touch(job)
        self._persist(job)
        return True

    async def _worker(self) -> None:
        assert self.queue is not None
        while True:
            job_id = await self.queue.get()
            job = self.jobs.get(job_id)
            if job is None:
                self.queue.task_done()
                continue
            try:
                if job.status == "cancelled" or job.cancel_event.is_set():
                    self._remove_workspace(job.id)
                    continue
                job.status = "building"
                job.stage = _PHASES[0]
                self._touch(job)
                self._persist(job)
                logger.info("Build %s started", job.id)
                output = self.root / job.id / "result.apk"
                log_path = self.root / job.id / "build.log"
                await asyncio.to_thread(
                    execute_build,
                    self.settings,
                    job.id,
                    job.app_name,
                    job.package_name,
                    job.project_upload,
                    job.project_filename,
                    job.logo_upload if job.logo_upload and job.logo_upload.exists() else None,
                    output,
                    log_path,
                    lambda stage, percent: self._update_progress(job, stage, percent),
                    job.cancel_event,
                )
                job.status = "success"
                job.stage = "Build complete"
                job.progress = 100
                job.error = None
                logger.info("Build %s completed", job.id)
            except BuildCancelled:
                job.status = "cancelled"
                job.stage = "Cancelled"
                job.error = "Build cancelled."
            except BuildFailure as exc:
                job.status = "failed"
                job.stage = "Build failed"
                job.error = exc.safe_message
                logger.warning("Build %s failed: %s; detailed diagnostics are in its private build log", job.id, exc.safe_message)
            except Exception:
                job.status = "failed"
                job.stage = "Build failed"
                job.error = "The Android build failed. Review the server-side build log and try again."
                logger.exception("Build %s failed unexpectedly", job.id)
            finally:
                self._remove_workspace_part(job.id, "incoming")
                self._remove_workspace_part(job.id, "work")
                if job.status != "success":
                    (self.root / job.id / "result.apk").unlink(missing_ok=True)
                self._touch(job)
                self._persist(job)
                self.queue.task_done()
                logger.info("Cleanup completed for build %s", job.id)

    def _update_progress(self, job: BuildJob, stage: str, progress: int) -> None:
        if job.cancel_event.is_set():
            return
        job.stage = stage
        job.progress = max(0, min(int(progress), 99))
        self._touch(job)
        self._persist(job)

    async def _cleanup_loop(self) -> None:
        while True:
            await asyncio.sleep(900)
            self.cleanup_expired()

    def cleanup_expired(self) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=self.settings.build_retention_hours)
        with self._db() as db:
            rows = db.execute("SELECT id, updated_at, status FROM builds").fetchall()
            expired = []
            for build_id, updated, status in rows:
                if status in {"receiving", "queued", "building"}:
                    continue
                try:
                    if datetime.fromisoformat(updated) < cutoff:
                        expired.append(build_id)
                except ValueError:
                    expired.append(build_id)
            for build_id in expired:
                if valid_build_id(build_id):
                    self._remove_workspace(build_id)
                db.execute("DELETE FROM builds WHERE id=?", (build_id,))
                self.jobs.pop(build_id, None)

    def _touch(self, job: BuildJob) -> None:
        job.updated_at = utc_now()

    def _persist(self, job: BuildJob) -> None:
        with self._db() as db:
            db.execute("""INSERT INTO builds (id, app_name, package_name, status, stage, progress, error, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET status=excluded.status, stage=excluded.stage,
                progress=excluded.progress, error=excluded.error, updated_at=excluded.updated_at""",
                       (job.id, job.app_name, job.package_name, job.status, job.stage, job.progress,
                        job.error, job.created_at, job.updated_at))

    @contextmanager
    def _db(self):
        connection = sqlite3.connect(self.root / "history.sqlite3", timeout=10)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _remove_workspace_part(self, build_id: str, part: str) -> None:
        if not valid_build_id(build_id) or part not in {"incoming", "work"}:
            return
        path = self.root / build_id / part
        if path.exists():
            shutil.rmtree(path)

    def _remove_workspace(self, build_id: str) -> None:
        if not valid_build_id(build_id):
            return
        path = self.root / build_id
        if path.exists():
            shutil.rmtree(path)
