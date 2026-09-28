from __future__ import annotations

import io
import re
import stat
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.project_files import UploadValidationError, stage_web_project
from app.security import SessionCookies, validate_app_name, validate_package_name, xml_attribute
from scripts.prepare_android import prepare
from scripts.protect_web import create_container


def test_app_name_validation_and_xml_escape() -> None:
    assert validate_app_name("  R&D <Tools>  ") == "R&D <Tools>"
    assert xml_attribute(validate_app_name('Me & "you"')) == "Me &amp; &quot;you&quot;"
    with pytest.raises(ValueError):
        validate_app_name("\n")
    with pytest.raises(ValueError):
        validate_app_name("x" * 51)


@pytest.mark.parametrize("package", ["com.example.myapp", "com.bd.alamin.webapp", "a.b_2.c3"])
def test_valid_package_names(package: str) -> None:
    assert validate_package_name(package) == package


@pytest.mark.parametrize("package", [
    "com", "com..example", "com.Example.app", "com.example/path", "com.example;exec",
    "java.lang.app", "android.app", "com.example.1bad", "../com.example.app", "com example.app",
])
def test_invalid_package_names(package: str) -> None:
    with pytest.raises(ValueError):
        validate_package_name(package)


def test_signed_session_and_csrf_binding() -> None:
    sessions = SessionCookies("random-test-secret")
    cookie, csrf = sessions.create()
    assert sessions.verify(cookie) == csrf
    assert sessions.check_csrf(csrf, csrf)
    assert not sessions.check_csrf(csrf, "wrong-token")
    assert sessions.verify(cookie + "x") is None


def test_single_html_upload_is_staged(tmp_path: Path) -> None:
    upload = tmp_path / "upload.bin"
    upload.write_bytes(b"<!doctype html><html><body>hello</body></html>")
    staged = stage_web_project(upload, "index.html", tmp_path / "staged", 1024)
    assert (staged / "index.html").read_bytes().startswith(b"<!doctype html>")


def test_zip_preserves_web_paths(tmp_path: Path) -> None:
    upload = tmp_path / "site.zip"
    with zipfile.ZipFile(upload, "w") as archive:
        archive.writestr("index.html", "<html><head><link href='css/site.css'></head></html>")
        archive.writestr("css/site.css", "body { color: #123; }")
        archive.writestr("images/icon.svg", "<svg/>")
    staged = stage_web_project(upload, "site.zip", tmp_path / "staged", 1024 * 1024)
    assert (staged / "css/site.css").read_text() == "body { color: #123; }"
    assert (staged / "images/icon.svg").is_file()


def test_zip_slip_is_rejected_before_writing_outside(tmp_path: Path) -> None:
    upload = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(upload, "w") as archive:
        archive.writestr("index.html", "<html></html>")
        archive.writestr("../outside.txt", "must not escape")
    with pytest.raises(UploadValidationError, match="unsafe file path"):
        stage_web_project(upload, "unsafe.zip", tmp_path / "stage", 1024 * 1024)
    assert not (tmp_path / "outside.txt").exists()


def test_zip_symlink_is_rejected(tmp_path: Path) -> None:
    upload = tmp_path / "link.zip"
    link = zipfile.ZipInfo("linked.txt")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(upload, "w") as archive:
        archive.writestr("index.html", "<html></html>")
        archive.writestr(link, "../../outside")
    with pytest.raises(UploadValidationError, match="symbolic link|non-regular"):
        stage_web_project(upload, "link.zip", tmp_path / "stage", 1024 * 1024)


def test_upload_size_is_limited(tmp_path: Path) -> None:
    upload = tmp_path / "index.html"
    upload.write_bytes(b"<html>oversized</html>")
    with pytest.raises(UploadValidationError, match="upload limit"):
        stage_web_project(upload, "index.html", tmp_path / "stage", 4)


def test_protected_package_authenticates_and_hides_html(tmp_path: Path) -> None:
    html = b"<!doctype html><title>private web source</title>"
    (tmp_path / "index.html").write_bytes(html)
    package, key = create_container([("index.html", tmp_path / "index.html")], key=b"k" * 32)
    assert package[:4] == b"WPK1"
    assert html not in package
    path_length, cipher_length = __import__("struct").unpack_from(">HQ", package, 8)
    offset = 18
    nonce = package[offset:offset + 12]
    path = package[offset + 12:offset + 12 + path_length]
    ciphertext = package[offset + 12 + path_length:offset + 12 + path_length + cipher_length]
    assert path == b"index.html"
    assert AESGCM(key).decrypt(nonce, ciphertext, path) == html
    tampered = bytearray(ciphertext)
    tampered[-1] ^= 0x01
    with pytest.raises(Exception):
        AESGCM(key).decrypt(nonce, bytes(tampered), path)


def test_android_template_preparation_escapes_xml_and_generates_icons(tmp_path: Path) -> None:
    logo = tmp_path / "logo.png"
    from PIL import Image

    Image.new("RGBA", (120, 80), "#6555e8").save(logo)
    output = tmp_path / "android"
    prepare(Path(__file__).resolve().parents[1] / "android-template", output,
            'R&D <Studio>', "com.example.studio", logo, "01" * 32)
    manifest = ET.parse(output / "app/src/main/AndroidManifest.xml").getroot()
    assert manifest.find("application").attrib["{http://schemas.android.com/apk/res/android}label"] == "R&D <Studio>"
    assert (output / "app/src/main/java/com/example/studio/NativeLoader.kt").is_file()
    assert (output / "app/src/main/res/mipmap-hdpi/ic_launcher.png").is_file()
    assert (output / "app/src/main/res/mipmap-anydpi-v26/ic_launcher.xml").is_file()
    native = (output / "app/src/main/cpp/native_loader.cpp").read_text()
    assert '#define APP_JNI_CLASS "com/example/studio/NativeLoader"' in native
    assert "@@PACKAGE_NAME@@" not in (output / "app/build.gradle.kts").read_text()
