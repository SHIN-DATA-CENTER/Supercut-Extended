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
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, Sequence

from .encoder import EncoderSpec, decode_args, video_args
from .model import Bgm, Framing, Segment, Timeline
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
    framing: Framing = field(default_factory=Framing)
    fps: float | None = None  # None keeps whatever the source runs at
    audio_volume: float = 1.0  # level for the footage's own audio
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


@dataclass
class RenderJob:
    """One source file and the slices wanted from it.

    Segment times are always relative to their own source, so several jobs can be
    combined into one montage without rebasing anything.
    """

    source: Path
    segments: Sequence[Segment]
    label: str = ""
    output: Path | None = None      # only used by render_each

    @property
    def content_s(self) -> float:
        return sum(s.duration_s for s in self.segments)


def _audio_index(mode: str, count: int) -> int:
    """Clamp a track-index audio mode to something the source actually has."""
    try:
        return max(0, min(int(mode), count - 1))
    except ValueError:
        return 0


def _fade_chain(prefix: str, fade_in_s: float, fade_out_s: float,
                duration_s: float) -> list[str]:
    """`fade`/`afade` pairs for one clip, or [] when neither end fades.

    Times are relative to the clip because -ss/-t make its output start at 0.
    """
    parts = []
    if fade_in_s > 0:
        parts.append(f"{prefix}=t=in:st=0:d={fade_in_s:.3f}")
    if fade_out_s > 0:
        start = max(0.0, duration_s - fade_out_s)
        parts.append(f"{prefix}=t=out:st={start:.3f}:d={fade_out_s:.3f}")
    return parts


def _audio_args(mode: str, info: MediaInfo, *, fade_in_s: float = 0.0,
                fade_out_s: float = 0.0, duration_s: float = 0.0,
                volume: float = 1.0) -> list[str]:
    n = len(info.audio)
    if n == 0 or mode == "none":
        return ["-an"]
    fades = _fade_chain("afade", fade_in_s, fade_out_s, duration_s)
    level = [] if abs(volume - 1.0) < 1e-3 else [f"volume={max(0.0, volume):.3f}"]

    if mode == "all":
        # Every track is mapped straight through, so there is no single stream to
        # hang a filter on. Fading each one would need a filter per track; the
        # montage-level fade in render_timeline covers the common case instead.
        if not level:
            return ["-map", "0:a", "-c:a", "aac", "-b:a", "192k"]
        # A level change does have to reach every track, so build one filter each.
        parts = [f"[0:a:{i}]{level[0]}[a{i}]" for i in range(n)]
        maps = []
        for i in range(n):
            maps += ["-map", f"[a{i}]"]
        return (["-filter_complex", ";".join(parts)] + maps
                + ["-c:a", "aac", "-b:a", "192k"])

    tail = ["-c:a", "aac", "-b:a", "192k", "-ac", "2"]
    if mode == "mix" and n > 1:
        inputs = "".join(f"[0:a:{i}]" for i in range(n))
        chain = f"{inputs}amix=inputs={n}:normalize=0"
        for extra in (level + fades):
            chain += "," + extra
        return ["-filter_complex", f"{chain}[aout]", "-map", "[aout]"] + tail

    index = 0 if mode == "mix" else _audio_index(mode, n)
    args = ["-map", f"0:a:{index}"]
    chain = level + fades
    if chain:
        args += ["-af", ",".join(chain)]
    return args + tail


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
            cmd += ["-map", f"0:a:{_audio_index(opts.audio, len(info.audio))}"]
    else:
        cmd += ["-an"]
    cmd += ["-c", "copy", "-avoid_negative_ts", "make_zero"]
    cmd += ["-progress", "pipe:1", "-nostats", str(dst)]
    return cmd


def _segment_cmd(src: Path, seg: Segment, dst: Path, opts: RenderOptions,
                 info: MediaInfo, *, hwaccel: bool,
                 fade_in_s: float = 0.0, fade_out_s: float = 0.0) -> list[str]:
    if opts.mode == "copy":
        return _copy_cmd(src, seg, dst, opts, info)

    # `fade` is a software filter and cannot touch frames sitting in GPU memory. Rather
    # than shuttle them back and forth with hwdownload/hwupload, decode this clip on the
    # CPU; the encode -- which is the expensive half -- still runs on the GPU.
    video_fades = _fade_chain("fade", fade_in_s, fade_out_s, seg.duration_s)
    framing = _framing_filters(opts, info)
    # crop/pad/setsar have no CUDA equivalent in the bundled build (only scale_cuda
    # does), so any framing work drops decoding back to the CPU. The encode -- the
    # expensive half -- still runs on the GPU.
    if video_fades or framing:
        hwaccel = False

    cmd = [ffmpeg_path(), "-hide_banner", "-nostdin", "-loglevel", "error", "-y"]
    if hwaccel:
        cmd += decode_args(opts.encoder)
    cmd += ["-ss", f"{seg.start_s:.6f}", "-i", str(src), "-t", f"{seg.duration_s:.6f}"]
    cmd += ["-map", "0:v:0"]
    chain = framing + video_fades
    if chain:
        cmd += ["-vf", ",".join(chain)]
    cmd += _audio_args(opts.audio, info, fade_in_s=fade_in_s, fade_out_s=fade_out_s,
                       duration_s=seg.duration_s, volume=opts.audio_volume)
    cmd += video_args(opts.encoder, preset=opts.preset, quality=opts.quality,
                      max_rate_kbps=opts.max_rate_kbps,
                      hw_frames=hwaccel and bool(decode_args(opts.encoder)))
    # An explicit rate goes after the filters so it applies to the output, not the
    # decode. cfr already forces a constant rate; -r decides which one.
    if opts.fps:
        cmd += ["-r", f"{opts.fps:g}"]
    cmd += ["-fps_mode", "cfr", "-video_track_timescale", "60000"]
    cmd += ["-progress", "pipe:1", "-nostats", str(dst)]
    return cmd


def _framing_filters(opts: RenderOptions, info: MediaInfo) -> list[str]:
    """crop/scale/pad chain for the requested framing, in that order."""
    fr = opts.framing
    if not fr.active:
        return []
    parts = []
    if fr.crops:
        x, y, w, h = fr.source_rect(info.width, info.height)
        parts.append(f"crop={w}:{h}:{x}:{y}")
    if fr.resizes:
        w, h = fr.output_size(info.width, info.height)
        if fr.stretch:
            # Ignore aspect ratio on purpose: fill the frame, distortion and all.
            parts.append(f"scale={w}:{h}")
        else:
            parts.append(f"scale={w}:{h}:force_original_aspect_ratio=decrease")
            parts.append(f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black")
    parts.append("setsar=1")
    return parts


# Frame rates a capture is actually trying to run at. A measured average over a
# short clip lands a hair off the nominal rate, and those hairs used to count as a
# difference: a VALORANT match of ten highlight recordings measured 59.98, 59.99 and
# 60.00 and was refused as "streams differ" even though every file is 60 fps.
STANDARD_FPS = (23.976, 24.0, 25.0, 29.97, 30.0, 48.0, 50.0, 59.94, 60.0,
                90.0, 100.0, 120.0, 144.0, 240.0)
FPS_SNAP = 0.05          # tight enough to keep 59.94 and 60 apart


def nominal_fps(fps: float) -> float:
    """The rate a source is nominally at, so measurement noise is not a mismatch.

    The NEAREST standard rate, not the first one in range: 59.98 sits 0.042 from
    59.94 and 0.018 from 60, so scanning in order snapped it to 59.94 and split a
    set of plain 60 fps recordings into two incompatible groups.
    """
    nearest = min(STANDARD_FPS, key=lambda standard: abs(fps - standard))
    return nearest if abs(fps - nearest) <= FPS_SNAP else round(fps, 2)


def _stream_shape(info: MediaInfo, opts: RenderOptions) -> tuple:
    """What stage 1 will emit for this source, as far as concat cares.

    The concat demuxer refuses to join parts whose streams differ. Re-encoding
    normalises the codec and the audio layout, but never the geometry -- the encoder
    inherits width/height/fps from whatever it was fed. A stream copy normalises
    nothing at all, so every property comes straight from the source.
    """
    fps = nominal_fps(opts.fps if opts.fps else info.fps)
    if opts.mode == "copy":
        video: tuple = (info.video_codec, info.pix_fmt, info.width, info.height, fps)
    else:
        # Framing resizes every source to the same frame, so recordings that would
        # otherwise refuse to join become compatible.
        video = (*opts.framing.output_size(info.width, info.height), fps)

    n = len(info.audio)
    if n == 0 or opts.audio == "none":
        audio: tuple = ()
    elif opts.audio == "all":
        # Track count and channel layouts survive; only the codec is normalised.
        audio = tuple((t.codec if opts.mode == "copy" else "aac", t.channels)
                      for t in info.audio)
    elif opts.mode == "copy":
        track = info.audio[_audio_index(opts.audio, n)]
        audio = ((track.codec, track.channels),)
    else:
        audio = (("aac", 2),)   # a single track and mix are both re-encoded to stereo
    return (video, audio)


def _describe_shape(info: MediaInfo, opts: RenderOptions) -> str:
    bits = [f"{info.width}x{info.height}", f"{info.fps:.0f}fps"]
    if opts.mode == "copy":
        bits.append(info.video_codec)
    if opts.audio == "none" or not info.audio:
        bits.append("no audio")
    elif opts.audio == "all":
        bits.append(f"{len(info.audio)} audio tracks")
    else:
        bits.append("1 audio track")
    return " ".join(bits)


def _check_combinable(jobs: Sequence[RenderJob], infos: dict[Path, MediaInfo],
                      opts: RenderOptions) -> None:
    """Refuse up front rather than letting concat fail after a long encode."""
    groups: dict[tuple, list[RenderJob]] = {}
    for job in jobs:
        groups.setdefault(_stream_shape(infos[Path(job.source)], opts), []).append(job)
    if len(groups) <= 1:
        return

    lines = []
    for group in groups.values():
        info = infos[Path(group[0].source)]
        names = ", ".join(Path(j.source).name for j in group)
        lines.append(f"  {_describe_shape(info, opts)}:  {names}")
    raise RenderError(
        "these recordings cannot be joined into one file because their streams "
        "differ:\n" + "\n".join(lines)
        + "\n\nRender them separately, or select recordings that match."
    )


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


def render_many(
    jobs: Sequence[RenderJob],
    output: Path,
    options: RenderOptions,
    *,
    progress: ProgressFn | None = None,
    cancel: threading.Event | None = None,
    clip_fades: Sequence[tuple[float, float]] | None = None,
) -> RenderResult:
    """Cut every job's segments and write them all to one montage at ``output``.

    Segments keep the order the jobs are given in, so the caller decides whether the
    result runs chronologically or some other way.
    """
    output = Path(output)
    jobs = [j for j in jobs if j.segments]
    if not jobs:
        raise RenderError("no segments to render")

    infos: dict[Path, MediaInfo] = {}
    for job in jobs:
        src = Path(job.source)
        if not src.exists():
            raise RenderError(f"source not found: {src}")
        if src not in infos:
            infos[src] = probe(src)

    if len(jobs) > 1:
        _check_combinable(jobs, infos, options)
        # Same nominal rate, different measured ones: each segment would otherwise be
        # encoded at its own source's rate and the parts still would not line up.
        # Pin the whole batch to the shared nominal rate.
        rates = {nominal_fps(infos[Path(j.source)].fps) for j in jobs}
        raw = {round(infos[Path(j.source)].fps, 3) for j in jobs}
        if (options.mode != "copy" and options.fps is None
                and len(rates) == 1 and len(raw) > 1):
            options = replace(options, fps=rates.pop())

    # Flatten to (source, segment, fades) so the whole batch shares one worker pool and
    # one progress total, rather than stalling between files. Order is preserved, which
    # is what lets an edited timeline hand us clips in the arrangement the user chose.
    tasks: list[tuple[Path, Segment, tuple[float, float]]] = []
    for i, job in enumerate(jobs):
        for j, seg in enumerate(job.segments):
            fade = (0.0, 0.0)
            if clip_fades is not None and j == 0 and i < len(clip_fades):
                fade = clip_fades[i]
            tasks.append((Path(job.source), seg, fade))
    total_s = sum(seg.duration_s for _, seg, _f in tasks) or 1.0
    started = time.monotonic()

    def report(done_s: float, msg: str) -> None:
        if progress:
            progress(max(0.0, min(1.0, done_s / total_s)), msg)

    tmp_dir = Path(tempfile.mkdtemp(prefix="supercut_render_"))
    done_per_seg = [0.0] * len(tasks)
    lock = threading.Lock()
    scope = (f"{len(tasks)} segments / {total_s:.1f}s via {options.encoder.name}"
             if len(jobs) == 1 else
             f"{len(jobs)} recordings / {len(tasks)} segments / {total_s:.1f}s "
             f"via {options.encoder.name}")
    report(0.0, scope)

    try:
        parts: list[Path] = []

        def encode_one(i: int) -> Path:
            source, seg, (fade_in, fade_out) = tasks[i]
            info = infos[source]
            dst = tmp_dir / f"seg_{i:04d}.mp4"

            def on_seconds(sec: float) -> None:
                with lock:
                    done_per_seg[i] = min(sec, seg.duration_s)
                    total_done = sum(done_per_seg)
                report(total_done, f"encoding segment {i + 1}/{len(tasks)}")

            try:
                _run_with_progress(
                    _segment_cmd(source, seg, dst, options, info, hwaccel=True,
                                 fade_in_s=fade_in, fade_out_s=fade_out),
                    on_seconds, cancel,
                )
            except Cancelled:
                raise
            except RenderError:
                # NVDEC cannot handle every stream; fall back to CPU decode but keep
                # the GPU encoder, which is where the real cost is anyway.
                _run_with_progress(
                    _segment_cmd(source, seg, dst, options, info, hwaccel=False,
                                 fade_in_s=fade_in, fade_out_s=fade_out),
                    on_seconds, cancel,
                )
            if not dst.exists() or dst.stat().st_size == 0:
                raise RenderError(f"segment {i + 1} produced no output")
            return dst

        workers = max(1, min(options.workers, len(tasks)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            parts = list(pool.map(encode_one, range(len(tasks))))

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
            segments=len(tasks),
            content_s=total_s,
            elapsed_s=elapsed,
            encoder=options.encoder.name,
            size_bytes=output.stat().st_size if output.exists() else 0,
            speed_x=(total_s / elapsed) if elapsed > 0 else 0.0,
        )
    finally:
        if not options.keep_temp:
            shutil.rmtree(tmp_dir, ignore_errors=True)


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
    return render_many([RenderJob(Path(source), segments)], output, options,
                       progress=progress, cancel=cancel)


def _mix_bgm(video: Path, bgm: Bgm, output: Path, opts: RenderOptions,
             duration_s: float, cancel: threading.Event | None) -> None:
    """Lay music under a finished montage, keeping the video stream untouched.

    The video is copied rather than re-encoded: only the audio graph changes, so there
    is no reason to pay for a second video pass (and no reason to lose quality to one).
    """
    music = [ffmpeg_path(), "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
             "-i", str(video)]
    if bgm.loop:
        music += ["-stream_loop", "-1"]
    if bgm.offset_ms > 0:
        music += ["-ss", f"{bgm.offset_ms / 1000.0:.3f}"]
    music += ["-i", str(bgm.path)]

    chain = [f"volume={max(0.0, bgm.volume):.3f}"]
    chain += _fade_chain("afade", bgm.fade_in_ms / 1000.0, bgm.fade_out_ms / 1000.0,
                         duration_s)
    # `duration=first` ends the mix with the montage, which matters when the music is
    # looping and would otherwise run forever.
    graph = (f"[1:a]{','.join(chain)}[bg];"
             f"[0:a][bg]amix=inputs=2:duration=first:normalize=0[aout]")

    music += ["-filter_complex", graph, "-map", "0:v:0", "-map", "[aout]",
              "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ac", "2",
              "-progress", "pipe:1", "-nostats", str(output)]
    _run_with_progress(music, lambda _s: None, cancel)
    if not output.exists() or output.stat().st_size == 0:
        raise RenderError("mixing the background music produced no output")


def render_timeline(
    timeline: Timeline,
    output: Path,
    options: RenderOptions,
    *,
    progress: ProgressFn | None = None,
    cancel: threading.Event | None = None,
) -> RenderResult:
    """Render an edited timeline: clips in their own order, with fades and music.

    Unlike render_many(), the clip order is whatever the user arranged -- clips are not
    sorted or merged, and two clips may come from the same source or overlap in it.
    """
    clips = timeline.active
    if not clips:
        raise RenderError("no clips to render")
    output = Path(output)

    opts = options
    # The timeline owns the footage level; carry it into the options the segment
    # encoder actually reads.
    if abs(timeline.video_volume - opts.audio_volume) > 1e-3:
        opts = replace(opts, audio_volume=timeline.video_volume)
    if timeline.needs_encode() and opts.mode == "copy":
        # Fades and music have nothing to act on in a stream copy. Re-encoding is the
        # only way to honour them, so switch rather than silently dropping the effects.
        opts = replace(opts, mode="encode")

    jobs = [RenderJob(source=c.source, segments=[c.as_segment()], label=c.label)
            for c in clips]
    fades = [(c.fade_in_ms / 1000.0, c.fade_out_ms / 1000.0) for c in clips]

    # The montage-level fade rides on the first and last clip.
    if timeline.fade_in_ms:
        fades[0] = (max(fades[0][0], timeline.fade_in_ms / 1000.0), fades[0][1])
    if timeline.fade_out_ms:
        fades[-1] = (fades[-1][0], max(fades[-1][1], timeline.fade_out_ms / 1000.0))

    stage_out = output
    tmp_mix: Path | None = None
    if timeline.bgm is not None:
        tmp_mix = output.with_name(output.stem + "-nomusic" + output.suffix)
        stage_out = tmp_mix

    # Reserve the tail of the progress bar for the music pass.
    span = 0.85 if timeline.bgm is not None else 1.0

    def stage_progress(frac: float, msg: str) -> None:
        if progress:
            progress(frac * span, msg)

    result = render_many(jobs, stage_out, opts, progress=stage_progress,
                         cancel=cancel, clip_fades=fades)

    if timeline.bgm is not None and tmp_mix is not None:
        if progress:
            progress(span, "mixing music")
        try:
            _mix_bgm(tmp_mix, timeline.bgm, output, opts, result.content_s, cancel)
        finally:
            tmp_mix.unlink(missing_ok=True)
        if progress:
            progress(1.0, "done")
        result = replace(result, output=output,
                         size_bytes=output.stat().st_size if output.exists() else 0)
    return result


def render_each(
    jobs: Sequence[RenderJob],
    options: RenderOptions,
    *,
    progress: ProgressFn | None = None,
    cancel: threading.Event | None = None,
    on_result: Callable[[RenderResult], None] | None = None,
) -> list[RenderResult]:
    """Render every job to its own ``job.output``, one after another.

    Progress is reported as a single fraction across the whole batch, weighted by how
    much footage each job contributes, so the bar does not restart per file. Jobs run
    sequentially: each one already saturates the encoder with its own worker pool.
    """
    jobs = [j for j in jobs if j.segments]
    if not jobs:
        raise RenderError("no segments to render")
    missing = [j for j in jobs if j.output is None]
    if missing:
        raise RenderError("render_each needs an output path on every job")

    weights = [j.content_s or 1.0 for j in jobs]
    grand = sum(weights)
    results: list[RenderResult] = []
    done_before = 0.0

    for i, job in enumerate(jobs):
        def sub(frac: float, msg: str, i=i, base=done_before) -> None:
            if progress:
                progress(min(1.0, (base + frac * weights[i]) / grand),
                         f"[{i + 1}/{len(jobs)}] {msg}")

        results.append(render_many([job], job.output, options,
                                   progress=sub, cancel=cancel))
        if on_result:
            on_result(results[-1])
        done_before += weights[i]

    return results
