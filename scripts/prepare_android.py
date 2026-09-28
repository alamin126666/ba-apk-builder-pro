from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

from app.security import validate_app_name, validate_package_name, xml_attribute

_DENSITIES = {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}


def _read_logo(logo: Path | None) -> Image.Image:
    if logo is None:
        image = Image.new("RGBA", (512, 512), "#6757e8")
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((96, 96, 416, 416), radius=96, fill="#ffffff")
        draw.text((180, 135), "APK", fill="#5141cf")
        return image
    try:
        with Image.open(logo) as probe:
            if probe.format not in {"PNG", "JPEG", "WEBP"}:
                raise ValueError("Use a PNG, JPG, or WEBP app icon.")
            if probe.width < 1 or probe.height < 1 or probe.width * probe.height > 40_000_000:
                raise ValueError("The app icon dimensions are too large.")
            probe.verify()
        with Image.open(logo) as source:
            source.load()
            return source.convert("RGBA")
    except (OSError, Image.DecompressionBombError) as exc:
        raise ValueError("The uploaded app icon is not a valid PNG, JPG, or WEBP image.") from exc


def generate_icons(logo: Path | None, res_dir: Path) -> None:
    image = _read_logo(logo)
    for density, size in _DENSITIES.items():
        target = res_dir / f"mipmap-{density}" / "ic_launcher.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        ImageOps.fit(image, (size, size), method=Image.Resampling.LANCZOS).save(target, "PNG", optimize=True)
    foreground = Image.new("RGBA", (432, 432), (0, 0, 0, 0))
    fitted = ImageOps.contain(image, (280, 280), method=Image.Resampling.LANCZOS)
    foreground.alpha_composite(fitted, ((432 - fitted.width) // 2, (432 - fitted.height) // 2))
    drawable = res_dir / "drawable-nodpi"
    drawable.mkdir(parents=True, exist_ok=True)
    foreground.save(drawable / "ic_launcher_foreground.png", "PNG", optimize=True)
    adaptive = res_dir / "mipmap-anydpi-v26"
    adaptive.mkdir(parents=True, exist_ok=True)
    (adaptive / "ic_launcher.xml").write_text(
        '<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">\n'
        '    <background android:drawable="@color/ic_launcher_background" />\n'
        '    <foreground android:drawable="@drawable/ic_launcher_foreground" />\n'
        '</adaptive-icon>\n', encoding="utf-8"
    )
    values = res_dir / "values"
    values.mkdir(parents=True, exist_ok=True)
    (values / "ic_launcher_colors.xml").write_text(
        '<resources><color name="ic_launcher_background">#6555E8</color></resources>\n', encoding="utf-8"
    )


def prepare(template: Path, output: Path, app_name: str, package_name: str, logo: Path | None, key_hex: str) -> None:
    app_name = validate_app_name(app_name)
    package_name = validate_package_name(package_name)
    if len(key_hex) != 64:
        raise ValueError("The generated protection key is invalid.")
    try:
        key = bytes.fromhex(key_hex)
    except ValueError as exc:
        raise ValueError("The generated protection key is invalid.") from exc
    if len(key) != 32:
        raise ValueError("The generated protection key is invalid.")
    shutil.copytree(template, output)
    main_src = output / "app/src/main/java/TEMPLATE_PACKAGE"
    if not main_src.is_dir():
        raise ValueError("The Android template source package is missing.")
    package_dir = output / "app/src/main/java" / Path(*package_name.split("."))
    package_dir.parent.mkdir(parents=True, exist_ok=True)
    main_src.replace(package_dir)
    substitutions = {
        "@@PACKAGE_NAME@@": package_name,
        "@@APP_NAME_XML@@": xml_attribute(app_name),
        "@@JNI_CLASS@@": (package_name + ".NativeLoader").replace(".", "/"),
    }
    for source in output.rglob("*"):
        if source.is_file() and source.suffix.lower() in {".kt", ".kts", ".xml", ".cpp", ".txt", ".pro"}:
            text = source.read_text(encoding="utf-8")
            for token, replacement in substitutions.items():
                text = text.replace(token, replacement)
            source.write_text(text, encoding="utf-8", newline="\n")
    key_bytes = ", ".join(f"0x{value:02x}" for value in key)
    header = "#pragma once\n#include <cstdint>\nstatic constexpr uint8_t kPackageKey[32] = {" + key_bytes + "};\n"
    (output / "app/src/main/cpp/key_material.h").write_text(header, encoding="ascii")
    generate_icons(logo, output / "app/src/main/res")


def main() -> None:
    parser = argparse.ArgumentParser(description="Copy and configure the trusted Android template for a build.")
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--app-name", required=True)
    parser.add_argument("--package-name", required=True)
    parser.add_argument("--logo", type=Path)
    parser.add_argument("--key-file", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.template, args.output, args.app_name, args.package_name, args.logo,
            args.key_file.read_text(encoding="ascii").strip())


if __name__ == "__main__":
    main()
