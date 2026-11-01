"""Dark / Light theme switching, settings persistence, contrast and the wide-body ZIF drawing.

Run:  python -m unittest discover -s tests -v
Set BACKBIT_SCREENSHOTS=<dir> to save light-mode screenshots of every tab.
"""

import json
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

from PyQt6.QtGui import QColor, QPalette  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

import main as appmain  # noqa: E402
from core.database import DEFAULT_DB_PATH, RomDatabase  # noqa: E402
from core.settings import SETTINGS_FILE, Settings  # noqa: E402
from ui import theme  # noqa: E402
from ui.widgets import COLORS, LEVEL_COLORS, ZifSocketWidget  # noqa: E402

APP = QApplication.instance() or QApplication(sys.argv)
SHOTS = os.environ.get("BACKBIT_SCREENSHOTS")


def rnd(n, seed):
    r = random.Random(seed)
    return bytes(r.randrange(256) for _ in range(n))


def pixel(widget, x, y) -> str:
    return widget.grab().toImage().pixelColor(x, y).name()


class TestSettings(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_default_and_persistence(self):
        s = Settings(self.dir)
        self.assertEqual(s.theme, "dark")                    # default without a file
        self.assertFalse((self.dir / SETTINGS_FILE).exists())  # nothing written until a change
        s.theme = "light"
        self.assertEqual(json.loads((self.dir / SETTINGS_FILE).read_text())["theme"], "light")
        self.assertEqual(Settings(self.dir).theme, "light")
        with self.assertRaises(ValueError):
            s.theme = "sepia"

    def test_corrupt_or_invalid_files_fall_back(self):
        (self.dir / SETTINGS_FILE).write_text("{not json")
        self.assertEqual(Settings(self.dir).theme, "dark")
        (self.dir / SETTINGS_FILE).write_text(json.dumps({"theme": "neon", "window": [1, 2]}))
        s = Settings(self.dir)
        self.assertEqual(s.theme, "dark")
        s.theme = "light"                                     # unknown keys are preserved
        self.assertEqual(json.loads((self.dir / SETTINGS_FILE).read_text()), {"theme": "light", "window": [1, 2]})

    def test_creates_missing_folder(self):
        s = Settings(self.dir / "new" / "nested")
        s.theme = "light"
        self.assertTrue((self.dir / "new" / "nested" / SETTINGS_FILE).exists())


class TestPalettes(unittest.TestCase):
    def test_same_tokens(self):
        self.assertEqual(set(theme.PALETTES["dark"]), set(theme.PALETTES["light"]))

    def test_contrast(self):
        """Every text/background pairing used by the UI meets WCAG AA (4.5:1, 3:1 for the 14pt bold banners)."""
        cr = theme.contrast_ratio
        for name, c in theme.PALETTES.items():
            for fg in ("text", "muted", "accent", "ok", "info", "warn", "error"):
                for bg in ("bg", "panel"):
                    self.assertGreaterEqual(cr(c[fg], c[bg]), 4.5, (name, fg, bg))
            self.assertGreaterEqual(cr(c["text"], c["panel2"]), 4.5, name)
            self.assertGreaterEqual(cr(c["muted"], c["panel2"]), 4.5, name)
            self.assertGreaterEqual(cr(c["on_accent"], c["accent"]), 4.5, name)
            self.assertGreaterEqual(cr(c["diff_fg"], c["diff_a_bg"]), 4.5, name)
            self.assertGreaterEqual(cr(c["diff_fg"], c["diff_b_bg"]), 4.5, name)
            self.assertGreaterEqual(cr(c["diff_a_text"], c["bit_mark_bg"]), 3.0, name)
            self.assertGreaterEqual(cr(c["diff_b_text"], c["bit_mark_bg"]), 3.0, name)
            self.assertGreaterEqual(cr(theme.contrast_text(c["badge"]), c["badge"]), 4.5, name)
            self.assertGreaterEqual(cr(c["chip_text"], c["chip_body"]), 4.5, name)
            self.assertGreaterEqual(cr(c["empty_text"], c["socket"]), 4.5, name)    # "EMPTY ZIF 15-34" label
            self.assertEqual(c["badge"], "#ffb000")           # the BackBit-yellow badge in both themes
        try:
            for name in theme.THEMES:
                theme.apply(APP, name)
                for kind in theme.BANNER_KINDS[:-1]:
                    bg, fg = theme.banner_colors(kind)
                    self.assertGreaterEqual(cr(fg, bg), 3.0, (name, kind))
        finally:
            theme.apply(APP, theme.DARK)

    def test_contrast_text(self):
        self.assertEqual(theme.contrast_text("#ffb000"), "#000000")
        self.assertEqual(theme.contrast_text("#17191c"), "#ffffff")
        self.assertEqual(theme.contrast_text("#3aa9e0"), "#000000")   # mid-tone blue: black reads better


class TestThemeSwitching(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        shutil.copy(DEFAULT_DB_PATH, self.tmp / "db.json")
        self.db = RomDatabase(self.tmp / "db.json", self.tmp / "imp.json", self.tmp / "user.json")
        appmain.apply_dark_theme(APP)
        self.win = appmain.MainWindow(self.db)
        self.win.resize(1440, 900)
        self.win.show()
        APP.processEvents()

    def tearDown(self):
        self.win.close()
        theme.apply(APP, theme.DARK)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def shot(self, name):
        APP.processEvents()
        if SHOTS:
            Path(SHOTS).mkdir(parents=True, exist_ok=True)
            self.win.grab().save(str(Path(SHOTS) / f"{name}.png"))

    def test_toggle_controls_and_persistence(self):
        w = self.win
        self.assertEqual(theme.current(), "dark")
        self.assertIs(w.tabs.cornerWidget(), w.theme_button)
        self.assertEqual(w.theme_button.text(), "\u2600 Light")
        self.assertEqual(w.theme_toggle_action.text(), "Switch to Light Mode")
        self.assertEqual(w.theme_toggle_action.shortcut().toString(), "Ctrl+T")
        self.assertTrue(w.theme_actions["dark"].isChecked())

        w.theme_button.click()                                  # one click from anywhere
        APP.processEvents()
        self.assertEqual(theme.current(), "light")
        self.assertEqual(COLORS["bg"], theme.PALETTES["light"]["bg"])
        self.assertEqual(LEVEL_COLORS["error"], theme.PALETTES["light"]["error"])
        self.assertEqual(APP.palette().color(QPalette.ColorRole.Window).name(), theme.PALETTES["light"]["bg"])
        self.assertIn(theme.PALETTES["light"]["panel"], APP.styleSheet())
        self.assertEqual(w.theme_button.text(), "\U0001f319 Dark")
        self.assertEqual(w.theme_toggle_action.text(), "Switch to Dark Mode")
        self.assertTrue(w.theme_actions["light"].isChecked())
        self.assertEqual(json.loads((self.tmp / SETTINGS_FILE).read_text())["theme"], "light")

        # a fresh window (= next launch) restores the saved theme
        theme.apply(APP, theme.DARK)
        w2 = appmain.MainWindow(RomDatabase(self.tmp / "db.json", self.tmp / "imp.json", self.tmp / "user.json"))
        self.assertEqual(theme.current(), "light")
        self.assertEqual(w2.theme_button.text(), "\U0001f319 Dark")
        w2.close()

        w.theme_toggle_action.trigger()                          # Ctrl+T / View menu
        self.assertEqual(theme.current(), "dark")
        w.theme_actions["light"].trigger()                       # View > Theme > Light
        self.assertEqual(theme.current(), "light")
        self.assertEqual(Settings(self.tmp).theme, "light")

    def test_every_tab_restyles_without_restart(self):
        w = self.win
        # Tab 1: a ROM with an alternative chip option selected
        w.show_rom_in_lookup("bbcm_mos320")
        w.lookup.option_combo.setCurrentIndex(1)
        self.assertEqual(w.lookup.badge.variant, "alt")
        # Tab 2: an unidentified file
        f = self.tmp / "mystery.bin"
        f.write_bytes(rnd(8192, 3))
        w.open_rom_file(str(f))
        # Tab 3: stuck-bit comparison with a selected differing cell
        ref = rnd(8192, 8)
        a, b = self.tmp / "a.bin", self.tmp / "b.bin"
        a.write_bytes(bytes(x & ~0x08 for x in ref))
        b.write_bytes(ref)
        w.compare.load_files(str(a), str(b))
        w.compare._jump(1)
        sel = w.compare.view.currentIndex()
        # Tab 4: built-ins plus one over-long label
        w.shatxt.builtin_btn.setChecked(True)
        w.shatxt.table.item(0, 2).setText("A LABEL LONGER THAN 16")
        APP.processEvents()

        dark = theme.PALETTES["dark"]
        light = theme.PALETTES["light"]
        self.assertEqual(pixel(w.lookup.zif, 2, 2), dark["panel"])
        w.toggle_theme()
        APP.processEvents()

        # Tab 1 - painted ZIF canvas, badge, selection and chip option all survive the switch
        self.assertEqual(pixel(w.lookup.zif, 2, 2), light["panel"])
        self.assertEqual(w.lookup._rom.id, "bbcm_mos320")
        self.assertEqual(w.lookup.option_combo.currentIndex(), 1)
        self.assertEqual(w.lookup.badge.variant, "alt")
        badge = w.lookup.badge
        self.assertEqual(pixel(badge, 14, badge.height() // 2), light["badge_alt"])
        w.lookup.option_combo.setCurrentIndex(0)
        APP.processEvents()
        self.assertEqual(pixel(badge, 14, badge.height() // 2), "#ffb000")
        self.assertIn(light["muted"], w.lookup.banks_label.text() + w.lookup.desc.styleSheet()
                      + APP.styleSheet())
        self.shot("light_tab1")
        # Tab 2 - identification HTML and bit bars re-rendered with light tokens
        w.tabs.setCurrentWidget(w.inspector)
        APP.processEvents()
        self.assertEqual(pixel(w.inspector.bits, 2, 2), light["panel"])
        self.assertTrue(w.inspector.register_btn.isVisibleTo(w.inspector))
        self.shot("light_tab2")
        # Tab 3 - banner colours, hex diff highlights and the selected cell
        w.tabs.setCurrentWidget(w.compare)
        APP.processEvents()
        banner = w.compare.banner
        self.assertEqual(banner.property("kind"), "different")
        self.assertEqual(pixel(banner, 12, banner.height() // 2), theme.banner_colors("different")[0])
        self.assertEqual(w.compare.view.currentIndex().row(), sel.row())
        m = w.compare.model
        diff_cell = m.index(sel.row(), sel.column())
        from PyQt6.QtCore import Qt
        self.assertEqual(m.data(diff_cell, Qt.ItemDataRole.BackgroundRole).name(), light["diff_a_bg"])
        self.assertEqual(m.data(diff_cell, Qt.ItemDataRole.ForegroundRole).name(), light["diff_fg"])
        self.assertIn(light["diff_a_text"], w.compare.bit_detail.text())
        self.assertIn(light["bit_mark_bg"], w.compare.bit_detail.text())
        self.shot("light_tab3")
        # Tab 4 - counter colours and preview box follow the theme; the list and edits are kept
        w.tabs.setCurrentWidget(w.shatxt)
        APP.processEvents()
        self.assertEqual(w.shatxt.entries[0].label, "A LABEL LONGER THAN 16")
        self.assertEqual(w.shatxt.table.item(0, 3).background().color().name(), light["error"])
        self.assertEqual(pixel(w.shatxt.preview.viewport(), 3, 3), light["panel"])
        self.shot("light_tab4")

        # and back again
        w.toggle_theme()
        APP.processEvents()
        self.assertEqual(pixel(w.lookup.zif, 2, 2), dark["panel"])
        self.assertEqual(w.shatxt.table.item(0, 3).background().color().name(), dark["error"])
        self.assertEqual(m.data(diff_cell, Qt.ItemDataRole.BackgroundRole).name(), dark["diff_a_bg"])

    def test_dialogs_follow_theme(self):
        from ui.about_dialog import AboutDialog
        from ui.rom_dialog import RomEditDialog
        self.win.set_theme("light")
        dlg = RomEditDialog(self.db)
        dlg.label.setText("X" * 17)
        self.assertEqual(dlg.label_count.property("state"), "over")
        dlg.show()
        APP.processEvents()
        self.assertEqual(dlg.label_count.palette().color(dlg.label_count.foregroundRole()).name(),
                         theme.PALETTES["light"]["error"])
        dlg.close()
        about = AboutDialog(self.db, appmain.APP_NAME, appmain.APP_VERSION, self.win)
        about.show()
        APP.processEvents()
        self.assertEqual(pixel(about, 2, 2), theme.PALETTES["light"]["bg"])
        if SHOTS:
            about.grab().save(str(Path(SHOTS) / "light_about.png"))
        about.close()


class TestWideZif(unittest.TestCase):
    def test_wide_body_proportions_and_no_clipping(self):
        z = ZifSocketWidget()
        self.assertGreaterEqual(z.minimumWidth(), 400)
        for size in ((z.minimumWidth(), z.minimumHeight()), (470, 760), (600, 900)):
            z.resize(*size)
            z.set_chip(28, "test", "23128")
            z.grab()
            g = z._geometry
            self.assertAlmostEqual(g["socket_w"] / g["pitch"], 7.8)             # was 6.4: +22%
            self.assertGreaterEqual(g["chip_w"] / g["pitch"], 3.76 * 1.2)        # chip body >= +20%
            self.assertLessEqual(g["chip_w"] / g["pitch"], 3.76 * 1.25)          # ... and <= +25%
            self.assertGreaterEqual(g["lever_x"] - 14, 0, size)                  # lever label not clipped
            self.assertLessEqual(g["right_label_end"], z.width(), size)          # pins 25-48 not clipped
            # short lever like the real socket: top at the socket edge, bottom beside ZIF pin 9 (row 9)
            row9 = g["lever_top"] + g["pitch"] * 9
            self.assertLess(abs(g["lever_bottom"] - row9), g["pitch"] * 0.5, size)

    def test_lookup_panel_gives_room(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            shutil.copy(DEFAULT_DB_PATH, tmp / "db.json")
            win = appmain.MainWindow(RomDatabase(tmp / "db.json", tmp / "i.json", tmp / "u.json"))
            win.resize(1440, 900)
            win.show()
            APP.processEvents()
            self.assertGreaterEqual(win.lookup.zif.width(), 400)
            win.show_rom_in_lookup("d64_1")
            win.lookup.zif.grab()
            self.assertLessEqual(win.lookup.zif._geometry["right_label_end"], win.lookup.zif.width())
            win.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
