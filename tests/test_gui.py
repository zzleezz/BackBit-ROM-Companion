"""Offscreen GUI smoke tests for all four tabs and the Database Manager wiring.

Run:  python -m unittest discover -s tests -v
Set BACKBIT_SCREENSHOTS=<dir> to also save PNG screenshots of every tab.
"""

import hashlib
import os
import random
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform.startswith("win"):
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PyQt6.QtWidgets import QApplication  # noqa: E402

import main as appmain  # noqa: E402
from core.database import DEFAULT_DB_PATH, RomDatabase  # noqa: E402

APP = QApplication.instance() or QApplication(sys.argv)
appmain.apply_dark_theme(APP)
SHOTS = os.environ.get("BACKBIT_SCREENSHOTS")


def rnd(n, seed):
    r = random.Random(seed)
    return bytes(r.randrange(256) for _ in range(n))


class TestGui(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        shutil.copy(DEFAULT_DB_PATH, self.tmp / "db.json")
        self.db = RomDatabase(self.tmp / "db.json", self.tmp / "imp.json", self.tmp / "user.json")
        self.win = appmain.MainWindow(self.db)
        self.win.resize(1440, 900)
        self.win.show()
        APP.processEvents()

    def tearDown(self):
        self.win.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def shot(self, name):
        APP.processEvents()
        if SHOTS:
            Path(SHOTS).mkdir(parents=True, exist_ok=True)
            self.win.grab().save(str(Path(SHOTS) / f"{name}.png"))

    def test_all_tabs_enabled(self):
        self.assertEqual(self.win.tabs.count(), 4)
        for i in range(4):
            self.assertTrue(self.win.tabs.isTabEnabled(i))

    def test_lookup(self):
        self.win.show_rom_in_lookup("d32_lo")
        self.assertEqual(self.win.lookup._rom.id, "d32_lo")
        self.shot("tab1_dragon")
        self.win.show_rom_in_lookup("bbcm_mos320")
        self.win.lookup.option_combo.setCurrentIndex(1)
        self.assertEqual(self.win.lookup.badge.text(), "Menu: ROM -> 32-Pin -> 27C1001")
        self.assertEqual(self.win.lookup.zif.describe()[-1], "Leave EMPTY: ZIF pins 17–32 (bottom 8 rows)")
        self.shot("tab1_master_32pin")
        self.win.show_rom_in_lookup("d64_1")
        self.assertEqual(self.win.lookup.badge.text(), "Menu: ROM -> 28-Pin -> 23128")
        self.assertEqual(self.win.lookup.zif.describe(), [
            "Chip pin 1 -> ZIF pin 1  •  Chip pin 14 -> ZIF pin 14",
            "Chip pin 15 -> ZIF pin 35  •  Chip pin 28 -> ZIF pin 48",
            "Leave EMPTY: ZIF pins 15–34 (bottom 10 rows)"])
        self.shot("tab1_dragon64_28pin")
        self.win.show_rom_in_lookup("c64_kernal_r3")
        self.assertEqual(self.win.lookup.zif.describe()[-1], "Leave EMPTY: ZIF pins 13–36 (bottom 12 rows)")
        self.shot("tab1_c64_24pin")

    def test_register_flow(self):
        data = rnd(16384, 5)
        f = self.tmp / "mystery.bin"
        f.write_bytes(data)
        self.win.open_rom_file(str(f))
        self.assertFalse(self.win.inspector.result.identified)
        self.assertTrue(self.win.inspector.register_btn.isVisibleTo(self.win.inspector))
        self.shot("tab2_unrecognised")
        # simulate what the dialog does on Save, then the refresh signal
        from ui.rom_dialog import RomEditDialog
        dlg = RomEditDialog(self.db, prefill={"size": len(data), "sha1": self.win.inspector.result.hashes.sha1,
                                              "crc32": self.win.inspector.result.hashes.crc32, "name": "Mystery"})
        self.assertEqual(dlg.size.value(), 16384)
        self.assertEqual(dlg.mask_setting.text(), "23128")
        self.assertEqual(dlg.pins.currentText(), "28")
        dlg.machine.setCurrentText("Workbench Specials")
        dlg.write_master.setChecked(True)
        dlg._save()
        self.assertTrue(dlg.saved_rom_id)
        self.win.lookup._after_change(dlg.saved_rom_id, "")
        self.assertTrue(self.win.inspector.result.whole_matches)
        self.assertFalse(self.win.inspector.register_btn.isVisibleTo(self.win.inspector))
        self.assertEqual(self.win.lookup._rom.id, dlg.saved_rom_id)

    def test_compare(self):
        ref = rnd(8192, 8)
        a = self.tmp / "a.bin"
        b = self.tmp / "b.bin"
        a.write_bytes(bytes(x & ~0x08 for x in ref))
        b.write_bytes(ref)
        self.win.compare.load_files(str(a), str(b))
        r = self.win.compare.result
        self.assertEqual(r.verdict, "different")
        self.assertIn("stuck LOW", " ".join(d.text for d in r.diagnoses))
        self.win.compare.only_diffs.setChecked(True)
        self.assertEqual(self.win.compare.model.rowCount(), len(self.win.compare.model.diff_rows))
        self.win.compare._jump(1)
        self.win.tabs.setCurrentWidget(self.win.compare)
        self.shot("tab3_stuck_bit")
        a.write_bytes(ref * 2)
        self.win.compare.load_files(str(a))
        self.assertEqual(self.win.compare.result.verdict, "content_match")
        self.shot("tab3_doubled")

    def test_shatxt(self):
        tab = self.win.shatxt
        tab.builtin_btn.setChecked(True)
        n = len(tab.entries)
        self.assertGreater(n, 150)
        f = self.tmp / "dead_test_diagnostic_cartridge_v781.bin"
        f.write_bytes(b"\x11" * 4096)
        tab.add_files([str(f)])
        self.assertEqual(len(tab.entries), n + 1)
        tab._update_preview()
        self.assertIn(tab.entries[-1].sha1 + "  ", tab.preview.toPlainText())  # sha1sum format by default
        out = self.tmp / "sha.txt"
        from core.shatxt import write_sha_txt
        write_sha_txt(out, tab.entries)
        lines = out.read_text().splitlines()
        self.assertEqual(len(lines), n + 1)
        self.win.tabs.setCurrentWidget(tab)
        self.shot("tab4_shatxt")

    def test_shatxt_sha1sum_default(self):
        tab = self.win.shatxt
        self.assertFalse(tab.upper.isChecked())
        self.assertEqual(tab.limit.value(), 16)
        tab.builtin_btn.setChecked(True)
        tab._update_preview()
        first = tab.preview.toPlainText().splitlines()[0]
        self.assertRegex(first, r"^[0-9a-f]{40}  \S")
        self.assertRegex(tab.table.item(0, 1).text(), r"^[0-9a-f]{40}$")
        long = [e for e in tab.entries if len(e.label) > 16]
        self.assertEqual(long, [])
        self.assertTrue(tab.save_btn.isEnabled())
        tab.upper.setChecked(True)
        tab._update_preview()
        self.assertRegex(tab.preview.toPlainText().splitlines()[0], r"^[0-9A-F]{40}  ")
        self.assertRegex(tab.table.item(0, 1).text(), r"^[0-9A-F]{40}$")
        # a 17-character label is flagged against the 16-column LCD
        tab.table.item(0, 2).setText("ABCDEFGHIJKLMNOPQ")
        self.assertEqual(tab.table.item(0, 3).text(), "17/16")
        self.shot("tab4_lcd_limit")

    def test_monospace_fonts(self):
        from PyQt6.QtGui import QFontInfo
        from ui.widgets import MONO_FAMILIES, mono_font, mono_families
        f = mono_font(9)
        self.assertTrue(QFontInfo(f).fixedPitch(), QFontInfo(f).family())
        self.assertTrue(mono_families())
        if sys.platform.startswith("win"):
            self.assertEqual(mono_families()[0], "Consolas")
        self.assertIn("Menlo", MONO_FAMILIES)
        self.assertIn("DejaVu Sans Mono", MONO_FAMILIES)
        self.assertTrue(QFontInfo(self.win.compare.model._mono).fixedPitch())

    def test_icon_and_about(self):
        from core import paths
        from ui.about_dialog import AboutDialog
        self.assertFalse(self.win.windowIcon().isNull())
        self.assertFalse(appmain.app_icon().isNull())
        dlg = AboutDialog(self.db, appmain.APP_NAME, appmain.APP_VERSION, self.win)
        self.assertEqual(appmain.APP_VERSION, "1.0.0")
        text = " ".join(lbl.text() for lbl in dlg.value_labels.values())
        self.assertIn(str(len(self.db.machines)), dlg.value_labels["Machines"].text())
        self.assertIn(str(self.db.user_db_path.parent), text)
        self.assertEqual(dlg.open_btn.text(), "Open User Data Folder")
        self.assertTrue(paths.icon_path("png").exists())
        dlg.show()
        APP.processEvents()
        if SHOTS:
            dlg.grab().save(str(Path(SHOTS) / "about.png"))
        dlg.close()

    def test_icon_generator_containers(self):
        import struct
        sys.path.insert(0, str(ROOT / "scripts"))
        import generate_icons as gi
        out = self.tmp / "icons"
        written = {p.name: p.read_bytes() for p in gi.generate(out)}
        ico = written["icon.ico"]
        reserved, kind, count = struct.unpack("<HHH", ico[:6])
        self.assertEqual((reserved, kind, count), (0, 1, len(gi.ICO_SIZES)))
        dims = []
        for i in range(count):
            w, h, _c, _r, planes, bpp, size, off = struct.unpack("<BBBBHHII", ico[6 + 16 * i:22 + 16 * i])
            self.assertEqual((planes, bpp), (1, 32))
            self.assertEqual(ico[off:off + 8], b"\x89PNG\r\n\x1a\n")
            self.assertEqual(off + size <= len(ico), True)
            dims.append(w or 256)
        self.assertEqual(dims, sorted(gi.ICO_SIZES))
        icns = written["icon.icns"]
        self.assertEqual(icns[:4], b"icns")
        self.assertEqual(struct.unpack(">I", icns[4:8])[0], len(icns))
        pos, kinds = 8, []
        while pos < len(icns):
            kinds.append(icns[pos:pos + 4].decode())
            pos += struct.unpack(">I", icns[pos + 4:pos + 8])[0]
        self.assertEqual(pos, len(icns))
        self.assertIn("ic10", kinds)
        from PyQt6.QtGui import QImage
        img = QImage.fromData(written["icon.png"])
        self.assertEqual((img.width(), img.height()), (gi.PNG_SIZE, gi.PNG_SIZE))
        self.assertEqual(img.pixelColor(0, 0).alpha(), 0)          # transparent corner
        # the shipped assets are what the generator produces
        self.assertEqual((ROOT / "assets" / "icon.ico").read_bytes()[:6], ico[:6])

    def test_menus_and_shortcuts(self):
        from PyQt6.QtGui import QAction, QKeySequence
        actions = {a.text().replace("&", ""): a for a in self.win.findChildren(QAction)}
        for name in ("Export User Database Additions…", "Import Database JSON…",
                     "Open User Data Folder", f"About {appmain.APP_NAME}"):
            self.assertIn(name, actions)
        self.assertEqual(actions["Open ROM image…"].shortcut(), QKeySequence(QKeySequence.StandardKey.Open))
        self.assertEqual(actions["Exit"].shortcut(), QKeySequence(QKeySequence.StandardKey.Quit))
        self.assertEqual(actions[f"About {appmain.APP_NAME}"].menuRole(), QAction.MenuRole.AboutRole)

    def test_pack_import_via_window(self):
        from unittest import mock
        from PyQt6.QtWidgets import QMessageBox
        data = rnd(8192, 21)
        self.db.upsert_rom("Shared Bench", "", {"name": "Diag", "size": 8192, "pins": 28,
                                                "backbit": [{"setting": "2764", "pins": 28, "chip": ""}],
                                                "sha1": hashlib.sha1(data).hexdigest()})
        pack = self.tmp / "pack.json"
        self.db.export_user_pack(pack)
        d = self.tmp / "fresh"
        d.mkdir()
        shutil.copy(DEFAULT_DB_PATH, d / "db.json")
        fresh = RomDatabase(d / "db.json", d / "imp.json", d / "user.json")
        win = appmain.MainWindow(fresh)
        yes = QMessageBox.StandardButton.Yes
        with mock.patch.object(appmain.QMessageBox, "question", return_value=yes) as q, \
                mock.patch.object(appmain.QMessageBox, "information") as info:
            win.import_database_file(str(pack))
        self.assertTrue(q.called)
        self.assertIn("Added 1", info.call_args[0][2])
        hit = fresh.lookup_sha1(hashlib.sha1(data).hexdigest())[0].rom
        self.assertEqual(hit.machine_name, "Shared Bench")
        self.assertIn(hit.id, {r.id for r in win.lookup.db.all_roms()})
        win.close()

    def test_rom_dialog_read_only_master(self):
        from ui.rom_dialog import RomEditDialog
        self.db.master_writable = False
        dlg = RomEditDialog(self.db)
        self.assertFalse(dlg.write_master.isChecked())
        self.assertFalse(dlg.write_master.isEnabled())


if __name__ == "__main__":
    unittest.main()
