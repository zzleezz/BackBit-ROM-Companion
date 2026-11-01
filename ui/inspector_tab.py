"""Tab 2 - ROM File Inspector & Identifier: hashes, database identification, bank splitting, health check."""

from __future__ import annotations

import html
from pathlib import Path

from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QAbstractItemView, QComboBox, QFileDialog, QFrame, QGridLayout, QGroupBox,
                             QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPushButton, QScrollArea, QSplitter,
                             QTableWidget, QTableWidgetItem, QTextBrowser, QVBoxLayout, QWidget)

from core.analyzer import CHIP_HINTS, AnalysisResult, BankSlice, analyze_file
from core.database import Match, RomDatabase, format_size
from ui import theme
from ui.widgets import set_role
from ui.widgets import COLORS, BitHealthWidget, CopyField, DropZone, finding_html, mono_font

ROM_FILTER = "ROM images (*.bin *.rom *.BIN *.ROM *.img *.dump *.hex);;All files (*)"


class InspectorTab(QWidget):
    showRomRequested = pyqtSignal(str)   # rom id -> open in Lookup tab
    fileAnalyzed = pyqtSignal(str)       # status-bar message
    registerRequested = pyqtSignal(dict)  # prefill dict for the Add ROM dialog

    def __init__(self, db: RomDatabase, parent=None):
        super().__init__(parent)
        self.db = db
        self.result: AnalysisResult | None = None
        self._last_dir = ""
        self.setAcceptDrops(True)

        # ---------------- top: drop zone + buttons
        self.drop = DropZone("⬇  Drag & drop a .bin / .rom dump from the BackBit SD card here  —  or click to browse")
        self.drop.fileDropped.connect(self.load_file)
        self.drop.browseRequested.connect(self.browse)
        btns = QVBoxLayout()
        self.browse_btn = QPushButton("Browse…")
        self.browse_btn.clicked.connect(self.browse)
        self.reanalyze_btn = QPushButton("Re-analyse")
        self.reanalyze_btn.setEnabled(False)
        self.reanalyze_btn.clicked.connect(self.refresh)
        btns.addWidget(self.browse_btn)
        btns.addWidget(self.reanalyze_btn)
        btns.addStretch(1)
        top = QHBoxLayout()
        top.addWidget(self.drop, 1)
        top.addLayout(btns)

        # ---------------- left column
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 4, 0)

        file_box = QGroupBox("File")
        fg = QGridLayout(file_box)
        fg.setColumnStretch(1, 1)
        self.f_name = self._value()
        self.f_size = self._value(big=True)
        self.f_hint = self._value()
        for row, (name, w) in enumerate([("Name", self.f_name), ("Size", self.f_size), ("Likely chip", self.f_hint)]):
            lab = QLabel(name)
            set_role(lab, "field-name")
            fg.addWidget(lab, row, 0, Qt.AlignmentFlag.AlignTop)
            fg.addWidget(w, row, 1)
        ll.addWidget(file_box)

        hash_box = QGroupBox("Checksums")
        hl = QVBoxLayout(hash_box)
        self.h_sha1 = CopyField("SHA-1")
        self.h_crc = CopyField("CRC32")
        self.h_md5 = CopyField("MD5")
        for w in (self.h_sha1, self.h_crc, self.h_md5):
            hl.addWidget(w)
        ll.addWidget(hash_box)

        ident_box = QGroupBox("Identification")
        il = QVBoxLayout(ident_box)
        self.register_btn = QPushButton("➕  Register in Database…")
        self.register_btn.setToolTip("Add this file as a new ROM entry - size, SHA-1 and CRC32 are pre-filled.")
        set_role(self.register_btn, "primary")
        self.register_btn.clicked.connect(self._register)
        self.register_btn.hide()
        il.addWidget(self.register_btn)
        self.ident = QTextBrowser()
        self.ident.setOpenLinks(False)
        self.ident.anchorClicked.connect(self._on_anchor)
        self.ident.setMinimumHeight(260)
        il.addWidget(self.ident)
        ll.addWidget(ident_box, 1)

        # ---------------- right column
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(4, 0, 0, 0)

        bank_box = QGroupBox("Multi-ROM / banked ROM analysis")
        bl = QVBoxLayout(bank_box)
        view_row = QHBoxLayout()
        view_row.addWidget(QLabel("View:"))
        self.bank_view = QComboBox()
        self.bank_view.currentIndexChanged.connect(self._fill_bank_table)
        view_row.addWidget(self.bank_view, 1)
        bl.addLayout(view_row)
        self.bank_table = QTableWidget(0, 5)
        self.bank_table.setHorizontalHeaderLabels(["Offset range", "Size", "SHA-1", "CRC32", "Identification"])
        self.bank_table.verticalHeader().setVisible(False)
        self.bank_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.bank_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.bank_table.setAlternatingRowColors(True)
        self.bank_table.setWordWrap(False)
        hh = self.bank_table.horizontalHeader()
        for col in range(4):
            hh.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.bank_table.cellDoubleClicked.connect(self._on_bank_double_clicked)
        self.bank_note = QLabel()
        self.bank_note.setWordWrap(True)
        set_role(self.bank_note, "muted")
        bl.addWidget(self.bank_table, 1)
        bl.addWidget(self.bank_note)
        rl.addWidget(bank_box, 3)

        health_box = QGroupBox("Blank / stuck-bit quick health check")
        hbl = QVBoxLayout(health_box)
        self.bits = BitHealthWidget()
        self.health_label = QLabel()
        self.health_label.setWordWrap(True)
        self.health_label.setTextFormat(Qt.TextFormat.RichText)
        self.health_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        hbl.addWidget(self.bits)
        hbl.addWidget(self.health_label)
        rl.addWidget(health_box, 2)

        def scrolled(w: QWidget) -> QScrollArea:
            s = QScrollArea()
            s.setWidgetResizable(True)
            s.setFrameShape(QFrame.Shape.NoFrame)
            s.setWidget(w)
            return s

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(scrolled(left))
        splitter.addWidget(scrolled(right))
        splitter.setSizes([560, 700])

        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(splitter, 1)

        self._clear()
        theme.manager.changed.connect(self.refresh_theme)

    @staticmethod
    def _value(big: bool = False) -> QLabel:
        lab = QLabel("—")
        lab.setWordWrap(True)
        lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        if big:
            set_role(lab, "value-big")
        return lab

    def refresh_theme(self, _name: str = "") -> None:
        """Re-render the current result's rich text / table colours for the new theme (no re-analysis)."""
        if self.result is not None:
            self._show(self.result)
        else:
            self._clear()

    # ------------------------------------------------------------------ drag & drop on the whole tab
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            if url.isLocalFile():
                self.load_file(url.toLocalFile())
                return

    # ------------------------------------------------------------------ loading
    def browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open ROM image", self._last_dir, ROM_FILTER)
        if path:
            self.load_file(path)

    def refresh(self) -> None:
        """Re-run the analysis (e.g. after a sha.txt import)."""
        if self.result and self.result.path:
            self.load_file(self.result.path)

    def load_file(self, path: str) -> None:
        p = Path(path)
        if not p.is_file():
            QMessageBox.warning(self, "Not a file", f"{path}\nis not a readable file.")
            return
        try:
            result = analyze_file(p, self.db)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Cannot analyse file", str(exc))
            return
        self._last_dir = str(p.parent)
        self.result = result
        self.reanalyze_btn.setEnabled(True)
        self._show(result)
        state = "identified" if result.identified else "not identified"
        self.fileAnalyzed.emit(f"Analysed {p.name}: {format_size(result.size)}, {state}.")

    # ------------------------------------------------------------------ rendering
    def _clear(self) -> None:
        self.f_name.setText("—")
        self.f_size.setText("—")
        self.f_hint.setText("—")
        for w in (self.h_sha1, self.h_crc, self.h_md5):
            w.set_value(None)
        self.ident.setHtml(f"<p style='color:{COLORS['muted']}'>Load a ROM image to identify it against the built-in "
                           f"database and any imported BackBit sha.txt entries.</p>")
        self.bank_view.clear()
        self.bank_table.setRowCount(0)
        self.bank_note.setText("")
        self.bits.set_bits(None)
        self.health_label.setText("")
        self.register_btn.hide()

    def _show(self, r: AnalysisResult) -> None:
        p = Path(r.path) if r.path else None
        self.f_name.setText(f"{html.escape(p.name)}<br><span style='color:{COLORS['muted']}'>{html.escape(str(p.parent))}</span>"
                            if p else "—")
        kb = f"{r.size / 1024:,.2f}".rstrip("0").rstrip(".")
        self.f_size.setText(f"{r.size:,} bytes  •  {kb} KB  •  0x{r.size:X}")
        self.f_hint.setText(CHIP_HINTS.get(r.size, "Non-standard size for a single 8-bit ROM"))
        self.h_sha1.set_value(r.hashes.sha1)
        self.h_crc.set_value(r.hashes.crc32)
        self.h_md5.set_value(r.hashes.md5)

        self.ident.setHtml(self._ident_html(r))
        unrecognised = not r.whole_matches and not r.health.is_blank and r.size > 0
        self.register_btn.setVisible(unrecognised)
        self.register_btn.setText("➕  Register in Database…" if not r.identified
                                  else "➕  Register whole file in Database…")
        self._fill_bank_views(r)
        self._show_health(r)

    def _match_html(self, m: Match) -> str:
        if m.rom is None:
            return (f"<div style='margin:6px 0; padding:6px; background:{COLORS['panel2']}'>"
                    f"<b>{html.escape(m.description)}</b><br>"
                    f"<span style='color:{COLORS['muted']}'>Source: imported sha.txt (hash + description only)</span></div>")
        rom = m.rom
        opts = " / ".join(f"<b style='color:{COLORS['accent']}'>{html.escape(o.setting)}</b> ({o.pins}-pin"
                          f"{', ' + html.escape(o.chip) if o.warning else ''})"
                          for o in rom.backbit)
        # de-duplicated hardware warnings for the settings (e.g. the Pin 27 interposer for 23128 mask ROMs)
        hw = list(dict.fromkeys(o.warning for o in rom.backbit if o.warning))
        hw_html = "".join(f"<br><span style='color:{COLORS['error']}'><b>⚠ Hardware / polarity:</b> "
                          f"{html.escape(w)}</span>" for w in hw)
        bank = (f"<br>Bank: <b>{html.escape(m.bank.name)}</b> "
                f"(offset 0x{m.bank.offset:04X} in the {rom.size // 1024}KB chip)" if m.bank else "")
        return (f"<div style='margin:6px 0; padding:6px; background:{COLORS['panel2']}'>"
                f"<b style='font-size:11pt'>{html.escape(rom.machine_name)}</b><br>"
                f"<b>{html.escape(rom.name)}</b>{bank}<br>"
                f"Socket: <b>{html.escape(rom.socket)}</b> &nbsp;•&nbsp; "
                f"Part no.: <b>{html.escape(', '.join(rom.part_numbers))}</b><br>"
                f"Chip: {rom.pins}-pin {html.escape(rom.chip_type)} &nbsp;•&nbsp; BackBit: {opts}{hw_html}<br>"
                f"<a href='rom:{rom.id}' style='color:{COLORS['info']}'>Open in Machine Lookup →</a></div>")

    def _ident_html(self, r: AnalysisResult) -> str:
        parts = ["".join(finding_html(f.level, f.text) for f in r.summary)]

        def section(title: str, matches: list[Match]) -> None:
            if matches:
                parts.append(f"<h4 style='color:{COLORS['text']}; margin:10px 0 2px 0'>{html.escape(title)}</h4>")
                parts.extend(self._match_html(m) for m in matches)

        section("Whole-file match", r.whole_matches)
        section("CRC32-only match (SHA-1 differs!)", r.crc_only_matches)
        if r.repeat and r.repeat.matches:
            section(f"Repeated {r.repeat.unit_size // 1024}KB unit ×{r.repeat.count}", r.repeat.matches)
        elif not r.whole_matches:
            seen: set[str] = set()
            bank_matches = []
            for s in r.layout:
                for m in s.matches:
                    key = f"{m.label}|{s.offset}"
                    if key not in seen:
                        seen.add(key)
                        bank_matches.append(m)
            section("Bank matches", bank_matches)
        if r.health.findings and any(f.level in ("warn", "error") for f in r.health.findings):
            parts.append(finding_html("warn", "See the health check panel for hardware warnings."))
        return "".join(parts)

    def _fill_bank_views(self, r: AnalysisResult) -> None:
        self.bank_view.blockSignals(True)
        self.bank_view.clear()
        if r.layout:
            self.bank_view.addItem("Best layout (largest identified banks)", "layout")
        for size in sorted(r.slices_by_size):
            self.bank_view.addItem(f"All {size // 1024}KB slices", size)
        self.bank_view.blockSignals(False)

        if r.whole_matches:
            self.bank_note.setText("Whole file matched — bank splitting not needed.")
        elif r.health.is_blank:
            self.bank_note.setText("Image is blank — nothing to split.")
        elif not r.slices_by_size:
            self.bank_note.setText("File is too small or not a multiple of 4KB — no bank splitting performed.")
        else:
            self.bank_note.setText("Double-click an identified row to open it in Machine Lookup. "
                                   "“= 0x…” marks a slice identical to an earlier one.")
        self.bank_view.setEnabled(self.bank_view.count() > 0)
        self._fill_bank_table()

    def _current_slices(self) -> list[BankSlice]:
        if not self.result:
            return []
        key = self.bank_view.currentData()
        if key == "layout":
            return self.result.layout
        if isinstance(key, int):
            return self.result.slices_by_size.get(key, [])
        return []

    def _fill_bank_table(self, *_args) -> None:
        slices = self._current_slices()
        self.bank_table.setRowCount(len(slices))
        mono = mono_font(9)
        for row, s in enumerate(slices):
            if s.matches:
                ident = " | ".join(m.label for m in s.matches)
                color = COLORS["ok"]
            elif s.blank_value is not None:
                ident = f"Blank (all 0x{s.blank_value:02X})"
                color = COLORS["muted"]
            else:
                ident = "Unidentified"
                color = COLORS["warn"]
            if s.duplicate_of is not None:
                ident += f"   = 0x{s.duplicate_of:04X}"
            values = [s.range_text, f"{s.size // 1024}KB", s.sha1, s.crc32, ident]
            for col, val in enumerate(values):
                item = QTableWidgetItem(val)
                if col in (0, 2, 3):
                    item.setFont(mono)
                if col == 4:
                    item.setForeground(QColor(color))
                    item.setToolTip(ident)
                if col == 2:
                    item.setToolTip(s.sha1)
                self.bank_table.setItem(row, col, item)

    def _on_bank_double_clicked(self, row: int, _col: int) -> None:
        slices = self._current_slices()
        if 0 <= row < len(slices):
            for m in slices[row].matches:
                if m.rom:
                    self.showRomRequested.emit(m.rom.id)
                    return

    def _show_health(self, r: AnalysisResult) -> None:
        h = r.health
        self.bits.set_bits([b.ratio for b in h.bits], [b.stuck for b in h.bits])
        extra = (f"<p style='color:{COLORS['muted']}'>{h.unique_values} distinct byte values • "
                 f"{h.size:,} bytes scanned</p>")
        self.health_label.setText("".join(finding_html(f.level, f.text) for f in h.findings) + extra)

    def _register(self) -> None:
        r = self.result
        if not r:
            return
        name = Path(r.path).stem.replace("_", " ") if r.path else ""
        self.registerRequested.emit({"size": r.size, "sha1": r.hashes.sha1, "crc32": r.hashes.crc32, "name": name})

    def _on_anchor(self, url: QUrl) -> None:
        text = url.toString()
        if text.startswith("rom:"):
            self.showRomRequested.emit(text[4:])
