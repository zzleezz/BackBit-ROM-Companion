"""Reusable custom widgets: ZIF socket visualiser, data-bit health bars, copy fields, drop zone, badges."""

from __future__ import annotations

import html

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QFont, QFontDatabase, QFontMetricsF, QGuiApplication, QPainter, QPen
from PyQt6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSizePolicy, QVBoxLayout,
                             QWidget)

from core import zif

# Palette tokens live in ui.theme; these dicts are updated in place when the theme changes.
from ui import theme  # noqa: E402
from ui.theme import COLORS, LEVEL_COLORS, set_role, set_state  # noqa: E402,F401

LEVEL_ICONS = {"ok": "✔", "info": "ℹ", "warn": "⚠", "error": "✖"}


# Monospace families in order of preference: Windows, macOS, Linux (Debian/Fedora, Ubuntu), generic.
MONO_FAMILIES = ["Consolas", "SF Mono", "Menlo", "DejaVu Sans Mono", "Ubuntu Mono", "Liberation Mono",
                 "Cascadia Mono", "Monaco", "Courier New", "Monospace"]
# Same list for Qt rich text (QLabel / QTextBrowser HTML) - Qt's CSS supports family fallback lists.
MONO_CSS = ", ".join(f"'{f}'" for f in MONO_FAMILIES[:-1]) + ", monospace"

_mono_family_cache: list[str] | None = None


def mono_families() -> list[str]:
    """Installed families from MONO_FAMILIES (needs a QGuiApplication); falls back to the system fixed font."""
    global _mono_family_cache
    if _mono_family_cache is None:
        installed = {f.lower(): f for f in QFontDatabase.families()}
        found = [installed[f.lower()] for f in MONO_FAMILIES if f.lower() in installed]
        if not found:
            found = [QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont).family()]
        _mono_family_cache = found
    return list(_mono_family_cache)


def mono_font(point_size: float = 10) -> QFont:
    """A fixed-pitch font that exists on this OS (hex columns must line up on Windows, macOS and Linux)."""
    families = mono_families()
    font = QFont(families[0])
    font.setFamilies(families)
    font.setStyleHint(QFont.StyleHint.Monospace, QFont.StyleStrategy.PreferDefault)
    font.setFixedPitch(True)
    font.setPointSizeF(float(point_size))
    return font


def finding_html(level: str, text: str) -> str:
    color = LEVEL_COLORS.get(level, COLORS["text"])
    icon = LEVEL_ICONS.get(level, "•")
    body = html.escape(text).replace("  ", "&nbsp;&nbsp;")
    return f'<div style="color:{color}; margin:2px 0;">{icon}&nbsp;{body}</div>'


# --------------------------------------------------------------------------- simple widgets
class BadgeLabel(QLabel):
    """High-contrast pill used for the BackBit menu setting."""

    def __init__(self, text: str = "", variant: str = "primary", point_size: int = 18, parent=None):
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.setStyleSheet(f"font-size:{point_size}pt;")   # colours come from the theme's badge role
        set_role(self, "badge", variant=variant)

    def set_variant(self, variant: str) -> None:
        """'primary' = BackBit yellow (the chip as fitted), 'alt' = blue (an alternative replacement chip)."""
        set_state(self, variant=variant)

    @property
    def variant(self) -> str:
        return str(self.property("variant"))


class CopyField(QWidget):
    """'Label: [read-only value] [Copy]' row."""

    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.name = QLabel(label)
        self.name.setMinimumWidth(60)
        set_role(self.name, "field-name")
        self.edit = QLineEdit()
        self.edit.setReadOnly(True)
        self.edit.setFont(mono_font(10))
        self.edit.setPlaceholderText("—")
        self.button = QPushButton("Copy")
        self.button.setFixedWidth(78)
        self.button.setEnabled(False)
        self.button.clicked.connect(self._copy)
        lay.addWidget(self.name)
        lay.addWidget(self.edit, 1)
        lay.addWidget(self.button)

    def set_value(self, value: str | None) -> None:
        self.edit.setText(value or "")
        self.edit.setCursorPosition(0)
        self.button.setEnabled(bool(value))

    def _copy(self) -> None:
        QGuiApplication.clipboard().setText(self.edit.text())
        self.button.setText("Copied ✔")
        QTimer.singleShot(1200, lambda: self.button.setText("Copy"))


class DropZone(QFrame):
    """Dashed drop target; click to browse."""

    fileDropped = pyqtSignal(str)
    browseRequested = pyqtSignal()

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(84)
        self.label = QLabel(text)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setWordWrap(True)
        lay = QVBoxLayout(self)
        lay.addWidget(self.label)
        self._set_hover(False)

    def _set_hover(self, hover: bool) -> None:
        set_state(self, hover="true" if hover else "false")   # styled by the theme's DropZone rules

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and any(u.isLocalFile() for u in event.mimeData().urls()):
            event.acceptProposedAction()
            self._set_hover(True)

    def dragLeaveEvent(self, event):
        self._set_hover(False)

    def dropEvent(self, event):
        self._set_hover(False)
        for url in event.mimeData().urls():
            if url.isLocalFile():
                self.fileDropped.emit(url.toLocalFile())
                event.acceptProposedAction()
                return

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.browseRequested.emit()


# --------------------------------------------------------------------------- ZIF socket
class ZifSocketWidget(QWidget):
    """Paints the BackBit Chip Tester Pro V2's 48-pin ZIF socket with a DIP chip TOP-justified in it.

    ZIF numbering: left column pins 1..24 top-to-bottom, right column 25..48 bottom-to-top (pin 1 faces 48).
    The chip's pin 1 always sits in ZIF pin 1 (notch up) and an N-pin chip fills the top N/2 rows; the
    bottom rows stay empty. The locking lever runs vertically down the LEFT side, its handle level with the
    socket's top edge; like the real (short) lever it ends level with ZIF pin 9. The pin mapping itself lives in :mod:`core.zif`.
    """

    ZIF_PINS = zif.ZIF_PINS
    ROWS = zif.ZIF_ROWS
    # Socket width in pin pitches. 7.8 (was 6.4) gives the body and the seated chip the proportions of a
    # 0.600" wide-body DIP; COLUMN_INSET is how far the pin columns sit in from the socket edges.
    SOCKET_PITCHES = 7.8
    COLUMN_INSET = 1.18
    LEVER_END_ROW = 9          # the real lever is short: its bottom end sits beside ZIF pin 9

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pins: int | None = None
        self._title = ""
        self._setting = ""
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(400, 600)
        theme.manager.changed.connect(self._on_theme)

    def sizeHint(self) -> QSize:
        return QSize(470, 760)

    def _on_theme(self, _name: str) -> None:
        self.update()

    def set_chip(self, pins: int | None, title: str = "", setting: str = "") -> None:
        if not zif.valid_chip(pins):
            pins = None
        self._pins, self._title, self._setting = pins, title, setting
        self.update()

    def describe(self) -> list[str]:
        if not self._pins:
            return ["No chip selected."]
        return zif.placement(self._pins).describe()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor(COLORS["panel"]))

        title_h, marker_h, footer_h = 26, 20, 100
        lever_frac = 0.8          # lever strip width, in pitches
        avail_h = h - title_h - marker_h - footer_h - 8
        pitch = max(8.0, min(avail_h / (self.ROWS + 1), (w - 104) / (self.SOCKET_PITCHES + lever_frac)))
        sock_w = pitch * self.SOCKET_PITCHES
        sock_h = pitch * (self.ROWS + 1)
        num_w = 28.0
        lever_w = pitch * lever_frac
        group_w = 14 + lever_w + 6 + num_w + sock_w + num_w
        lever_x = (w - group_w) / 2 + 14
        x0 = lever_x + lever_w + 6 + num_w          # socket body left edge
        y0 = title_h + marker_h + 4                 # socket body top edge

        # title
        p.setPen(QColor(COLORS["text"]))
        f = QFont(self.font())
        f.setPointSize(10)
        f.setBold(True)
        p.setFont(f)
        p.drawText(QRectF(0, 2, w, title_h), Qt.AlignmentFlag.AlignCenter,
                   "BackBit 48-pin ZIF — chip TOP-justified")

        # top marker: chip pin 1 = ZIF pin 1, notch up
        p.setPen(QPen(QColor(COLORS["ok"]), 2))
        my = y0 - 4
        p.drawLine(QPointF(x0, my), QPointF(x0 + sock_w, my))
        f2 = QFont(self.font())
        f2.setPointSize(9)
        f2.setBold(True)
        p.setFont(f2)
        p.drawText(QRectF(0, title_h, w, marker_h - 2), Qt.AlignmentFlag.AlignCenter,
                   "▼ chip pin 1 = ZIF pin 1, notch UP ▼")

        # socket body
        p.setPen(QPen(QColor(COLORS["socket_edge"]), 2))
        p.setBrush(QColor(COLORS["socket"]))
        p.drawRoundedRect(QRectF(x0, y0, sock_w, sock_h), 6, 6)

        # lever: vertical along the LEFT side, handle level with the socket's top edge
        p.setPen(QPen(QColor(COLORS["lever_edge"]), 1.5))
        p.setBrush(QColor(COLORS["lever"]))
        bar_w = lever_w * 0.42
        lever_bottom = y0 + pitch * (self.LEVER_END_ROW + 0.35)   # just past the centre of row 9
        lever_len = lever_bottom - y0
        p.drawRoundedRect(QRectF(lever_x + (lever_w - bar_w) / 2, y0, bar_w, lever_len), 3, 3)
        p.setBrush(QColor(COLORS["knob"]))
        p.drawRoundedRect(QRectF(lever_x, y0, lever_w, pitch * 1.3), pitch * 0.3, pitch * 0.3)
        # rotated caption alongside the lever, shrunk (then elided) to fit the short lever
        p.save()
        small = QFont(self.font())
        text = "LEVER (lift to open, press down to lock)"
        size = max(6.5, pitch * 0.36)
        small.setPointSizeF(size)
        while size > 6.0 and QFontMetricsF(small).horizontalAdvance(text) > lever_len:
            size -= 0.25
            small.setPointSizeF(size)
        fm = QFontMetricsF(small)
        text = fm.elidedText(text, Qt.TextElideMode.ElideRight, lever_len)
        p.setFont(small)
        p.setPen(QColor(COLORS["muted"]))
        p.translate(lever_x - 3, y0 + lever_len / 2)
        p.rotate(-90)
        p.drawText(QRectF(-lever_len / 2, -fm.height(), lever_len, fm.height()), Qt.AlignmentFlag.AlignCenter, text)
        p.restore()

        left_x = x0 + pitch * self.COLUMN_INSET
        right_x = x0 + sock_w - pitch * self.COLUMN_INSET
        self._geometry = {"pitch": pitch, "socket_w": sock_w, "chip_w": (right_x - left_x) - pitch * 0.84,
                          "lever_x": lever_x, "lever_top": y0, "lever_bottom": lever_bottom,
                          "socket_x": x0, "right_label_end": x0 + sock_w + 4 + num_w}

        def row_y(r: int) -> float:
            return y0 + pitch * r

        chip = self._pins
        half = chip // 2 if chip else 0
        place = zif.placement(chip) if chip else None

        # empty-region shading (bottom rows)
        if place and place.empty_range:
            a, b = place.empty_range
            empty_rect = QRectF(x0 + 3, row_y(half + 1) - pitch * 0.5, sock_w - 6, pitch * place.empty_rows)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(COLORS["empty_shade"]))
            p.drawRoundedRect(empty_rect, 4, 4)
            p.setPen(QColor(COLORS["empty_text"]))     # drawn on the (always dark) socket body
            ef = QFont(self.font())
            ef.setBold(True)
            ef.setPointSizeF(max(7.0, pitch * 0.42))
            p.setFont(ef)
            p.drawText(empty_rect, Qt.AlignmentFlag.AlignCenter, f"EMPTY\nZIF {a}–{b}")

        # holes + ZIF pin numbers
        hole_r = pitch * 0.24
        num_font = mono_font(max(6.0, pitch * 0.36))
        p.setFont(num_font)
        for r in range(1, self.ROWS + 1):
            y = row_y(r)
            occupied = bool(chip) and r <= half
            for x, zp, align, lab_x in (
                    (left_x, r, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, x0 - num_w - 2),
                    (right_x, self.ZIF_PINS + 1 - r, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                     x0 + sock_w + 4)):
                if occupied:
                    p.setPen(QPen(QColor(COLORS["pin_lit_edge"]), 1))
                    p.setBrush(QColor(COLORS["pin_lit"]))
                else:
                    p.setPen(QPen(QColor(COLORS["hole_edge"]), 1))
                    p.setBrush(QColor(COLORS["hole"]))
                p.drawEllipse(QPointF(x, y), hole_r, hole_r)
                key_pin = zp in (1, self.ROWS, self.ROWS + 1, self.ZIF_PINS) or \
                    bool(chip and zp in (half, self.ZIF_PINS + 1 - half))
                p.setPen(QColor(COLORS["accent"] if occupied else (COLORS["text"] if key_pin else COLORS["muted"])))
                p.drawText(QRectF(lab_x, y - pitch / 2, num_w, pitch), align, str(zp))

        # chip body in the top rows
        if chip:
            body = QRectF(left_x + pitch * 0.42, row_y(1) - pitch * 0.48,
                          (right_x - left_x) - pitch * 0.84, pitch * (half - 1) + pitch * 0.96)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(COLORS["chip_leg"]))
            for r in range(1, half + 1):
                y = row_y(r)
                p.drawRect(QRectF(left_x, y - pitch * 0.09, body.left() - left_x, pitch * 0.18))
                p.drawRect(QRectF(body.right(), y - pitch * 0.09, right_x - body.right(), pitch * 0.18))
            p.setPen(QPen(QColor(COLORS["chip_edge"]), 1.5))
            p.setBrush(QColor(COLORS["chip_body"]))
            p.drawRoundedRect(body, 3, 3)
            # notch at the top (pin-1 end, pointing up)
            p.save()
            p.setClipRect(body)
            p.setBrush(QColor(COLORS["socket"]))
            nr = pitch * 0.42
            p.drawEllipse(QPointF(body.center().x(), body.top()), nr, nr)
            p.restore()
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(COLORS["pin_lit"]))
            p.drawEllipse(QPointF(body.left() + pitch * 0.42, body.top() + pitch * 0.42), pitch * 0.14, pitch * 0.14)
            p.setFont(mono_font(max(6.0, pitch * 0.34)))
            for i in range(half):
                y = row_y(1 + i)
                p.setPen(QColor(COLORS["pin_lit"] if i == 0 else COLORS["chip_text"]))
                p.drawText(QRectF(body.left() + pitch * 0.62, y - pitch / 2, pitch * 1.2, pitch),
                           Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, str(i + 1))
                p.setPen(QColor(COLORS["chip_text"]))
                p.drawText(QRectF(body.right() - pitch * 1.82, y - pitch / 2, pitch * 1.2, pitch),
                           Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, str(chip - i))
            if half >= 4:
                p.save()
                p.translate(body.center())
                p.rotate(-90)
                lf = QFont(self.font())
                lf.setBold(True)
                lf.setPointSizeF(max(7.0, pitch * 0.45))
                p.setFont(lf)
                p.setPen(QColor(COLORS["chip_label"]))
                label = f"{self._setting}  •  {chip}-PIN" if self._setting else f"{chip}-PIN DIP"
                p.drawText(QRectF(-body.height() / 2, -pitch, body.height(), pitch * 2),
                           Qt.AlignmentFlag.AlignCenter, label)
                p.restore()
        else:
            p.setPen(QColor(COLORS["muted"]))
            p.setFont(num_font)
            p.drawText(QRectF(x0, y0, sock_w, sock_h), Qt.AlignmentFlag.AlignCenter, "Select a ROM\nto place it")

        # footer legend
        p.setPen(QColor(COLORS["text"]))
        f3 = QFont(self.font())
        f3.setPointSize(9)
        p.setFont(f3)
        yy = y0 + sock_h + 8
        for line in self.describe():
            p.drawText(QRectF(4, yy, w - 8, 17), Qt.AlignmentFlag.AlignCenter, line)
            yy += 17
        f3.setPointSize(8)
        p.setFont(f3)
        p.setPen(QColor(COLORS["muted"]))
        p.drawText(QRectF(4, yy + 2, w - 8, 16), Qt.AlignmentFlag.AlignCenter,
                   "Default placement - turning the knob clockwise selects Offset Mode.")
        p.end()


# --------------------------------------------------------------------------- data-bit bars
class BitHealthWidget(QWidget):
    """Eight vertical bars (D7..D0) showing how often each data bit is 1; stuck bits shown in red."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ratios: list[float] | None = None
        self._stuck: list[str | None] = [None] * 8
        self.setMinimumHeight(150)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        theme.manager.changed.connect(self._on_theme)

    def _on_theme(self, _name: str) -> None:
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(420, 160)

    def set_bits(self, ratios: list[float] | None, stuck: list[str | None] | None = None) -> None:
        self._ratios = ratios
        self._stuck = stuck or [None] * 8
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(COLORS["panel"]))
        w, h = self.width(), self.height()
        top, bottom = 22, h - 22
        bar_area = bottom - top
        slot = w / 8
        f = QFont(self.font())
        f.setPointSize(8)
        p.setFont(f)
        p.setPen(QColor(COLORS["muted"]))
        p.drawText(QRectF(0, 0, w, 18), Qt.AlignmentFlag.AlignCenter, "Data-bit activity (share of bytes with bit = 1)")
        for i in range(8):
            bit = 7 - i
            x = i * slot + slot * 0.2
            bw = slot * 0.6
            p.setPen(QPen(QColor(COLORS["border"]), 1))
            p.setBrush(QColor(COLORS["panel2"]))
            p.drawRect(QRectF(x, top, bw, bar_area))
            label_color = COLORS["muted"]
            if self._ratios is not None:
                ratio = self._ratios[bit]
                stuck = self._stuck[bit]
                color = QColor(COLORS["error"] if stuck else COLORS["ok"])
                bh = bar_area * ratio
                if stuck == "low":
                    bh = 3
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QBrush(color))
                p.drawRect(QRectF(x, bottom - bh, bw, bh))
                p.setPen(QColor(COLORS["text"]))
                txt = "STUCK 0" if stuck == "low" else "STUCK 1" if stuck == "high" else f"{ratio:.0%}"
                p.drawText(QRectF(x - 10, top + 2, bw + 20, 16), Qt.AlignmentFlag.AlignCenter, txt)
                label_color = COLORS["error"] if stuck else COLORS["text"]
            p.setPen(QColor(label_color))
            p.drawText(QRectF(i * slot, bottom + 2, slot, 18), Qt.AlignmentFlag.AlignCenter, f"D{bit}")
        p.end()
