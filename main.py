"""BackBit ROM Companion - entry point and main window.

Run:  python main.py [optional_rom_file.bin]
Works on Windows, macOS and Linux; see README.md for packaging with build.py.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

# Allow "python main.py" from any working directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from datetime import date

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QAction, QActionGroup, QIcon, QKeySequence
from PyQt6.QtWidgets import (QApplication, QFileDialog, QLabel, QMainWindow, QMessageBox, QPushButton, QTabWidget,
                             QVBoxLayout, QWidget)

from core import __version__, paths
from core.database import DatabaseError, RomDatabase
from core.settings import Settings
from ui import theme
from ui.about_dialog import AboutDialog, open_folder
from ui.compare_tab import CompareTab
from ui.inspector_tab import InspectorTab
from ui.lookup_tab import LookupTab
from ui.shatxt_tab import ShaTxtTab
from ui.widgets import set_role

APP_NAME = paths.APP_NAME
APP_VERSION = __version__
APP_ID = "BackBit.ROMCompanion"


def app_icon() -> QIcon:
    """Application icon from the bundled assets (empty icon if the assets are missing)."""
    icon = QIcon()
    for ext in ("ico", "png") if sys.platform.startswith("win") else ("png",):
        path = paths.icon_path(ext)
        if path.exists():
            icon.addFile(str(path))
    return icon


# --------------------------------------------------------------------------- tab registry
@dataclass
class TabSpec:
    """Describes a tab. ``factory(db, window) -> QWidget`` builds it; a spec with ``factory=None`` is shown as a
    disabled 'planned' tab (kept for future phases)."""
    key: str
    title: str
    description: str
    factory: Callable[[RomDatabase, "MainWindow"], QWidget] | None = None


PHASE2_TABS: list[TabSpec] = [
    TabSpec("compare", "3 · Smart ROM Compare && Diagnostics",
            "Byte-level comparison of a dump against a known-good image with automated fault diagnosis.",
            lambda db, win: CompareTab(db)),
    TabSpec("shagen", "4 · BackBit sha.txt Generator",
            "Build and export BackBit-compatible sha.txt files.",
            lambda db, win: ShaTxtTab(db)),
]


def _planned_page(spec: TabSpec) -> QWidget:
    page = QWidget()
    lay = QVBoxLayout(page)
    lay.addStretch(1)
    title = QLabel(spec.title)
    title.setAlignment(Qt.AlignmentFlag.AlignCenter)
    title.setStyleSheet("font-size:16pt; font-weight:700;")
    text = QLabel(f"Planned for Phase 2.\n\n{spec.description}")
    text.setAlignment(Qt.AlignmentFlag.AlignCenter)
    text.setWordWrap(True)
    set_role(text, "muted")
    lay.addWidget(title)
    lay.addWidget(text)
    lay.addStretch(2)
    return page


# --------------------------------------------------------------------------- theme
def apply_theme(app: QApplication, name: str) -> str:
    """Apply "dark" or "light" to the whole application (see ui/theme.py)."""
    return theme.apply(app, name)


def apply_dark_theme(app: QApplication) -> None:
    """Backwards-compatible helper used by tests and scripts."""
    theme.apply(app, theme.DARK)


# --------------------------------------------------------------------------- main window
class MainWindow(QMainWindow):
    def __init__(self, db: RomDatabase):
        super().__init__()
        self.db = db
        self.setWindowTitle(f"{APP_NAME} — BackBit Chip Tester Pro V2 helper")
        self.setWindowIcon(app_icon())
        self.resize(1440, 900)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(False)
        self.setCentralWidget(self.tabs)
        self.tab_widgets: dict[str, QWidget] = {}

        self.lookup = LookupTab(db)
        self.inspector = InspectorTab(db)
        self.register_tab(TabSpec("lookup", "1 · Machine → BackBit Lookup", "", lambda d, w: self.lookup))
        self.register_tab(TabSpec("inspector", "2 · ROM File Inspector && Identifier", "",
                                  lambda d, w: self.inspector))
        for spec in PHASE2_TABS:
            self.register_tab(spec)
        self.compare: CompareTab = self.tab_widgets["compare"]
        self.shatxt: ShaTxtTab = self.tab_widgets["shagen"]

        self.inspector.showRomRequested.connect(self.show_rom_in_lookup)
        self.inspector.fileAnalyzed.connect(lambda msg: self.statusBar().showMessage(msg, 10000))
        self.inspector.registerRequested.connect(self.register_from_inspector)
        self.lookup.databaseChanged.connect(self.on_database_changed)

        # theme: remembered in settings.json next to the user database (OS data folder or portable_data/)
        self.settings = Settings(db.user_db_path.parent)
        self.theme_button = QPushButton()
        set_role(self.theme_button, "theme-toggle")
        self.theme_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.theme_button.clicked.connect(self.toggle_theme)
        self.tabs.setCornerWidget(self.theme_button, Qt.Corner.TopRightCorner)

        self._build_menu()
        self.db_label = QLabel()
        self.statusBar().addPermanentWidget(self.db_label)
        self._update_db_label()
        self.set_theme(self.settings.theme, save=False)
        self.statusBar().showMessage("Ready. Pick a machine in Tab 1, or drop a ROM dump into Tab 2.", 8000)

    # -- hook used by Phase 2 to add real tabs
    def register_tab(self, spec: TabSpec) -> QWidget:
        if spec.factory is not None:
            widget = spec.factory(self.db, self)
            index = self.tabs.addTab(widget, spec.title)
        else:
            widget = _planned_page(spec)
            index = self.tabs.addTab(widget, spec.title)
            self.tabs.setTabEnabled(index, False)
            self.tabs.setTabToolTip(index, f"Phase 2: {spec.description}")
        self.tab_widgets[spec.key] = widget
        return widget

    def _action(self, text: str, slot, shortcut=None, role: QAction.MenuRole | None = None) -> QAction:
        """QAction helper. Shortcuts use QKeySequence.StandardKey where one exists; Qt maps "Ctrl" to the
        Command key on macOS automatically, so the custom "Ctrl+..." sequences are Cmd-based there."""
        act = QAction(text, self)
        if shortcut is not None:
            act.setShortcut(QKeySequence(shortcut) if isinstance(shortcut, str) else shortcut)
        if role is not None:
            act.setMenuRole(role)
        act.triggered.connect(lambda _checked=False: slot())
        return act

    def _build_menu(self) -> None:
        sk = QKeySequence.StandardKey
        file_menu = self.menuBar().addMenu("&File")
        file_menu.addAction(self._action("&Open ROM image\u2026", self._open_rom, sk.Open))
        file_menu.addSeparator()
        file_menu.addAction(self._action("&Import BackBit sha.txt\u2026", self.import_sha_txt, "Ctrl+I"))
        file_menu.addAction(self._action("&Clear imported sha.txt entries", self.clear_imported))
        file_menu.addSeparator()
        file_menu.addAction(self._action("&Add Machine / ROM to database\u2026",
                                         lambda: self.lookup.open_add_dialog(), sk.New))
        file_menu.addAction(self._action("&Export User Database Additions\u2026", self.export_user_database,
                                         "Ctrl+Shift+E"))
        file_menu.addAction(self._action("I&mport Database JSON\u2026", self.import_database_json, "Ctrl+Shift+I"))
        file_menu.addSeparator()
        file_menu.addAction(self._action("E&xit", self.close, sk.Quit, QAction.MenuRole.QuitRole))

        view_menu = self.menuBar().addMenu("&View")
        self.theme_toggle_action = self._action("Switch to Light Mode", self.toggle_theme, "Ctrl+T")
        view_menu.addAction(self.theme_toggle_action)
        theme_menu = view_menu.addMenu("&Theme")
        group = QActionGroup(self)
        group.setExclusive(True)
        self.theme_actions: dict[str, QAction] = {}
        for name, label in ((theme.DARK, "\U0001f319  &Dark"), (theme.LIGHT, "\u2600  &Light")):
            act = self._action(label, lambda n=name: self.set_theme(n))
            act.setCheckable(True)
            group.addAction(act)
            theme_menu.addAction(act)
            self.theme_actions[name] = act

        help_menu = self.menuBar().addMenu("&Help")
        help_menu.addAction(self._action("Open &User Data Folder", self.open_user_data_folder))
        help_menu.addSeparator()
        help_menu.addAction(self._action(f"&About {APP_NAME}", self._about, None, QAction.MenuRole.AboutRole))

    # ------------------------------------------------------------------ theme
    def set_theme(self, name: str, save: bool = True) -> str:
        """Switch every tab, dialog and painted widget to ``name`` instantly and remember the choice."""
        app = QApplication.instance()
        name = theme.apply(app, name) if app is not None else theme.normalize(name)
        if save:
            try:
                self.settings.theme = name
            except OSError as exc:
                self.statusBar().showMessage(f"Theme applied but could not be saved: {exc}", 8000)
        nxt = theme.other(name)
        self.theme_button.setText("\u2600 Light" if nxt == theme.LIGHT else "\U0001f319 Dark")
        self.theme_button.setToolTip(f"Switch to {nxt.title()} Mode (Ctrl+T)")
        self.theme_toggle_action.setText(f"Switch to {nxt.title()} Mode")
        self.theme_actions[name].setChecked(True)
        return name

    def toggle_theme(self) -> str:
        return self.set_theme(theme.other(theme.current()))

    def _update_db_label(self) -> None:
        user = f" incl. {self.db.user_rom_count} user" if self.db.user_rom_count else ""
        self.db_label.setText(f"DB: {self.db.builtin_rom_count} ROMs{user} ({self.db.builtin_hash_count} hashes)"
                              f"  •  {self.db.imported_count:,} imported sha.txt hashes  ")

    def register_from_inspector(self, prefill: dict) -> None:
        rom_id = self.lookup.open_add_dialog(prefill)
        if rom_id:
            self.statusBar().showMessage("ROM registered - the file is now recognised.", 8000)

    def on_database_changed(self, _rom_id: str = "") -> None:
        """Refresh every tab after a Database Manager add / edit / delete."""
        self._update_db_label()
        self.inspector.refresh()
        self.compare.refresh()
        self.shatxt.refresh_builtin()

    def _open_rom(self) -> None:
        self.tabs.setCurrentWidget(self.inspector)
        self.inspector.browse()

    def open_rom_file(self, path: str) -> None:
        self.tabs.setCurrentWidget(self.inspector)
        self.inspector.load_file(path)

    def show_rom_in_lookup(self, rom_id: str) -> None:
        self.tabs.setCurrentWidget(self.lookup)
        self.lookup.select_rom(rom_id)

    def import_sha_txt(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import BackBit sha.txt", "",
                                              "sha.txt files (*.txt);;All files (*)")
        if not path:
            return
        try:
            res = self.db.import_sha_txt(path)
        except OSError as exc:
            QMessageBox.critical(self, "Import failed", f"Could not import {path}:\n{exc}")
            return
        self._update_db_label()
        self.inspector.refresh()
        self.compare.refresh()
        QMessageBox.information(
            self, "sha.txt imported",
            f"{Path(path).name}\n\n"
            f"Valid entries read: {res.lines_read - res.invalid:,}\n"
            f"New entries added: {res.added:,}\n"
            f"Already known: {res.duplicates:,}\n"
            f"Unparseable lines skipped: {res.invalid:,}\n\n"
            f"Total imported hashes: {self.db.imported_count:,}\n"
            f"Stored in: {self.db.user_store}")

    def clear_imported(self) -> None:
        if not self.db.imported_count:
            QMessageBox.information(self, "Nothing to clear", "No sha.txt entries have been imported.")
            return
        if QMessageBox.question(self, "Clear imported entries",
                                f"Remove all {self.db.imported_count:,} imported sha.txt hashes?\n"
                                "The built-in database is not affected.") != QMessageBox.StandardButton.Yes:
            return
        try:
            n = self.db.clear_imported()
        except OSError as exc:
            QMessageBox.critical(self, "Clear failed", str(exc))
            return
        self._update_db_label()
        self.inspector.refresh()
        self.compare.refresh()
        self.statusBar().showMessage(f"Removed {n:,} imported hashes.", 6000)

    # ------------------------------------------------------------------ database sharing
    def export_user_database(self) -> None:
        if not self.db.user_rom_count:
            QMessageBox.information(self, "Nothing to export",
                                    "You have not added or edited any ROMs yet. Use Tab 1 \u2192 '+ Add Machine / "
                                    "ROM' or Tab 2 \u2192 'Register in Database\u2026' first.")
            return
        start = str(Path.home() / f"backbit_rom_pack_{date.today():%Y%m%d}.json")
        path, _ = QFileDialog.getSaveFileName(self, "Export User Database Additions", start,
                                              "BackBit ROM pack (*.json);;All files (*)")
        if not path:
            return
        if not Path(path).suffix:
            path += ".json"
        try:
            n = self.db.export_user_pack(path, APP_VERSION)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Export failed", str(exc))
            return
        noun = "entry" if n == 1 else "entries"
        QMessageBox.information(self, "ROM pack exported",
                                f"Exported {n:,} ROM {noun} to\n{path}\n\nShare this file with other users "
                                f"(File \u2192 Import Database JSON) or submit it to the project.")

    def import_database_json(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import Database JSON", "",
                                              "BackBit ROM pack / database (*.json);;All files (*)")
        if path:
            self.import_database_file(path)

    def import_database_file(self, path: str) -> None:
        try:
            plan = self.db.plan_import(path)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Import failed", f"{Path(path).name}\n\n{exc}")
            return
        summary = (f"{Path(path).name}\n\nNew ROMs to add: {len(plan.to_add):,}\n"
                   f"Already identical: {plan.unchanged:,}\n"
                   f"Skipped - SHA-1 already in your database: {plan.duplicates:,}\n"
                   f"Different from your copy (same ID): {len(plan.conflicts):,}\n"
                   f"Invalid entries: {len(plan.invalid):,}")
        if plan.invalid:
            more = "\n\u2026" if len(plan.invalid) > 6 else ""
            summary += "\n\n" + "\n".join(plan.invalid[:6]) + more
        if not plan.to_add and not plan.conflicts:
            QMessageBox.information(self, "Nothing to import", summary)
            return
        replace = False
        buttons = QMessageBox.StandardButton
        if plan.conflicts:
            answer = QMessageBox.question(
                self, "Import Database JSON",
                summary + f"\n\n{len(plan.conflicts):,} existing entry/entries differ from your current copy. "
                "Replace them with the imported version?\n\nYes = replace, No = keep yours and import only new ROMs.",
                buttons.Yes | buttons.No | buttons.Cancel, buttons.No)
            if answer == buttons.Cancel:
                return
            replace = answer == buttons.Yes
        else:
            answer = QMessageBox.question(self, "Import Database JSON", summary + "\n\nImport now?")
            if answer != buttons.Yes:
                return
        try:
            res = self.db.apply_import(plan, replace_conflicts=replace, write_master=self.db.master_writable)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Import failed", str(exc))
            return
        self.lookup.reload()
        self.on_database_changed()
        msg = f"Added {res.added:,}, replaced {res.replaced:,}, skipped {res.skipped:,}."
        if res.message:
            msg += "\n\n" + res.message
        QMessageBox.information(self, "Import complete", msg)
        self.statusBar().showMessage(f"ROM pack imported: {res.added + res.replaced:,} entries.", 8000)

    def open_user_data_folder(self) -> None:
        open_folder(self, self.db.user_db_path.parent)

    def _about(self) -> None:
        AboutDialog(self.db, APP_NAME, APP_VERSION, self).exec()

def _set_windows_app_id() -> None:
    """Give the process its own taskbar identity so Windows shows our icon instead of python.exe's."""
    if sys.platform.startswith("win"):
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        except (AttributeError, OSError):
            pass


def main() -> int:
    _set_windows_app_id()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName("BackBitROMCompanion")
    app.setWindowIcon(app_icon())
    apply_dark_theme(app)
    try:
        db = RomDatabase()
    except DatabaseError as exc:
        QMessageBox.critical(None, APP_NAME, str(exc))
        return 1
    win = MainWindow(db)
    win.show()
    for arg in sys.argv[1:]:
        if Path(arg).is_file():
            win.open_rom_file(arg)
            break
    smoke_file = os.environ.get("BACKBIT_SMOKE_TEST")
    if smoke_file:
        QTimer.singleShot(500, lambda: _smoke_report(app, win, smoke_file))
    return app.exec()


def _smoke_report(app: QApplication, win: MainWindow, out_file: str) -> None:
    """CI check for frozen builds: the window came up, the bundled DB and icon resolved. Written to a file
    because windowed executables have no stdout on Windows."""
    db = win.db
    report = (f"SMOKE OK v{APP_VERSION} tabs={win.tabs.count()} theme={theme.current()} machines={len(db.machines)} "
              f"roms={db.builtin_rom_count} icon={paths.icon_path('png').exists()} frozen={paths.is_frozen()} "
              f"bundle={paths.bundle_dir()} data={db.user_db_path.parent}\n")
    Path(out_file).write_text(report, encoding="utf-8")
    app.quit()


if __name__ == "__main__":
    sys.exit(main())
