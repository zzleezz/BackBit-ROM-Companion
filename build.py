"""Build a standalone BackBit ROM Companion for the current OS with PyInstaller.

Usage:
    python build.py                 # one-folder build (fast start-up) -> dist/BackBitROMCompanion[.app]
    python build.py --onefile       # single executable -> dist/onefile/
    python build.py --archive       # also zip / tar.gz the result for distribution
    python build.py --clean         # wipe build/ and dist/ first

Requires:  pip install -r requirements.txt -r requirements-dev.txt
"""

from __future__ import annotations

import argparse
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP_NAME = "BackBitROMCompanion"
DISPLAY_NAME = "BackBit ROM Companion"
BUNDLE_ID = "io.github.backbitromcompanion"
DIST = ROOT / "dist"
ONEFILE_DIST = DIST / "onefile"   # kept apart: on Linux the one-file binary and one-folder dir share a name
BUILD = ROOT / "build"


def app_version() -> str:
    text = (ROOT / "core" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    if not m:
        raise SystemExit("Cannot find __version__ in core/__init__.py")
    return m.group(1)


def os_tag() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def arch_tag() -> str:
    m = platform.machine().lower()
    return {"amd64": "x64", "x86_64": "x64", "x64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(m, m or "unknown")


def ensure_icons() -> None:
    needed = [ROOT / "assets" / n for n in ("icon.png", "icon.ico", "icon.icns")]
    if all(p.exists() for p in needed):
        return
    print("Generating icons ...")
    subprocess.run([sys.executable, str(ROOT / "scripts" / "generate_icons.py")], check=True, cwd=ROOT)


def pyinstaller_args(onefile: bool) -> list[str]:
    sep = os.pathsep  # PyInstaller --add-data uses ';' on Windows and ':' elsewhere
    icon = {"windows": "icon.ico", "macos": "icon.icns"}.get(os_tag(), "icon.png")
    args = [
        sys.executable, "-m", "PyInstaller", str(ROOT / "main.py"),
        "--name", APP_NAME,
        "--noconfirm",
        "--windowed",
        "--onefile" if onefile else "--onedir",
        "--distpath", str(ONEFILE_DIST if onefile else DIST),
        "--workpath", str(BUILD),
        "--specpath", str(BUILD),
        "--icon", str(ROOT / "assets" / icon),
        "--add-data", f"{ROOT / 'data' / 'roms_database.json'}{sep}data",
        "--add-data", f"{ROOT / 'assets'}{sep}assets",
        "--exclude-module", "tkinter",
    ]
    if os_tag() == "macos":
        args += ["--osx-bundle-identifier", BUNDLE_ID]
    return args


def built_target(onefile: bool) -> Path:
    base = ONEFILE_DIST if onefile else DIST
    if os_tag() == "macos":
        app = base / f"{APP_NAME}.app"
        if app.exists():
            return app
    if onefile:
        return base / (f"{APP_NAME}.exe" if os_tag() == "windows" else APP_NAME)
    return base / APP_NAME


def make_archive(target: Path, version: str, onefile: bool) -> Path:
    kind = "onefile" if onefile else "portable"
    stem = f"{APP_NAME}-v{version}-{os_tag()}-{arch_tag()}" + ("-onefile" if onefile else "")
    if os_tag() == "macos":
        out = DIST / f"{stem}.zip"
        out.unlink(missing_ok=True)
        # ditto keeps the .app's symlinks, permissions and extended attributes intact (zipfile would not).
        subprocess.run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(target), str(out)], check=True)
    elif os_tag() == "linux":
        out = DIST / f"{stem}.tar.gz"
        with tarfile.open(out, "w:gz") as tar:  # tar keeps the executable bit
            tar.add(target, arcname=target.name)
            tar.add(ROOT / "README.md", arcname=f"{target.name}-README.md" if onefile else
                    f"{target.name}/README.md")
    else:
        out = DIST / f"{stem}.zip"
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            if target.is_dir():
                for f in sorted(target.rglob("*")):
                    zf.write(f, f.relative_to(target.parent).as_posix())
                zf.write(ROOT / "README.md", f"{target.name}/README.md")
            else:
                zf.write(target, target.name)
                zf.write(ROOT / "README.md", "README.md")
    print(f"Archive ({kind}): {out}  [{out.stat().st_size / 1e6:.1f} MB]")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--onedir", action="store_true", help="one-folder build (default, fastest start-up)")
    mode.add_argument("--onefile", action="store_true", help="single self-extracting executable")
    ap.add_argument("--archive", action="store_true", help="zip / tar.gz the build for distribution")
    ap.add_argument("--clean", action="store_true", help="delete build/ and dist/ first")
    ap.add_argument("--dry-run", action="store_true", help="print the PyInstaller command and exit")
    args = ap.parse_args(argv)

    version = app_version()
    cmd = pyinstaller_args(args.onefile)
    if args.dry_run:
        print(" ".join(f'"{c}"' if " " in c else c for c in cmd))
        return 0
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller is not installed. Run:  pip install -r requirements-dev.txt", file=sys.stderr)
        return 2
    if args.clean:
        for d in (BUILD, DIST):
            shutil.rmtree(d, ignore_errors=True)
    if args.onefile and os_tag() == "macos":
        print("Note: PyInstaller deprecates --onefile for windowed macOS apps; the one-folder .app is recommended.")
    ensure_icons()
    print(f"Building {DISPLAY_NAME} v{version} for {os_tag()}-{arch_tag()} "
          f"({'one-file' if args.onefile else 'one-folder'}) ...")
    subprocess.run(cmd, check=True, cwd=ROOT)
    target = built_target(args.onefile)
    if not target.exists():
        print(f"Build finished but {target} was not found.", file=sys.stderr)
        return 1
    print(f"Built: {target}")
    if args.archive:
        make_archive(target, version, args.onefile)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
