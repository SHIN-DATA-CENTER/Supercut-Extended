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

from .probe import ffmpeg_path, run

NVENC_PRESETS = ("p1", "p2", "p3", "p4", "p5", "p6", "p7")
X264_PRESETS = ("ultrafast", "veryfast", "faster", "fast", "medium", "slow")


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
    EncoderSpec("h264_qsv", "Intel QSV H.264 (GPU)", "h264", True),
    EncoderSpec("libx264", "libx264 (CPU fallback)", "h264", False),
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
    elif spec.name == "h264_amf":
        args += ["-quality", "quality", "-rc", "vbr_peak",
                 "-qp_i", str(quality), "-qp_p", str(quality),
                 "-maxrate", f"{max_rate_kbps}k"]
    elif spec.name == "h264_qsv":
        args += ["-preset", "veryfast", "-global_quality", str(quality),
                 "-maxrate", f"{max_rate_kbps}k"]
    else:
        args += ["-preset", preset, "-crf", str(quality),
                 "-maxrate", f"{max_rate_kbps}k", "-bufsize", f"{max_rate_kbps * 2}k",
                 "-profile:v", "high"]

    # Explicit 4:2:0 -- this is the bug in Outplayed's own output. Only meaningful on
    # the software path; GPU frames are already NV12 and adding it breaks the graph.
    if not hw_frames:
        args += ["-pix_fmt", "yuv420p"]
    return args


def decode_args(spec: EncoderSpec) -> list[str]:
    """Input-side hardware decode flags, so frames never leave the GPU."""
    if spec.name.endswith("_nvenc"):
        return ["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"]
    if spec.name == "h264_qsv":
        return ["-hwaccel", "qsv", "-hwaccel_output_format", "qsv"]
    if spec.name == "h264_amf":
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
