"""Two-stage GPU render: cut each segment on NVENC, then concatenate losslessly.

Why two stages rather than one ``filter_complex`` with trim/concat: a single input
graph has to walk the entire source, so a 15-minute recording costs 15 minutes of
decode even when the highlights add up to 40 seconds. Seeking per segment with ``-ss``
ahead of ``-i`` only touches the parts we actually want. The source carries a keyframe
every second, so those seeks are cheap and still frame-accurate.

Stage 2 is a plain concat demuxer with ``-c copy``. Every segment was encoded with
identical parameters, so the join is lossless and effectively instant.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

from .encoder import EncoderSpec, decode_args, video_args
from .model import Segment
from .probe import MediaInfo, _NO_WINDOW, ffmpeg_path, probe

ProgressFn = Callable[[float, str], None]


class RenderError(RuntimeError):
    pass


class Cancelled(RuntimeError):
    pass


@dataclass
class RenderOptions:
    encoder: EncoderSpec
    preset: str = "p4"
    quality: int = 23
    max_rate_kbps: int = 25000
    audio: str = "0"          # track index, "all", "mix" or "none"
    workers: int = 2
    keep_temp: bool = False
    mode: str = "encode"      # "encode" (Outplayed-equivalent) or "copy"


@dataclass
class RenderResult:
    output: Path
    segments: int
    content_s: float
    elapsed_s: float
    encoder: str
    size_bytes: int = 0
    speed_x: float = field(default=0.0)


def _audio_args(mode: str, info: MediaInfo) -> list[str]:
    n = len(info.audio)
    if n == 0 or mode == "none":
        return ["-an"]
    if mode == "all":
        return ["-map", "0:a", "-c:a", "aac", "-b:a", "192k"]
    if mode == "mix":
        if n == 1:
            return ["-map", "0:a:0", "-c:a", "aac", "-b:a", "192k", "-ac", "2"]
        inputs = "".join(f"[0:a:{i}]" for i in range(n))
        return [
            "-filter_complex", f"{inputs}amix=inputs={n}:normalize=0[aout]",
            "-map", "[aout]", "-c:a", "aac", "-b:a", "192k", "-ac", "2",
        ]
    try:
        idx = max(0, min(int(mode), n - 1))
    except ValueError:
        idx = 0
    return ["-map", f"0:a:{idx}", "-c:a", "aac", "-b:a", "192k", "-ac", "2"]


def _copy_cmd(src: Path, seg: Segment, dst: Path, opts: RenderOptions,
              info: MediaInfo) -> list[str]:
    """Extract a segment without re-encoding.

    Only viable because Outplayed's recordings carry a keyframe every second (measured:
    91 keyframes in 90s, on exact second boundaries). ffmpeg snaps the in-point back to
    the preceding keyframe, so cuts land up to ~1s early -- in exchange the segment
    costs essentially nothing to produce and loses no quality at all.
    """
    cmd = [ffmpeg_path(), "-hide_banner", "-nostdin", "-loglevel", "error", "-y"]
    cmd += ["-ss", f"{seg.start_s:.6f}", "-i", str(src), "-t", f"{seg.duration_s:.6f}"]
    cmd += ["-map", "0:v:0"]
    if info.audio and opts.audio != "none":
        if opts.audio == "all":
            cmd += ["-map", "0:a"]
        else:
            try:
                idx = max(0, min(int(opts.audio), len(info.audio) - 1))
            except ValueError:
                idx = 0
            cmd += ["-map", f"0:a:{idx}"]
    else:
        cmd += ["-an"]
    cmd += ["-c", "copy", "-avoid_negative_ts", "make_zero"]
    cmd += ["-progress", "pipe:1", "-nostats", str(dst)]
    return cmd


def _segment_cmd(src: Path, seg: Segment, dst: Path, opts: RenderOptions,
                 info: MediaInfo, *, hwaccel: bool) -> list[str]:
    if opts.mode == "copy":
        return _copy_cmd(src, seg, dst, opts, info)

    cmd = [ffmpeg_path(), "-hide_banner", "-nostdin", "-loglevel", "error", "-y"]
    if hwaccel:
        cmd += decode_args(opts.encoder)
    cmd += ["-ss", f"{seg.start_s:.6f}", "-i", str(src), "-t", f"{seg.duration_s:.6f}"]
    cmd += ["-map", "0:v:0"]
    cmd += _audio_args(opts.audio, info)
    cmd += video_args(opts.encoder, preset=opts.preset, quality=opts.quality,
                      max_rate_kbps=opts.max_rate_kbps,
                      hw_frames=hwaccel and bool(decode_args(opts.encoder)))
    cmd += ["-fps_mode", "cfr", "-video_track_timescale", "60000"]
    cmd += ["-progress", "pipe:1", "-nostats", str(dst)]
    return cmd


def _run_with_progress(cmd: list[str], on_seconds: Callable[[float], None],
                       cancel: threading.Event | None) -> None:
    """Run ffmpeg, following -progress on stdout.

    stderr must be drained concurrently. ffmpeg can emit tens of kilobytes on a single
    filter-graph error; once the 64K pipe buffer fills it blocks forever and the
    progress loop waits on a process that will never move.
    """
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1, creationflags=_NO_WINDOW,
    )
    errors: list[str] = []

    def drain_stderr() -> None:
        if proc.stderr is not None:
            for line in proc.stderr:
                errors.append(line)

    pump = threading.Thread(target=drain_stderr, daemon=True)
    pump.start()

    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            if cancel is not None and cancel.is_set():
                proc.kill()
                raise Cancelled("render cancelled")
            line = line.strip()
            if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
                raw = line.split("=", 1)[1]
                if raw.isdigit():
                    # Despite the name, ffmpeg reports out_time_ms in microseconds.
                    on_seconds(int(raw) / 1_000_000)
        proc.wait()
    finally:
        if proc.poll() is None:
            proc.kill()
        pump.join(timeout=5)

    if proc.returncode != 0:
        tail = "".join(errors).strip().splitlines()[-6:]
        raise RenderError("\n".join(tail) or f"ffmpeg exited {proc.returncode}")


def render(
    source: Path,
    segments: Sequence[Segment],
    output: Path,
    options: RenderOptions,
    *,
    progress: ProgressFn | None = None,
    cancel: threading.Event | None = None,
) -> RenderResult:
    """Cut ``segments`` out of ``source`` and write a single montage to ``output``."""
    source, output = Path(source), Path(output)
    if not segments:
        raise RenderError("no segments to render")
    if not source.exists():
        raise RenderError(f"source not found: {source}")

    info = probe(source)
    total_s = sum(s.duration_s for s in segments) or 1.0
    started = time.monotonic()

    def report(done_s: float, msg: str) -> None:
        if progress:
            progress(max(0.0, min(1.0, done_s / total_s)), msg)

    tmp_dir = Path(tempfile.mkdtemp(prefix="supercut_render_"))
    done_per_seg = [0.0] * len(segments)
    lock = threading.Lock()
    report(0.0, f"{len(segments)} segments / {total_s:.1f}s via {options.encoder.name}")

    try:
        parts: list[Path] = []

        def encode_one(i: int) -> Path:
            seg = segments[i]
            dst = tmp_dir / f"seg_{i:04d}.mp4"

            def on_seconds(sec: float) -> None:
                with lock:
                    done_per_seg[i] = min(sec, seg.duration_s)
                    total_done = sum(done_per_seg)
                report(total_done, f"encoding segment {i + 1}/{len(segments)}")

            try:
                _run_with_progress(
                    _segment_cmd(source, seg, dst, options, info, hwaccel=True),
                    on_seconds, cancel,
                )
            except Cancelled:
                raise
            except RenderError:
                # NVDEC cannot handle every stream; fall back to CPU decode but keep
                # the GPU encoder, which is where the real cost is anyway.
                _run_with_progress(
                    _segment_cmd(source, seg, dst, options, info, hwaccel=False),
                    on_seconds, cancel,
                )
            if not dst.exists() or dst.stat().st_size == 0:
                raise RenderError(f"segment {i + 1} produced no output")
            return dst

        workers = max(1, min(options.workers, len(segments)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            parts = list(pool.map(encode_one, range(len(segments))))

        if cancel is not None and cancel.is_set():
            raise Cancelled("render cancelled")

        report(total_s, "joining segments")
        list_file = tmp_dir / "concat.txt"
        list_file.write_text(
            "".join(f"file '{p.as_posix()}'\n" for p in parts), encoding="utf-8"
        )

        output.parent.mkdir(parents=True, exist_ok=True)
        join = subprocess.run(
            [ffmpeg_path(), "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
             "-f", "concat", "-safe", "0", "-i", str(list_file),
             "-c", "copy", "-movflags", "+faststart", str(output)],
            capture_output=True, text=True, creationflags=_NO_WINDOW,
        )
        if join.returncode != 0:
            raise RenderError(f"concat failed:\n{join.stderr.strip()}")

        elapsed = time.monotonic() - started
        report(total_s, "done")
        return RenderResult(
            output=output,
            segments=len(segments),
            content_s=total_s,
            elapsed_s=elapsed,
            encoder=options.encoder.name,
            size_bytes=output.stat().st_size if output.exists() else 0,
            speed_x=(total_s / elapsed) if elapsed > 0 else 0.0,
        )
    finally:
        if not options.keep_temp:
            shutil.rmtree(tmp_dir, ignore_errors=True)
