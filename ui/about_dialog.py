"""Help > About dialog: version, database statistics and the active user-data location."""

from __future__ import annotations

import html
import platform
from pathlib import Path

from PyQt6.QtCore import QT_VERSION_STR, PYQT_VERSION_STR, Qt, QUrl
from PyQt6.QtGui import QDesktopServices, QPixmap
from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QGridLayout, QHBoxLayout, QLabel, QMessageBox, QPushButton,
                             QVBoxLayout)

from core import paths
from core.database import RomDatabase
from ui.widgets import COLORS, MONO_CSS, set_role


def open_folder(parent, folder: Path) -> bool:
    """Reveal a folder in Explorer / Finder / the Linux file manager (creates it first)."""
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        QMessageBox.warning(parent, "Cannot open folder", f"{folder}\n\n{exc}")
        return False
    ok = QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
    if not ok:
        QMessageBox.information(parent, "User data folder", f"Could not launch a file manager. The folder is:\n\n"
                                                            f"{folder}")
    return ok


class AboutDialog(QDialog):
    def __init__(self, db: RomDatabase, app_name: str, version: str, parent=None):
        super().__init__(parent)
        self.db = db
        self.data_dir = db.user_db_path.parent
        self.setWindowTitle(f"About {app_name}")
        self.setMinimumWidth(620)

        icon = QLabel()
        pix = QPixmap(str(paths.icon_path("png")))
        if not pix.isNull():
            icon.setPixmap(pix.scaled(112, 112, Qt.AspectRatioMode.KeepAspectRatio,
                                      Qt.TransformationMode.SmoothTransformation))
        icon.setAlignment(Qt.AlignmentFlag.AlignTop)
        title = QLabel(f"<div style='font-size:18pt; font-weight:800; color:{COLORS['accent']}'>"
                       f"{html.escape(app_name)}</div><div style='font-size:12pt'>Version v{html.escape(version)}"
                       f"</div><p style='color:{COLORS['muted']}'>Workbench companion for the BackBit Chip Tester "
                       f"Pro V2: tester settings and ZIF placement for vintage ROMs, dump identification, health "
                       f"checks, fault diagnosis and sha.txt generation.<br>Built-in hashes are transcribed from "
                       f"MAME driver sources.</p>")
        title.setWordWrap(True)
        top = QHBoxLayout()
        top.addWidget(icon)
        top.addWidget(title, 1)

        portable = paths.is_portable(self.data_dir)
        frozen = " • frozen build" if paths.is_frozen() else ""
        stats = [
            ("Machines", f"{len(db.machines):,}"),
            ("ROM entries", f"{db.builtin_rom_count:,}  ({db.builtin_hash_count:,} distinct SHA-1 hashes incl. banks)"),
            ("Your entries", f"{db.user_rom_count:,} added or edited in the Database Manager"),
            ("Imported sha.txt", f"{db.imported_count:,} hashes"),
            ("User data", str(self.data_dir)),
            ("Storage mode", "Portable (portable_data next to the app)" if portable else
             "Per-user OS data folder"),
            ("Master database", f"{db.db_path}"),
            ("Master access", "Writable - Database Manager edits are also saved here" if db.master_writable else
             "Read-only bundle - edits are kept in your user data"),
            ("Runtime", f"Python {platform.python_version()} • Qt {QT_VERSION_STR} • PyQt {PYQT_VERSION_STR}"
                        f" • {platform.system()} {platform.machine()}"
                        f"{frozen}"),
        ]
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        self.value_labels: dict[str, QLabel] = {}
        for row, (k, v) in enumerate(stats):
            key = QLabel(k)
            set_role(key, "field-name")
            val = QLabel(v)
            val.setWordWrap(k not in ("User data", "Master database"))  # never break a path mid-way
            val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            if k in ("User data", "Master database"):
                val.setStyleSheet(f"font-family:{MONO_CSS};")
            grid.addWidget(key, row, 0, Qt.AlignmentFlag.AlignTop)
            grid.addWidget(val, row, 1)
            self.value_labels[k] = val

        self.open_btn = QPushButton("Open User Data Folder")
        self.open_btn.clicked.connect(lambda: open_folder(self, self.data_dir))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        bottom = QHBoxLayout()
        bottom.addWidget(self.open_btn)
        bottom.addStretch(1)
        bottom.addWidget(buttons)

        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addLayout(grid)
        lay.addSpacing(8)
        lay.addLayout(bottom)

