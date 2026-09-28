from __future__ import annotations

import shutil
import stat
import zipfile
import unicodedata
from pathlib import Path, PurePosixPath

WEB_EXTENSIONS = {
    ".html", ".htm", ".xhtml", ".css", ".js", ".mjs", ".json", ".webmanifest", ".txt", ".md", ".xml", ".svg",
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico", ".avif", ".woff", ".woff2",
    ".ttf", ".otf", ".eot", ".wasm", ".bin", ".data", ".gltf", ".glb", ".mp3", ".mp4", ".m4a",
    ".m4v", ".mpeg", ".mpg", ".ogg", ".wav", ".aac", ".mov", ".webm", ".pdf", ".map",
}
_IGNORED_PARTS = {"__MACOSX"}
MAX_PROJECT_FILES = 1000


class UploadValidationError(ValueError):
    pass


def stage_web_project(upload_path: Path, filename: str, destination: Path, max_upload_bytes: int) -> Path:
    """Validate an HTML or ZIP upload and safely stage only web project files."""
    suffix = Path(filename.replace("\\", "/")).suffix.lower()
    if suffix not in {".html", ".htm", ".zip"}:
        raise UploadValidationError("Upload one .html file or a .zip web project.")
    if upload_path.stat().st_size > max_upload_bytes:
        raise UploadValidationError("The uploaded project exceeds the configured upload limit.")
    destination.mkdir(parents=True, exist_ok=False)
    if suffix in {".html", ".htm"}:
        data = upload_path.read_bytes()
        if not data or b"\x00" in data or not _looks_like_html(data):
            raise UploadValidationError("The uploaded file is not a valid HTML document.")
        (destination / "index.html").write_bytes(data)
        return destination
    if not zipfile.is_zipfile(upload_path):
        raise UploadValidationError("The uploaded .zip file is invalid.")
    expanded_limit = min(max_upload_bytes * 8, 512 * 1024 * 1024)
    seen: set[str] = set()
    total = 0
    file_count = 0
    with zipfile.ZipFile(upload_path) as archive:
        entries = archive.infolist()
        if len(entries) > MAX_PROJECT_FILES * 2:
            raise UploadValidationError("The ZIP contains too many entries.")
        for info in entries:
            name = info.filename
            if not name or "\x00" in name or "\\" in name or ":" in name:
                raise UploadValidationError("The ZIP contains an unsafe file path.")
            raw_parts = name.split("/")
            check_parts = raw_parts[:-1] if info.is_dir() and raw_parts[-1] == "" else raw_parts
            if any(part in {"", ".", ".."} for part in check_parts):
                raise UploadValidationError("The ZIP contains an unsafe file path.")
            path = PurePosixPath(name)
            if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
                # A final empty component is normal for a directory marker.
                if not (info.is_dir() and name.endswith("/") and not path.is_absolute()
                        and all(part not in {".", ".."} for part in path.parts)):
                    raise UploadValidationError("The ZIP contains an unsafe file path.")
            if any(part in _IGNORED_PARTS for part in path.parts):
                continue
            if any(any(unicodedata.category(character).startswith("C") for character in part) for part in path.parts):
                raise UploadValidationError("The ZIP contains an unsafe file path.")
            if path.name == ".DS_Store":
                continue
            if info.flag_bits & 0x1:
                raise UploadValidationError("Password-protected ZIP files are not supported.")
            mode = (info.external_attr >> 16) & 0xFFFF
            file_type = stat.S_IFMT(mode)
            if file_type == stat.S_IFLNK or file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
                raise UploadValidationError("The ZIP contains a non-regular file.")
            if info.is_dir():
                continue
            if path.suffix.lower() not in WEB_EXTENSIONS:
                raise UploadValidationError("The ZIP contains a file type that is not allowed in a web project.")
            normalized = path.as_posix()
            folded = normalized.casefold()
            if folded in seen:
                raise UploadValidationError("The ZIP contains duplicate file paths.")
            seen.add(folded)
            file_count += 1
            total += info.file_size
            if file_count > MAX_PROJECT_FILES or total > expanded_limit:
                raise UploadValidationError("The ZIP expands beyond the allowed project size.")
            if info.file_size and info.compress_size == 0:
                raise UploadValidationError("The ZIP contains an invalid compressed entry.")
            if info.file_size > 1024 * 1024 and info.file_size / max(info.compress_size, 1) > 200:
                raise UploadValidationError("The ZIP has an unsafe compression ratio.")
            target = destination.joinpath(*path.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                with archive.open(info, "r") as source, target.open("xb") as output:
                    copied = 0
                    while True:
                        block = source.read(1024 * 1024)
                        if not block:
                            break
                        copied += len(block)
                        if copied > info.file_size or copied > expanded_limit:
                            raise UploadValidationError("The ZIP contains an invalid oversized entry.")
                        output.write(block)
                    if copied != info.file_size:
                        raise UploadValidationError("The ZIP contains a truncated file.")
            except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
                raise UploadValidationError("The ZIP could not be safely extracted.") from exc
    if file_count == 0:
        raise UploadValidationError("The ZIP does not contain any web files.")
    root_index = destination / "index.html"
    if root_index.is_file():
        return destination
    root_htm = destination / "index.htm"
    if root_htm.is_file():
        root_htm.replace(root_index)
        return destination
    children = list(destination.iterdir())
    if len(children) == 1 and children[0].is_dir():
        nested = children[0]
        nested_index = nested / "index.html"
        nested_htm = nested / "index.htm"
        if nested_htm.is_file() and not nested_index.exists():
            nested_htm.replace(nested_index)
        if nested_index.is_file():
            return nested
    raise UploadValidationError("Place index.html at the ZIP root (or inside one top-level project folder).")


def _looks_like_html(data: bytes) -> bool:
    sample = data[:8192].lstrip().lower()
    return b"<html" in sample or b"<!doctype html" in sample or b"<head" in sample or b"<body" in sample


def copy_allowed_tree(source: Path, destination: Path) -> list[tuple[str, Path]]:
    """Return sorted project files and copy no executable or symlink entries."""
    destination.mkdir(parents=True, exist_ok=True)
    files: list[tuple[str, Path]] = []
    for path in source.rglob("*"):
        if path.is_symlink():
            raise UploadValidationError("The project contains an unsafe symbolic link.")
        if not path.is_file():
            continue
        relative = path.relative_to(source)
        if relative.suffix.lower() not in WEB_EXTENSIONS:
            raise UploadValidationError("The project contains a file type that is not allowed in a web project.")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if path.resolve() != target.resolve():
            shutil.copyfile(path, target)
        files.append((relative.as_posix(), target))
    files.sort(key=lambda item: item[0])
    if not any(name.casefold() == "index.html" for name, _ in files):
        raise UploadValidationError("The web project must contain an index.html file.")
    return files
