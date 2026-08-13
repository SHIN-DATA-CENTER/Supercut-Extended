"""Check that the preview shows the OUTPUT frame, not the source frame.

Plays a real pillarboxed video in the real widget and reads the pixels back off the
screen. Asserting on the transform alone would pass even if the video never reached
the scene -- and swapping QVideoWidget for a graphics item is exactly the kind of
change that can leave a black box behind.

    python tools/test_preview_framing.py
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QEventLoop, QPoint, QPointF, QTimer
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from supercut_extended.gui.player import VideoPlayer   # noqa: E402
from supercut_extended.model import Framing            # noqa: E402
from supercut_extended.probe import ffmpeg_path        # noqa: E402

WORK = Path(tempfile.mkdtemp(prefix="supercut_preview_"))
failures: list[str] = []


def expect(cond: bool, label: str, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}: {label}" + (f"  [{detail}]" if detail else ""))
    if not cond:
        failures.append(label)


def make_pillarboxed() -> Path:
    """1920x1080 holding a 1440x1080 picture -- 240px of black down each side.

    A solid red picture, not a test pattern: the check is "is there picture at the
    frame edge", and a pattern's own dark columns would blur that answer.
    """
    dst = WORK / "pillarbox.mp4"
    subprocess.run(
        [ffmpeg_path(), "-y", "-v", "error",
         "-f", "lavfi", "-i", "color=c=red:size=1440x1080:rate=30:duration=4",
         "-vf", "pad=1920:1080:240:0:black",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "30", str(dst)],
        check=True, capture_output=True)
    return dst


def settle(ms: int) -> None:
    """Let Qt run for a while -- decoding and painting are both asynchronous."""
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def at(shot: QImage, x: float, y: float):
    """Sample a WIDGET coordinate: grab() images are in device pixels, so on a
    scaled display they are larger than the widget and logical coordinates miss."""
    ratio = shot.devicePixelRatio() or 1.0
    px = min(shot.width() - 1, max(0, int(x * ratio)))
    py = min(shot.height() - 1, max(0, int(y * ratio)))
    return QImage.pixelColor(shot, px, py)


def column_red(shot: QImage, frac: float) -> float:
    """Mean red level down a column at `frac` across the *frame* (not the widget)."""
    w, h = shot.width(), shot.height()
    x = min(w - 1, max(0, int(w * frac)))
    total = sum(QImage.pixelColor(shot, x, int(h * f)).red()
                for f in (0.3, 0.5, 0.7))
    return total / 3.0


def shoot(player: VideoPlayer) -> QImage:
    """Grab just the visible frame area, so widget letterboxing is not measured."""
    view = player.video
    rect = view.mapFromScene(view._scene.sceneRect()).boundingRect()
    return view.grab(rect.intersected(view.rect())).toImage()


def main() -> int:
    app = QApplication(sys.argv)
    src = make_pillarboxed()

    player = VideoPlayer()
    player.resize(640, 400)
    player.audio_out.setMuted(True)
    player.show()
    player.load(src)
    settle(1500)
    player.player.play()
    settle(1200)
    player.player.pause()
    settle(400)

    print("-- the source really is pillarboxed --")
    shot = shoot(player)
    edge, middle = column_red(shot, 0.02), column_red(shot, 0.5)
    expect(middle > 60, "the video is actually being displayed",
           f"centre red={middle:.0f}")
    expect(edge < 40, "black bar visible at the frame edge with no framing",
           f"edge red={edge:.0f}")

    print("-- cropping the bars away removes them from the preview --")
    player.set_framing(Framing(crop_left=240, crop_right=240))
    settle(400)
    shot = shoot(player)
    cropped_edge = column_red(shot, 0.02)
    expect(cropped_edge > edge + 60, "picture now reaches the frame edge",
           f"{edge:.0f} -> {cropped_edge:.0f}")
    expect(player.video.output_size() == (1440, 1080), "output frame follows the crop",
           str(player.video.output_size()))

    print("-- crop + stretch fills a 16:9 frame --")
    player.set_framing(Framing(width=1920, height=1080, crop_left=240,
                               crop_right=240, stretch=True))
    settle(400)
    shot = shoot(player)
    expect(column_red(shot, 0.02) > edge + 60,
           "stretched picture still reaches the edge",
           f"{column_red(shot, 0.02):.0f}")
    expect(abs(shot.width() / shot.height() - 16 / 9) < 0.08,
           "the visible frame is 16:9", f"{shot.width()}x{shot.height()}")
    expect("1920x1080" in player.frame_label.text(),
           "the readout names the output size", player.frame_label.text())

    print("-- crop without stretch pads instead of distorting --")
    player.set_framing(Framing(width=1920, height=1080, crop_left=240,
                               crop_right=240, stretch=False))
    settle(400)
    shot = shoot(player)
    expect(column_red(shot, 0.02) < 40,
           "fitting shows black padding, matching the render",
           f"{column_red(shot, 0.02):.0f}")
    expect(column_red(shot, 0.5) > 60, "the picture is still there in the middle",
           f"{column_red(shot, 0.5):.0f}")

    print("-- a resolution preset alone keeps the whole source frame --")
    player.set_framing(Framing(width=1280, height=720))
    settle(400)
    expect(player.video.output_size() == (1280, 720), "output is the preset size",
           str(player.video.output_size()))
    shot = shoot(player)
    expect(column_red(shot, 0.02) < 40, "the untouched bars are still shown",
           f"{column_red(shot, 0.02):.0f}")

    print("-- outside the frame is the window background, not black bars --")
    # The widget is almost never the same shape as the video, and the leftover used to
    # be painted black -- bars that are in neither the footage nor the output.
    player.set_framing(Framing())
    player.setStyleSheet("background: #1e5aa8;")   # an unmistakable stand-in
    player.resize(900, 300)                        # far wider than 16:9, so there IS leftover
    settle(600)
    view = player.video
    # Grab the PLAYER, not the view. Rendering the view on its own paints no parent
    # behind it, so the leftover comes back as uninitialised black no matter what the
    # widget does -- which looks exactly like the bug being tested for.
    shot = player.grab().toImage()
    frame = view.mapFromScene(view._scene.sceneRect()).boundingRect()
    expect(frame.width() < view.width() - 20,
           "the video really does leave the widget unfilled",
           f"frame {frame.width()}px in a {view.width()}px widget")
    left = view.mapTo(player, QPoint(4, view.height() // 2))
    outside = at(shot, left.x(), left.y())
    expect(outside.blue() > 100 and outside.red() < 90,
           "the leftover shows the parent background",
           f"rgb({outside.red()},{outside.green()},{outside.blue()})")
    expect(not (outside.red() < 20 and outside.green() < 20 and outside.blue() < 20),
           "the leftover is not black")

    print("-- the cropped-off part must not spill outside the frame --")
    # setSceneRect only decides what is scrolled to, not what is painted. A crop moves
    # the video item to a negative offset, so the bars being cropped away carried on
    # being drawn beside the frame and came back as soon as the widget was wider.
    player.set_framing(Framing(crop_left=240, crop_right=240))
    player.resize(1100, 300)
    settle(600)
    shot = player.grab().toImage()
    frame = view.mapFromScene(view._scene.sceneRect()).boundingRect()
    expect(frame.left() > 6, "there is room beside the frame to spill into",
           f"frame starts at x={frame.left()} of {view.width()}")
    beside = view.mapTo(player, QPoint(max(0, frame.left() - 5), view.height() // 2))
    spill = at(shot, beside.x(), beside.y())
    expect(spill.blue() > 100 and spill.red() < 90,
           "beside the frame is still the parent background",
           f"rgb({spill.red()},{spill.green()},{spill.blue()})")

    print("-- padding that WILL be encoded is still shown black --")
    player.set_framing(Framing(width=1920, height=1080, crop_left=240, crop_right=240))
    settle(600)
    shot = player.grab().toImage()
    # Pick the sample in SCENE coordinates and map it out, rather than deriving it
    # from the mapped bounding rect. Cropping 240 off each side of 1920 leaves padding
    # 12.5% of the frame wide, so 5% in is padding by construction -- whereas working
    # back from boundingRect() carried its rounding, and under load the widget
    # geometry could still be a frame behind, so the point fell outside the frame and
    # the check failed on timing instead of on behaviour.
    scene_rect = view._scene.sceneRect()
    pad_point = QPointF(scene_rect.left() + scene_rect.width() * 0.05,
                        scene_rect.center().y())
    inside = view.mapTo(player, view.mapFromScene(pad_point))
    inside_edge = at(shot, inside.x(), inside.y())
    expect(inside_edge.red() < 40 and inside_edge.blue() < 60,
           "real output padding is drawn black inside the frame",
           f"rgb({inside_edge.red()},{inside_edge.green()},{inside_edge.blue()})")

    player.stop()
    print("\n" + ("preview framing OK" if not failures
                  else f"{len(failures)} CHECK(S) FAILED: {failures}"))
    app.quit()
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
