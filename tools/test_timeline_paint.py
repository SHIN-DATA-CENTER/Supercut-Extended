"""Render the editing timeline and read the pixels back.

Every timeline bug reported so far passed the logic tests: positions were computed
correctly and then drawn wrong, or drawn in the wrong order. Checking numbers cannot
catch "the clips painted over the track names", so this renders the widget for real
and looks at what came out.

    python tools/test_timeline_paint.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QScrollArea

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from supercut_extended.gui.clip_timeline import (        # noqa: E402
    BGM_TOP, CLIP_TOP, HEADER_W, ClipTimelineWidget,
)
from supercut_extended.model import Bgm, Clip, Timeline  # noqa: E402

failures: list[str] = []


def expect(cond: bool, label: str, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}: {label}" + (f"  [{detail}]" if detail else ""))
    if not cond:
        failures.append(label)


def shoot(track: ClipTimelineWidget) -> QImage:
    track.repaint()
    return track.grab().toImage()


def colour(img: QImage, x: float, y: float) -> tuple[int, int, int]:
    """Sample at a WIDGET coordinate, not an image one.

    grab() returns a device-pixel image, so on a 150% display it is 1.5x the widget's
    logical size. Reading logical coordinates straight out of it lands somewhere else
    entirely -- which is exactly how this test started reporting the header and a clip
    as the same colour.
    """
    ratio = img.devicePixelRatio() or 1.0
    px = min(img.width() - 1, max(0, int(x * ratio)))
    py = min(img.height() - 1, max(0, int(y * ratio)))
    c = QImage.pixelColor(img, px, py)
    return c.red(), c.green(), c.blue()


def main() -> int:
    app = QApplication(sys.argv)
    src = Path(tempfile.gettempdir()) / "paint_dummy.mp4"
    timeline = Timeline(clips=[Clip(src, i * 20_000, i * 20_000 + 9_000, f"C{i}")
                               for i in range(20)])
    timeline.bgm = Bgm(path=src, volume=0.6)
    limits = {src: 600_000.0}

    area = QScrollArea()
    track = ClipTimelineWidget()
    area.setWidget(track)
    area.setWidgetResizable(True)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
    area.resize(900, 260)
    area.show()
    track.set_timeline(timeline, limits)
    track.set_zoom(6.0)          # force the track wider than the viewport
    app.processEvents()

    bar = area.horizontalScrollBar()
    expect(bar.maximum() > 200, "the track is wider than the viewport, so it scrolls",
           f"max={bar.maximum()}")

    print("-- the track names survive being scrolled past --")
    for offset in (0, bar.maximum() // 2, bar.maximum()):
        bar.setValue(offset)
        app.processEvents()
        img = shoot(track)
        x = track._scroll_x()
        # Inside the pinned column, on the V1 row. With the header painted too early
        # a clip covers this and it comes back as clip blue.
        head = colour(img, x + 6, CLIP_TOP + 30)
        # A clip well to the right of the column, for comparison.
        body = colour(img, x + HEADER_W + 60, CLIP_TOP + 30)
        expect(head != body,
               f"header is not painted over by clips at offset {offset}",
               f"header rgb{head} vs clip rgb{body}")
        # The header background is the panel colour (#171c28), never a clip fill.
        expect(abs(head[2] - 0x28) < 26 and head[2] >= head[0],
               f"the pinned column keeps its own background at offset {offset}",
               f"rgb{head}")

    print("-- the ruler stops at the pinned column --")
    # Timestamps belong over the clips. They used to be drawn across the full width,
    # so once the track scrolled they sat on top of the V1/BGM track names.
    for offset in (0, bar.maximum() // 2, bar.maximum()):
        bar.setValue(offset)
        app.processEvents()
        img = shoot(track)
        x = track._scroll_x()
        # Ruler text is TEXT_DIM (#8b98ad) on SURFACE_2 (#171c28): any pixel that
        # bright inside the corner cell can only be a timestamp or a tick.
        bright = 0
        for px in range(int(x) + 2, int(x + HEADER_W) - 2, 2):
            for py in range(2, 22):
                r, g, b = colour(img, px, py)
                if r > 90 and g > 90 and b > 90:
                    bright += 1
        expect(bright == 0,
               f"nothing is drawn above the track names at offset {offset}",
               f"{bright} bright pixels in the corner")
        # ...and the ruler is still there where the clips are.
        lit = 0
        for px in range(int(x + HEADER_W) + 4, int(x + HEADER_W) + 400, 2):
            for py in range(2, 22):
                r, g, b = colour(img, px, py)
                if r > 90 and g > 90 and b > 90:
                    lit += 1
        expect(lit > 0, f"the ruler still labels the clip area at offset {offset}",
               f"{lit} bright pixels")

    print("-- the level bars are drawn inside the pinned column --")
    bar.setValue(bar.maximum())
    app.processEvents()
    img = shoot(track)
    x = track._scroll_x()
    for track_name, row_top in (("v1", CLIP_TOP), ("bgm", BGM_TOP)):
        rect = track._vol_rect(track_name)
        expect(x <= rect.left() and rect.right() <= x + HEADER_W,
               f"{track_name} level bar sits within the column",
               f"{rect.left():.0f}..{rect.right():.0f} in {x:.0f}..{x + HEADER_W:.0f}")
        # Filled portion should differ from the empty portion.
        filled = colour(img, rect.left() + 2, rect.center().y())
        empty = colour(img, rect.right() - 2, rect.center().y())
        expect(filled != empty, f"{track_name} bar shows its level",
               f"filled rgb{filled} vs empty rgb{empty}")

    print("-- the drop marker lands on a real clip boundary --")
    track._index = 0
    track._mode = "move"
    track._insert_at = 3
    slots = track._slots()
    img = shoot(track)
    want = slots[3][1]
    expect(abs(want - track._x_for_seconds(timeline.start_of(3))) < 0.51,
           "the marker uses the same layout as the clips",
           f"{want:.1f} vs {track._x_for_seconds(timeline.start_of(3)):.1f}")
    track._mode = ""
    track._insert_at = -1

    print("\n" + ("timeline painting OK" if not failures
                  else f"{len(failures)} CHECK(S) FAILED: {failures}"))
    app.quit()
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
