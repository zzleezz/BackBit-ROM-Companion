"""Tab 4 - BackBit sha.txt Generator & Exporter."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QFileDialog, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
                             QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QSplitter, QTableWidget,
                             QTableWidgetItem, QVBoxLayout, QWidget)

from core.database import RomDatabase
from core.shatxt import (DEFAULT_LABEL_LIMIT, DEFAULT_UPPERCASE, SOURCE_BUILTIN, SOURCE_USER, ShaEntry,
                         builtin_entries,
                         entries_from_sha_txt, entry_from_file, format_sha_txt, imported_entries, merge_entries,
                         sanitize_label, write_sha_txt)
from ui import theme
from ui.widgets import COLORS, MONO_CSS, DropZone, mono_font, set_role

COL_INC, COL_SHA, COL_LABEL, COL_LEN, COL_SRC, COL_NOTE = range(6)

FORMAT_HELP = (
    "<b>sha.txt format</b> (BackBit manual - identical to Linux <i>sha1sum</i> output): one ROM per line, "
    "<span style=\"font-family:%s; color:%s\">&lt;40 lower-case hex SHA-1&gt;&lt;two spaces&gt;&lt;label&gt;"
    "</span>, e.g. <span style=\"font-family:%s\">b376885ac8452b6cbf9ced81b1080bfd570d9b91&nbsp;&nbsp;ZEROES</span>. "
    "When a chip you read hashes to a listed SHA-1, the tester shows the label on its <b>16x2 character LCD</b>, "
    "so keep labels to <b>16 characters</b> of plain ASCII. Copy the saved file to the root of the BackBit's SD card."
)


def format_help() -> str:
    """Help text with the active theme's accent colour."""
    return FORMAT_HELP % (MONO_CSS, COLORS["accent"], MONO_CSS)


class ShaTxtTab(QWidget):
    def __init__(self, db: RomDatabase, parent=None):
        super().__init__(parent)
        self.db = db
        self.entries: list[ShaEntry] = []
        self._last_dir = ""
        self.setAcceptDrops(True)
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(150)
        self._preview_timer.timeout.connect(self._update_preview)

        self.help_label = help_label = QLabel(format_help())
        help_label.setWordWrap(True)
        help_label.setTextFormat(Qt.TextFormat.RichText)
        set_role(help_label, "panel-box")

        # ---- populate buttons
        self.builtin_btn = QPushButton("Include Built-in Database ROMs")
        self.builtin_btn.setCheckable(True)
        self.builtin_btn.toggled.connect(self._toggle_builtin)
        imported_btn = QPushButton("Add Imported sha.txt Entries")
        imported_btn.setToolTip("Hashes previously imported via File → Import BackBit sha.txt")
        imported_btn.clicked.connect(self._add_imported)
        files_btn = QPushButton("Drop / Add ROM Files…")
        files_btn.clicked.connect(self._browse_files)
        merge_btn = QPushButton("Merge Existing sha.txt…")
        merge_btn.clicked.connect(self._merge_existing)
        pop = QHBoxLayout()
        for b in (self.builtin_btn, imported_btn, files_btn, merge_btn):
            pop.addWidget(b)

        self.drop = DropZone("⬇  Drop .bin / .rom files here (Dead Test, JiffyDOS, diagnostics, …) — their "
                             "SHA-1 is computed and you can type a label")
        self.drop.setMinimumHeight(52)
        self.drop.fileDropped.connect(lambda p: self.add_files([p]))
        self.drop.browseRequested.connect(self._browse_files)

        # ---- table
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter by label, hash or source…")
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(self._apply_filter)
        sel_all = QPushButton("Include all")
        sel_none = QPushButton("Exclude all")
        remove = QPushButton("Remove selected")
        clear = QPushButton("Clear list")
        sel_all.clicked.connect(lambda: self._set_all_included(True))
        sel_none.clicked.connect(lambda: self._set_all_included(False))
        remove.clicked.connect(self._remove_selected)
        clear.clicked.connect(self._clear)
        trow = QHBoxLayout()
        trow.addWidget(self.filter, 1)
        for b in (sel_all, sel_none, remove, clear):
            trow.addWidget(b)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Include", "SHA-1 hash", "BackBit LCD label (editable)", "Chars",
                                              "Source", "Note"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked |
                                   QAbstractItemView.EditTrigger.EditKeyPressed |
                                   QAbstractItemView.EditTrigger.AnyKeyPressed)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(COL_INC, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(COL_SHA, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(COL_LABEL, QHeaderView.ResizeMode.Interactive)
        hh.setSectionResizeMode(COL_LEN, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(COL_SRC, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(COL_NOTE, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(COL_LABEL, 230)
        self.table.itemChanged.connect(self._on_item_changed)
        self.count_label = QLabel()
        set_role(self.count_label, "muted")

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 4, 0)
        ll.addLayout(trow)
        ll.addWidget(self.table, 1)
        ll.addWidget(self.count_label)

        # ---- preview + export
        opts = QHBoxLayout()
        self.upper = QCheckBox("Upper-case hashes")
        self.crlf = QCheckBox("Windows line endings (CRLF)")
        self.limit = QSpinBox()
        self.limit.setRange(8, 64)
        self.limit.setValue(DEFAULT_LABEL_LIMIT)
        self.limit.setPrefix("LCD label limit: ")
        self.limit.setSuffix(" chars")
        self.upper.setChecked(DEFAULT_UPPERCASE)
        self.upper.setToolTip("The BackBit manual specifies lower-case hashes (sha1sum format). "
                              "Only tick this if you know your firmware accepts upper case.")
        self.upper.toggled.connect(lambda _on: self._rebuild_table())
        self.limit.valueChanged.connect(self._limit_changed)
        opts.addWidget(self.upper)
        opts.addWidget(self.crlf)
        opts.addStretch(1)
        opts.addWidget(self.limit)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setFont(mono_font(9))
        self.preview.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.preview_info = QLabel()
        self.save_btn = QPushButton("\U0001f4be  Generate && Save sha.txt As…")
        set_role(self.save_btn, "primary", size="large")
        self.save_btn.clicked.connect(self.save)
        right = QGroupBox("Live sha.txt preview")
        rl = QVBoxLayout(right)
        rl.addLayout(opts)
        rl.addWidget(self.preview, 1)
        rl.addWidget(self.preview_info)
        rl.addWidget(self.save_btn)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([860, 520])

        lay = QVBoxLayout(self)
        lay.addWidget(help_label)
        lay.addLayout(pop)
        lay.addWidget(self.drop)
        lay.addWidget(splitter, 1)
        self._rebuild_table()
        theme.manager.changed.connect(self.refresh_theme)

    def refresh_theme(self, _name: str = "") -> None:
        """Re-colour the character counters / help text for the new theme (the list itself is kept)."""
        self.help_label.setText(format_help())
        self._rebuild_table()

    # ------------------------------------------------------------------ drag & drop
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.add_files(paths)

    # ------------------------------------------------------------------ populate
    def _toggle_builtin(self, on: bool) -> None:
        if on:
            added, dup = merge_entries(self.entries, builtin_entries(self.db, self.limit.value()))
            self._rebuild_table()
            self._status(f"Added {added} built-in database hashes ({dup} already listed).")
        else:
            before = len(self.entries)
            self.entries = [e for e in self.entries if e.source not in (SOURCE_BUILTIN, SOURCE_USER)]
            self._rebuild_table()
            self._status(f"Removed {before - len(self.entries)} built-in database entries.")

    def refresh_builtin(self) -> None:
        """Called after the database changes: refresh the built-in set if it is included."""
        if self.builtin_btn.isChecked():
            labels = {e.sha1: e for e in self.entries if e.source in (SOURCE_BUILTIN, SOURCE_USER)}
            self.entries = [e for e in self.entries if e.source not in (SOURCE_BUILTIN, SOURCE_USER)]
            fresh = builtin_entries(self.db, self.limit.value())
            for e in fresh:  # keep user edits to labels / include flags
                old = labels.get(e.sha1)
                if old:
                    e.label, e.include = old.label, old.include
            merge_entries(self.entries, fresh)
            self._rebuild_table()

    def _add_imported(self) -> None:
        new = imported_entries(self.db)
        if not new:
            QMessageBox.information(self, "No imported entries",
                                    "No sha.txt has been imported yet (File → Import BackBit sha.txt), or use "
                                    "'Merge Existing sha.txt' to load one straight into this list.")
            return
        added, dup = merge_entries(self.entries, new)
        self._rebuild_table()
        self._status(f"Added {added} imported hashes ({dup} duplicates skipped).")

    def _browse_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Add ROM files", self._last_dir,
                                                "ROM images (*.bin *.rom *.BIN *.ROM *.img);;All files (*)")
        if paths:
            self.add_files(paths)

    def add_files(self, paths: list[str]) -> None:
        new, errors = [], []
        for p in paths:
            try:
                if Path(p).is_file():
                    new.append(entry_from_file(p, self.limit.value()))
                    self._last_dir = str(Path(p).parent)
            except OSError as exc:
                errors.append(f"{p}: {exc}")
        first_new = len(self.entries)
        added, dup = merge_entries(self.entries, new)
        self._rebuild_table()
        msg = f"Added {added} file(s)" + (f", {dup} already listed (same SHA-1)" if dup else "") + "."
        self._status(msg)
        if errors:
            QMessageBox.warning(self, "Some files could not be read", "\n".join(errors))
        if added:
            row = first_new
            self.table.setCurrentCell(row, COL_LABEL)
            self.table.scrollToItem(self.table.item(row, COL_LABEL))
            self.table.editItem(self.table.item(row, COL_LABEL))

    def _merge_existing(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Merge existing sha.txt", self._last_dir,
                                              "sha.txt (*.txt);;All files (*)")
        if not path:
            return
        try:
            new, invalid = entries_from_sha_txt(RomDatabase.read_text_file(path))
        except OSError as exc:
            QMessageBox.critical(self, "Cannot read file", str(exc))
            return
        for e in new:
            e.note = Path(path).name
        added, dup = merge_entries(self.entries, new)
        self._last_dir = str(Path(path).parent)
        self._rebuild_table()
        QMessageBox.information(self, "sha.txt merged",
                                f"{Path(path).name}\n\nEntries read: {len(new):,}\nAdded: {added:,}\n"
                                f"Duplicates skipped (same SHA-1): {dup:,}\nUnparseable lines: {invalid:,}")

    # ------------------------------------------------------------------ table
    def _rebuild_table(self) -> None:
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.entries))
        mono = mono_font(9)
        for row, e in enumerate(self.entries):
            inc = QTableWidgetItem()
            inc.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            inc.setCheckState(Qt.CheckState.Checked if e.include else Qt.CheckState.Unchecked)
            sha = QTableWidgetItem(e.sha1.upper() if self.upper.isChecked() else e.sha1.lower())
            sha.setFont(mono)
            sha.setFlags(sha.flags() & ~Qt.ItemFlag.ItemIsEditable)
            label = QTableWidgetItem(e.label)
            length = QTableWidgetItem()
            length.setFlags(length.flags() & ~Qt.ItemFlag.ItemIsEditable)
            length.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            src = QTableWidgetItem(e.source)
            src.setFlags(src.flags() & ~Qt.ItemFlag.ItemIsEditable)
            note = QTableWidgetItem(e.note)
            note.setFlags(note.flags() & ~Qt.ItemFlag.ItemIsEditable)
            note.setToolTip(e.note)
            for col, item in ((COL_INC, inc), (COL_SHA, sha), (COL_LABEL, label), (COL_LEN, length),
                              (COL_SRC, src), (COL_NOTE, note)):
                self.table.setItem(row, col, item)
            self._style_len(row)
        self.table.blockSignals(False)
        self._apply_filter()
        self._schedule_preview()

    def _style_len(self, row: int) -> None:
        e = self.entries[row]
        n = len(sanitize_label(e.label))
        item = self.table.item(row, COL_LEN)
        label_item = self.table.item(row, COL_LABEL)
        limit = self.limit.value()
        item.setText(f"{n}/{limit}")
        if n > limit:
            item.setForeground(QColor(theme.contrast_text(COLORS["error"])))
            item.setBackground(QColor(COLORS["error"]))
            item.setToolTip(f"Label is {n} characters - it will be cut off on the BackBit's 16x2 LCD (limit {limit}).")
            label_item.setForeground(QColor(COLORS["error"]))
        elif n == 0:
            item.setBackground(QColor(COLORS["warn"]))
            item.setForeground(QColor(theme.contrast_text(COLORS["warn"])))
            item.setToolTip("Empty label - 'Unnamed ROM' will be written.")
            label_item.setForeground(QColor(COLORS["text"]))
        else:
            item.setBackground(QColor(0, 0, 0, 0))
            item.setForeground(QColor(COLORS["ok"]))
            item.setToolTip("")
            label_item.setForeground(QColor(COLORS["text"]))

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        row = item.row()
        if not 0 <= row < len(self.entries):
            return
        e = self.entries[row]
        if item.column() == COL_INC:
            e.include = item.checkState() == Qt.CheckState.Checked
        elif item.column() == COL_LABEL:
            clean = sanitize_label(item.text())
            e.label = clean
            if clean != item.text():
                self.table.blockSignals(True)
                item.setText(clean)
                self.table.blockSignals(False)
            self.table.blockSignals(True)
            self._style_len(row)
            self.table.blockSignals(False)
        self._schedule_preview()

    def _apply_filter(self) -> None:
        text = self.filter.text().lower().strip()
        for row, e in enumerate(self.entries):
            hide = bool(text) and text not in f"{e.label} {e.sha1} {e.source} {e.note}".lower()
            self.table.setRowHidden(row, hide)

    def _set_all_included(self, on: bool) -> None:
        for row, e in enumerate(self.entries):
            if not self.table.isRowHidden(row):
                e.include = on
        self._rebuild_table()

    def _remove_selected(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        if not rows:
            return
        for r in rows:
            del self.entries[r]
        self._rebuild_table()
        self._status(f"Removed {len(rows)} row(s).")

    def _clear(self) -> None:
        if self.entries and QMessageBox.question(self, "Clear list", "Remove all entries from the staging list?") \
                != QMessageBox.StandardButton.Yes:
            return
        self.entries.clear()
        self.builtin_btn.blockSignals(True)
        self.builtin_btn.setChecked(False)
        self.builtin_btn.blockSignals(False)
        self._rebuild_table()

    def _limit_changed(self) -> None:
        self.table.blockSignals(True)
        for row in range(len(self.entries)):
            self._style_len(row)
        self.table.blockSignals(False)
        self._schedule_preview()

    # ------------------------------------------------------------------ preview / save
    def _schedule_preview(self, *_args) -> None:
        self._preview_timer.start()

    def _update_preview(self) -> None:
        text = format_sha_txt(self.entries, self.upper.isChecked())
        self.preview.setPlainText(text)
        included = sum(1 for e in self.entries if e.include)
        long = sum(1 for e in self.entries if e.include and len(sanitize_label(e.label)) > self.limit.value())
        info = f"{included:,} of {len(self.entries):,} entries will be written"
        if long:
            info += f" • <span style='color:{COLORS['error']}'>{long} label(s) longer than {self.limit.value()} chars</span>"
        self.preview_info.setText(info)
        self.count_label.setText(f"{len(self.entries):,} entries in staging list")
        self.save_btn.setEnabled(included > 0)

    def _status(self, msg: str) -> None:
        self.count_label.setText(f"{len(self.entries):,} entries in staging list • {msg}")

    def save(self) -> None:
        included = [e for e in self.entries if e.include]
        if not included:
            return
        long = [e for e in included if len(sanitize_label(e.label)) > self.limit.value()]
        if long and QMessageBox.question(
                self, "Long labels",
                f"{len(long)} label(s) are longer than {self.limit.value()} characters and may be cut off on the "
                f"BackBit's 16x2 LCD, e.g.\n\n" + "\n".join(e.label for e in long[:5]) + "\n\nSave anyway?") \
                != QMessageBox.StandardButton.Yes:
            return
        start = str(Path(self._last_dir) / "sha.txt") if self._last_dir else "sha.txt"
        path, _ = QFileDialog.getSaveFileName(self, "Save sha.txt As", start, "sha.txt (*.txt);;All files (*)")
        if not path:
            return
        try:
            n = write_sha_txt(path, self.entries, self.upper.isChecked(), self.crlf.isChecked())
        except OSError as exc:
            QMessageBox.critical(self, "Save failed", str(exc))
            return
        self._last_dir = str(Path(path).parent)
        QMessageBox.information(self, "sha.txt saved", f"Wrote {n:,} entries to\n{path}")
