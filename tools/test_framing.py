"""Verify output framing: resolution presets, black-bar crop, stretch-to-fill.

Renders real files and probes the result, because the whole point is the geometry that
comes out the other end -- a filter string that looks right can still produce the wrong
frame (or fail outright on the GPU path).

    python tools/test_framing.py
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from supercut_extended.encoder import available_encoders          # noqa: E402
from supercut_extended.model import Clip, Framing, Timeline       # noqa: E402
from supercut_extended.probe import ffmpeg_path, probe            # noqa: E402
from supercut_extended.render import RenderOptions, render_timeline  # noqa: E402

WORK = Path(tempfile.mkdtemp(prefix="supercut_framing_"))
failures: list[str] = []


def expect(cond: bool, label: str, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}: {label}" + (f"  [{detail}]" if detail else ""))
    if not cond:
        failures.append(label)


def make_pillarboxed() -> Path:
    """1920x1080 with a 1440x1080 picture centred -- 240px of black each side.

    This is the shape the request is about: a 4:3 game inside a 16:9 capture.
    """
    dst = WORK / "pillarbox.mp4"
    subprocess.run(
        [ffmpeg_path(), "-y", "-v", "error",
         "-f", "lavfi", "-i", "testsrc=size=1440x1080:rate=60:duration=3",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
         "-vf", "pad=1920:1080:240:0:black",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "60",
         "-c:a", "aac", str(dst)],
        check=True, capture_output=True)
    return dst


def left_edge_brightness(video: Path) -> float:
    """Mean luma of the leftmost 8px column of the first frame.

    Reading raw gray bytes rather than parsing ffmpeg's log: signalstats prints to
    stderr in a format that varies between builds, and a silent parse failure would
    look exactly like a passing test.
    """
    res = subprocess.run(
        [ffmpeg_path(), "-v", "error", "-i", str(video),
         "-vf", "crop=8:ih:0:0,format=gray", "-frames:v", "1",
         "-f", "rawvideo", "-"],
        capture_output=True)
    data = res.stdout
    return sum(data) / len(data) if data else -1.0


def render(src: Path, framing: Framing, name: str) -> Path:
    out = WORK / name
    timeline = Timeline(clips=[Clip(src, 0, 2000, "A")])
    opts = RenderOptions(encoder=available_encoders()[0], workers=1, audio="0",
                         framing=framing)
    render_timeline(timeline, out, opts)
    return out


def main() -> int:
    src = make_pillarboxed()
    info = probe(src)
    expect((info.width, info.height) == (1920, 1080), "source is 1920x1080",
           f"{info.width}x{info.height}")

    print("\n-- no framing keeps the source size --")
    out = render(src, Framing(), "asis.mp4")
    got = probe(out)
    expect((got.width, got.height) == (1920, 1080), "unchanged",
           f"{got.width}x{got.height}")

    print("\n-- resolution preset --")
    out = render(src, Framing(width=1280, height=720), "720p.mp4")
    got = probe(out)
    expect((got.width, got.height) == (1280, 720), "resized to 1280x720",
           f"{got.width}x{got.height}")

    print("\n-- crop the pillarbox, then stretch to fill 16:9 --")
    out = render(src, Framing(width=1920, height=1080, crop_left=240, crop_right=240,
                              stretch=True), "stretch.mp4")
    got = probe(out)
    expect((got.width, got.height) == (1920, 1080), "output is still 1920x1080",
           f"{got.width}x{got.height}")
    # Compare against the source and the crop-only render rather than a fixed
    # threshold: the test pattern's own left edge is dark, so an absolute cut-off
    # would fail even when the bar is correctly gone.
    src_edge = left_edge_brightness(src)
    stretched_edge = left_edge_brightness(out)
    expect(src_edge < 1.0, "source really does have a black bar",
           f"{src_edge:.1f}")
    expect(stretched_edge > src_edge + 5,
           "the black bar is gone -- picture reaches the frame edge",
           f"{src_edge:.1f} -> {stretched_edge:.1f}")

    print("\n-- crop without stretch pads instead of distorting --")
    out = render(src, Framing(width=1920, height=1080, crop_left=240, crop_right=240,
                              stretch=False), "fit.mp4")
    got = probe(out)
    expect((got.width, got.height) == (1920, 1080), "output is 1920x1080",
           f"{got.width}x{got.height}")
    expect(left_edge_brightness(out) < 1.0,
           "fitting pads with black instead of distorting",
           f"{left_edge_brightness(out):.1f}")

    print("\n-- crop only, no target resolution --")
    out = render(src, Framing(crop_left=240, crop_right=240), "cropped.mp4")
    got = probe(out)
    expect((got.width, got.height) == (1440, 1080), "output follows the crop",
           f"{got.width}x{got.height}")
    expect(abs(left_edge_brightness(out) - stretched_edge) < 2.0,
           "stretching shows the same picture edge as cropping alone",
           f"{left_edge_brightness(out):.1f} vs {stretched_edge:.1f}")

    print("\n-- output frame rate --")
    src_fps = probe(src).fps
    expect(abs(src_fps - 60.0) < 0.1, "the test source is 60 fps", f"{src_fps:.2f}")
    for want in (30.0, 120.0):
        out = WORK / f"fps{want:g}.mp4"
        timeline = Timeline(clips=[Clip(src, 0, 2000, "A")])
        opts = RenderOptions(encoder=available_encoders()[0], workers=1, audio="0",
                             fps=want)
        render_timeline(timeline, out, opts)
        got = probe(out)
        expect(abs(got.fps - want) < 0.1, f"output really runs at {want:g} fps",
               f"{got.fps:.2f}")
        # Re-timing must not turn into a speed change: the montage has to stay the
        # length it says it is.
        expect(abs(got.duration_s - 2.0) < 0.15,
               f"at {want:g} fps the clip is still its own length",
               f"{got.duration_s:.2f}s")

    out = WORK / "fps_source.mp4"
    timeline = Timeline(clips=[Clip(src, 0, 2000, "A")])
    render_timeline(timeline, out,
                    RenderOptions(encoder=available_encoders()[0], workers=1,
                                  audio="0"))
    expect(abs(probe(out).fps - src_fps) < 0.1,
           "no fps setting keeps the source rate", f"{probe(out).fps:.2f}")

    print("\n" + ("framing OK" if not failures
                  else f"{len(failures)} CHECK(S) FAILED: {failures}"))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
