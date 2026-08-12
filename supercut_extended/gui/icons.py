"""Coolicons SVG loading, recolouring and caching.

The icon set draws with ``stroke="currentColor"``, which is a CSS/HTML concept that
QSvgRenderer does not implement -- rendered as-is every icon comes out black and
invisible on a dark background. So the SVG source is patched to a literal colour
before rendering, which also lets one icon serve several palettes (dim in a toolbar,
bright when hovered, accent when active).

Qt stylesheets cannot take a QIcon, only a file URL, so ``css_icon`` renders to PNG in
a cache directory and hands back a path. That is what dresses the checkboxes, radio
buttons, combo arrows and spin buttons.
"""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

ICON_DIR_NAME = "cooliocns SVG"

# Rendering above the display size keeps edges crisp on HiDPI screens and when Qt
# scales a stylesheet image down into a sub-control.
SUPERSAMPLE = 3


def _bundle_candidates(relative: str) -> list[Path]:
    """Every place a bundled data file may live, most specific first.

    Covers the PyInstaller one-file extraction dir, the folder next to a frozen exe,
    the source checkout, and finally the working directory.
    """
    candidates = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / relative)
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).parent / relative)
    here = Path(__file__).resolve()
    candidates.append(here.parent.parent.parent / relative)
    candidates.append(Path.cwd() / relative)
    return candidates


def icon_root() -> Path:
    """Locate the icon set in both source checkouts and the PyInstaller bundle."""
    candidates = _bundle_candidates(ICON_DIR_NAME)
    for c in candidates:
        if c.is_dir():
            return c
    return candidates[-1]


@lru_cache(maxsize=1)
def _cache_dir() -> Path:
    d = Path(tempfile.gettempdir()) / "supercut_icons"
    d.mkdir(parents=True, exist_ok=True)
    return d


# Glyphs the coolicons set does not contain. An editor needs a pointer and a cutting
# tool, and there is neither a mouse cursor nor a blade in the set.
#
# Kept here as source rather than dropped into "cooliocns SVG/": that folder is a
# third-party set which is also gitignored, so anything added to it would be both
# mixed in with someone else's work and missing from the repository. Written in the
# same shape as the set (24x24, stroke="currentColor", width 2, round caps) so
# _recolour and the renderer treat them identically.
_BUILTIN_SVG: dict[str, str] = {
    # Classic arrow cursor.
    "Tool/Select":
        '<svg width="24" height="24" viewBox="0 0 24 24" fill="none"'
        ' xmlns="http://www.w3.org/2000/svg">'
        '<path d="M5.5 3.2 L5.5 17.8 L9.4 14.1 L12 20 L14.9 18.7 L12.3 12.9'
        ' L17.9 12.4 Z" stroke="currentColor" stroke-width="2"'
        ' stroke-linecap="round" stroke-linejoin="round"/></svg>',
    # Scissors: two handles and a pair of crossing blades.
    "Tool/Cut":
        '<svg width="24" height="24" viewBox="0 0 24 24" fill="none"'
        ' xmlns="http://www.w3.org/2000/svg">'
        '<circle cx="6.5" cy="18" r="2.6" stroke="currentColor" stroke-width="2"/>'
        '<circle cx="17.5" cy="18" r="2.6" stroke="currentColor" stroke-width="2"/>'
        '<path d="M8.5 16.2 L19 3.5" stroke="currentColor" stroke-width="2"'
        ' stroke-linecap="round"/>'
        '<path d="M15.5 16.2 L5 3.5" stroke="currentColor" stroke-width="2"'
        ' stroke-linecap="round"/></svg>',
}


@lru_cache(maxsize=512)
def _svg_source(name: str) -> str | None:
    """`name` is "Category/Icon_Name", e.g. "Media/Play"."""
    builtin = _BUILTIN_SVG.get(name)
    if builtin is not None:
        return builtin
    path = icon_root() / f"{name}.svg"
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _recolour(svg: str, color: str) -> str:
    return svg.replace("currentColor", color)


def pixmap(name: str, color: str = "#e6edf6", size: int = 20) -> QPixmap:
    """Render one icon. Returns a transparent pixmap if the icon is missing."""
    px = QPixmap(size, size)
    px.fill(Qt.transparent)

    svg = _svg_source(name)
    if svg is None:
        return px

    renderer = QSvgRenderer(QByteArray(_recolour(svg, color).encode("utf-8")))
    if not renderer.isValid():
        return px

    big = QPixmap(size * SUPERSAMPLE, size * SUPERSAMPLE)
    big.fill(Qt.transparent)
    painter = QPainter(big)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setRenderHint(QPainter.SmoothPixmapTransform)
    renderer.render(painter, QRectF(0, 0, big.width(), big.height()))
    painter.end()

    return big.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)


@lru_cache(maxsize=256)
def icon(name: str, color: str = "#e6edf6", size: int = 20) -> QIcon:
    return QIcon(pixmap(name, color, size))


@lru_cache(maxsize=256)
def css_icon(name: str, color: str = "#e6edf6", size: int = 20) -> str:
    """Render to a PNG on disk and return a path usable in a Qt stylesheet url().

    Paths are returned with forward slashes: Qt stylesheets treat a backslash as an
    escape character, so a raw Windows path silently fails to load.
    """
    digest = hashlib.sha1(f"{name}|{color}|{size}".encode()).hexdigest()[:16]
    out = _cache_dir() / f"{digest}.png"
    if not out.exists():
        px = pixmap(name, color, size)
        if px.isNull() or not px.save(str(out), "PNG"):
            return ""
    return out.as_posix()


def available() -> bool:
    """True when the icon set was found -- callers fall back to text if not."""
    return icon_root().is_dir()


APP_ICON_NAME = "app.ico"
APP_GLYPH = "Edit/Layers"
APP_GLYPH_COLOR = "#60a5fa"


@lru_cache(maxsize=1)
def app_icon() -> QIcon:
    """The window/taskbar icon, matching the icon compiled into the executable.

    Windows shows the .ico from the exe in Explorer but the *window* icon on the
    taskbar button, so loading the same file for both stops the icon changing the
    moment the app launches. A source checkout that has not run tools/make_icon.py
    yet has no .ico, so fall back to the bare glyph rather than showing nothing.
    """
    for candidate in _bundle_candidates(f"assets/{APP_ICON_NAME}"):
        if candidate.is_file():
            ico = QIcon(str(candidate))
            if not ico.isNull():
                return ico
    return icon(APP_GLYPH, APP_GLYPH_COLOR, 64)
