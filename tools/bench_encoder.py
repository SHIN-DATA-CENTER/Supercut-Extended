"""Benchmark NVENC settings against a real recording.

The first pass at encoder options (p5 + lookahead + B-pyramid + spatial AQ, cq 21)
only reached 2.5x realtime and produced a 1.8 GB file, which is barely better than the
libx264 path it was meant to replace. This measures the actual cost of each knob so
the defaults are chosen from data instead of taste.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

SRC = Path(
    "G:/Outplayed/Counter-Strike 2/Counter-Strike 2_08-10-2026_9-37-38-71/"
    "Counter-Strike 2_08-10-2026_10-53-3-933.mp4"
)
START = 300.0
LENGTH = 60.0
FPS = 60

BASE = [
    "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
    "-hwaccel", "cuda", "-hwaccel_output_format", "cuda",
    "-ss", str(START), "-i", str(SRC), "-t", str(LENGTH),
    "-map", "0:v:0", "-an",
]

VARIANTS: list[tuple[str, list[str]]] = [
    ("p1 cq23 plain",        ["-preset", "p1", "-rc", "vbr", "-cq", "23", "-b:v", "0"]),
    ("p3 cq23 plain",        ["-preset", "p3", "-rc", "vbr", "-cq", "23", "-b:v", "0"]),
    ("p4 cq23 plain",        ["-preset", "p4", "-rc", "vbr", "-cq", "23", "-b:v", "0"]),
    ("p5 cq23 plain",        ["-preset", "p5", "-rc", "vbr", "-cq", "23", "-b:v", "0"]),
    ("p7 cq23 plain",        ["-preset", "p7", "-rc", "vbr", "-cq", "23", "-b:v", "0"]),
    ("p4 cq23 +bf3",         ["-preset", "p4", "-rc", "vbr", "-cq", "23", "-b:v", "0",
                              "-bf", "3", "-b_ref_mode", "middle"]),
    ("p4 cq23 +aq",          ["-preset", "p4", "-rc", "vbr", "-cq", "23", "-b:v", "0",
                              "-spatial-aq", "1", "-aq-strength", "8"]),
    ("p4 cq23 +lookahead20", ["-preset", "p4", "-rc", "vbr", "-cq", "23", "-b:v", "0",
                              "-rc-lookahead", "20"]),
    ("p5 cq21 ORIGINAL",     ["-preset", "p5", "-tune", "hq", "-rc", "vbr", "-cq", "21",
                              "-b:v", "0", "-maxrate", "40000k", "-bufsize", "80000k",
                              "-rc-lookahead", "20", "-bf", "3", "-b_ref_mode", "middle",
                              "-spatial-aq", "1", "-aq-strength", "8"]),
    ("p4 cq25 +bf3 +aq",     ["-preset", "p4", "-rc", "vbr", "-cq", "25", "-b:v", "0",
                              "-maxrate", "25000k", "-bufsize", "50000k",
                              "-bf", "3", "-b_ref_mode", "middle", "-spatial-aq", "1"]),
]


def main() -> int:
    if not SRC.exists():
        print(f"source missing: {SRC}", file=sys.stderr)
        return 1

    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("bench_out")
    out.mkdir(parents=True, exist_ok=True)
    frames = LENGTH * FPS

    print(f"source {SRC.name}  slice {START}s +{LENGTH}s  ({frames:.0f} frames @{FPS}fps)\n")
    print(f"{'variant':<24} {'wall':>7} {'fps':>8} {'xRT':>7} {'MB':>7} {'Mbps':>7}")
    print("-" * 66)

    results = []
    for label, args in VARIANTS:
        dst = out / (label.replace(" ", "_").replace("+", "") + ".mp4")
        cmd = BASE + ["-c:v", "h264_nvenc"] + args + [str(dst)]
        t0 = time.monotonic()
        res = subprocess.run(cmd, capture_output=True, text=True)
        wall = time.monotonic() - t0
        if res.returncode != 0:
            print(f"{label:<24}  FAILED: {res.stderr.strip().splitlines()[-1][:40]}")
            continue
        mb = dst.stat().st_size / 1e6
        fps = frames / wall
        results.append((label, wall, fps, LENGTH / wall, mb, mb * 8 / LENGTH))
        print(f"{label:<24} {wall:6.1f}s {fps:7.0f} {LENGTH / wall:6.1f}x "
              f"{mb:6.1f} {mb * 8 / LENGTH:6.1f}")

    if results:
        best = max(results, key=lambda r: r[3])
        print(f"\nfastest: {best[0]}  ({best[3]:.1f}x realtime, {best[5]:.1f} Mbps)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
