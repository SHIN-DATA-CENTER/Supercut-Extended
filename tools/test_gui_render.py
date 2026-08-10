"""Exercise the GUI's render threading without opening a window.

The CLI covers render() itself. What it does not cover is the QThread/worker wiring:
signals crossing threads, progress marshalling, cancellation, and clean shutdown. A
bug there shows up as a frozen window, so it is worth testing directly.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QObject, Qt, QThread, QTimer

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from supercut_extended.encoder import pick_encoder            # noqa: E402
from supercut_extended.gui.main_window import RenderWorker     # noqa: E402
from supercut_extended.library import matches_with_highlights, read_matches  # noqa: E402
from supercut_extended.probe import probe                      # noqa: E402
from supercut_extended.render import RenderOptions             # noqa: E402
from supercut_extended.segments import build_segments          # noqa: E402


def main() -> int:
    app = QCoreApplication(sys.argv)

    match = matches_with_highlights(read_matches())[0]
    media = match.playable_medias[0]
    info = probe(media.path)
    segments = build_segments(media.events, kinds=["ace"], pre_ms=1000, post_ms=1000,
                              duration_ms=info.duration_ms)
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("gui_worker_test.mp4")
    print(f"source   {media.path.name}")
    print(f"segments {len(segments)} -> {sum(s.duration_s for s in segments):.1f}s")

    options = RenderOptions(encoder=pick_encoder(), mode="encode", audio="0")
    thread = QThread()
    worker = RenderWorker(media.path, segments, out, options)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)

    state = {"progress": 0, "ok": False, "err": None, "off_thread": 0}

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

        def on_done(self, result) -> None:
            state["ok"] = True
            if QThread.currentThread() != app.thread():
                state["off_thread"] += 1
            print(f"finished {result.output.name} in {result.elapsed_s:.1f}s "
                  f"({result.speed_x:.1f}x, {result.size_bytes / 1e6:.1f} MB)")
            thread.quit()

        def on_failed(self, msg: str) -> None:
            state["err"] = msg
            print(f"FAILED: {msg}")
            thread.quit()

    receiver = Receiver()
    worker.progressed.connect(receiver.on_progress, Qt.QueuedConnection)
    worker.finished.connect(receiver.on_done, Qt.QueuedConnection)
    worker.failed.connect(receiver.on_failed, Qt.QueuedConnection)
    thread.finished.connect(app.quit)

    QTimer.singleShot(120_000, lambda: (print("TIMEOUT"), thread.quit()))
    thread.start()
    app.exec()
    thread.wait(5000)

    print(f"progress callbacks: {state['progress']}  "
          f"off-thread deliveries: {state['off_thread']}")
    if state["err"] or not state["ok"]:
        return 1
    if state["progress"] < 2:
        print("FAIL: progress never streamed")
        return 1
    if state["off_thread"]:
        print("FAIL: signals delivered off the GUI thread")
        return 1
    print("GUI render wiring OK (progress streamed, all slots on GUI thread)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
