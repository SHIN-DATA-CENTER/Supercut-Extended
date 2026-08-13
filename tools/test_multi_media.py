"""A match is not always one file: cover Outplayed's Highlight capture mode.

Highlight mode writes a separate recording per highlight, so one VALORANT match here
is ten files. Every code path used to take playable_medias[0], so nine of them were
silently dropped -- the montage came out as the first clip and nothing else.

    python tools/test_multi_media.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from supercut_extended.cli import pick_medias                   # noqa: E402
from supercut_extended.gui.main_window import MainWindow        # noqa: E402
from supercut_extended.library import matches_with_highlights   # noqa: E402
from supercut_extended.library import read_matches              # noqa: E402
from supercut_extended.render import nominal_fps                # noqa: E402

failures: list[str] = []


def expect(cond: bool, label: str, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}: {label}" + (f"  [{detail}]" if detail else ""))
    if not cond:
        failures.append(label)


def check_fps() -> None:
    print("-- measurement noise is not a frame-rate mismatch --")
    # Ten recordings of one 60 fps match measured 59.979 .. 60.000. Comparing the
    # rounded values split them into three groups and the join was refused.
    for measured in (59.979945, 59.981593, 59.991754, 59.998320, 60.0):
        expect(nominal_fps(measured) == 60.0,
               f"{measured:.6f} is 60 fps", str(nominal_fps(measured)))
    expect(nominal_fps(59.94) == 59.94, "but 59.94 stays 59.94 (NTSC is a real rate)")
    expect(nominal_fps(59.94) != nominal_fps(60.0),
           "so 59.94 and 60 are still treated as different")
    expect(nominal_fps(29.97) == 29.97, "29.97 survives too")
    expect(nominal_fps(55.5) == 55.5, "an unusual rate is left alone")


def check_library() -> None:
    print("-- the library really does hold multi-recording matches --")
    matches = matches_with_highlights(read_matches())
    multi = [m for m in matches if len(m.playable_medias) > 1]
    expect(bool(multi), "at least one match has several recordings",
           f"{len(multi)} of {len(matches)}")
    if not multi:
        return
    worst = max(multi, key=lambda m: len(m.playable_medias))
    print(f"     largest: {worst.label()}  "
          f"{len(worst.playable_medias)} recordings")
    first_only = len(worst.playable_medias[0].events)
    every = sum(len(md.events) for md in worst.playable_medias)
    expect(every > first_only,
           "and the rest of them carry events the first one does not",
           f"{first_only} events in recording 1, {every} in all of them")

    print("-- the CLI builds from all of them --")
    medias = pick_medias(worst, None)
    expect(len(medias) == len(worst.playable_medias),
           "pick_medias returns every recording", str(len(medias)))
    one = pick_medias(worst, worst.playable_medias[1].media_id)
    expect(len(one) == 1, "--media still narrows it to a single recording")


def check_gui(win: MainWindow) -> None:
    matches = [m for m in win._matches if len(m.playable_medias) > 1]
    if not matches:
        print("  (no multi-recording match in this library -- GUI checks skipped)")
        return
    target = max(matches, key=lambda m: len(m.playable_medias))

    print("-- the window targets every recording of the selected match --")
    rows = win._visible_matches()
    row = next((i for i, m in enumerate(rows) if m is target), -1)
    if row < 0:
        print("  (the match is filtered out of the table -- skipped)")
        return
    win.table.selectRow(row)
    QApplication.processEvents()

    expect(len(win._target_medias()) == len(target.playable_medias),
           "_target_medias covers the whole match",
           f"{len(win._target_medias())} of {len(target.playable_medias)}")

    counts = win._target_event_counts()
    expect(sum(counts.values()) == len(target.events),
           "the event tally adds up over all recordings",
           f"{sum(counts.values())} vs {len(target.events)}")

    print("-- the recording picker appears, and only when it is needed --")
    expect(win.media_row.isVisible(), "the picker is shown for a multi-file match")
    expect(win.media_combo.count() == len(target.playable_medias),
           "one entry per recording", str(win.media_combo.count()))

    before = win._media
    win.media_combo.setCurrentIndex(1)
    QApplication.processEvents()
    expect(win._media is not before, "picking another entry switches the preview")
    expect(win._media is target.playable_medias[1], "to the recording it names")
    win.media_combo.setCurrentIndex(0)
    QApplication.processEvents()

    print("-- the editor timeline spans every recording --")
    for kind, box in win._kind_boxes.items():
        box.setChecked(True)
    QApplication.processEvents()
    timeline, limits = win._build_timeline()
    expect(len(limits) == len(target.playable_medias),
           "clips are cut from all the recordings, not just the first",
           f"{len(limits)} sources")
    expect(len(timeline.clips) > 0, "and there are clips to show",
           f"{len(timeline.clips)} clips")

    single = [m for m in win._matches if len(m.playable_medias) == 1]
    if single:
        row = next((i for i, m in enumerate(win._visible_matches())
                    if m is single[0]), -1)
        if row >= 0:
            win.table.selectRow(row)
            QApplication.processEvents()
            expect(not win.media_row.isVisible(),
                   "the picker hides again for an ordinary one-file match")


def main() -> int:
    app = QApplication(sys.argv)
    check_fps()
    check_library()

    win = MainWindow()
    win.show()

    def go() -> None:
        if win.table.rowCount() == 0 or win._info is None:
            QTimer.singleShot(500, go)
            return
        try:
            check_gui(win)
        except Exception as exc:                      # noqa: BLE001
            import traceback
            traceback.print_exc()
            failures.append(f"crashed: {exc!r}")
        finally:
            print("\n" + ("multi-recording OK" if not failures
                          else f"{len(failures)} CHECK(S) FAILED: {failures}"))
            app.exit(1 if failures else 0)

    QTimer.singleShot(800, go)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
