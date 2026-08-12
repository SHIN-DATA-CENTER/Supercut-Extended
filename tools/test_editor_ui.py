"""Drive the clip editor window without clicking, and assert what it does.

Simulates real mouse drags on the timeline, because reorder-vs-trim is decided by
where the press lands and that logic is easy to break silently.

    python tools/test_editor_ui.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QPoint, QPointF, Qt, QTimer
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from supercut_extended.encoder import DEFAULT_QUALITY, available_encoders  # noqa: E402
from supercut_extended.gui.editor import EditorWindow                      # noqa: E402
from supercut_extended.model import Bgm, Clip, Timeline                    # noqa: E402
from supercut_extended.render import RenderOptions                         # noqa: E402

failures: list[str] = []


def expect(cond: bool, label: str, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}: {label}" + (f"  [{detail}]" if detail else ""))
    if not cond:
        failures.append(label)


def drag(widget, x0: float, x1: float, y: float) -> None:
    """Press at x0, move to x1, release -- as the widget's handlers see it."""
    for kind, x in ((QMouseEvent.Type.MouseButtonPress, x0),
                    (QMouseEvent.Type.MouseMove, x1),
                    (QMouseEvent.Type.MouseButtonRelease, x1)):
        ev = QMouseEvent(kind, QPointF(x, y), Qt.LeftButton, Qt.LeftButton,
                         Qt.NoModifier)
        if kind == QMouseEvent.Type.MouseButtonPress:
            widget.mousePressEvent(ev)
        elif kind == QMouseEvent.Type.MouseMove:
            widget.mouseMoveEvent(ev)
        else:
            widget.mouseReleaseEvent(ev)


def main() -> int:
    app = QApplication(sys.argv)
    src = Path(tempfile.gettempdir()) / "editor_dummy.mp4"
    timeline = Timeline(clips=[
        Clip(src, 0, 4000, "A", event_kind="kill"),
        Clip(src, 10_000, 14_000, "B", event_kind="ace"),
        Clip(src, 20_000, 24_000, "C", event_kind="victory"),
    ])
    limits = {src: 60_000.0}
    opts = RenderOptions(encoder=available_encoders()[0], quality=DEFAULT_QUALITY)

    win = EditorWindow(timeline, limits, Path(tempfile.gettempdir()) / "out.mp4", opts)
    win.resize(1100, 620)
    win.show()

    def go() -> None:
        try:
            track = win.track
            track.resize(900, track.height())
            rects = track._rects()
            expect(len(rects) == 3, "three clips laid out", str(len(rects)))
            mid_y = rects[0][1].center().y()

            print("-- reorder by dragging the clip body --")
            first_rect = rects[0][1]
            last_rect = rects[-1][1]
            drag(track, first_rect.center().x(), last_rect.center().x(), mid_y)
            order = [c.label for c in timeline.clips]
            expect(order[-1] == "A", "dragging A to the right moves it later",
                   " ".join(order))

            print("-- trim by dragging an edge --")
            rects = track._rects()
            idx = next(i for i, _r in rects if timeline.clips[i].label == "B")
            rect = dict(rects)[idx]
            before = timeline.clips[idx].duration_ms
            # Grab within EDGE_GRAB px of the right border and pull left.
            drag(track, rect.right() - 2, rect.right() - 60, mid_y)
            after = timeline.clips[idx].duration_ms
            expect(after < before, "dragging the right edge shortens the clip",
                   f"{before:.0f}ms -> {after:.0f}ms")
            expect(after >= 200, "trim respects the minimum length", f"{after:.0f}ms")
            expect(len(timeline.clips) == 3, "trimming did not reorder anything")

            print("-- trim cannot exceed the source --")
            clip = timeline.clips[idx]
            clip.source_end_ms = 59_000
            drag(track, dict(track._rects())[idx].right() - 2, 5000, mid_y)
            expect(clip.source_end_ms <= limits[src] + 1,
                   "trim clamped to the real recording length",
                   f"{clip.source_end_ms:.0f} <= {limits[src]:.0f}")

            print("-- disable and fades --")
            win.track._index = 0
            win._on_select(0)
            win.enabled_box.setChecked(False)
            expect(not timeline.clips[0].enabled, "unticking excludes the clip")
            expect(len(timeline.active) == 2, "excluded clip drops out of active",
                   str(len(timeline.active)))
            win.enabled_box.setChecked(True)
            win.clip_fade_in_ms.setValue(0.75)
            expect(abs(timeline.clips[0].fade_in_ms - 750) < 1,
                   "fade in is stored in ms", str(timeline.clips[0].fade_in_ms))
            expect(timeline.needs_encode(), "a fade means the render must encode")

            print("-- music --")
            timeline.bgm = Bgm(path=src, volume=0.4)
            win.vol.setValue(60)
            win._on_bgm_change()
            expect(abs(timeline.bgm.volume - 0.60) < 0.01, "volume slider drives the mix",
                   str(timeline.bgm.volume))
            win._clear_bgm()
            expect(timeline.bgm is None, "music can be removed")

            print("-- tool palette --")
            for name, btn in (("select", win.tool_select), ("cut", win.tool_cut),
                              ("duplicate", win.dup_btn), ("delete", win.del_btn)):
                expect(not btn.icon().isNull(), f"{name} has an icon")
                expect(btn.text() == "", f"{name} is icon-only, not a text button",
                       repr(btn.text()))
                expect(bool(btn.toolTip()), f"{name} explains itself on hover")
            expect(win.tool_select.isCheckable() and win.tool_cut.isCheckable(),
                   "the modal tools are toggles")
            expect(not win.dup_btn.isCheckable() and not win.del_btn.isCheckable(),
                   "the one-shot commands are not toggles")
            # Vertical strip: the tools sit above one another, not side by side.
            xs = {win.tool_select.pos().x(), win.tool_cut.pos().x(),
                  win.dup_btn.pos().x(), win.del_btn.pos().x()}
            expect(len(xs) == 1, "the tools share one column", str(sorted(xs)))
            expect(win.tool_cut.pos().y() > win.tool_select.pos().y(),
                   "cut sits below select")
            palette = win.tool_select.parentWidget()
            expect(palette.x() < win.track.mapTo(palette.parentWidget(),
                                                 QPoint(0, 0)).x(),
                   "the palette is left of the timeline")

            print("-- razor tool --")
            win._set_tool("cut")
            expect(track.tool() == "cut", "cut tool active", track.tool())
            before = len(timeline.clips)
            rects = dict(track._rects())
            idx = 0
            r = rects[idx]
            span = timeline.clips[idx].duration_ms
            # Press mid-clip: in cut mode that splits rather than starting a drag.
            ev_x, ev_y = r.center().x(), r.center().y()
            from PySide6.QtGui import QMouseEvent as _ME
            track.mousePressEvent(_ME(_ME.Type.MouseButtonPress, QPointF(ev_x, ev_y),
                                      Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
            expect(len(timeline.clips) == before + 1, "razor splits into two clips",
                   f"{before} -> {len(timeline.clips)}")
            halves = timeline.clips[idx].duration_ms + timeline.clips[idx + 1].duration_ms
            expect(abs(halves - span) < 1.0, "the two halves add up to the original",
                   f"{halves:.0f} vs {span:.0f}")
            expect(track._mode == "", "razor did not start a drag", track._mode or "-")
            win._set_tool("select")
            expect(track.tool() == "select", "back to the selection tool")

            print("-- undo / redo --")
            n_after_split = len(timeline.clips)
            win.undo()
            expect(len(timeline.clips) == before, "undo reverses the split",
                   f"{n_after_split} -> {len(timeline.clips)}")
            win.redo()
            expect(len(timeline.clips) == n_after_split, "redo re-applies it",
                   str(len(timeline.clips)))
            win.undo()

            # Undo must restore values, not just counts -- clips are edited in place,
            # so a shallow snapshot would silently "undo" to the current state.
            win.track._index = 0
            win._on_select(0)
            original = timeline.clips[0].fade_in_ms
            win.push_undo()
            timeline.clips[0].fade_in_ms = 1234.0
            win.undo()
            expect(timeline.clips[0].fade_in_ms == original,
                   "undo restores edited values, not just the clip count",
                   f"{timeline.clips[0].fade_in_ms} vs {original}")

            print("-- duplicate --")
            win.track._index = 0
            win._on_select(0)
            n = len(timeline.clips)
            win._duplicate()
            expect(len(timeline.clips) == n + 1, "duplicate adds a copy",
                   f"{n} -> {len(timeline.clips)}")
            expect(timeline.clips[0].duration_ms == timeline.clips[1].duration_ms,
                   "the copy matches the original")
            win.undo()

            print("-- removal --")
            win.track._index = 0
            win._on_select(0)
            n = len(timeline.clips)
            win._remove_clip()
            expect(len(timeline.clips) == n - 1, "remove deletes the selected clip",
                   f"{n} -> {len(timeline.clips)}")
        finally:
            print("\n" + ("editor UI OK" if not failures
                          else f"{len(failures)} CHECK(S) FAILED: {failures}"))
            app.exit(1 if failures else 0)

    QTimer.singleShot(400, go)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
