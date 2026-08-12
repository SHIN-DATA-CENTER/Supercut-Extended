"""Encoder selection and ffmpeg argument construction.

This is the whole point of the tool. Outplayed's Supercut path hands the job to
``outplayed-plugin-io.dll``, which encodes with libx264 on the CPU -- measured at about
1.0x realtime on this machine, and observed timing out after 5 minutes and emitting a
truncated file. We do the identical cut list on NVENC instead.

Two details that cost real time if you get them wrong:

* Availability cannot be read off ``ffmpeg -encoders``. An RTX 3070 lists av1_nvenc but
  cannot encode AV1 (Ampere has no AV1 encoder; OBS logs ``AV1 supported: false``). The
  only reliable test is to actually encode a frame, which is what probing does here.
* Outplayed's own output lands in ``High 4:4:4 Predictive`` because it never sets a
  pixel format. 4:4:4 is slower, larger and less compatible, so we pin yuv420p.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Sequence

from .probe import ffmpeg_path, run

NVENC_PRESETS = ("p1", "p2", "p3", "p4", "p5", "p6", "p7")
X264_PRESETS = ("ultrafast", "veryfast", "faster", "fast", "medium", "slow")

# CQ/QP/CRF-style quality values, all on the same "lower is better, bigger file" scale
# ffmpeg uses for -cq/-global_quality/-qp_i/-crf. One shared scale is enough because
# every branch in video_args() applies the number without per-vendor rescaling.
QUALITY_TIERS = (18, 21, 23, 26, 30)
DEFAULT_QUALITY = 23


@dataclass(frozen=True)
class EncoderSpec:
    name: str
    label: str
    codec: str
    hardware: bool

    @property
    def presets(self) -> tuple[str, ...]:
        return NVENC_PRESETS if self.hardware else X264_PRESETS

    @property
    def default_preset(self) -> str:
        # p4 measured 4.5x realtime on an RTX 3070 vs 3.3x at p5 and 2.1x at p7, for
        # nearly the same bitrate (17.0 / 16.8 / 16.6 Mbps at cq23) -- the slower
        # presets buy almost no efficiency here, so p4 is the sensible default.
        return "p4" if self.hardware else "veryfast"


CANDIDATES = (
    EncoderSpec("h264_nvenc", "NVENC H.264 (GPU)", "h264", True),
    EncoderSpec("hevc_nvenc", "NVENC HEVC (GPU)", "hevc", True),
    EncoderSpec("h264_amf", "AMD AMF H.264 (GPU)", "h264", True),
    EncoderSpec("hevc_amf", "AMD AMF HEVC (GPU)", "hevc", True),
    EncoderSpec("h264_qsv", "Intel QSV H.264 (GPU)", "h264", True),
    EncoderSpec("hevc_qsv", "Intel QSV HEVC (GPU)", "hevc", True),
    EncoderSpec("libx264", "libx264 (CPU fallback)", "h264", False),
    EncoderSpec("libx265", "libx265 (CPU fallback)", "hevc", False),
)


def _encoder_works(name: str) -> bool:
    """Actually encode a few frames -- listing an encoder does not mean it runs."""
    res = run([
        ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-nostdin",
        "-f", "lavfi", "-i", "color=black:s=256x144:r=30",
        "-frames:v", "3", "-c:v", name, "-f", "null", "-",
    ])
    return res.returncode == 0


@lru_cache(maxsize=1)
def available_encoders() -> tuple[EncoderSpec, ...]:
    """Encoders this machine can genuinely use, best first."""
    return tuple(spec for spec in CANDIDATES if _encoder_works(spec.name))


def group_by_engine(specs: Sequence[EncoderSpec]) -> dict[tuple[bool, str], EncoderSpec]:
    """Best usable encoder per (hardware, codec) pair, for a CPU/GPU x H.264/HEVC picker.

    ``specs`` is expected in CANDIDATES priority order, so on a machine with more than
    one GPU (rare, but AMF+QSV laptops exist) the first -- best -- hit for a given
    codec wins and later ones are ignored via ``setdefault``.
    """
    out: dict[tuple[bool, str], EncoderSpec] = {}
    for spec in specs:
        out.setdefault((spec.hardware, spec.codec), spec)
    return out


def vendor_label(name: str) -> str:
    """Short GPU vendor name for a hardware encoder, for display in the UI."""
    if name.endswith("_nvenc"):
        return "NVIDIA"
    if name.endswith("_amf"):
        return "AMD"
    if name.endswith("_qsv"):
        return "Intel"
    return ""


# --- speed model -----------------------------------------------------------
#
# Measured with tools/bench_presets.py on an RTX 3070 / i5-12600KF at 1080p60, then
# normalised so each encoder's default preset and DEFAULT_QUALITY are exactly 1.00.
# Only ratios are stored: BASE_RATES below carries the absolute end-to-end speed,
# which includes per-segment and muxing overhead a single synthetic clip cannot show.
#
# The headline result is that the preset dominates and the quality barely matters on
# a GPU: NVENC is fixed-function, so changing the rate-control target does not change
# how much work the silicon does (measured 0.98-1.01 across cq18..cq30, i.e. noise).
# On the CPU the CRF genuinely changes the bit count and so the encode time.
_PRESET_FACTORS: dict[str, dict[str, float]] = {
    "h264_nvenc": {"p1": 1.56, "p2": 1.53, "p3": 1.33, "p4": 1.00,
                   "p5": 0.62, "p6": 0.58, "p7": 0.46},
    "hevc_nvenc": {"p1": 1.17, "p2": 1.17, "p3": 1.17, "p4": 1.00,
                   "p5": 0.78, "p6": 0.46, "p7": 0.42},
    "libx264": {"ultrafast": 1.99, "veryfast": 1.00, "faster": 0.64,
                "fast": 0.54, "medium": 0.41, "slow": 0.34},
    "libx265": {"ultrafast": 1.68, "veryfast": 1.00, "faster": 0.93,
                "fast": 0.68, "medium": 0.68, "slow": 0.26},
}

_QUALITY_FACTORS: dict[str, dict[int, float]] = {
    # Flat within measurement noise -- kept explicit so it is clear this was measured
    # rather than forgotten.
    "h264_nvenc": {18: 1.00, 21: 1.00, 23: 1.00, 26: 1.00, 30: 1.00},
    "hevc_nvenc": {18: 1.00, 21: 1.00, 23: 1.00, 26: 1.00, 30: 1.00},
    "libx264": {18: 0.91, 21: 0.95, 23: 1.00, 26: 1.05, 30: 1.15},
    "libx265": {18: 0.82, 21: 0.92, 23: 1.00, 26: 1.11, 30: 1.23},
}

# End-to-end realtime multipliers at each encoder's default preset and quality,
# measured on a real 27-segment / 55.5s job at 1080p60 rather than on a single clip.
# That distinction matters: every segment is cut by its own ffmpeg invocation, so a
# montage made of many short clips pays a fixed startup per segment on top of the
# encoding. Anchoring on a synthetic single-clip run gave 4.3x for h264_nvenc where
# the real pipeline delivers 2.6x, and an absurd 1.0x for libx264 that was really 2.5x.
#
# A single scalar cannot capture the per-segment cost exactly: jobs built from long
# clips run faster than this and jobs of very short clips slower. It is an estimate.
BASE_RATES: dict[str, float] = {
    "h264_nvenc": 2.6,
    "hevc_nvenc": 2.8,
    "libx264": 2.5,
    "libx265": 1.0,
}
COPY_RATE = 120.0          # stream copy does no encoding at all


def _interpolate(table: dict[int, float], value: int) -> float:
    """Look `value` up in a quality table, interpolating between the measured tiers.

    The GUI only offers the tiers, but --quality on the CLI takes any number.
    """
    if not table:
        return 1.0
    if value in table:
        return table[value]
    keys = sorted(table)
    if value <= keys[0]:
        return table[keys[0]]
    if value >= keys[-1]:
        return table[keys[-1]]
    lo = max(k for k in keys if k < value)
    hi = min(k for k in keys if k > value)
    span = hi - lo
    return table[lo] + (table[hi] - table[lo]) * ((value - lo) / span)


def speed_factor(spec: EncoderSpec, *, preset: str, quality: int) -> float:
    """How much faster/slower than this encoder's defaults these settings run.

    Preset and quality are treated as independent and multiplied. That held to within
    a few percent for NVENC and libx265; libx264 was the outlier at about -20% at the
    extreme corners (ultrafast+cq30, slow+cq18), which is acceptable for an estimate
    that is already labelled approximate.

    Encoders whose video_args() ignore the preset (AMF, QSV) fall back to 1.0.
    """
    preset_factor = _PRESET_FACTORS.get(spec.name, {}).get(preset, 1.0)
    quality_factor = _interpolate(_QUALITY_FACTORS.get(spec.name, {}), quality)
    return preset_factor * quality_factor


# Share of a segment's time that does NOT scale with the output frame -- decoding the
# source, plus ffmpeg's own start-up. Fitted to measurements on a 1080p60 source
# rendered at 1440x1080, 1920x1080 and 3840x2160: the model predicted 1.22x / 1.00x /
# 0.32x against measured 1.19x / 0.98x / 0.30x.
_FIXED_SHARE = 0.29


def framing_factor(framing, width: int, height: int) -> float:
    """How much framing changes the render speed, from the output pixel count.

    Encoding dominates and scales with area, so shrinking the frame -- which is what
    cropping black bars does -- actually makes a render *faster*. Upscaling is the
    expensive direction. The CPU decode that framing forces is real but minor; it is
    part of the fixed share below.
    """
    if framing is None or not framing.active or width <= 0 or height <= 0:
        return 1.0
    out_w, out_h = framing.output_size(width, height)
    ratio = (out_w * out_h) / float(width * height)
    return 1.0 / max(0.05, _FIXED_SHARE + (1.0 - _FIXED_SHARE) * ratio)


def estimate_rate(spec: EncoderSpec | None, *, preset: str, quality: int,
                  mode: str = "encode", framing=None,
                  source_size: tuple[int, int] | None = None) -> float:
    """Rough realtime multiplier for a render, for showing an ETA.

    Never a guarantee -- it ignores what else the machine is doing and the fixed cost
    of starting ffmpeg once per segment. Framing is accounted for only when the source
    size is known, since the cost depends on the ratio between the two.
    """
    if mode == "copy":
        return COPY_RATE
    if spec is None:
        return BASE_RATES["h264_nvenc"]
    base = BASE_RATES.get(spec.name)
    if base is None:
        # AMF/QSV are not measured here; assume they land near the NVENC figures, and
        # treat an unknown CPU encoder as the slower HEVC case.
        base = 2.6 if spec.hardware else (1.0 if spec.codec == "hevc" else 2.5)
    rate = base * speed_factor(spec, preset=preset, quality=quality)
    if source_size:
        rate *= framing_factor(framing, *source_size)
    return max(0.01, rate)


def pick_encoder(preferred: str | None = None) -> EncoderSpec:
    """Choose an encoder, preferring hardware, falling back to libx264 with a warning."""
    found = available_encoders()
    if not found:
        raise RuntimeError("no usable video encoder found (is ffmpeg installed?)")
    if preferred:
        for spec in found:
            if spec.name == preferred:
                return spec
        raise RuntimeError(
            f"encoder {preferred!r} is not usable here. Available: "
            + ", ".join(s.name for s in found)
        )
    return found[0]


def video_args(spec: EncoderSpec, *, preset: str, quality: int, max_rate_kbps: int,
               hw_frames: bool = False) -> list[str]:
    """Encoder settings for one segment.

    Quality-targeted rather than bitrate-targeted so short action-heavy clips keep
    their detail, with a ceiling so a busy scene cannot balloon the file.

    ``hw_frames`` means decoded frames stay in GPU memory. In that case ``-pix_fmt``
    must NOT be set: it forces a software scale filter that cannot accept cuda frames,
    and ffmpeg dies with "Impossible to convert between the formats". The frames are
    already NV12, so NVENC writes yuv420p regardless.
    """
    args = ["-c:v", spec.name]
    if spec.name.endswith("_nvenc"):
        # B-frames, B-pyramid and spatial AQ measured within noise of plain p4
        # (4.6x vs 4.5x realtime) while improving compression, so they stay on.
        # rc-lookahead cost ~5% for little gain here and is left off.
        args += [
            "-preset", preset,
            "-rc", "vbr",
            "-cq", str(quality),
            "-b:v", "0",
            "-maxrate", f"{max_rate_kbps}k",
            "-bufsize", f"{max_rate_kbps * 2}k",
            "-bf", "3",
            "-b_ref_mode", "middle",
            "-spatial-aq", "1",
            "-aq-strength", "8",
        ]
        args += ["-profile:v", "main" if spec.codec == "hevc" else "high"]
    elif spec.name.endswith("_amf"):
        args += ["-quality", "quality", "-rc", "vbr_peak",
                 "-qp_i", str(quality), "-qp_p", str(quality),
                 "-maxrate", f"{max_rate_kbps}k"]
    elif spec.name.endswith("_qsv"):
        args += ["-preset", "veryfast", "-global_quality", str(quality),
                 "-maxrate", f"{max_rate_kbps}k"]
    else:
        args += ["-preset", preset, "-crf", str(quality),
                 "-maxrate", f"{max_rate_kbps}k", "-bufsize", f"{max_rate_kbps * 2}k",
                 "-profile:v", "main" if spec.codec == "hevc" else "high"]

    # Explicit 4:2:0 -- this is the bug in Outplayed's own output. Only meaningful on
    # the software path; GPU frames are already NV12 and adding it breaks the graph.
    if not hw_frames:
        args += ["-pix_fmt", "yuv420p"]
    return args


def decode_args(spec: EncoderSpec) -> list[str]:
    """Input-side hardware decode flags, so frames never leave the GPU."""
    if spec.name.endswith("_nvenc"):
        return ["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"]
    if spec.name.endswith("_qsv"):
        return ["-hwaccel", "qsv", "-hwaccel_output_format", "qsv"]
    if spec.name.endswith("_amf"):
        return ["-hwaccel", "d3d11va"]
    return []


def describe() -> str:
    found = available_encoders()
    if not found:
        return "no usable encoders"
    lines = []
    for spec in found:
        mark = "GPU" if spec.hardware else "CPU"
        lines.append(f"  [{mark}] {spec.name:<12} {spec.label}")
    return "\n".join(lines)
