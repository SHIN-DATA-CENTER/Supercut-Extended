"""Generate assets/app.ico from the coolicons glyph the app already uses.

Windows needs a real multi-resolution .ico for the executable, taskbar and Explorer;
PyInstaller will not take an SVG. This renders the same "Edit/Layers" glyph at every
size Windows asks for and packs them into one file.

A rounded plate sits behind the glyph. The in-app window icon is a bare stroke on
transparency, which works against the app's own dark chrome, but as a file icon at
16px on a white Explorer background a thin blue outline nearly disappears. The plate
keeps the icon legible wherever Windows draws it.

ICO entries are stored as PNG (supported since Vista), which avoids hand-rolling the
BMP + AND-mask encoding.

    python tools/make_icon.py
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from supercut_extended.gui import icons  # noqa: E402

GLYPH = "Edit/Layers"
GLYPH_COLOR = "#7cb2ff"
PLATE_TOP = "#1b2436"
PLATE_BOTTOM = "#10151f"
PLATE_EDGE = "#2b3purple"  # replaced below; kept obvious if ever mis-edited
PLATE_EDGE = "#33405a"

SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def render(size: int) -> QPixmap:
    px = QPixmap(size, size)
    px.fill(Qt.transparent)

    p = QPainter(px)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)

    # Rounded plate, inset slightly so the corners are not clipped by the bitmap.
    inset = max(0.5, size * 0.03)
    radius = size * 0.22
    plate = QRectF(inset, inset, size - inset * 2, size - inset * 2)
    path = QPainterPath()
    path.addRoundedRect(plate, radius, radius)

    from PySide6.QtGui import QLinearGradient
    grad = QLinearGradient(plate.topLeft(), plate.bottomLeft())
    grad.setColorAt(0.0, QColor(PLATE_TOP))
    grad.setColorAt(1.0, QColor(PLATE_BOTTOM))
    p.fillPath(path, grad)

    if size >= 24:
        from PySide6.QtGui import QPen
        p.setPen(QPen(QColor(PLATE_EDGE), max(1.0, size * 0.012)))
        p.drawPath(path)

    # Glyph centred at ~62% of the plate.
    glyph_size = int(size * 0.62)
    if glyph_size >= 4:
        glyph = icons.pixmap(GLYPH, GLYPH_COLOR, glyph_size)
        p.drawPixmap(int((size - glyph_size) / 2), int((size - glyph_size) / 2), glyph)
    p.end()
    return px


def png_bytes(px: QPixmap) -> bytes:
    # QBuffer only stores a *pointer* to the QByteArray it wraps, so the array must be
    # kept alive in a named variable -- an inline QByteArray() temporary gets garbage
    # collected while still referenced, corrupting the heap.
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QBuffer.WriteOnly)
    px.save(buf, "PNG")
    return bytes(buf.data())


def build_ico(images: list[tuple[int, bytes]], dest: Path) -> None:
    """ICONDIR + one ICONDIRENTRY per size + the PNG payloads."""
    count = len(images)
    header = struct.pack("<HHH", 0, 1, count)          # reserved, type=icon, count
    entries = b""
    payload = b""
    offset = 6 + 16 * count

    for size, data in images:
        # 0 means 256 in the ICO directory.
        dim = 0 if size >= 256 else size
        entries += struct.pack(
            "<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        payload += data
        offset += len(data)

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(header + entries + payload)


def main() -> int:
    app = QApplication(sys.argv)  # noqa: F841  (needed for QPixmap)
    if not icons.available():
        print(f"icon set not found at {icons.icon_root()}", file=sys.stderr)
        return 1

    dest = Path(__file__).resolve().parent.parent / "assets" / "app.ico"
    images = [(s, png_bytes(render(s))) for s in SIZES]
    build_ico(images, dest)

    print(f"wrote {dest}  ({dest.stat().st_size / 1024:.1f} KB)")
    print("sizes: " + ", ".join(f"{s}x{s}" for s in SIZES))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
