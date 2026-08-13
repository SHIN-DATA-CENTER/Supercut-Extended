"""Play a real montage in the editor, across two different source files.

The two bugs this pins down both came from the same thing: a seek issued straight
after load() is thrown away, because the media has not reported itself loaded yet.
So cueing a clip from another recording left the player sitting at 0 and paused --
which showed up as "moving to a clip from a different video stops playback" and as
"pressing play starts the source recording from the beginning".

    python tools/test_editor_playback.py
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from supercut_extended.encoder import DEFAULT_QUALITY, available_encoders  # noqa: E402
from supercut_extended.gui.editor import EditorWindow                      # noqa: E402
from supercut_extended.model import Clip, Timeline                         # noqa: E402
from supercut_extended.probe import ffmpeg_path                            # noqa: E402
from supercut_extended.render import RenderOptions                         # noqa: E402

WORK = Path(tempfile.mkdtemp(prefix="supercut_playback_"))
failures: list[str] = []


def expect(cond: bool, label: str, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}: {label}" + (f"  [{detail}]" if detail else ""))
    if not cond:
        failures.append(label)


def make_clip(name: str, colour: str) -> Path:
    """A real 30 second file -- these bugs only appear with media that must load."""
    dst = WORK / name
    subprocess.run(
        [ffmpeg_path(), "-y", "-v", "error",
         "-f", "lavfi", "-i", f"color=c={colour}:size=320x180:rate=30:duration=30",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=30",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "30",
         "-c:a", "aac", str(dst)],
        check=True, capture_output=True)
    return dst


def settle(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def main() -> int:
    app = QApplication(sys.argv)
    first = make_clip("a.mp4", "red")
    second = make_clip("b.mp4", "blue")

    # Two clips from ONE file, then one from another: the boundary that broke.
    timeline = Timeline(clips=[
        Clip(first, 2_000, 6_000, "A1"),
        Clip(first, 10_000, 14_000, "A2"),
        Clip(second, 20_000, 24_000, "B1"),
    ])
    limits = {first: 30_000.0, second: 30_000.0}
    opts = RenderOptions(encoder=available_encoders()[0], quality=DEFAULT_QUALITY)

    win = EditorWindow(timeline, limits, WORK / "out.mp4", opts)
    win.resize(1100, 620)
    win.show()
    win.player.audio_out.setMuted(True)
    settle(600)

    print("-- cueing a clip lands on the clip, not on 0 --")
    win._cue(1)                      # 10s into the first recording
    settle(1400)
    pos = win.player.position_s()
    expect(abs(pos - 10.0) < 1.0,
           "the player sits at the clip's in point after loading",
           f"{pos:.2f}s, wanted 10.0s")
    expect(pos > 1.0,
           "and specifically NOT at the start of the recording",
           f"{pos:.2f}s")

    print("-- cueing across recordings also lands on the clip --")
    win._cue(2)                      # 20s into the OTHER recording
    settle(1600)
    pos = win.player.position_s()
    expect(abs(pos - 20.0) < 1.0,
           "switching source still honours the in point",
           f"{pos:.2f}s, wanted 20.0s")
    expect(win._loaded_source == second, "and the other file is what is loaded",
           str(win._loaded_source and win._loaded_source.name))

    print("-- playing on into another recording does not stop --")
    win._cue(1, play=True)
    settle(900)
    expect(win.player.player.playbackState() == QMediaPlayer.PlayingState,
           "playback is running before the boundary",
           str(win.player.player.playbackState()))

    # Drive the sequence over the end of clip 2, which lives in the other file.
    win._preview_index = 1
    win._on_position(timeline.clips[1].source_end_ms / 1000.0 + 0.1)
    settle(1600)
    expect(win._preview_index == 2, "the sequence advanced to the next clip",
           str(win._preview_index))
    expect(win._loaded_source == second, "which loaded the other recording")
    expect(win.player.player.playbackState() == QMediaPlayer.PlayingState,
           "and playback is STILL running after crossing files",
           str(win.player.player.playbackState()))
    pos = win.player.position_s()
    expect(pos > 19.0,
           "playing from the new clip's in point, not from 0",
           f"{pos:.2f}s")

    win.player.player.pause()
    print("\n" + ("editor playback OK" if not failures
                  else f"{len(failures)} CHECK(S) FAILED: {failures}"))
    app.quit()
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
