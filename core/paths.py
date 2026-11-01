"""Cross-platform path resolution: bundled read-only resources vs. writable per-user data.

* **Bundled resources** (``data/roms_database.json``, ``assets/``) come from the PyInstaller bundle
  (``sys._MEIPASS``) when frozen, otherwise from the project folder.
* **User data** (``user_database.json``, ``imported_sha.json``) goes to the OS-native location:

  ============  ==========================================================
  Windows       ``%APPDATA%\\BackBitROMCompanion``
  macOS         ``~/Library/Application Support/BackBitROMCompanion``
  Linux / BSD   ``$XDG_DATA_HOME/BackBitROMCompanion`` (default ``~/.local/share``)
  ============  ==========================================================

* **Portable mode**: if ``portable.mode`` or a writable ``portable_data/`` folder sits next to the executable
  (next to the ``.app`` bundle on macOS, next to ``main.py`` when run from source), all user data is kept in
  ``portable_data/`` so the app runs self-contained from a USB stick.
* ``BACKBIT_DATA_DIR`` overrides everything (handy for tests and shared bench machines).
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Mapping

APP_NAME = "BackBit ROM Companion"
APP_DIR_NAME = "BackBitROMCompanion"
PORTABLE_FLAG = "portable.mode"
PORTABLE_DIR = "portable_data"
ENV_OVERRIDE = "BACKBIT_DATA_DIR"

_SOURCE_ROOT = Path(__file__).resolve().parent.parent


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def bundle_dir() -> Path:
    """Root of the read-only bundled resources."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return _SOURCE_ROOT


def resource_path(*parts: str) -> Path:
    return bundle_dir().joinpath(*parts)


def master_db_path() -> Path:
    return resource_path("data", "roms_database.json")


def icon_path(ext: str = "png") -> Path:
    return resource_path("assets", f"icon.{ext}")


def app_dir(executable: str | None = None, frozen: bool | None = None, platform: str | None = None) -> Path:
    """The folder the user sees the program in (where ``portable.mode`` is looked for)."""
    frozen = is_frozen() if frozen is None else frozen
    platform = platform or sys.platform
    if not frozen:
        return _SOURCE_ROOT
    exe = Path(executable or sys.executable).resolve()
    if platform == "darwin":
        # .../Foo.app/Contents/MacOS/Foo  ->  folder containing Foo.app
        for parent in exe.parents:
            if parent.suffix == ".app":
                return parent.parent
    return exe.parent


def native_user_data_dir(platform: str | None = None, env: Mapping[str, str] | None = None,
                         home: Path | None = None) -> Path:
    platform = platform or sys.platform
    env = os.environ if env is None else env
    home = Path(home) if home is not None else Path.home()
    if platform.startswith("win"):
        base = Path(env["APPDATA"]) if env.get("APPDATA") else home / "AppData" / "Roaming"
    elif platform == "darwin":
        base = home / "Library" / "Application Support"
    else:
        xdg = env.get("XDG_DATA_HOME", "")
        # The XDG spec says relative paths must be ignored.
        base = Path(xdg) if xdg and Path(xdg).is_absolute() else home / ".local" / "share"
    return base / APP_DIR_NAME


def is_writable_dir(path: Path) -> bool:
    """Real write test (os.access is unreliable for directories on Windows)."""
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path, prefix=".write_test_", delete=True):
            pass
        return True
    except OSError:
        return False


def portable_data_dir(base: Path | None = None) -> Path | None:
    """``<app dir>/portable_data`` if portable mode is requested and usable, else None."""
    base = app_dir() if base is None else Path(base)
    target = base / PORTABLE_DIR
    if (base / PORTABLE_FLAG).exists() or target.is_dir():
        if is_writable_dir(target):
            return target
    return None


def user_data_dir(base: Path | None = None, platform: str | None = None, env: Mapping[str, str] | None = None,
                  home: Path | None = None) -> Path:
    env = os.environ if env is None else env
    if env.get(ENV_OVERRIDE):
        return Path(env[ENV_OVERRIDE]).expanduser()
    portable = portable_data_dir(base)
    if portable is not None:
        return portable
    return native_user_data_dir(platform, env, home)


def is_portable(data_dir: Path | None = None, base: Path | None = None) -> bool:
    data_dir = user_data_dir(base) if data_dir is None else data_dir
    base = app_dir() if base is None else Path(base)
    return Path(data_dir).resolve() == (base / PORTABLE_DIR).resolve()


def master_db_writable(db_path: Path) -> bool:
    """User edits may also update the master JSON only when running from source.

    Inside a frozen bundle the master is either a throw-away temp copy (one-file builds), replaced by the
    next update (one-folder builds) or part of a signed macOS ``.app`` - so edits go to the user overlay only."""
    db_path = Path(db_path).resolve()
    if is_frozen():
        try:
            db_path.relative_to(bundle_dir().resolve())
            return False
        except ValueError:
            pass
    if db_path.exists():
        return os.access(db_path, os.W_OK)
    return is_writable_dir(db_path.parent)
