"""Tab 3 - Smart ROM Compare & Hardware Fault Diagnostics."""

from __future__ import annotations

import bisect
import hashlib
import html
import re
from pathlib import Path

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QFileDialog, QFrame, QGroupBox, QHBoxLayout, QHeaderView,
                             QLabel, QMessageBox, QPushButton, QSplitter, QTableView, QTextBrowser, QVBoxLayout,
                             QWidget)

from core.analyzer import MAX_FILE_SIZE, CompareResult, check_health, compare_images
from core.database import RomDatabase, format_size
from ui import theme
from ui.widgets import COLORS, MONO_CSS, DropZone, finding_html, mono_font, set_role, set_state

ROM_FILTER = "ROM images (*.bin *.rom *.BIN *.ROM *.img *.dump);;All files (*)"
BYTES_PER_ROW = 16
COL_A0 = 1
COL_A_ASCII = COL_A0 + BYTES_PER_ROW
COL_B0 = COL_A_ASCII + 1
COL_B_ASCII = COL_B0 + BYTES_PER_ROW
NCOLS = COL_B_ASCII + 1



def _bits_html(value: int | None, other: int | None, color: str) -> str:
    """Binary digits of ``value``; bits that differ from ``other`` are highlighted (theme-aware)."""
    if value is None:
        return "--------"
    out = []
    for k in range(7, -1, -1):
        bit = value >> k & 1
        if other is not None and (other >> k & 1) != bit:
            out.append(f"<b style='color:{color}; background:{COLORS['bit_mark_bg']}'>{bit}</b>")
        else:
            out.append(str(bit))
        if k == 4:
            out.append(" ")
    return "".join(out)


class HexDiffModel(QAbstractTableModel):
    """16 bytes per row: offset | A hex x16 | A ascii | B hex x16 | B ascii."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.a = b""
        self.b = b""
        self.length = 0
        self.diff_rows: list[int] = []
        self._diff_row_set: set[int] = set()
        self.rows: list[int] | None = None   # None = all rows
        self.only_diffs = False
        self._mono = mono_font(9)

    def set_data(self, a: bytes, b: bytes) -> None:
        self.beginResetModel()
        self.a, self.b = a, b
        self.length = max(len(a), len(b))
        m = min(len(a), len(b))
        rows = set()
        if m:
            xb = (int.from_bytes(a[:m], "big") ^ int.from_bytes(b[:m], "big")).to_bytes(m, "big")
            rows = {mm.start() // BYTES_PER_ROW for mm in re.finditer(rb"[^\x00]", xb)}
        if len(a) != len(b):
            rows.update(range(m // BYTES_PER_ROW, (self.length + BYTES_PER_ROW - 1) // BYTES_PER_ROW))
        self.diff_rows = sorted(rows)
        self._diff_row_set = rows
        self._apply_filter()
        self.endResetModel()

    def set_only_diffs(self, on: bool) -> None:
        self.beginResetModel()
        self.only_diffs = on
        self._apply_filter()
        self.endResetModel()

    def _apply_filter(self) -> None:
        self.rows = self.diff_rows if self.only_diffs else None

    def row_to_line(self, row: int) -> int:
        return self.rows[row] if self.rows is not None else row

    def line_to_row(self, line: int) -> int | None:
        if self.rows is None:
            return line
        i = bisect.bisect_left(self.rows, line)
        return i if i < len(self.rows) and self.rows[i] == line else None

    def rowCount(self, parent=QModelIndex()) -> int:
        if parent.isValid():
            return 0
        if self.rows is not None:
            return len(self.rows)
        return (self.length + BYTES_PER_ROW - 1) // BYTES_PER_ROW

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else NCOLS

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole or orientation != Qt.Orientation.Horizontal:
            return None
        if section == 0:
            return "Offset"
        if COL_A0 <= section < COL_A_ASCII:
            return f"{section - COL_A0:X}"
        if section == COL_A_ASCII:
            return "A (dump) ASCII"
        if COL_B0 <= section < COL_B_ASCII:
            return f"{section - COL_B0:X}"
        return "B (reference) ASCII"

    def byte_at(self, side: str, offset: int) -> int | None:
        buf = self.a if side == "a" else self.b
        return buf[offset] if offset < len(buf) else None

    def cell_offset(self, index: QModelIndex) -> tuple[str, int] | None:
        col = index.column()
        line = self.row_to_line(index.row())
        if COL_A0 <= col < COL_A_ASCII:
            return "a", line * BYTES_PER_ROW + col - COL_A0
        if COL_B0 <= col < COL_B_ASCII:
            return "b", line * BYTES_PER_ROW + col - COL_B0
        return None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        col = index.column()
        line = self.row_to_line(index.row())
        base = line * BYTES_PER_ROW
        if role == Qt.ItemDataRole.FontRole:
            return self._mono
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return int(Qt.AlignmentFlag.AlignCenter) if col not in (COL_A_ASCII, COL_B_ASCII) else \
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        if col == 0:
            if role == Qt.ItemDataRole.DisplayRole:
                return f"{base:06X}" if self.length > 0xFFFF else f"{base:04X}"
            if role == Qt.ItemDataRole.ForegroundRole:
                return QColor(COLORS["accent"]) if line in self._diff_row_set else QColor(COLORS["muted"])
            return None
        if col in (COL_A_ASCII, COL_B_ASCII):
            if role == Qt.ItemDataRole.DisplayRole:
                buf = self.a if col == COL_A_ASCII else self.b
                chunk = buf[base:base + BYTES_PER_ROW]
                return "".join(chr(c) if 32 <= c < 127 else "." for c in chunk)
            return None
        cell = self.cell_offset(index)
        if cell is None:
            return None
        side, off = cell
        val = self.byte_at(side, off)
        other = self.byte_at("b" if side == "a" else "a", off)
        differs = val != other
        if role == Qt.ItemDataRole.DisplayRole:
            return "--" if val is None else f"{val:02X}"
        # colours are read from the live theme tokens on every paint, so a theme switch needs no rebuild
        if role == Qt.ItemDataRole.BackgroundRole and differs:
            return QColor(COLORS["diff_a_bg"] if side == "a" else COLORS["diff_b_bg"])
        if role == Qt.ItemDataRole.ForegroundRole:
            if val is None:
                return QColor(COLORS["muted"])
            return QColor(COLORS["diff_fg"]) if differs else None
        if role == Qt.ItemDataRole.ToolTipRole:
            a, b = self.byte_at("a", off), self.byte_at("b", off)
            fa = "--" if a is None else f"0x{a:02X} {a:08b}"
            fb = "--" if b is None else f"0x{b:02X} {b:08b}"
            tip = f"Offset 0x{off:X}\nA (dump):      {fa}\nB (reference): {fb}"
            if a is not None and b is not None and a != b:
                bits = ", ".join(f"D{k}" for k in range(7, -1, -1) if (a ^ b) >> k & 1)
                tip += f"\nDiffering bits: {bits}"
            return tip
        return None


class FilePanel(QGroupBox):
    """One side of the comparison: drop zone + file info."""

    loaded = pyqtSignal()

    def __init__(self, title: str, db: RomDatabase, parent=None):
        super().__init__(title, parent)
        self.db = db
        self.path: Path | None = None
        self.data: bytes | None = None
        self.drop = DropZone("Drop file here or click to browse")
        self.drop.setMinimumHeight(56)
        self.drop.fileDropped.connect(self.load)
        self.drop.browseRequested.connect(self.browse)
        self.info = QLabel("No file loaded.")
        self.info.setWordWrap(True)
        self.info.setTextFormat(Qt.TextFormat.RichText)
        self.info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        clear = QPushButton("Clear")
        clear.clicked.connect(self.clear)
        row = QHBoxLayout()
        row.addWidget(self.drop, 1)
        row.addWidget(clear)
        lay = QVBoxLayout(self)
        lay.addLayout(row)
        lay.addWidget(self.info)

    def browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, f"Open {self.title()}", "", ROM_FILTER)
        if path:
            self.load(path)

    def load(self, path: str) -> None:
        p = Path(path)
        try:
            if p.stat().st_size > MAX_FILE_SIZE:
                raise ValueError(f"{p.name} is larger than {MAX_FILE_SIZE // (1024 * 1024)}MB.")
            data = p.read_bytes()
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Cannot load file", str(exc))
            return
        self.path, self.data = p, data
        self.refresh_info()
        self.loaded.emit()

    def set_bytes(self, data: bytes | None, path: Path | None) -> None:
        self.data, self.path = data, path
        self.refresh_info()

    def clear(self) -> None:
        self.path, self.data = None, None
        self.refresh_info()
        self.loaded.emit()

    def refresh_info(self) -> None:
        if self.data is None:
            self.info.setText(f"<span style='color:{COLORS['muted']}'>No file loaded.</span>")
            return
        sha = hashlib.sha1(self.data).hexdigest()
        matches = self.db.lookup_sha1(sha)
        ident = (f"<span style='color:{COLORS['ok']}'>✔ {html.escape(matches[0].label)}</span>"
                 if matches else f"<span style='color:{COLORS['muted']}'>Not in database</span>")
        name = html.escape(self.path.name) if self.path else "(memory)"
        self.info.setText(f"<b>{name}</b> — {format_size(len(self.data))}<br>"
                          f"<span style=\"font-family:{MONO_CSS}\">SHA-1 {sha}</span><br>{ident}")


class CompareTab(QWidget):
    def __init__(self, db: RomDatabase, parent=None):
        super().__init__(parent)
        self.db = db
        self.result: CompareResult | None = None

        self.panel_a = FilePanel("File A — ripped chip dump", db)
        self.panel_b = FilePanel("File B — known-good reference", db)
        self.panel_a.loaded.connect(self.run_compare)
        self.panel_b.loaded.connect(self.run_compare)
        swap = QPushButton("⇄ Swap")
        swap.setToolTip("Swap File A and File B")
        swap.clicked.connect(self.swap)
        files = QHBoxLayout()
        files.addWidget(self.panel_a, 1)
        files.addWidget(swap)
        files.addWidget(self.panel_b, 1)

        self.banner = QLabel()
        self.banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.banner.setWordWrap(True)
        self.banner.setMinimumHeight(46)
        set_role(self.banner, "banner", kind="empty")

        self.diag = QTextBrowser()
        self.diag.setMinimumWidth(320)
        diag_box = QGroupBox("Automated hardware fault diagnosis")
        dl = QVBoxLayout(diag_box)
        dl.addWidget(self.diag)

        # hex viewer
        self.model = HexDiffModel(self)
        self.view = QTableView()
        self.view.setModel(self.model)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.view.verticalHeader().setVisible(False)
        self.view.verticalHeader().setDefaultSectionSize(20)
        self.view.setShowGrid(False)
        self.view.setWordWrap(False)
        hh = self.view.horizontalHeader()
        hh.setFont(mono_font(9))
        hh.setMinimumSectionSize(20)
        for c in range(NCOLS):
            if c in (COL_A_ASCII, COL_B_ASCII):
                hh.setSectionResizeMode(c, QHeaderView.ResizeMode.Fixed)
                self.view.setColumnWidth(c, 150)
            elif c == 0:
                self.view.setColumnWidth(c, 66)
            else:
                self.view.setColumnWidth(c, 26)
        self.view.selectionModel().currentChanged.connect(self._on_cell)

        self.only_diffs = QCheckBox("Show only rows with differences")
        self.only_diffs.toggled.connect(self._toggle_filter)
        prev_btn = QPushButton("◀ Prev diff")
        next_btn = QPushButton("Next diff ▶")
        prev_btn.clicked.connect(lambda: self._jump(-1))
        next_btn.clicked.connect(lambda: self._jump(1))
        self.row_info = QLabel()
        set_role(self.row_info, "muted")
        tools = QHBoxLayout()
        tools.addWidget(self.only_diffs)
        tools.addStretch(1)
        tools.addWidget(self.row_info)
        tools.addWidget(prev_btn)
        tools.addWidget(next_btn)
        self.bit_detail = QLabel("Click a byte to see its bits. Differing bytes are highlighted "
                                 "(red = dump A, blue = reference B).")
        self.bit_detail.setTextFormat(Qt.TextFormat.RichText)
        set_role(self.bit_detail, "panel-box")
        hex_box = QGroupBox("Side-by-side hex diff")
        hl = QVBoxLayout(hex_box)
        hl.addLayout(tools)
        hl.addWidget(self.view, 1)
        hl.addWidget(self.bit_detail)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(diag_box)
        splitter.addWidget(hex_box)
        splitter.setSizes([420, 1000])

        lay = QVBoxLayout(self)
        lay.addLayout(files)
        lay.addWidget(self.banner)
        lay.addWidget(splitter, 1)
        self.run_compare()
        theme.manager.changed.connect(self.refresh_theme)

    def refresh_theme(self, _name: str = "") -> None:
        """Re-render file info, diagnosis HTML and the bit breakdown, keeping the selected hex cell."""
        cur = self.view.currentIndex()
        row, col = (cur.row(), cur.column()) if cur.isValid() else (-1, -1)
        self.refresh()
        if row >= 0 and row < self.model.rowCount():
            idx = self.model.index(row, col)
            self.view.setCurrentIndex(idx)
            self._on_cell(idx, idx)
        self.view.viewport().update()

    # ------------------------------------------------------------------ public
    def load_files(self, a: str | None = None, b: str | None = None) -> None:
        if a:
            self.panel_a.load(a)
        if b:
            self.panel_b.load(b)

    def refresh(self) -> None:
        self.panel_a.refresh_info()
        self.panel_b.refresh_info()
        self.run_compare()

    def swap(self) -> None:
        da, pa = self.panel_a.data, self.panel_a.path
        self.panel_a.set_bytes(self.panel_b.data, self.panel_b.path)
        self.panel_b.set_bytes(da, pa)
        self.run_compare()

    # ------------------------------------------------------------------ comparison
    def _set_banner(self, kind: str, text: str) -> None:
        """Verdict banner; colours come from the theme's banner role (green / blue / amber / red)."""
        self.banner.setText(text)
        set_state(self.banner, kind=kind)

    def run_compare(self) -> None:
        a, b = self.panel_a.data, self.panel_b.data
        if a is None or b is None:
            self.result = None
            self.model.set_data(a or b"", b or b"")
            self._set_banner("empty", "Load File A (dump) and File B (reference) to compare."
                             if a is None and b is None else "Load the second file to compare — "
                             "single-file database check shown below.")
            self.diag.setHtml(self._single_file_html(a if a is not None else b, "A" if a is not None else "B")
                              if (a is not None or b is not None) else
                              f"<p style='color:{COLORS['muted']}'>Drop a ripped dump into File A and a known-good "
                              f"image into File B. The engine detects doubled/padded images, stuck data bits, "
                              f"shorted data lines, stuck address lines and small patches.</p>")
            self._update_row_info()
            return
        r = compare_images(a, b)
        self.result = r
        kind = r.verdict
        if kind == "different" and (r.diff_count < 16 or r.diff_percent < 1):
            kind = "different_small"
        self._set_banner(kind, r.banner)
        self.diag.setHtml(self._result_html(r))
        self.model.set_data(r.view_a, r.view_b)
        self._update_row_info()

    def _single_file_html(self, data: bytes, name: str) -> str:
        sha = hashlib.sha1(data).hexdigest()
        matches = self.db.lookup_sha1(sha)
        parts = [f"<h3>Database hash check — File {name}</h3>"]
        if matches:
            for m in matches:
                parts.append(finding_html("ok", f"SHA-1 matches: {m.label}" + (" [sha.txt]" if m.source == "sha.txt" else "")))
            parts.append(finding_html("info", "The file is a known-good image. Load a reference anyway to see a byte "
                                              "diff, or use it as File B against another dump."))
        else:
            parts.append(finding_html("warn", "SHA-1 is not in the database or imported sha.txt files. Only hashes are "
                                              "stored, so a byte-level comparison needs a reference file in File B."))
        for f in check_health(data).findings:
            parts.append(finding_html(f.level, f.text))
        return "".join(parts)

    def _result_html(self, r: CompareResult) -> str:
        parts = []
        for dgn in r.diagnoses:
            color = {"ok": COLORS["ok"], "info": COLORS["info"], "warn": COLORS["warn"],
                     "error": COLORS["error"]}[dgn.level]
            parts.append(f"<div style='margin:6px 0; padding:6px; background:{COLORS['panel2']};"
                         f" border-left:4px solid {color}'><b style='color:{color}'>{html.escape(dgn.title)}</b><br>"
                         f"{html.escape(dgn.text)}</div>")
        if r.verdict == "different" and r.diff_count:
            rows = "".join(f"<tr><td>D{k}</td><td align='right'>{r.bit_mismatch[k]:,}</td>"
                           f"<td align='right'>{100 * r.bit_mismatch[k] / r.compared_size:.2f}%</td></tr>"
                           for k in range(7, -1, -1))
            parts.append(f"<h4>Mismatches per data bit</h4><table cellspacing='4'>"
                         f"<tr><th>Bit</th><th>Bytes</th><th>%</th></tr>{rows}</table>"
                         f"<p>Bits 0→1 in dump: {r.flips_0_to_1:,} &nbsp;•&nbsp; "
                         f"Bits 1→0 in dump: {r.flips_1_to_0:,}</p>")
        return "".join(parts)

    # ------------------------------------------------------------------ hex view helpers
    def _toggle_filter(self, on: bool) -> None:
        self.model.set_only_diffs(on)
        self._update_row_info()

    def _update_row_info(self) -> None:
        total = (self.model.length + BYTES_PER_ROW - 1) // BYTES_PER_ROW
        self.row_info.setText(f"{len(self.model.diff_rows):,} of {total:,} rows differ")

    def _jump(self, direction: int) -> None:
        rows = self.model.diff_rows
        if not rows:
            return
        cur = self.view.currentIndex()
        line = self.model.row_to_line(cur.row()) if cur.isValid() else (-1 if direction > 0 else 1 << 30)
        if direction > 0:
            i = bisect.bisect_right(rows, line)
            target = rows[i] if i < len(rows) else rows[0]
        else:
            i = bisect.bisect_left(rows, line) - 1
            target = rows[i] if i >= 0 else rows[-1]
        row = self.model.line_to_row(target)
        if row is None:
            return
        # select the first differing byte in that line
        base = target * BYTES_PER_ROW
        col = COL_A0
        for k in range(BYTES_PER_ROW):
            if self.model.byte_at("a", base + k) != self.model.byte_at("b", base + k):
                col = COL_A0 + k
                break
        idx = self.model.index(row, col)
        self.view.setCurrentIndex(idx)
        self.view.scrollTo(idx, QAbstractItemView.ScrollHint.PositionAtCenter)

    def _on_cell(self, index: QModelIndex, _prev: QModelIndex) -> None:
        cell = self.model.cell_offset(index) if index.isValid() else None
        if cell is None:
            return
        _side, off = cell
        a, b = self.model.byte_at("a", off), self.model.byte_at("b", off)
        fa = "--" if a is None else f"0x{a:02X}"
        fb = "--" if b is None else f"0x{b:02X}"
        diff = ""
        if a is not None and b is not None and a != b:
            diff = " &nbsp; Differing bits: <b style='color:%s'>%s</b>" % (
                COLORS["warn"], ", ".join(f"D{k}" for k in range(7, -1, -1) if (a ^ b) >> k & 1))
        elif a == b and a is not None:
            diff = f" &nbsp; <span style='color:{COLORS['ok']}'>match</span>"
        self.bit_detail.setText(
            f"<span style=\"font-family:{MONO_CSS}\">Offset <b>0x{off:X}</b> &nbsp; "
            f"A {fa} [{_bits_html(a, b, COLORS['diff_a_text'])}] &nbsp; "
            f"B {fb} [{_bits_html(b, a, COLORS['diff_b_text'])}]</span>{diff}")
