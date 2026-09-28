from __future__ import annotations

import argparse
import secrets
import struct
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"WPK1"
MAX_ENTRIES = 5000
MAX_PATH_BYTES = 4096
MAX_RESOURCE_BYTES = 256 * 1024 * 1024
MAX_PACKAGE_BYTES = 512 * 1024 * 1024


def create_container(files: list[tuple[str, Path]], key: bytes | None = None) -> tuple[bytes, bytes]:
    if not files or len(files) > MAX_ENTRIES:
        raise ValueError("The protected package has an invalid number of files.")
    key = key or secrets.token_bytes(32)
    if len(key) != 32:
        raise ValueError("AES-256 requires a 32-byte key.")
    output = bytearray(struct.pack(">4sI", MAGIC, len(files)))
    cipher = AESGCM(key)
    names: set[str] = set()
    for name, path in sorted(files, key=lambda item: item[0]):
        canonical = Path(name).as_posix()
        if canonical.startswith("/") or ".." in Path(canonical).parts or "\\" in canonical:
            raise ValueError("An unsafe resource path was found.")
        path_bytes = canonical.encode("utf-8")
        if not path_bytes or len(path_bytes) > MAX_PATH_BYTES or canonical.casefold() in names:
            raise ValueError("A resource path is invalid or duplicated.")
        names.add(canonical.casefold())
        content = path.read_bytes()
        if len(content) > MAX_RESOURCE_BYTES:
            raise ValueError("A web resource exceeds the maximum supported size.")
        nonce = secrets.token_bytes(12)
        encrypted = cipher.encrypt(nonce, content, path_bytes)
        output.extend(struct.pack(">HQ", len(path_bytes), len(encrypted)))
        output.extend(nonce)
        output.extend(path_bytes)
        output.extend(encrypted)
        if len(output) > MAX_PACKAGE_BYTES:
            raise ValueError("The protected web package exceeds the maximum supported size.")
    return bytes(output), key


def protect_directory(source: Path, output: Path, key_output: Path) -> None:
    from app.project_files import copy_allowed_tree

    files = copy_allowed_tree(source, source)
    container, key = create_container(files)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(container)
    key_output.write_text(key.hex(), encoding="ascii")
    try:
        key_output.chmod(0o600)
    except OSError:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Encrypt a validated web project into the WPK1 container.")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--key-output", type=Path, required=True)
    args = parser.parse_args()
    protect_directory(args.source, args.output, args.key_output)


if __name__ == "__main__":
    main()
