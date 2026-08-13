"""The v1.4.0 additions: tabbed settings, preview-timeline zoom, the About box.

    python tools/test_v140_ui.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QPoint, QPointF, Qt, QTimer
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QLabel, QScrollArea

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from supercut_extended import __version__                      # noqa: E402
from supercut_extended.gui.about_dialog import AboutDialog     # noqa: E402
from supercut_extended.gui.i18n import tr                      # noqa: E402
from supercut_extended.gui.main_window import MainWindow       # noqa: E402
from supercut_extended.model import GameEvent                  # noqa: E402
from supercut_extended.segments import build_segments          # noqa: E402
from supercut_extended.gui.timeline import (                   # noqa: E402
    TimelineControls, TimelineWidget)

failures: list[str] = []


def expect(cond: bool, label: str, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}: {label}" + (f"  [{detail}]" if detail else ""))
    if not cond:
        failures.append(label)


def wheel(widget, x: float, mods, clicks: int = 1) -> None:
    pos = QPointF(x, widget.height() / 2)
    delta = QPoint(0, 120 * clicks)
    QApplication.sendEvent(widget, QWheelEvent(
        pos, widget.mapToGlobal(pos), delta, delta,
        Qt.NoButton, mods, Qt.NoScrollPhase, False))


def check_tabs(win: MainWindow) -> None:
    print("-- the settings are split into four tabs --")
    tabs = win.tabs
    labels = [tabs.tabText(i) for i in range(tabs.count())]
    expect(labels == [tr("group.events"), tr("group.timing"),
                      tr("group.output"), tr("group.dest")],
           "four tabs, in the order the work happens", " / ".join(labels))

    # Each tab scrolls on its own; a shared scroll area would defeat the split.
    areas = [tabs.widget(i) for i in range(tabs.count())]
    expect(all(isinstance(a, QScrollArea) for a in areas),
           "every tab scrolls independently")
    expect(len({id(a) for a in areas}) == 4, "and they are four separate areas")

    print("-- every control still reaches the render --")
    # The controls moved between parents; what matters is that the accessors the
    # render path uses still find them.
    for name, getter in (
            ("対象イベント", lambda: win._selected_kinds()),
            ("クリップの長さ", lambda: win.pre_spin.value()),
            ("出力", lambda: win._current_framing()),
            ("フレームレート", lambda: win._current_fps()),
            ("保存先", lambda: win.output_edit.text())):
        try:
            getter()
            expect(True, f"{name} は読み取れる")
        except Exception as exc:                      # noqa: BLE001
            expect(False, f"{name} は読み取れる", repr(exc))

    print("-- switching tabs does not disturb the settings --")
    before = (win._current_framing(), win._current_fps(), win._selected_kinds())
    for i in range(tabs.count()):
        tabs.setCurrentIndex(i)
        QApplication.processEvents()
    after = (win._current_framing(), win._current_fps(), win._selected_kinds())
    expect(before == after, "values survive a walk through every tab")

    # The custom width/height row follows the resolution choice. Asserted against the
    # choice rather than against "hidden", because these settings are restored from
    # the user's own config and may legitimately already be on Custom.
    tabs.setCurrentIndex(2)
    QApplication.processEvents()
    win.res_combo.setCurrentIndex(win.res_combo.findData("custom"))
    QApplication.processEvents()
    expect(win.custom_row.isVisible(),
           "picking Custom reveals the width/height row inside its tab")
    win.res_combo.setCurrentIndex(0)
    QApplication.processEvents()
    expect(not win.custom_row.isVisible(),
           "picking a preset hides it again")


def check_timing(win: MainWindow) -> None:
    print("-- the clip timing spins --")
    win.tabs.setCurrentIndex(1)
    win.use_defaults.setChecked(False)
    QApplication.processEvents()

    expect(abs(win.pre_spin.singleStep() - 0.1) < 1e-6,
           "before steps in 0.1s", str(win.pre_spin.singleStep()))
    expect(abs(win.post_spin.singleStep() - 0.1) < 1e-6,
           "after steps in 0.1s", str(win.post_spin.singleStep()))
    win.post_spin.setValue(2.0)
    win.post_spin.stepBy(-1)
    expect(abs(win.post_spin.value() - 1.9) < 1e-6,
           "one step really moves it a tenth", str(win.post_spin.value()))

    print("-- after can go negative, to trim the tail --")
    expect(win.post_spin.minimum() < 0, "the spin accepts negative values",
           str(win.post_spin.minimum()))
    win.pre_spin.setValue(8.0)
    win.post_spin.setValue(-2.0)
    QApplication.processEvents()
    expect(win.post_spin.value() == -2.0, "and keeps the value it was given",
           str(win.post_spin.value()))

    # The window really has to end before the event, not merely be shorter.
    ev = GameEvent("kill", 30_000, 15_000, 5_000)
    segs = build_segments([ev], kinds=["kill"], pre_ms=8_000, post_ms=-2_000,
                          duration_ms=60_000)
    expect(len(segs) == 1, "a negative tail still produces a segment")
    if segs:
        expect(abs(segs[0].end_ms / 1000.0 - 28.0) < 0.01,
               "which ends 2s BEFORE the event at 30s",
               f"{segs[0].end_ms / 1000.0:.2f}s")
        expect(abs(segs[0].duration_s - 6.0) < 0.01, "and is 6s long",
               f"{segs[0].duration_s:.2f}s")

    print("-- an impossible window is called out rather than silently empty --")
    win.pre_spin.setValue(2.0)
    win.post_spin.setValue(-3.0)
    QApplication.processEvents()
    expect(win.timing_warn.isVisible(),
           "before+after <= 0 warns instead of just producing nothing")
    empty = build_segments([ev], kinds=["kill"], pre_ms=2_000, post_ms=-3_000,
                           duration_ms=60_000)
    expect(not empty, "and that combination really does produce nothing")
    win.pre_spin.setValue(8.0)
    win.post_spin.setValue(2.0)
    QApplication.processEvents()
    expect(not win.timing_warn.isVisible(), "the warning clears again")
    win.use_defaults.setChecked(True)
    QApplication.processEvents()


def check_timeline_zoom() -> None:
    print("-- the preview timeline zooms --")
    tl = TimelineWidget()
    tl.resize(800, 90)
    tl.set_data(1800.0, [], [])
    expect(tl.zoom() == 1.0, "starts showing the whole recording")
    expect(abs(tl.view_span_s() - 1800.0) < 0.01, "span is the full duration")

    print("-- zoom is centred on the playhead --")
    tl.set_playhead(1200.0)
    for factor in (2.0, 4.0, 8.0, 40.0):
        tl.set_zoom(factor)
        centre = tl.view_start_s() + tl.view_span_s() / 2.0
        expect(abs(centre - 1200.0) < 0.05,
               f"at {factor:g}x the playhead is the middle of the window",
               f"centre {centre:.1f}s")
    tl.reset_zoom()

    # Near the ends the window cannot be centred, but the playhead must still be in it.
    for where in (20.0, 1795.0):
        tl.reset_zoom()
        tl.set_playhead(where)
        tl.set_zoom(10.0)
        inside = tl.view_start_s() <= where <= tl.view_start_s() + tl.view_span_s()
        expect(inside, f"the playhead at {where:g}s stays inside the window",
               f"{tl.view_start_s():.1f}..{tl.view_start_s() + tl.view_span_s():.1f}")

    tl.reset_zoom()
    tl.set_playhead(900.0)
    wheel(tl, 400.0, Qt.ControlModifier, +1)
    expect(tl.zoom() > 1.0, "ctrl+wheel zooms in", f"{tl.zoom():.2f}x")
    centre = tl.view_start_s() + tl.view_span_s() / 2.0
    expect(abs(centre - 900.0) < 0.05,
           "the wheel uses the playhead too, not wherever the pointer happens to be",
           f"centre {centre:.1f}s")

    print("-- playback that leaves the window pulls it along --")
    tl.set_zoom(20.0)
    tl.set_playhead(1500.0)
    expect(tl.view_start_s() <= 1500.0 <= tl.view_start_s() + tl.view_span_s(),
           "a jump outside the window brings it back into view",
           f"{tl.view_start_s():.1f}..{tl.view_start_s() + tl.view_span_s():.1f}")
    before = tl.view_start_s()
    tl.set_playhead(1500.0 + tl.view_span_s() * 0.1)
    expect(tl.view_start_s() == before,
           "but playing along inside it does not keep re-centring")
    tl.reset_zoom()

    tl.set_playhead(900.0)
    tl.set_zoom(4.0)
    start = tl.view_start_s()
    wheel(tl, 400.0, Qt.AltModifier, -1)
    expect(tl.view_start_s() > start, "alt+wheel pans along the recording",
           f"{start:.1f}s -> {tl.view_start_s():.1f}s")

    tl.set_zoom(100.0, anchor_s=1800.0)
    expect(tl.view_start_s() + tl.view_span_s() <= 1800.01,
           "the window never runs off the end of the recording",
           f"{tl.view_start_s():.1f}+{tl.view_span_s():.1f}")
    tl.set_zoom(0.1)
    expect(tl.zoom() == 1.0, "cannot zoom out past the whole recording",
           f"{tl.zoom():.2f}x")

    tl.set_zoom(8.0, anchor_s=900.0)
    tl.reset_zoom()
    expect(tl.zoom() == 1.0 and tl.view_start_s() == 0.0, "reset shows everything")

    # Seeking has to report real timestamps while zoomed, not view-relative ones.
    tl.set_zoom(4.0, anchor_s=900.0)
    left_edge = tl._seconds_for(tl._track().left())
    expect(abs(left_edge - tl.view_start_s()) < 1.0,
           "the left edge maps to where the window starts",
           f"{left_edge:.1f}s vs {tl.view_start_s():.1f}s")

    print("-- a new recording starts unzoomed --")
    tl.set_data(600.0, [], [])
    expect(tl.zoom() == 1.0 and tl.view_start_s() == 0.0,
           "loading another match resets the view")


def check_timeline_controls() -> None:
    print("-- the scrollbar and zoom buttons --")
    tl = TimelineWidget()
    tl.resize(800, 90)
    tl.set_data(1800.0, [], [])
    controls = TimelineControls(tl)
    controls.resize(800, 24)

    expect(not controls.bar.isEnabled(),
           "nothing to scroll while the whole recording is shown")
    expect(not controls.out_btn.isEnabled(), "zoom out is dead at 1x")
    expect(controls.factor.text() == "1.0x", "the factor is shown",
           controls.factor.text())

    controls.in_btn.click()
    expect(tl.zoom() > 1.0, "the + button zooms in", f"{tl.zoom():.2f}x")
    expect(controls.bar.isEnabled(), "and the scrollbar wakes up")
    expect(controls.bar.maximum() > 0, "with a range to scroll through",
           f"0..{controls.bar.maximum()}")

    controls.bar.setValue(controls.bar.maximum())
    expect(abs(tl.view_start_s() + tl.view_span_s() - 1800.0) < 0.05,
           "dragging the bar to the end lands exactly on the end of the recording",
           f"{tl.view_start_s() + tl.view_span_s():.2f}s")

    # The wheel and the bar are two views of one position: moving the widget has to
    # move the bar, or the bar snaps the view back the next time it is touched.
    tl.set_view_start(0.0)
    expect(controls.bar.value() == 0,
           "panning the widget moves the scrollbar too", str(controls.bar.value()))

    controls.out_btn.click()
    expect(tl.zoom() < 2.0, "the - button zooms back out", f"{tl.zoom():.2f}x")
    controls.fit_btn.click()
    expect(tl.zoom() == 1.0 and not controls.bar.isEnabled(),
           "fit returns to the whole recording and parks the bar")


def check_about(win: MainWindow) -> None:
    print("-- the about box says who made it and what it bundles --")
    dlg = AboutDialog(win)
    texts = [w.text() for w in dlg.findChildren(QLabel)]
    joined = "\n".join(texts)
    expect(__version__ in joined, "shows the running version", __version__)
    expect("SHIN DATA CENTER" in joined, "carries the copyright")
    expect("github.com/" in joined and "href=" in joined,
           "links to the repository")
    expect("GPLv3" in joined,
           "names the ffmpeg licence it redistributes under")
    # coolicons is CC BY 4.0: redistribution is fine, attribution is the condition.
    # Putting the files inside the exe does not change that -- they still ship.
    expect("coolicons" in joined, "credits the icon set by name")
    expect("Kryston Schwarze" in joined, "and its author")
    expect("CC BY 4.0" in joined, "and the licence it is used under")
    expect("krystonschwarze/coolicons" in joined, "with a link to the source")
    expect(len(tr("about.description")) > 100,
           "the description is a real description, not one line",
           f"{len(tr('about.description'))} chars")
    dlg.deleteLater()


def main() -> int:
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()

    def go() -> None:
        if win.table.rowCount() == 0 or win._info is None:
            QTimer.singleShot(500, go)
            return
        try:
            check_tabs(win)
            check_timing(win)
            check_timeline_zoom()
            check_timeline_controls()
            check_about(win)
        finally:
            print("\n" + ("v1.4.0 UI OK" if not failures
                          else f"{len(failures)} CHECK(S) FAILED: {failures}"))
            app.exit(1 if failures else 0)

    QTimer.singleShot(800, go)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
