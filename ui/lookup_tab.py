"""Tab 1 - Machine -> BackBit Lookup: pick a machine/ROM, see tester settings, caveats and ZIF placement."""

from __future__ import annotations

import html
import re

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QComboBox, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                             QPushButton, QScrollArea, QSplitter, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from core.database import RomDatabase, RomEntry, format_size
from core.analyzer import hex_range
from ui.rom_dialog import RomEditDialog, confirm_delete
from ui import theme
from ui.widgets import COLORS, MONO_CSS, set_role, BadgeLabel, CopyField, ZifSocketWidget

_URL = re.compile(r"https?://[^\s<>\"]+[^\s<>\".,;:!?)\]]")
# selectable with mouse and keyboard, and links open in the browser
_TEXT_FLAGS = Qt.TextInteractionFlag.TextBrowserInteraction


def _rich(text: str) -> str:
    """Escape database text for a rich-text label and turn web addresses into clickable links."""
    out, pos = [], 0
    for m in _URL.finditer(text):
        out.append(html.escape(text[pos:m.start()]))
        url = html.escape(m.group(0), quote=True)
        out.append(f"<a href=\"{url}\" style=\"color:{COLORS['accent']}\">{url}</a>")
        pos = m.end()
    out.append(html.escape(text[pos:]))
    return "".join(out)


def _selectable(label: QLabel) -> None:
    label.setTextInteractionFlags(_TEXT_FLAGS)
    label.setOpenExternalLinks(True)

ROLE_ROM_ID = Qt.ItemDataRole.UserRole


class LookupTab(QWidget):
    databaseChanged = pyqtSignal(str)   # rom id that was added / edited / deleted

    def __init__(self, db: RomDatabase, parent=None):
        super().__init__(parent)
        self.db = db
        self._rom: RomEntry | None = None

        # ---------------- left: search + tree
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search machine, part no., socket, chip type…  (e.g. 901227, U4, 2332)")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._populate)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Machine / ROM", "Socket", "Type"])
        self.tree.setColumnWidth(0, 235)
        self.tree.setColumnWidth(1, 70)
        self.tree.setAlternatingRowColors(True)
        self.tree.currentItemChanged.connect(self._on_item_changed)
        ll.addWidget(self.search)
        ll.addWidget(self.tree, 1)
        btn_row = QHBoxLayout()
        self.add_btn = QPushButton("+ Add Machine / ROM")
        set_role(self.add_btn, "link")
        self.edit_btn = QPushButton("Edit Selected")
        self.del_btn = QPushButton("Delete")
        self.add_btn.clicked.connect(self.add_entry)
        self.edit_btn.clicked.connect(self.edit_selected)
        self.del_btn.clicked.connect(self.delete_selected)
        self.tree.itemDoubleClicked.connect(lambda item, _c: self.edit_selected() if item.data(0, ROLE_ROM_ID) else None)
        for b in (self.add_btn, self.edit_btn, self.del_btn):
            btn_row.addWidget(b)
        ll.addLayout(btn_row)
        self.count_label = QLabel()
        set_role(self.count_label, "muted")
        ll.addWidget(self.count_label)

        # ---------------- middle: details
        detail = QWidget()
        dl = QVBoxLayout(detail)
        dl.setSpacing(10)
        self.title = QLabel("Select a machine and ROM")
        self.title.setWordWrap(True)
        self.title.setStyleSheet("font-size:16pt; font-weight:700;")
        self.desc = QLabel()
        self.desc.setWordWrap(True)
        set_role(self.desc, "muted")
        dl.addWidget(self.title)
        dl.addWidget(self.desc)

        opt_row = QHBoxLayout()
        opt_label = QLabel("Chip physically fitted:")
        opt_label.setStyleSheet("font-weight:600;")
        self.option_combo = QComboBox()
        # long chip descriptions must not widen the detail panel; the drop-down list still shows them in full
        self.option_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.option_combo.setMinimumContentsLength(24)
        self.option_combo.view().setTextElideMode(Qt.TextElideMode.ElideNone)
        self.option_combo.currentIndexChanged.connect(self._on_option_changed)
        opt_row.addWidget(opt_label)
        opt_row.addWidget(self.option_combo, 1)
        dl.addLayout(opt_row)

        badge_caption = QLabel("BACKBIT CHIP TESTER PRO V2 — MENU SETTING")
        set_role(badge_caption, "caption")
        self.badge = BadgeLabel("—", point_size=20)
        dl.addWidget(badge_caption)
        dl.addWidget(self.badge)
        # chip-specific hardware warning for the selected setting (e.g. 23128 read as 27128 needs an interposer)
        self.option_warn = QLabel()
        self.option_warn.setWordWrap(True)
        self.option_warn.setTextFormat(Qt.TextFormat.RichText)
        _selectable(self.option_warn)
        set_role(self.option_warn, "alert")
        self.option_warn.hide()
        dl.addWidget(self.option_warn)

        info = QGroupBox("Chip details")
        grid = QGridLayout(info)
        grid.setColumnStretch(1, 1)
        self.f_parts = self._value_label()
        self.f_socket = self._value_label(big=True)
        self.f_size = self._value_label(big=True)
        self.f_pins = self._value_label()
        self.f_source = self._value_label()
        for row, (name, widget) in enumerate([("Original part no.", self.f_parts), ("Motherboard socket", self.f_socket),
                                              ("Size", self.f_size), ("Package / type", self.f_pins),
                                              ("Hash source", self.f_source)]):
            lab = QLabel(name)
            set_role(lab, "field-name")
            grid.addWidget(lab, row, 0, Qt.AlignmentFlag.AlignTop)
            grid.addWidget(widget, row, 1)
        self.f_sha1 = CopyField("SHA-1")
        self.f_crc = CopyField("CRC32")
        grid.addWidget(self.f_sha1, 5, 0, 1, 2)
        grid.addWidget(self.f_crc, 6, 0, 1, 2)
        dl.addWidget(info)

        self.banks_box = QGroupBox("Banks inside this chip")
        bl = QVBoxLayout(self.banks_box)
        self.banks_label = QLabel()
        self.banks_label.setWordWrap(True)
        self.banks_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        bl.addWidget(self.banks_label)
        dl.addWidget(self.banks_box)

        self.warn_box = QGroupBox("⚠  Polarity && adapter warnings")
        set_role(self.warn_box, "warn-box")
        wl = QVBoxLayout(self.warn_box)
        self.warn_label = QLabel()
        self.warn_label.setWordWrap(True)
        self.warn_label.setTextFormat(Qt.TextFormat.RichText)
        _selectable(self.warn_label)
        wl.addWidget(self.warn_label)
        dl.addWidget(self.warn_box)

        notes_box = QGroupBox("Replacement && notes")
        nl = QVBoxLayout(notes_box)
        self.notes_label = QLabel()
        self.notes_label.setWordWrap(True)
        self.notes_label.setTextFormat(Qt.TextFormat.RichText)
        _selectable(self.notes_label)
        nl.addWidget(self.notes_label)
        dl.addWidget(notes_box)
        dl.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.badge.setWordWrap(True)
        scroll.setWidget(detail)

        # ---------------- right: ZIF
        self.zif = ZifSocketWidget()

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(scroll)
        splitter.addWidget(self.zif)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        splitter.setStretchFactor(2, 2)
        splitter.setSizes([340, 520, 470])   # room for the wide-body ZIF drawing

        lay = QVBoxLayout(self)
        lay.addWidget(splitter)

        self._populate()
        self._show_rom(None)
        theme.manager.changed.connect(self.refresh_theme)

    def refresh_theme(self, _name: str = "") -> None:
        """Re-render rich text and item colours for the new theme, keeping the selection and chip option."""
        option = self.option_combo.currentIndex()
        self.reload()
        if self._rom is not None and 0 <= option < self.option_combo.count():
            self.option_combo.setCurrentIndex(option)
            self._on_option_changed(option)

    @staticmethod
    def _value_label(big: bool = False) -> QLabel:
        lab = QLabel("—")
        lab.setWordWrap(True)
        lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        if big:
            set_role(lab, "value-big")
        return lab

    # ------------------------------------------------------------------ tree
    def reload(self) -> None:
        current = self._rom.id if self._rom else None
        self._populate()
        if current:
            self.select_rom(current)

    def _populate(self) -> None:
        text = self.search.text()
        matches = {rom.id for rom in self.db.search(text)}
        self.tree.blockSignals(True)
        self.tree.clear()
        shown = 0
        for machine in self.db.machines:
            roms = [r for r in machine.roms if r.id in matches]
            if not roms:
                continue
            year = f" ({machine.year})" if machine.year else ""
            parent = QTreeWidgetItem([f"{machine.name}{year}", "", ""])
            font = parent.font(0)
            font.setBold(True)
            parent.setFont(0, font)
            parent.setFlags(parent.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            for rom in roms:
                part = rom.part_numbers[0] if rom.part_numbers else ""
                label = rom.name + (f"  [{part}]" if part and " " not in part and len(part) <= 12 else "")
                socket = "?" if rom.socket.startswith("See") else rom.socket.split(" ")[0]
                if rom.is_user_entry:
                    label = "✎ " + label
                child = QTreeWidgetItem([label, socket, rom.backbit[0].setting if rom.backbit else ""])
                child.setData(0, ROLE_ROM_ID, rom.id)
                child.setToolTip(0, rom.description + ("\n(user-added / edited entry)" if rom.is_user_entry else ""))
                if rom.is_user_entry:
                    child.setForeground(0, QColor(COLORS["info"]))
                parent.addChild(child)
                shown += 1
            self.tree.addTopLevelItem(parent)
        self.tree.expandAll()
        self.tree.blockSignals(False)
        user = f" ({self.db.user_rom_count} user-added/edited)" if self.db.user_rom_count else ""
        self.count_label.setText(f"{shown} ROM(s) shown • {self.db.builtin_rom_count} in database{user}")
        if self._rom and self._rom.id in matches:
            self._select_item(self._rom.id)

    def _select_item(self, rom_id: str) -> bool:
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            for j in range(top.childCount()):
                item = top.child(j)
                if item.data(0, ROLE_ROM_ID) == rom_id:
                    self.tree.setCurrentItem(item)
                    self.tree.scrollToItem(item)
                    return True
        return False

    def select_rom(self, rom_id: str) -> None:
        """Public hook: show a ROM (used by the inspector's 'show in lookup' links)."""
        if not self._select_item(rom_id):
            self.search.clear()  # repopulates
            self._select_item(rom_id)

    def _on_item_changed(self, current, _previous) -> None:
        rom_id = current.data(0, ROLE_ROM_ID) if current else None
        self._show_rom(self.db.rom_by_id(rom_id) if rom_id else None)

    # ------------------------------------------------------------------ details
    def _show_rom(self, rom: RomEntry | None) -> None:
        self._rom = rom
        self.edit_btn.setEnabled(rom is not None)
        self.del_btn.setEnabled(rom is not None)
        self.option_combo.blockSignals(True)
        self.option_combo.clear()
        if rom is None:
            self.title.setText("Select a machine and ROM")
            self.desc.setText("Pick the chip you have pulled from the board to see the BackBit menu setting, "
                              "socket, polarity caveats and where to seat it in the ZIF socket.")
            for lab in (self.f_parts, self.f_socket, self.f_size, self.f_pins, self.f_source):
                lab.setText("—")
            self.f_sha1.set_value(None)
            self.f_crc.set_value(None)
            self.badge.setText("—")
            self.option_warn.hide()
            self.banks_box.hide()
            self.warn_box.hide()
            self.notes_label.setText("")
            self.option_combo.setEnabled(False)
            self.option_combo.blockSignals(False)
            self.zif.set_chip(None)
            return

        self.option_combo.setEnabled(True)
        for opt in rom.backbit:
            self.option_combo.addItem(f"{opt.setting} — {opt.chip}")
        self.option_combo.blockSignals(False)

        self.title.setText(rom.title)
        self.desc.setText(rom.description)
        self.f_parts.setText(", ".join(rom.part_numbers) or "—")
        self.f_socket.setText(rom.socket or "—")
        self.f_size.setText(format_size(rom.size))
        self.f_pins.setText(f"{rom.pins}-pin DIP • {rom.chip_type}")
        self.f_source.setText(rom.source or "—")
        self.f_sha1.set_value(rom.sha1)
        self.f_crc.set_value(rom.crc32)
        if not rom.sha1:
            self.f_sha1.edit.setPlaceholderText("No single whole-chip hash — identified by banks below")

        if rom.banks:
            rows = []
            for b in rom.banks:
                rows.append(f"<b>{html.escape(hex_range(b.offset, b.size))}</b> ({b.size // 1024}KB) — "
                            f"{html.escape(b.name)}<br><span style=\"font-family:{MONO_CSS}; color:{COLORS['muted']}\">"
                            f"SHA-1 {b.sha1}{'  CRC32 ' + b.crc32 if b.crc32 else ''}</span>")
            self.banks_label.setText("<br>".join(rows))
            self.banks_box.show()
        else:
            self.banks_box.hide()

        notes = []
        if rom.replacement:
            notes.append(f"<p><b style='color:{COLORS['ok']}'>Modern replacement:</b> {_rich(rom.replacement)}</p>")
        for n in rom.notes:
            notes.append(f"<p>• {_rich(n)}</p>")
        self.notes_label.setText("".join(notes) or "—")

        self.option_combo.setCurrentIndex(0)
        self._on_option_changed(0)

    def _on_option_changed(self, index: int) -> None:
        rom = self._rom
        if rom is None or not rom.backbit or index < 0:
            return
        opt = rom.backbit[index]
        self.badge.setText(opt.menu_text)
        self.badge.set_variant("primary" if index == 0 else "alt")
        self.zif.set_chip(opt.pins, rom.title, opt.setting)
        if opt.warning:
            self.option_warn.setText(
                f"<div style='font-size:11pt; font-weight:800; color:{COLORS['error']}'>"
                f"⚠ HARDWARE / POLARITY WARNING</div>"
                f"<div style='margin-top:4px'>{_rich(opt.warning)}</div>")
            self.option_warn.show()
        else:
            self.option_warn.hide()

        warnings = list(rom.warnings)
        if opt.pins != rom.pins:
            warnings.insert(0, f"The {opt.setting} is a {opt.pins}-pin part but the motherboard socket is "
                               f"{rom.pins}-pin. A {opt.pins}-pin replacement needs a {opt.pins}-to-{rom.pins} "
                               f"pin adapter in the machine, and must be taken OFF that adapter and seated "
                               f"directly in the ZIF to read it with the {opt.setting} setting.")
        if warnings:
            self.warn_label.setText("".join(f"<p style='margin:4px 0'>⚠ {_rich(w)}</p>"
                                            for w in warnings))
            self.warn_box.show()
        else:
            self.warn_box.hide()

    # ------------------------------------------------------------------ Database Manager
    def open_add_dialog(self, prefill: dict | None = None) -> str | None:
        """Open the Add dialog (optionally pre-filled, e.g. from the Inspector). Returns the new ROM id."""
        dlg = RomEditDialog(self.db, prefill=prefill, parent=self)
        if dlg.exec() != RomEditDialog.DialogCode.Accepted or not dlg.saved_rom_id:
            return None
        self._after_change(dlg.saved_rom_id, dlg.save_message)
        return dlg.saved_rom_id

    def add_entry(self) -> None:
        prefill = {"machine": self._rom.machine_name} if self._rom else None
        self.open_add_dialog(prefill)

    def edit_selected(self) -> None:
        if not self._rom:
            QMessageBox.information(self, "Nothing selected", "Select a ROM in the list first.")
            return
        dlg = RomEditDialog(self.db, rom=self._rom, parent=self)
        if dlg.exec() == RomEditDialog.DialogCode.Accepted and dlg.saved_rom_id:
            self._after_change(dlg.saved_rom_id, dlg.save_message)

    def delete_selected(self) -> None:
        if not self._rom:
            QMessageBox.information(self, "Nothing selected", "Select a ROM in the list first.")
            return
        rom_id = self._rom.id
        if confirm_delete(self, self.db, self._rom):
            self._rom = None
            self._populate()
            self._show_rom(None)
            self.databaseChanged.emit(rom_id)

    def _after_change(self, rom_id: str, message: str) -> None:
        if message:
            QMessageBox.warning(self, "Saved to user database only", message)
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        self._populate()
        self.select_rom(rom_id)
        self.databaseChanged.emit(rom_id)
