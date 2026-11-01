"""Generate the application icon (assets/icon.png, icon.ico, icon.icns) with QPainter.

Run:  python scripts/generate_icons.py [output_dir]

The artwork is drawn as vectors at every target size (no down-scaling blur): a dark ceramic DIP ROM
with gold pins, a pin-1 dot and a teal UV-EPROM quartz window. ICO and ICNS containers are written
directly (PNG-compressed entries), so no Qt image-format plugin or Pillow is required.
"""

from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt  # noqa: E402
from PyQt6.QtGui import (QBrush, QColor, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath,  # noqa: E402
                         QPen, QRadialGradient)

ROOT = Path(__file__).resolve().parent.parent
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
# ICNS PNG entry types: (type, pixel size)
ICNS_TYPES = (("icp4", 16), ("icp5", 32), ("icp6", 64), ("ic07", 128), ("ic08", 256), ("ic09", 512),
              ("ic10", 1024), ("ic11", 32), ("ic12", 64), ("ic13", 256), ("ic14", 512))
PNG_SIZE = 512

GOLD_LIGHT, GOLD, GOLD_DARK = QColor("#ffe08a"), QColor("#d4a017"), QColor("#8a6508")
CERAMIC_TOP, CERAMIC_BOTTOM = QColor("#3a3f47"), QColor("#16191d")
TEAL, TEAL_DARK = QColor("#3fe0d0"), QColor("#0b6f6a")
AMBER = QColor("#ffb000")


def render_icon(size: int) -> QImage:
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    s = float(size)

    body = QRectF(s * 0.25, s * 0.06, s * 0.50, s * 0.88)
    pins_per_side = 4 if size <= 24 else 7
    pin_len = s * 0.13
    pitch = body.height() / pins_per_side
    pin_h = pitch * (0.62 if size > 24 else 0.7)

    # gold legs
    for side in (-1, 1):
        x = body.left() - pin_len + s * 0.015 if side < 0 else body.right() - s * 0.015
        for i in range(pins_per_side):
            y = body.top() + pitch * (i + 0.5) - pin_h / 2
            r = QRectF(x, y, pin_len, pin_h)
            g = QLinearGradient(r.topLeft(), r.bottomLeft())
            g.setColorAt(0.0, GOLD_LIGHT)
            g.setColorAt(0.5, GOLD)
            g.setColorAt(1.0, GOLD_DARK)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(g))
            p.drawRoundedRect(r, pin_h * 0.25, pin_h * 0.25)

    # ceramic body
    radius = s * 0.05
    g = QLinearGradient(body.topLeft(), body.bottomRight())
    g.setColorAt(0.0, CERAMIC_TOP)
    g.setColorAt(1.0, CERAMIC_BOTTOM)
    p.setBrush(QBrush(g))
    p.setPen(QPen(QColor("#5b636e"), max(1.0, s * 0.008)))
    p.drawRoundedRect(body, radius, radius)

    # notch (top centre)
    notch_r = body.width() * 0.13
    notch = QPainterPath()
    notch.addEllipse(QPointF(body.center().x(), body.top()), notch_r, notch_r)
    clip = QPainterPath()
    clip.addRect(body)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#0c0e11"))
    p.drawPath(notch.intersected(clip))

    # pin-1 dot (teal accent)
    if size >= 24:
        dot = body.width() * 0.07
        p.setBrush(TEAL)
        p.drawEllipse(QPointF(body.left() + body.width() * 0.17, body.top() + body.width() * 0.2), dot, dot)

    # UV EPROM quartz window
    c = QPointF(body.center().x(), body.top() + body.height() * 0.47)
    outer = body.width() * 0.34
    p.setBrush(QColor("#9aa4b1") if size >= 32 else QColor("#6c7480"))
    p.drawEllipse(c, outer, outer)
    inner = outer * 0.80
    rg = QRadialGradient(c - QPointF(inner * 0.3, inner * 0.3), inner * 1.4)
    rg.setColorAt(0.0, TEAL)
    rg.setColorAt(1.0, TEAL_DARK)
    p.setBrush(QBrush(rg))
    p.drawEllipse(c, inner, inner)
    if size >= 32:
        # silicon die visible through the window
        die = inner * 0.85
        p.setBrush(QColor(20, 30, 32, 210))
        p.drawRect(QRectF(c.x() - die / 2, c.y() - die / 2, die, die))
        p.setPen(QPen(GOLD, max(1.0, s * 0.006)))
        for k in range(4):
            off = -die / 2 + die * (k + 0.5) / 4
            p.drawLine(QPointF(c.x() + off, c.y() - die / 2), QPointF(c.x() + off, c.y() - die * 0.25))
            p.drawLine(QPointF(c.x() + off, c.y() + die / 2), QPointF(c.x() + off, c.y() + die * 0.25))
        p.setPen(Qt.PenStyle.NoPen)
        # glint
        p.setBrush(QColor(255, 255, 255, 70))
        p.drawEllipse(c - QPointF(inner * 0.38, inner * 0.38), inner * 0.22, inner * 0.14)

    # amber label stripe
    if size >= 32:
        stripe = QRectF(body.left() + body.width() * 0.18, body.top() + body.height() * 0.80,
                        body.width() * 0.64, body.height() * 0.045)
        p.setBrush(AMBER)
        p.drawRoundedRect(stripe, stripe.height() / 2, stripe.height() / 2)
        stripe2 = QRectF(stripe.left(), stripe.bottom() + stripe.height() * 0.9, stripe.width() * 0.6,
                         stripe.height())
        p.setBrush(QColor("#9aa4b1"))
        p.drawRoundedRect(stripe2, stripe2.height() / 2, stripe2.height() / 2)
    p.end()
    return img


def png_bytes(img: QImage) -> bytes:
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    if not img.save(buf, "PNG"):
        raise RuntimeError("PNG encoding failed")
    buf.close()
    return bytes(ba)


def build_ico(pngs: dict[int, bytes]) -> bytes:
    """ICO container with PNG-compressed images (supported since Windows Vista)."""
    sizes = sorted(pngs)
    header = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries, blobs = b"", b""
    for sz in sizes:
        data = pngs[sz]
        dim = 0 if sz >= 256 else sz
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        blobs += data
        offset += len(data)
    return header + entries + blobs


def build_icns(pngs: dict[int, bytes]) -> bytes:
    body = b""
    for kind, sz in ICNS_TYPES:
        data = pngs[sz]
        body += kind.encode("ascii") + struct.pack(">I", len(data) + 8) + data
    return b"icns" + struct.pack(">I", len(body) + 8) + body


def generate(out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    sizes = set(ICO_SIZES) | {sz for _k, sz in ICNS_TYPES} | {PNG_SIZE}
    pngs = {sz: png_bytes(render_icon(sz)) for sz in sorted(sizes)}
    written = []
    for name, data in (("icon.png", pngs[PNG_SIZE]),
                       ("icon.ico", build_ico({sz: pngs[sz] for sz in ICO_SIZES})),
                       ("icon.icns", build_icns(pngs))):
        path = out_dir / name
        path.write_bytes(data)
        written.append(path)
    return written


def main() -> int:
    _app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "assets"
    for path in generate(out):
        print(f"wrote {path} ({path.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
