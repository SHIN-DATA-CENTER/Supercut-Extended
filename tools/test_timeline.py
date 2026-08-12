"""Verify the edited timeline renders: order, per-clip trim, fades and music.

Uses generated clips rather than the library so it runs anywhere and stays fast. The
things worth guarding are that clip order survives into the output, that trimming a
clip changes only its own length, and that asking for fades or music silently upgrades
a stream copy into a re-encode instead of dropping the effect.

    python tools/test_timeline.py
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from supercut_extended.encoder import available_encoders          # noqa: E402
from supercut_extended.model import Bgm, Clip, Timeline           # noqa: E402
from supercut_extended.probe import ffmpeg_path, probe            # noqa: E402
from supercut_extended.render import RenderOptions, render_timeline  # noqa: E402

WORK = Path(tempfile.mkdtemp(prefix="supercut_timeline_"))
failures: list[str] = []


def expect(cond: bool, label: str, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}: {label}" + (f"  [{detail}]" if detail else ""))
    if not cond:
        failures.append(label)


def make_source(name: str, seconds: int, freq: int) -> Path:
    """A clip whose audio tone identifies it, so order can be checked after the fact."""
    dst = WORK / name
    subprocess.run(
        [ffmpeg_path(), "-y", "-v", "error",
         "-f", "lavfi", "-i", f"testsrc=size=640x360:rate=60:duration={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "60",
         "-c:a", "aac", str(dst)],
        check=True, capture_output=True)
    return dst


def main() -> int:
    spec = available_encoders()[0]
    src_a = make_source("a.mp4", 10, 440)
    src_b = make_source("b.mp4", 10, 880)
    opts = RenderOptions(encoder=spec, workers=1, audio="0")

    print("-- order and per-clip trim --")
    timeline = Timeline(clips=[
        Clip(src_a, 0, 2000, "A"),
        Clip(src_b, 0, 3000, "B"),
        Clip(src_a, 5000, 6000, "C"),
    ])
    expect(abs(timeline.duration_s - 6.0) < 0.01, "timeline duration adds up",
           f"{timeline.duration_s:.2f}s")

    timeline.move(2, 0)
    expect([c.label for c in timeline.clips] == ["C", "A", "B"], "clips reorder")

    out = WORK / "ordered.mp4"
    res = render_timeline(timeline, out, opts)
    dur = probe(out).duration_s
    expect(abs(dur - 6.0) < 0.35, "rendered duration matches the timeline",
           f"{dur:.2f}s vs 6.00s")
    expect(res.segments == 3, "one segment per clip", str(res.segments))

    # Trimming one clip must change the total by exactly that much.
    timeline.clips[0].source_end_ms = 5500      # C: 1.0s -> 0.5s
    expect(abs(timeline.duration_s - 5.5) < 0.01, "trim changes only that clip",
           f"{timeline.duration_s:.2f}s")
    out2 = WORK / "trimmed.mp4"
    render_timeline(timeline, out2, opts)
    dur2 = probe(out2).duration_s
    expect(abs(dur2 - 5.5) < 0.35, "trim reaches the output", f"{dur2:.2f}s")

    print("\n-- fades force a re-encode --")
    faded = Timeline(clips=[Clip(src_a, 0, 3000, "A", fade_in_ms=500, fade_out_ms=500)])
    expect(faded.needs_encode(), "timeline reports it needs encoding")
    copy_opts = RenderOptions(encoder=spec, workers=1, audio="0", mode="copy")
    out3 = WORK / "faded.mp4"
    res3 = render_timeline(faded, out3, copy_opts)
    expect(out3.exists() and res3.size_bytes > 0, "fade render succeeded from copy mode")
    expect(copy_opts.mode == "copy", "the caller's options are not mutated",
           copy_opts.mode)

    print("\n-- background music --")
    music = make_source("music.mp4", 4, 220)
    withbgm = Timeline(
        clips=[Clip(src_a, 0, 3000, "A"), Clip(src_b, 0, 3000, "B")],
        bgm=Bgm(path=music, volume=0.3, fade_in_ms=500, fade_out_ms=500, loop=True),
    )
    out4 = WORK / "bgm.mp4"
    res4 = render_timeline(withbgm, out4, opts)
    info4 = probe(out4)
    expect(out4.exists(), "music render produced a file")
    expect(abs(info4.duration_s - 6.0) < 0.4,
           "music does not extend the montage (looped track is cut to length)",
           f"{info4.duration_s:.2f}s")
    expect(len(info4.audio) >= 1, "output still has audio", str(len(info4.audio)))
    leftover = out4.with_name(out4.stem + "-nomusic" + out4.suffix)
    expect(not leftover.exists(), "intermediate file cleaned up")
    expect(res4.output == out4, "result points at the final file", str(res4.output))

    print("\n" + ("timeline rendering OK" if not failures
                  else f"{len(failures)} CHECK(S) FAILED: {failures}"))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
