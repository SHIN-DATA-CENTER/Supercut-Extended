"""Scrolling the settings panel must not change any setting.

Found the hard way: scrolling down to reach the framing controls moved the encoder
preset from p1 to p7 and the quality from 23 to 30 on the way past, with no click and
nothing to undo. Every one of these controls lives inside a QScrollArea, so this is
reachable by ordinary use.

    python tools/test_wheel_guard.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QPoint, QPointF, Qt, QTimer
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from supercut_extended.gui.main_window import MainWindow    # noqa: E402

failures: list[str] = []


def expect(cond: bool, label: str, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}: {label}" + (f"  [{detail}]" if detail else ""))
    if not cond:
        failures.append(label)


def wheel(widget, clicks: int = -3) -> None:
    """Send wheel events the way a mouse over the widget would."""
    for _ in range(abs(clicks)):
        pos = QPointF(widget.rect().center())
        delta = 120 if clicks > 0 else -120
        QApplication.sendEvent(widget, QWheelEvent(
            pos, widget.mapToGlobal(pos), QPoint(0, delta), QPoint(0, delta),
            Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False))


def run(win: MainWindow) -> None:
    print("-- scrolling past a control leaves it alone --")
    checks = [
        ("preset", win.preset_combo, lambda w: w.currentIndex()),
        ("quality", win.quality_combo, lambda w: w.currentIndex()),
        ("audio", win.audio_combo, lambda w: w.currentIndex()),
        ("resolution", win.res_combo, lambda w: w.currentIndex()),
        ("crop left", win.crop_spins["crop_left"], lambda w: w.value()),
        ("crop top", win.crop_spins["crop_top"], lambda w: w.value()),
        ("custom width", win.res_w, lambda w: w.value()),
        ("clip before", win.pre_spin, lambda w: w.value()),
        ("merge gap", win.gap_spin, lambda w: w.value()),
        ("preview volume", win.player.volume, lambda w: w.value()),
    ]
    for name, widget, read in checks:
        before = read(widget)
        wheel(widget, -3)
        wheel(widget, +3)
        expect(read(widget) == before, f"{name} unchanged by scrolling",
               f"{before} -> {read(widget)}")

    print("-- but the control still works once focused --")
    win.preset_combo.setFocus()
    before = win.preset_combo.currentIndex()
    wheel(win.preset_combo, -1)
    expect(win.preset_combo.currentIndex() != before,
           "a focused combo still responds to the wheel",
           f"{before} -> {win.preset_combo.currentIndex()}")
    win.preset_combo.setCurrentIndex(before)

    win.crop_spins["crop_left"].setFocus()
    before = win.crop_spins["crop_left"].value()
    wheel(win.crop_spins["crop_left"], +1)
    expect(win.crop_spins["crop_left"].value() != before,
           "a focused spin box still responds to the wheel",
           f"{before} -> {win.crop_spins['crop_left'].value()}")

    print("-- wheel alone never steals focus --")
    win.search.setFocus()
    wheel(win.quality_combo, -3)
    expect(not win.quality_combo.hasFocus(),
           "scrolling over a combo does not focus it")


def main() -> int:
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()

    def go() -> None:
        if win.table.rowCount() == 0 or win._info is None:
            QTimer.singleShot(500, go)
            return
        try:
            run(win)
        finally:
            print("\n" + ("wheel guard OK" if not failures
                          else f"{len(failures)} CHECK(S) FAILED: {failures}"))
            app.exit(1 if failures else 0)

    QTimer.singleShot(800, go)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
