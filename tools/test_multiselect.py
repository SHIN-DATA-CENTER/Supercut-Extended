"""Drive the multi-select UI without clicking, and assert what it actually shows.

Covers the state that only exists once several matches are ticked: the output-shape
radios waking up, the summary switching to the aggregate wording, the output field
turning from a file into a folder, and ticks surviving a search filter.

The radio check earns its keep -- Qt unchecks the outgoing radio before checking the
incoming one, so a handler wired to only one of them reads the old shape back.

    python tools/test_multiselect.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# The console is often cp932 here; the UI strings contain characters it cannot encode,
# and an encode error mid-run would abort the checks rather than fail one of them.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from supercut_extended.gui.i18n import tr                  # noqa: E402
from supercut_extended.gui.main_window import MainWindow    # noqa: E402

failures: list[str] = []


def expect(cond: bool, label: str, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}: {label}"
          + (f"  [{detail}]" if detail else ""))
    if not cond:
        failures.append(label)


def tick(win: MainWindow, row: int, on: bool = True) -> None:
    win.table.item(row, 0).setCheckState(Qt.Checked if on else Qt.Unchecked)


def run(win: MainWindow) -> None:
    print(f"library rows: {win.table.rowCount()}")

    print("\n-- nothing ticked: behaves exactly as before --")
    expect(not win.shape_combine.isEnabled(), "shape radios disabled")
    expect(win.output_caption.text() == tr("out.file"), "caption is a file",
           win.output_caption.text())
    expect(win.output_edit.text().endswith(".mp4"), "output is a file path")
    expect(len(win._target_matches()) == 1, "target is the previewed match only")

    single_counts = dict(win._target_event_counts())

    print("\n-- two ticked --")
    tick(win, 1)
    tick(win, 2)
    expect(len(win._checked) == 2, "two matches checked", str(len(win._checked)))
    expect(win.shape_combine.isEnabled(), "shape radios enabled")
    expect(len(win._target_matches()) == 2, "targets are the ticked matches")
    expect("→" in win.summary.text(), "summary recomputed", win.summary.text())

    # The event tallies must cover every ticked match, not just the previewed one.
    rows = win._visible_matches()
    wanted: dict[str, int] = {}
    for row in (1, 2):
        media = rows[row].playable_medias[0]
        for kind, n in media.event_counts().items():
            wanted[kind] = wanted.get(kind, 0) + n
    got = win._target_event_counts()
    expect(got == wanted, "event counts summed over both matches",
           f"got {got} want {wanted}")
    expect(got != single_counts, "counts changed from the single-match tally")
    for kind, n in got.items():
        box = win._kind_boxes.get(kind)
        expect(box is not None and f"({n})" in box.text(),
               f"'{kind}' box shows the combined count",
               box.text() if box else "no box")
    expect("matches" in Path(win.output_edit.text()).name,
           "output renamed for a combined file", Path(win.output_edit.text()).name)
    expect(win.build_btn.isEnabled(), "build button enabled")

    print("\n-- switched to separate output --")
    win.shape_separate.setChecked(True)
    expect(win._writing_separate(), "separate mode active")
    expect(win.output_caption.text() == tr("out.dir"), "caption is a folder",
           win.output_caption.text())
    expect(not win.output_edit.text().endswith(".mp4"), "output is a folder",
           win.output_edit.text())

    print("\n-- filtering must not drop ticks --")
    win.search.setText("zzz-no-such-game")
    expect(win.table.rowCount() == 0, "filter hides everything")
    expect(len(win._checked) == 2, "ticks survive filtering", str(len(win._checked)))
    win.search.setText("")
    expect(win.table.item(1, 0).checkState() == Qt.Checked,
           "tick restored on the row after clearing the filter")

    print("\n-- unticking returns to single-match behaviour --")
    win.shape_combine.setChecked(True)
    tick(win, 1, False)
    tick(win, 2, False)
    expect(not win._checked, "all unticked")
    expect(not win.shape_combine.isEnabled(), "shape radios disabled again")
    expect(win.output_edit.text().endswith(".mp4"), "output back to a file")


def main() -> int:
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()

    def go() -> None:
        if win.table.rowCount() == 0:      # library still loading on its own thread
            QTimer.singleShot(500, go)
            return
        try:
            run(win)
        finally:
            print("\n" + ("multi-select UI OK" if not failures
                          else f"{len(failures)} CHECK(S) FAILED: {failures}"))
            app.exit(1 if failures else 0)

    QTimer.singleShot(800, go)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
