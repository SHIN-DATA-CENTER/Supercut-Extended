"""ffprobe/ffmpeg discovery and media inspection."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

# Keep console windows from flashing when the GUI shells out.
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


class ProbeError(RuntimeError):
    pass


def app_dir() -> Path:
    """Directory the app runs from -- next to the .exe once frozen."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def _find_tool(name: str, env_var: str) -> str:
    """Locate ffmpeg/ffprobe.

    Checked in order: explicit override, then alongside the executable (so the exe can
    be made portable by dropping the binaries next to it), then PATH.
    """
    override = os.environ.get(env_var)
    if override and Path(override).exists():
        return override

    exe_name = f"{name}.exe" if os.name == "nt" else name
    base = app_dir()
    for candidate in (base / exe_name, base / "ffmpeg" / exe_name,
                      base / "bin" / exe_name):
        if candidate.exists():
            return str(candidate)

    found = shutil.which(name)
    if found:
        return found

    raise ProbeError(
        f"{name} not found.\n\n"
        f"Install ffmpeg and make sure it is on PATH, or put {exe_name} next to "
        f"the application, or set {env_var} to its full path."
    )


@lru_cache(maxsize=1)
def ffmpeg_path() -> str:
    return _find_tool("ffmpeg", "SUPERCUT_FFMPEG")


@lru_cache(maxsize=1)
def ffprobe_path() -> str:
    return _find_tool("ffprobe", "SUPERCUT_FFPROBE")


def run(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(
        args, capture_output=True, text=True, creationflags=_NO_WINDOW, **kwargs
    )


@dataclass
class AudioTrack:
    index: int
    codec: str
    channels: int
    language: str | None = None
    title: str | None = None

    def label(self) -> str:
        name = self.title or self.language or f"Track {self.index + 1}"
        return f"{name} ({self.codec}, {self.channels}ch)"


@dataclass
class MediaInfo:
    path: Path
    duration_s: float
    width: int
    height: int
    fps: float
    video_codec: str
    pix_fmt: str
    bit_rate: int
    audio: list[AudioTrack] = field(default_factory=list)

    @property
    def duration_ms(self) -> float:
        return self.duration_s * 1000.0


def _fraction(value: str | None) -> float:
    if not value or "/" not in value:
        try:
            return float(value or 0)
        except ValueError:
            return 0.0
    num, den = value.split("/", 1)
    try:
        d = float(den)
        return float(num) / d if d else 0.0
    except ValueError:
        return 0.0


def probe(path: Path) -> MediaInfo:
    """Read the real duration/geometry. Never trust filenames or DB fields for this."""
    path = Path(path)
    if not path.exists():
        raise ProbeError(f"media not found: {path}")

    res = run([
        ffprobe_path(), "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ])
    if res.returncode != 0:
        raise ProbeError(f"ffprobe failed for {path}:\n{res.stderr.strip()}")

    data = json.loads(res.stdout)
    fmt = data.get("format") or {}
    streams = data.get("streams") or []

    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise ProbeError(f"no video stream in {path}")

    audio: list[AudioTrack] = []
    for i, s in enumerate(x for x in streams if x.get("codec_type") == "audio"):
        tags = s.get("tags") or {}
        audio.append(AudioTrack(
            index=i,
            codec=str(s.get("codec_name") or "?"),
            channels=int(s.get("channels") or 0),
            language=tags.get("language"),
            title=tags.get("title") or tags.get("handler_name"),
        ))

    duration = float(fmt.get("duration") or video.get("duration") or 0.0)
    fps = _fraction(video.get("avg_frame_rate")) or _fraction(video.get("r_frame_rate"))

    return MediaInfo(
        path=path,
        duration_s=duration,
        width=int(video.get("width") or 0),
        height=int(video.get("height") or 0),
        fps=fps,
        video_codec=str(video.get("codec_name") or "?"),
        pix_fmt=str(video.get("pix_fmt") or "?"),
        bit_rate=int(fmt.get("bit_rate") or 0),
        audio=audio,
    )
