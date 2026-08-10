"""Measure how much preset and quality actually move encode speed.

The ETA shown in the GUI used one number per encoder, so changing the preset or the
quality did not change the estimate at all. This measures the real cost of both knobs
so `encoder.speed_factor()` can be built from data rather than taste.

Only *ratios* are measured, normalised to each encoder's default preset and quality.
That keeps the numbers comparable with the existing end-to-end baselines (4.3x for
NVENC, ~1.0x for libx264, ~0.5x for libx265) which were measured on full renders and
so already include segment and muxing overhead -- something a synthetic single-clip
run does not reproduce.

    python tools/bench_presets.py [--seconds N]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from supercut_extended.encoder import (DEFAULT_QUALITY, QUALITY_TIERS,  # noqa: E402
                                       available_encoders, decode_args, video_args)
from supercut_extended.library import (matches_with_highlights,  # noqa: E402
                                       read_matches)
from supercut_extended.probe import ffmpeg_path, probe  # noqa: E402

START = 300.0


def find_source() -> Path:
    """The longest 1080p60 recording in the library -- a realistic, busy sample."""
    best = None
    for match in matches_with_highlights(read_matches()):
        for media in match.playable_medias:
            if media.is_derived or media.duration_s < 400:
                continue
            if best is None or media.duration_s > best.duration_s:
                best = media
    if best is None:
        raise SystemExit("no suitable source recording found")
    return best.path


def measure(src: Path, spec, preset: str, quality: int, seconds: float) -> float:
    """Realtime multiplier for one setting, video only."""
    cmd = [ffmpeg_path(), "-hide_banner", "-nostdin", "-loglevel", "error", "-y"]
    cmd += decode_args(spec)
    cmd += ["-ss", str(START), "-i", str(src), "-t", str(seconds), "-map", "0:v:0", "-an"]
    cmd += video_args(spec, preset=preset, quality=quality, max_rate_kbps=25000,
                      hw_frames=bool(decode_args(spec)))
    cmd += ["-f", "null", "-"]

    t0 = time.monotonic()
    res = subprocess.run(cmd, capture_output=True, text=True)
    wall = time.monotonic() - t0
    if res.returncode != 0:
        tail = (res.stderr or "").strip().splitlines()
        raise RuntimeError(tail[-1][:120] if tail else "ffmpeg failed")
    return seconds / wall


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=0.0,
                    help="clip length; default 10s on GPU, 5s on CPU")
    args = ap.parse_args()

    src = find_source()
    info = probe(src)
    print(f"source  {src.name}")
    print(f"        {info.width}x{info.height} {info.fps:.0f}fps  "
          f"slice from {START:.0f}s\n")

    for spec in available_encoders():
        seconds = args.seconds or (10.0 if spec.hardware else 5.0)
        presets = spec.presets
        base_preset = spec.default_preset
        print(f"=== {spec.name} ({'GPU' if spec.hardware else 'CPU'})  "
              f"slice {seconds:.0f}s ===")

        try:
            base = measure(src, spec, base_preset, DEFAULT_QUALITY, seconds)
        except RuntimeError as exc:
            print(f"  baseline failed: {exc}\n")
            continue
        print(f"  baseline {base_preset} cq{DEFAULT_QUALITY}: {base:.2f}x realtime\n")

        print(f"  {'preset':<12} {'xRT':>7} {'factor':>8}")
        for p in presets:
            try:
                r = measure(src, spec, p, DEFAULT_QUALITY, seconds)
            except RuntimeError as exc:
                print(f"  {p:<12}   FAILED  {exc}")
                continue
            print(f"  {p:<12} {r:6.2f}x {r / base:7.2f}"
                  + ("   <- default" if p == base_preset else ""))

        print(f"\n  {'quality':<12} {'xRT':>7} {'factor':>8}")
        for q in QUALITY_TIERS:
            try:
                r = measure(src, spec, base_preset, q, seconds)
            except RuntimeError as exc:
                print(f"  cq{q:<10}   FAILED  {exc}")
                continue
            print(f"  cq{q:<10} {r:6.2f}x {r / base:7.2f}"
                  + ("   <- default" if q == DEFAULT_QUALITY else ""))

        # Are the two knobs roughly independent? If so a product of factors is a fair
        # model; if not, the ETA needs a full table instead.
        fast, slow = presets[0], presets[-1]
        print("\n  separability check (predicted = preset factor x quality factor)")
        for p, q in ((fast, 30), (slow, 18)):
            try:
                pf = measure(src, spec, p, DEFAULT_QUALITY, seconds) / base
                qf = measure(src, spec, base_preset, q, seconds) / base
                actual = measure(src, spec, p, q, seconds) / base
            except RuntimeError as exc:
                print(f"    {p} cq{q}: FAILED {exc}")
                continue
            pred = pf * qf
            err = (actual - pred) / pred * 100 if pred else 0
            print(f"    {p:<10} cq{q:<3} predicted {pred:5.2f}  actual {actual:5.2f}"
                  f"  ({err:+.0f}%)")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
