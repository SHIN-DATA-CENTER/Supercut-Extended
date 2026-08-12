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

            print("-- clips line up with the time axis --")
            # The gap between clips used to be added to the next clip's offset, so
            # every clip drifted 2px further right than the one before and the last
            # one overhung the end of its own timeline by 2px x clip count.
            track.set_timeline(timeline, limits)
            rects = dict(track._rects())
            for i in range(len(timeline.clips)):
                want = track._x_for_seconds(timeline.start_of(i))
                expect(abs(rects[i].left() - want) < 0.51,
                       f"clip {i + 1} starts where the ruler says it does",
                       f"{rects[i].left():.1f} vs {want:.1f}")
            end_x = track._x_for_seconds(timeline.duration_s)
            last = rects[len(timeline.clips) - 1]
            expect(last.right() <= end_x + 0.51,
                   "the last clip does not overhang the end of the sequence",
                   f"clip ends {last.right():.1f}, sequence ends {end_x:.1f}")
            expect(track.minimumWidth() >= end_x,
                   "the widget is wide enough for the whole sequence",
                   f"{track.minimumWidth()} >= {end_x:.0f}")

            print("-- an excluded clip keeps its place but not its time --")
            timeline.clips[0].enabled = False
            rects = dict(track._rects())
            expect(rects[1].left() > rects[0].left(),
                   "the clip after an excluded one is still drawn after it")
            expect(abs(track._x_for_seconds(0.0) - rects[1].left()) < 0.51,
                   "sequence time 0 now points at the first *included* clip",
                   f"{track._x_for_seconds(0.0):.1f} vs {rects[1].left():.1f}")
            timeline.clips[0].enabled = True

            print("-- reaching the end of the sequence --")
            # Playing to the end used to call player.stop(), which drops the source.
            # After that _loaded_source still named the file, so _cue() decided there
            # was nothing to reload and every clip silently refused to play.
            last = len(timeline.clips) - 1
            win._cue(last)
            win._preview_index = last
            end_s = timeline.clips[last].source_end_ms / 1000.0
            win._on_position(end_s + 1.0)
            expect(not win.player.player.source().isEmpty(),
                   "the media is still loaded after the sequence ends",
                   win.player.player.source().toString()[-28:] or "(empty)")
            expect(win._loaded_source is not None,
                   "the editor still believes a file is loaded")
            win._cue(0)
            expect(win._preview_index == 0,
                   "a clip can still be cued after the end was reached")

            print("-- the readout follows the montage, not the recording --")
            expect(win.player.time_map is not None, "the player asks the editor for time")
            expect(win.player.seek_map is not None, "and hands seeking back to it")
            win._preview_index = 1
            clip = timeline.clips[1]
            pos, dur = win._preview_time(clip.start_s + 1.0)
            expect(abs(dur - timeline.duration_s) < 0.01,
                   "the duration shown is the montage's",
                   f"{dur:.2f}s vs sequence {timeline.duration_s:.2f}s")
            expect(abs(pos - (timeline.start_of(1) + 1.0)) < 0.01,
                   "the position shown is where we are in the montage",
                   f"{pos:.2f}s")
            expect(dur < 60_000, "not the 60s dummy recording's own length",
                   f"{dur:.2f}s")

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
