"""Drive the preview scrubber the way a pointer would, and check what it does.

The bar is drawn by hand rather than being a QSlider, so nothing about it is
guaranteed by Qt: the mapping from x to a time, the thickening under the pointer and
the "do not fight the drag" rule are all ours to get wrong.

    python tools/test_seekbar.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt, QTimer
from PySide6.QtGui import QEnterEvent, QMouseEvent
from PySide6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from supercut_extended.gui.player import SeekBar   # noqa: E402

failures: list[str] = []


def expect(cond: bool, label: str, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}: {label}" + (f"  [{detail}]" if detail else ""))
    if not cond:
        failures.append(label)


def press(bar: SeekBar, x: float) -> None:
    bar.mousePressEvent(QMouseEvent(
        QMouseEvent.Type.MouseButtonPress, QPointF(x, bar.height() / 2),
        Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))


def move(bar: SeekBar, x: float) -> None:
    bar.mouseMoveEvent(QMouseEvent(
        QMouseEvent.Type.MouseMove, QPointF(x, bar.height() / 2),
        Qt.NoButton, Qt.LeftButton, Qt.NoModifier))


def release(bar: SeekBar, x: float) -> None:
    bar.mouseReleaseEvent(QMouseEvent(
        QMouseEvent.Type.MouseButtonRelease, QPointF(x, bar.height() / 2),
        Qt.LeftButton, Qt.NoButton, Qt.NoModifier))


def main() -> int:
    app = QApplication(sys.argv)
    bar = SeekBar()
    bar.resize(400, bar.height())
    bar.show()

    seeks: list[float] = []
    drags: list[bool] = []
    bar.seeked.connect(seeks.append)
    bar.scrubbing.connect(drags.append)

    def go() -> None:
        try:
            print("-- with no media loaded --")
            press(bar, 200)
            release(bar, 200)
            expect(seeks == [], "a bar with no duration cannot be seeked", str(seeks))

            bar.set_duration(100.0)
            bar.set_position(0.0)
            track = bar._track()

            print("-- clicking maps x to a time --")
            mid = track.left() + track.width() / 2
            press(bar, mid)
            release(bar, mid)
            expect(len(seeks) == 1 and abs(seeks[-1] - 50.0) < 1.0,
                   "the middle of the bar is halfway through", f"{seeks[-1]:.1f}s")
            expect(drags == [True, False], "start and end of the drag are reported",
                   str(drags))

            press(bar, track.left())
            release(bar, track.left())
            expect(abs(seeks[-1]) < 0.5, "the far left is the start", f"{seeks[-1]:.2f}s")
            press(bar, track.right())
            release(bar, track.right())
            expect(abs(seeks[-1] - 100.0) < 0.5, "the far right is the end",
                   f"{seeks[-1]:.2f}s")

            print("-- dragging past the ends is clamped --")
            press(bar, -500)
            release(bar, -500)
            expect(seeks[-1] == 0.0, "dragging off the left clamps to 0",
                   f"{seeks[-1]:.2f}s")
            press(bar, 99999)
            release(bar, 99999)
            expect(abs(seeks[-1] - 100.0) < 0.01, "dragging off the right clamps to the end",
                   f"{seeks[-1]:.2f}s")

            print("-- playback must not fight the drag --")
            press(bar, track.left() + track.width() * 0.25)
            expect(bar.is_dragging(), "dragging is in progress")
            bar.set_position(90.0)          # as if playback kept reporting
            expect(abs(bar._position - 25.0) < 1.0,
                   "position updates are ignored while dragging",
                   f"{bar._position:.1f}s")
            release(bar, track.left() + track.width() * 0.25)
            bar.set_position(90.0)
            expect(abs(bar._position - 90.0) < 0.01,
                   "and accepted again once released", f"{bar._position:.1f}s")

            print("-- it thickens under the pointer --")
            idle = bar._track().height()
            bar._dragging = True            # _target_grow follows hover or drag
            for _ in range(40):
                bar._step()
            hovered = bar._track().height()
            bar._dragging = False
            expect(hovered > idle, "the bar grows when reached for",
                   f"{idle:.1f}px -> {hovered:.1f}px")
            for _ in range(40):
                bar._step()
            expect(abs(bar._track().height() - idle) < 0.01,
                   "and shrinks back when left alone",
                   f"{bar._track().height():.1f}px")

            print("-- the hover time readout --")
            expect(bar.hover_seconds() is not None,
                   "the pointer position maps to a time")
            move(bar, track.left() + track.width() * 0.75)
            expect(abs(bar.hover_seconds() - 75.0) < 1.0,
                   "three quarters across reads as 75s",
                   f"{bar.hover_seconds():.1f}s")
            bar.leaveEvent(QEvent(QEvent.Type.Leave))
            expect(bar.hover_seconds() is None,
                   "and clears when the pointer leaves")
        finally:
            print("\n" + ("seek bar OK" if not failures
                          else f"{len(failures)} CHECK(S) FAILED: {failures}"))
            app.exit(1 if failures else 0)

    QTimer.singleShot(300, go)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
