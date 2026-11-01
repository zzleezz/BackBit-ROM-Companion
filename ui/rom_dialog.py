"""Database Manager dialog: add a new machine/ROM or edit an existing entry."""

from __future__ import annotations

import hashlib
import re
import zlib
from pathlib import Path

from PyQt6.QtCore import QRegularExpression, Qt
from PyQt6.QtGui import QRegularExpressionValidator
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QGroupBox,
                             QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox,
                             QVBoxLayout, QWidget)

from core.analyzer import CHIP_HINTS
from core.database import USER_SOURCE, RomDatabase, RomEntry, guess_pins, rom_to_dict
from core.shatxt import DEFAULT_LABEL_LIMIT, auto_label, sanitize_label
from ui.widgets import mono_font, set_role, set_state

PIN_CHOICES = ["24", "28", "32", "40"]

# Default (mask, EPROM) BackBit settings and chip type by size, used to pre-fill new entries.
SIZE_DEFAULTS = {
    2048: ("2316", "2716", 24, "2316 mask ROM"),
    4096: ("2332", "2532", 24, "2332 mask ROM"),
    8192: ("2364", "2764", 24, "2364 mask ROM"),
    16384: ("23128", "27128", 28, "23128 mask ROM"),
    32768: ("23256", "27256", 28, "23256 mask ROM"),
    65536: ("23512", "27512", 28, "27512 EPROM"),
    131072: ("231024", "27C010", 28, "1Mbit mask ROM"),
}


class RomEditDialog(QDialog):
    def __init__(self, db: RomDatabase, rom: RomEntry | None = None, prefill: dict | None = None, parent=None):
        super().__init__(parent)
        self.db = db
        self.rom = rom
        self.saved_rom_id: str | None = None
        self.save_message = ""
        self.setWindowTitle("Edit ROM Entry" if rom else "Add Machine / ROM")
        self.setMinimumWidth(640)

        hex40 = QRegularExpressionValidator(QRegularExpression(r"[0-9A-Fa-f]{0,40}"))
        hex8 = QRegularExpressionValidator(QRegularExpression(r"(0x|0X)?[0-9A-Fa-f]{0,8}"))

        # --- machine
        mbox = QGroupBox("Machine")
        mf = QFormLayout(mbox)
        self.machine = QComboBox()
        self.machine.setEditable(True)
        self.machine.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.machine.addItems(sorted(m.name for m in db.machines))
        self.machine.setCurrentText("")
        self.machine.lineEdit().setPlaceholderText("Pick an existing machine or type a new one")
        self.machine.currentTextChanged.connect(self._machine_changed)
        self.manufacturer = QLineEdit()
        self.manufacturer.setPlaceholderText("e.g. Commodore, Sinclair, Acorn …")
        mf.addRow("Machine name *", self.machine)
        mf.addRow("Manufacturer / category", self.manufacturer)

        # --- ROM
        rbox = QGroupBox("ROM / chip")
        rf = QFormLayout(rbox)
        self.name = QLineEdit()
        self.name.setPlaceholderText("e.g. KERNAL ROM - Revision 3")
        label_row = QHBoxLayout()
        self.label = QLineEdit()
        self.label.setPlaceholderText("Short BackBit LCD label (max 16 chars), e.g. 'C64 KERNAL R3'")
        self.label_count = QLabel()
        self.label_count.setMinimumWidth(60)
        set_role(self.label_count, "counter")
        self.label.textChanged.connect(self._update_label_count)
        auto_btn = QPushButton("Auto")
        auto_btn.setToolTip("Generate a short label from machine, ROM name and socket")
        auto_btn.clicked.connect(self._auto_label)
        label_row.addWidget(self.label, 1)
        label_row.addWidget(self.label_count)
        label_row.addWidget(auto_btn)
        self.socket = QLineEdit()
        self.socket.setPlaceholderText("e.g. U4, IC5, UE12")
        self.parts = QLineEdit()
        self.parts.setPlaceholderText("Comma separated, e.g. 901227-03")
        size_row = QHBoxLayout()
        self.size = QSpinBox()
        self.size.setRange(1, 16 * 1024 * 1024)
        self.size.setSingleStep(1024)
        self.size.setSuffix(" bytes")
        self.size.setValue(8192)
        self.size_kb = QLabel()
        self.size.valueChanged.connect(self._size_changed)
        size_row.addWidget(self.size, 1)
        size_row.addWidget(self.size_kb)
        self.pins = QComboBox()
        self.pins.addItems(PIN_CHOICES)
        self.pins.setEditable(False)
        self.chip_type = QLineEdit()
        self.chip_type.setPlaceholderText("e.g. 2364 mask ROM")
        self.mask_setting = QLineEdit()
        self.mask_setting.setPlaceholderText("e.g. 2364")
        self.eprom_setting = QLineEdit()
        self.eprom_setting.setPlaceholderText("e.g. 2764 (leave blank if none)")
        rf.addRow("ROM name *", self.name)
        rf.addRow("BackBit LCD label", label_row)
        rf.addRow("Motherboard socket", self.socket)
        rf.addRow("Original part number(s)", self.parts)
        rf.addRow("Size", size_row)
        rf.addRow("Pin count", self.pins)
        rf.addRow("Chip type", self.chip_type)
        rf.addRow("BackBit mask ROM setting *", self.mask_setting)
        rf.addRow("EPROM equivalent setting", self.eprom_setting)

        # --- hashes
        hbox = QGroupBox("Hashes")
        hf = QFormLayout(hbox)
        self.sha1 = QLineEdit()
        self.sha1.setValidator(hex40)
        self.sha1.setFont(mono_font(10))
        self.sha1.setPlaceholderText("40 hex characters (optional, but needed for identification)")
        self.crc32 = QLineEdit()
        self.crc32.setValidator(hex8)
        self.crc32.setFont(mono_font(10))
        self.crc32.setPlaceholderText("8 hex characters (optional)")
        from_file = QPushButton("Fill size && hashes from a ROM file…")
        from_file.clicked.connect(self._fill_from_file)
        hf.addRow("SHA-1", self.sha1)
        hf.addRow("CRC32", self.crc32)
        hf.addRow("", from_file)

        # --- notes
        nbox = QGroupBox("Notes")
        nf = QFormLayout(nbox)
        self.description = QLineEdit()
        self.replacement = QLineEdit()
        self.replacement.setPlaceholderText("e.g. 27C64 on a 2364 adapter")
        self.warnings = QPlainTextEdit()
        self.warnings.setPlaceholderText("Hardware / polarity warnings — one per line")
        self.warnings.setFixedHeight(90)
        nf.addRow("Description", self.description)
        nf.addRow("Modern replacement", self.replacement)
        nf.addRow("Hardware / polarity warnings", self.warnings)

        self.write_master = QCheckBox(f"Also save into the master database file ({db.db_path.name})")
        self.write_master.setChecked(True)
        self.write_master.setToolTip("Changes are always kept in your user database (survives app updates). "
                                     "Ticking this also writes them into data/roms_database.json so a shared "
                                     "folder or build includes them.")
        if not db.master_writable:
            # Packaged build: the bundled master is read-only (and replaced on update) - overlay only.
            self.write_master.setChecked(False)
            self.write_master.setEnabled(False)
            self.write_master.setText("Saved to your user database (the bundled master database is read-only)")
            self.write_master.setToolTip("Use File > Export User Database Additions to share your entries.")

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)

        left = QVBoxLayout()
        left.addWidget(mbox)
        left.addWidget(rbox)
        right = QVBoxLayout()
        right.addWidget(hbox)
        right.addWidget(nbox)
        right.addStretch(1)
        cols = QHBoxLayout()
        cols.addLayout(left, 1)
        cols.addLayout(right, 1)
        lay = QVBoxLayout(self)
        lay.addLayout(cols)
        lay.addWidget(self.write_master)
        lay.addWidget(buttons)
        self.resize(1040, 640)

        if rom:
            self._load_rom(rom)
        else:
            self._size_changed(self.size.value())
        if prefill:
            self._apply_prefill(prefill)
        self._update_label_count()

    # ------------------------------------------------------------------ helpers
    def _machine_changed(self, text: str) -> None:
        mid = self.db.machine_id_for_name(text)
        m = self.db.machine_by_id(mid) if mid else None
        if m and not self.manufacturer.text():
            self.manufacturer.setText(m.manufacturer)

    def _size_changed(self, value: int) -> None:
        self.size_kb.setText(f"= {value / 1024:g} KB" + (f"  •  {CHIP_HINTS[value]}" if value in CHIP_HINTS else ""))

    def _apply_size_defaults(self, size: int, force: bool = False) -> None:
        if size not in SIZE_DEFAULTS:
            return
        mask, eprom, pins, chip = SIZE_DEFAULTS[size]
        if force or not self.mask_setting.text():
            self.mask_setting.setText(mask)
        if force or not self.eprom_setting.text():
            self.eprom_setting.setText(eprom)
        if force or not self.chip_type.text():
            self.chip_type.setText(chip)
        self.pins.setCurrentText(str(pins))

    def _update_label_count(self) -> None:
        n = len(sanitize_label(self.label.text()))
        self.label_count.setText(f"{n}/{DEFAULT_LABEL_LIMIT}")
        set_state(self.label_count, state="over" if n > DEFAULT_LABEL_LIMIT else "ok")

    def _auto_label(self) -> None:
        mid = self.db.machine_id_for_name(self.machine.currentText())
        m = self.db.machine_by_id(mid) if mid else None
        short = (m.short if m and m.short else self.machine.currentText().split(" ")[0])
        self.label.setText(auto_label(short, self.name.text(), self.socket.text()))

    def _fill_from_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Compute hashes from ROM file", "",
                                              "ROM images (*.bin *.rom *.BIN *.ROM);;All files (*)")
        if not path:
            return
        try:
            data = Path(path).read_bytes()
        except OSError as exc:
            QMessageBox.critical(self, "Cannot read file", str(exc))
            return
        self._apply_prefill({"size": len(data), "sha1": hashlib.sha1(data).hexdigest(),
                             "crc32": f"{zlib.crc32(data) & 0xFFFFFFFF:08x}"})

    def _apply_prefill(self, p: dict) -> None:
        if "size" in p:
            self.size.setValue(int(p["size"]))
            if not self.rom:
                self._apply_size_defaults(int(p["size"]), force=True)
        for key, widget in (("sha1", self.sha1), ("crc32", self.crc32), ("name", self.name),
                            ("description", self.description), ("socket", self.socket)):
            if p.get(key):
                widget.setText(str(p[key]))
        if p.get("machine"):
            self.machine.setCurrentText(p["machine"])

    def _load_rom(self, rom: RomEntry) -> None:
        self.machine.setCurrentText(rom.machine_name)
        self.manufacturer.setText(rom.manufacturer)
        self.name.setText(rom.name)
        self.label.setText(rom.label)
        self.socket.setText(rom.socket)
        self.parts.setText(", ".join(rom.part_numbers))
        self.size.setValue(rom.size)
        self._size_changed(rom.size)
        self.pins.setCurrentText(str(rom.pins) if str(rom.pins) in PIN_CHOICES else "28")
        self.chip_type.setText(rom.chip_type)
        if rom.backbit:
            self.mask_setting.setText(rom.backbit[0].setting)
        if len(rom.backbit) > 1:
            self.eprom_setting.setText(rom.backbit[1].setting)
        self.sha1.setText(rom.sha1 or "")
        self.crc32.setText(rom.crc32 or "")
        self.description.setText(rom.description)
        self.replacement.setText(rom.replacement)
        self.warnings.setPlainText("\n".join(rom.warnings))

    # ------------------------------------------------------------------ save
    def _save(self) -> None:
        machine = self.machine.currentText().strip()
        name = self.name.text().strip()
        sha = self.sha1.text().strip().lower()
        crc = self.crc32.text().strip().lower().removeprefix("0x")
        mask = self.mask_setting.text().strip()
        problems = []
        if not machine:
            problems.append("Machine name is required.")
        if not name:
            problems.append("ROM name is required.")
        if not mask:
            problems.append("The BackBit mask ROM setting is required.")
        if sha and not re.fullmatch(r"[0-9a-f]{40}", sha):
            problems.append("SHA-1 must be exactly 40 hex characters (or empty).")
        if crc and not re.fullmatch(r"[0-9a-f]{1,8}", crc):
            problems.append("CRC32 must be up to 8 hex characters (or empty).")
        if problems:
            QMessageBox.warning(self, "Please check the entry", "\n".join(problems))
            return

        pins = int(self.pins.currentText())
        eprom = self.eprom_setting.text().strip()
        backbit = [{"setting": mask, "pins": pins,
                    "chip": f"Original {self.chip_type.text().strip() or mask}"}]
        if eprom and eprom.lower() != mask.lower():
            backbit.append({"setting": eprom, "pins": guess_pins(eprom, pins), "chip": f"{eprom} EPROM equivalent"})

        if self.rom:
            base = rom_to_dict(self.rom)
            base.pop("id", None)
            extra_opts = base["backbit"][2:]
            backbit.extend(extra_opts)
            source = self.rom.source if self.rom.source == USER_SOURCE or "edited" in self.rom.source \
                else (self.rom.source + " (edited by user)" if self.rom.source else USER_SOURCE)
        else:
            base = {"notes": []}
            source = USER_SOURCE
        base.update({
            "name": name, "label": sanitize_label(self.label.text()), "description": self.description.text().strip() or name,
            "part_numbers": [p.strip() for p in self.parts.text().split(",") if p.strip()],
            "socket": self.socket.text().strip(), "size": self.size.value(), "pins": pins,
            "chip_type": self.chip_type.text().strip(), "backbit": backbit, "sha1": sha or None,
            "crc32": crc.zfill(8) if crc else None, "source": source,
            "replacement": self.replacement.text().strip(),
            "warnings": [w.strip() for w in self.warnings.toPlainText().splitlines() if w.strip()],
        })
        if sha and (not self.rom or sha != (self.rom.sha1 or "")):
            dupes = [m.label for m in self.db.lookup_sha1(sha) if m.rom and (not self.rom or m.rom.id != self.rom.id)]
            if dupes and QMessageBox.question(
                    self, "Hash already known",
                    "This SHA-1 already identifies:\n\n" + "\n".join(dupes[:6]) + "\n\nSave anyway?") \
                    != QMessageBox.StandardButton.Yes:
                return
        try:
            res = self.db.upsert_rom(machine, self.manufacturer.text(), base,
                                     rom_id=self.rom.id if self.rom else None,
                                     write_master=self.write_master.isChecked())
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Could not save", str(exc))
            return
        self.saved_rom_id = res.rom_id
        self.save_message = res.message
        self.accept()


def confirm_delete(parent: QWidget, db: RomDatabase, rom: RomEntry) -> bool:
    """Ask, then delete. Returns True when the entry was removed."""
    if QMessageBox.question(parent, "Delete ROM entry",
                            f"Delete this entry from the database?\n\n{rom.title}\n\n"
                            "It is removed from your user database and the master JSON file.",
                            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                            QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
        return False
    try:
        res = db.delete_rom(rom.id)
    except (OSError, KeyError) as exc:
        QMessageBox.critical(parent, "Delete failed", str(exc))
        return False
    if res.message:
        QMessageBox.warning(parent, "Partly saved", res.message)
    return True
