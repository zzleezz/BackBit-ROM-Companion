"""Cross-platform path resolution tests (core/paths.py). Run:  python -m unittest discover -s tests -v"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path, PurePosixPath
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import paths  # noqa: E402

APP = paths.APP_DIR_NAME


class TestNativeLocations(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def test_windows(self):
        appdata = self.home / "Roaming"
        self.assertEqual(paths.native_user_data_dir("win32", {"APPDATA": str(appdata)}, self.home), appdata / APP)
        self.assertEqual(paths.native_user_data_dir("win32", {}, self.home),
                         self.home / "AppData" / "Roaming" / APP)

    def test_macos(self):
        self.assertEqual(paths.native_user_data_dir("darwin", {"APPDATA": "ignored"}, self.home),
                         self.home / "Library" / "Application Support" / APP)

    def test_linux_xdg(self):
        xdg = self.home / "xdg"
        self.assertEqual(paths.native_user_data_dir("linux", {"XDG_DATA_HOME": str(xdg)}, self.home), xdg / APP)
        default = self.home / ".local" / "share" / APP
        self.assertEqual(paths.native_user_data_dir("linux", {}, self.home), default)
        # the XDG spec says relative paths must be ignored
        self.assertEqual(paths.native_user_data_dir("linux", {"XDG_DATA_HOME": "rel/dir"}, self.home), default)
        self.assertEqual(paths.native_user_data_dir("freebsd13", {}, self.home), default)


class TestPortableAndOverride(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp())
        self.home = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)
        shutil.rmtree(self.home, ignore_errors=True)

    def native(self):
        return paths.native_user_data_dir("linux", {}, self.home)

    def test_not_portable_by_default(self):
        self.assertEqual(paths.user_data_dir(self.base, "linux", {}, self.home), self.native())
        self.assertFalse((self.base / paths.PORTABLE_DIR).exists())
        self.assertFalse(paths.is_portable(self.native(), self.base))

    def test_portable_flag_file(self):
        (self.base / paths.PORTABLE_FLAG).touch()
        d = paths.user_data_dir(self.base, "linux", {}, self.home)
        self.assertEqual(d, self.base / paths.PORTABLE_DIR)
        self.assertTrue(d.is_dir())  # created on demand
        self.assertTrue(paths.is_portable(d, self.base))

    def test_portable_data_folder(self):
        (self.base / paths.PORTABLE_DIR).mkdir()
        self.assertEqual(paths.user_data_dir(self.base, "darwin", {}, self.home), self.base / paths.PORTABLE_DIR)

    def test_unwritable_portable_falls_back(self):
        (self.base / paths.PORTABLE_FLAG).touch()
        with mock.patch.object(paths, "is_writable_dir", return_value=False):
            self.assertEqual(paths.user_data_dir(self.base, "linux", {}, self.home), self.native())

    def test_env_override_wins(self):
        (self.base / paths.PORTABLE_FLAG).touch()
        target = self.home / "bench"
        self.assertEqual(paths.user_data_dir(self.base, "linux", {paths.ENV_OVERRIDE: str(target)}, self.home), target)

    def test_writable_probe(self):
        self.assertTrue(paths.is_writable_dir(self.base / "new" / "sub"))


class TestFrozenBundle(unittest.TestCase):
    def setUp(self):
        self.bundle = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.bundle, ignore_errors=True)

    def test_source_layout(self):
        self.assertFalse(paths.is_frozen())
        self.assertEqual(paths.bundle_dir(), ROOT)
        self.assertEqual(paths.master_db_path(), ROOT / "data" / "roms_database.json")
        self.assertTrue(paths.master_db_path().exists())
        self.assertTrue(paths.icon_path("png").exists())
        self.assertEqual(paths.app_dir(), ROOT)

    def test_meipass_resolution(self):
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "_MEIPASS", str(self.bundle), create=True):
            self.assertTrue(paths.is_frozen())
            self.assertEqual(paths.resource_path("data", "roms_database.json"),
                             self.bundle / "data" / "roms_database.json")
            self.assertEqual(paths.icon_path("ico"), self.bundle / "assets" / "icon.ico")
            # the bundled master must never be written in a frozen build
            db = self.bundle / "data" / "roms_database.json"
            db.parent.mkdir()
            db.write_text("{}")
            self.assertFalse(paths.master_db_writable(db))
            # ...but a database outside the bundle (e.g. a user-chosen file) may be
            other = Path(tempfile.mkdtemp()) / "x.json"
            other.write_text("{}")
            self.assertTrue(paths.master_db_writable(other))
            shutil.rmtree(other.parent, ignore_errors=True)

    def test_master_writable_from_source(self):
        f = self.bundle / "db.json"
        f.write_text("{}")
        self.assertTrue(paths.master_db_writable(f))

    def test_app_dir_frozen(self):
        exe = PurePosixPath("/Applications/Bench/BackBitROMCompanion.app/Contents/MacOS/BackBitROMCompanion")
        self.assertEqual(paths.app_dir(str(exe), frozen=True, platform="darwin").name, "Bench")
        win_exe = self.bundle / "BackBitROMCompanion" / "BackBitROMCompanion.exe"
        self.assertEqual(paths.app_dir(str(win_exe), frozen=True, platform="win32"), win_exe.parent.resolve())
        self.assertEqual(paths.app_dir(str(win_exe), frozen=True, platform="linux"), win_exe.parent.resolve())


if __name__ == "__main__":
    unittest.main()
