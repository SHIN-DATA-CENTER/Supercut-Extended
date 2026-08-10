"""Exercise the GUI's render threading without opening a window.

The CLI covers the render functions themselves. What it does not cover is the
QThread/worker wiring: signals crossing threads, progress marshalling, and clean
shutdown. A bug there shows up as a frozen window, so it is worth testing directly.

Three shapes are checked, because the worker builds segments itself and each shape
reaches render.py through a different entry point:

    one match              -> render_many with a single job
    several, combined      -> render_many with several jobs (one output)
    several, separate      -> render_each (one output per match)
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QObject, Qt, QThread, QTimer

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from supercut_extended.encoder import pick_encoder                 # noqa: E402
from supercut_extended.gui.main_window import BatchPlan, RenderWorker  # noqa: E402
from supercut_extended.library import (matches_with_highlights,    # noqa: E402
                                       read_matches)
from supercut_extended.model import Match                          # noqa: E402
from supercut_extended.render import RenderOptions                 # noqa: E402

KINDS = ["ace"]


def run_worker(app: QCoreApplication, plan: BatchPlan) -> dict:
    """Drive one worker to completion, watching which thread each slot runs on."""
    thread = QThread()
    worker = RenderWorker(plan)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)

    state = {"progress": 0, "results": None, "err": None, "off_thread": 0}

    class Receiver(QObject):
        """Stands in for MainWindow: a QObject that lives on the GUI thread.

        The receiver must be a QObject for a queued connection to mean anything --
        connecting a signal to a bare function gives Qt no thread to marshal to, and
        the slot then runs on whichever pool thread emitted it.
        """

        def on_progress(self, frac: float, msg: str) -> None:
            state["progress"] += 1
            if QThread.currentThread() != app.thread():
                state["off_thread"] += 1

        def on_done(self, results) -> None:
            state["results"] = results
            if QThread.currentThread() != app.thread():
                state["off_thread"] += 1
            thread.quit()

        def on_failed(self, msg: str) -> None:
            state["err"] = msg
            thread.quit()

    receiver = Receiver()
    worker.progressed.connect(receiver.on_progress, Qt.QueuedConnection)
    worker.finished.connect(receiver.on_done, Qt.QueuedConnection)
    worker.failed.connect(receiver.on_failed, Qt.QueuedConnection)
    thread.finished.connect(app.quit)

    QTimer.singleShot(180_000, lambda: (state.update(err="TIMEOUT"), thread.quit()))
    thread.start()
    app.exec()
    thread.wait(5000)
    return state


def items_for(matches: list[Match]) -> list[tuple[Path, list, str]]:
    return [(m.playable_medias[0].path, list(m.playable_medias[0].events), m.label())
            for m in matches]


def check(name: str, state: dict, expect_outputs: int) -> bool:
    if state["err"]:
        print(f"FAIL [{name}]: {state['err']}")
        return False
    results = state["results"] or []
    if len(results) != expect_outputs:
        print(f"FAIL [{name}]: expected {expect_outputs} output(s), "
              f"got {len(results)}")
        return False
    if state["progress"] < 2:
        print(f"FAIL [{name}]: progress never streamed")
        return False
    if state["off_thread"]:
        print(f"FAIL [{name}]: {state['off_thread']} signal(s) off the GUI thread")
        return False
    for r in results:
        if not Path(r.output).exists():
            print(f"FAIL [{name}]: {r.output} was not written")
            return False
    total = sum(r.size_bytes for r in results) / 1e6
    print(f"PASS [{name}]: {len(results)} output(s), "
          f"{sum(r.segments for r in results)} segments, {total:.1f} MB, "
          f"{state['progress']} progress callbacks, all on the GUI thread")
    return True


def main() -> int:
    app = QCoreApplication(sys.argv)

    with_aces = [m for m in matches_with_highlights(read_matches())
                 if any(e.kind in KINDS for e in m.events) and m.playable_medias]
    if len(with_aces) < 2:
        print(f"need 2 matches with {KINDS} events, found {len(with_aces)}")
        return 1
    pair = sorted(with_aces[:2], key=lambda m: m.start_time_ms)
    for m in pair:
        print(f"  using {m.playable_medias[0].path.name}")

    options = RenderOptions(encoder=pick_encoder(), mode="encode", audio="0")
    out_dir = Path(tempfile.mkdtemp(prefix="supercut_guitest_"))

    def plan(matches, combine, output):
        return BatchPlan(items=items_for(matches), kinds=KINDS,
                         pre_ms=1000, post_ms=1000, gap_ms=0.0,
                         options=options, combine=combine, output=output)

    ok = True
    ok &= check("single", run_worker(app, plan(
        pair[:1], True, out_dir / "single.mp4")), 1)
    ok &= check("combined", run_worker(app, plan(
        pair, True, out_dir / "combined.mp4")), 1)
    ok &= check("separate", run_worker(app, plan(pair, False, out_dir)), 2)

    if ok:
        print("\nGUI render wiring OK (progress streamed, all slots on GUI thread)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
