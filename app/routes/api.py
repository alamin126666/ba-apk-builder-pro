from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse

from app.auth import require_auth, require_csrf
from app.builder import signing_is_configured
from app.project_files import UploadValidationError
from app.security import validate_app_name, validate_package_name, valid_build_id

router = APIRouter(prefix="/api")


async def _save_upload(upload: UploadFile, path: Path, remaining: int) -> int:
    total = 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as target:
        try:
            path.chmod(0o600)
        except OSError:
            pass
        while block := await upload.read(1024 * 1024):
            total += len(block)
            if total > remaining:
                raise HTTPException(status_code=413, detail="The upload exceeds the configured size limit.")
            target.write(block)
    return total


def _web_filename(upload: UploadFile) -> str:
    original = (upload.filename or "").replace("\\", "/").split("/")[-1]
    if Path(original).suffix.lower() not in {".html", ".htm", ".zip"}:
        raise HTTPException(status_code=400, detail="Upload one .html file or a .zip web project.")
    return original[:180] or "project.html"


def _logo_filename(upload: UploadFile | None) -> str | None:
    if upload is None or not upload.filename:
        return None
    original = upload.filename.replace("\\", "/").split("/")[-1]
    if Path(original).suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
        raise HTTPException(status_code=400, detail="Use a PNG, JPG, or WEBP app icon.")
    return original[:180]


@router.get("/builds")
async def builds(request: Request) -> dict:
    require_auth(request)
    return {"builds": request.app.state.build_manager.history()}


@router.post("/build", status_code=status.HTTP_202_ACCEPTED)
async def create_build(
    request: Request,
    app_name: str = Form(...),
    package_name: str = Form(...),
    web_project: UploadFile = File(...),
    logo: UploadFile | None = File(None),
) -> dict:
    require_csrf(request)
    if not signing_is_configured():
        raise HTTPException(status_code=503, detail="Release signing is not configured on this server.")
    try:
        clean_name = validate_app_name(app_name)
        clean_package = validate_package_name(package_name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    project_filename = _web_filename(web_project)
    logo_filename = _logo_filename(logo)
    manager = request.app.state.build_manager
    job = manager.reserve(clean_name, clean_package, project_filename)
    limit = request.app.state.settings.max_upload_mb * 1024 * 1024
    used = 0
    try:
        used += await _save_upload(web_project, job.project_upload, limit - used)
        if logo_filename and logo:
            job.logo_upload = manager.root / job.id / "incoming" / "logo.upload"
            used += await _save_upload(logo, job.logo_upload, limit - used)
        if not manager.enqueue(job):
            manager.discard(job)
            raise HTTPException(status_code=429, detail="The build queue is full. Wait for a build to finish and try again.")
    except Exception:
        if manager.get(job.id) is not None:
            manager.discard(job)
        raise
    finally:
        await web_project.close()
        if logo is not None:
            await logo.close()
    return {"build": manager.public(job)}


@router.get("/build/{build_id}/status")
async def build_status(build_id: str, request: Request) -> dict:
    require_auth(request)
    job = request.app.state.build_manager.get(build_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Build not found.")
    return {"build": request.app.state.build_manager.public(job)}


@router.post("/build/{build_id}/cancel")
async def cancel_build(build_id: str, request: Request) -> dict:
    require_csrf(request)
    job = request.app.state.build_manager.get(build_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Build not found.")
    request.app.state.build_manager.cancel(job)
    return {"build": request.app.state.build_manager.public(job)}


@router.get("/build/{build_id}/download")
async def download_build(build_id: str, request: Request) -> FileResponse:
    require_auth(request)
    if not valid_build_id(build_id):
        raise HTTPException(status_code=404, detail="Build not found.")
    job = request.app.state.build_manager.get(build_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Build not found.")
    if job.status != "success":
        raise HTTPException(status_code=409, detail="This build does not have a downloadable APK.")
    apk = request.app.state.build_manager.root / job.id / "result.apk"
    if not apk.is_file():
        raise HTTPException(status_code=410, detail="This APK has expired. Start a new build.")
    safe_name = "".join(char.lower() if char.isalnum() else "-" for char in job.app_name).strip("-")[:48] or "android-app"
    return FileResponse(
        apk,
        media_type="application/vnd.android.package-archive",
        filename=f"{safe_name}.apk",
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )
