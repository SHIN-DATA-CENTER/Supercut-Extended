"""Data model mirroring Outplayed's IndexedDB records.

Field semantics were established empirically in Phase 0 (see tools/calibrate.py):

* ``GameEvent.time_ms``  -- milliseconds from the START OF THE MEDIA FILE, not epoch
  and not relative to the match. Verified against 13 montage records and confirmed
  bit-exact against Outplayed's own ``IOPlugin.createMontage`` arguments.
* ``Media.start_s`` / ``end_s`` -- SECONDS (note the unit change from events).
* ``timing.past`` / ``timing.future`` -- per-event-type default pre/post roll. These
  differ by game AND by event type (Apex kill is 30s/10s, CS2 ace is 20s/5s), so they
  must always be read from the data rather than hardcoded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# Event kinds observed across the whole library (4588 events / 226 matches).
KNOWN_EVENT_KINDS = (
    "kill", "knockdown", "knocked_out", "death", "assist",
    "ace", "victory", "respawned", "revived",
)

# Sensible default ordering for UI and for --events presets.
HIGHLIGHT_KINDS = ("kill", "ace", "knockdown", "victory")


def _count_kinds(events) -> dict[str, int]:
    counts: dict[str, int] = {}
    for e in events:
        counts[e.kind] = counts.get(e.kind, 0) + 1
    return counts


@dataclass(frozen=True)
class GameEvent:
    """A single detected in-game moment inside one media file."""

    kind: str
    time_ms: float
    pre_ms: int
    post_ms: int
    counter: str | None = None

    def window(self, pre_ms: float | None = None, post_ms: float | None = None
               ) -> tuple[float, float]:
        """The [start, end] slice this event wants, in ms from the media start."""
        pre = self.pre_ms if pre_ms is None else pre_ms
        post = self.post_ms if post_ms is None else post_ms
        return (self.time_ms - pre, self.time_ms + post)


@dataclass
class Media:
    """One video file belonging to a match."""

    media_id: int | str
    path: Path | None
    url: str
    kind: str
    start_s: float
    end_s: float
    events: list[GameEvent] = field(default_factory=list)
    audio_track_map: list = field(default_factory=list)
    original_path: Path | None = None
    deleted_at_ms: float | None = None

    @property
    def duration_s(self) -> float:
        return max(0.0, self.end_s - self.start_s)

    @property
    def exists(self) -> bool:
        return self.path is not None and self.path.exists()

    @property
    def game_name(self) -> str:
        """Prefer the url, which always names the game.

        The url is ``overwolf://media/videos/Outplayed/<Game>/<session>/<file>.mp4``.
        The on-disk path is less reliable: files moved to Exports or Recently Deleted
        lose the game directory, and would otherwise report "Outplayed".
        """
        marker = "/videos/Outplayed/"
        if marker in self.url:
            tail = self.url.split(marker, 1)[1].split("/")
            if len(tail) >= 2 and tail[0]:
                return tail[0]
        p = self.path or self.original_path
        if p and len(p.parts) >= 3:
            return p.parts[-3]
        return "Unknown"

    def event_counts(self) -> dict[str, int]:
        return _count_kinds(self.events)

    @property
    def is_derived(self) -> bool:
        """True for montage/export outputs rather than raw recordings."""
        name = (self.path.name if self.path else "") or self.url
        return "-montage-" in name or "/Exports/" in self.url.replace("\\", "/")


@dataclass
class Match:
    """A recorded match, as Outplayed stores it in MediaDatabase/matches."""

    match_id: str
    session_id: str
    original_match_id: str
    game_id: int
    index: int
    capture_mode: str
    start_time_ms: float
    end_time_ms: float
    info: dict
    medias: list[Media] = field(default_factory=list)

    @property
    def started_at(self) -> datetime:
        return datetime.fromtimestamp(self.start_time_ms / 1000)

    @property
    def duration_s(self) -> float:
        return max(0.0, (self.end_time_ms - self.start_time_ms) / 1000)

    @property
    def game_name(self) -> str:
        for m in self.medias:
            name = m.game_name
            if name != "Unknown":
                return name
        return f"Game {self.game_id}"

    @property
    def events(self) -> list[GameEvent]:
        return [e for m in self.medias for e in m.events]

    @property
    def playable_medias(self) -> list[Media]:
        return [m for m in self.medias if m.exists and m.events]

    def event_counts(self) -> dict[str, int]:
        return _count_kinds(self.events)

    def label(self) -> str:
        info = self.info or {}
        bits = [self.game_name, self.started_at.strftime("%Y-%m-%d %H:%M")]
        if info.get("map"):
            bits.append(str(info["map"]))
        if info.get("gameMode"):
            bits.append(str(info["gameMode"]))
        return "  ".join(bits)


@dataclass(frozen=True)
class Segment:
    """A merged slice of a source video, in milliseconds from its start."""

    start_ms: float
    end_ms: float

    @property
    def duration_ms(self) -> float:
        return self.end_ms - self.start_ms

    @property
    def start_s(self) -> float:
        return self.start_ms / 1000.0

    @property
    def duration_s(self) -> float:
        return self.duration_ms / 1000.0


@dataclass
class Clip:
    """One piece of the edited timeline: a slice of a source, in the order it plays.

    Deliberately mutable and NOT derived on the fly, unlike Segment. Segments come out
    of build_segments() already sorted and merged, which is right for "just cut my
    kills" but makes hand editing impossible: reordering or trimming one of them would
    be undone the next time anything recomputed. A Clip is the user's copy -- seeded
    from the segments, then owned by the timeline.

    ``source_start_ms``/``source_end_ms`` are offsets into ``source``, so trimming only
    moves these bounds and never touches the recording.
    """

    source: Path
    source_start_ms: float
    source_end_ms: float
    label: str = ""
    fade_in_ms: float = 0.0
    fade_out_ms: float = 0.0
    # Where this came from, so the UI can show "kill at 4:32" and keep selection
    # meaningful after a reorder.
    event_kind: str | None = None
    event_time_ms: float | None = None
    enabled: bool = True

    @property
    def duration_ms(self) -> float:
        return max(0.0, self.source_end_ms - self.source_start_ms)

    @property
    def duration_s(self) -> float:
        return self.duration_ms / 1000.0

    @property
    def start_s(self) -> float:
        return self.source_start_ms / 1000.0

    def as_segment(self) -> Segment:
        return Segment(start_ms=self.source_start_ms, end_ms=self.source_end_ms)


@dataclass(frozen=True)
class Framing:
    """How the source is fitted into the output frame.

    ``crop_*`` removes pixels from each edge of the SOURCE before anything else. It
    exists for recordings where the game rendered 4:3 inside a 16:9 capture: the
    pillarbox is baked into the video, so the only way to fill the frame is to cut the
    bars off and resize what is left.

    ``stretch`` decides what happens when the cropped picture and the output frame
    disagree on shape -- distort to fill, or fit and pad. Distorting is usually wrong,
    which is why it is off by default, but it is exactly what someone who plays 4:3
    stretched wants their montage to look like.
    """

    width: int | None = None          # None keeps whatever the source is
    height: int | None = None
    crop_left: int = 0
    crop_right: int = 0
    crop_top: int = 0
    crop_bottom: int = 0
    stretch: bool = False

    @property
    def crops(self) -> bool:
        return bool(self.crop_left or self.crop_right
                    or self.crop_top or self.crop_bottom)

    @property
    def resizes(self) -> bool:
        return self.width is not None and self.height is not None

    @property
    def active(self) -> bool:
        return self.crops or self.resizes

    def source_rect(self, width: int, height: int) -> tuple[int, int, int, int]:
        """The kept region of a source frame, as (x, y, w, h)."""
        x = max(0, self.crop_left)
        y = max(0, self.crop_top)
        w = max(16, width - x - max(0, self.crop_right))
        h = max(16, height - y - max(0, self.crop_bottom))
        return x, y, w, h

    def output_size(self, width: int, height: int) -> tuple[int, int]:
        if self.resizes:
            return int(self.width), int(self.height)
        _x, _y, w, h = self.source_rect(width, height)
        # Encoders reject odd dimensions on 4:2:0.
        return w - (w % 2), h - (h % 2)


# Common output sizes, plus "leave it alone" as the default.
RESOLUTION_PRESETS: tuple[tuple[str, int | None, int | None], ...] = (
    ("ソースのまま", None, None),
    ("1920 x 1080 (FHD)", 1920, 1080),
    ("2560 x 1440 (QHD)", 2560, 1440),
    ("3840 x 2160 (4K)", 3840, 2160),
    ("1280 x 720 (HD)", 1280, 720),
    ("1080 x 1080 (正方形)", 1080, 1080),
    ("1080 x 1920 (縦)", 1080, 1920),
)


@dataclass
class Bgm:
    """Background music laid under the whole montage."""

    path: Path
    volume: float = 0.25          # 1.0 would drown the game audio
    fade_in_ms: float = 0.0
    fade_out_ms: float = 0.0
    offset_ms: float = 0.0        # skip this far into the music before it starts
    loop: bool = True             # repeat if the montage outlasts the track


@dataclass
class Timeline:
    """The edited sequence: clips in play order, plus an optional music bed."""

    clips: list[Clip] = field(default_factory=list)
    bgm: Bgm | None = None
    # Fades applied to the montage as a whole, on top of any per-clip fades.
    fade_in_ms: float = 0.0
    fade_out_ms: float = 0.0

    @property
    def active(self) -> list[Clip]:
        return [c for c in self.clips if c.enabled and c.duration_ms > 0]

    @property
    def duration_s(self) -> float:
        return sum(c.duration_s for c in self.active)

    def move(self, index: int, to: int) -> None:
        """Reorder one clip, clamping so a drag past either end just parks it there."""
        if not (0 <= index < len(self.clips)):
            return
        to = max(0, min(to, len(self.clips) - 1))
        if to == index:
            return
        self.clips.insert(to, self.clips.pop(index))

    def locate(self, seconds: float) -> tuple[int, float]:
        """Map a position in the finished montage to (clip index, offset into it).

        The sequence only exists as this list, so scrubbing the output has to be
        translated back into "which clip, and how far into its source".
        """
        remaining = max(0.0, seconds)
        active = self.active
        for clip in active:
            if remaining < clip.duration_s or clip is active[-1]:
                return self.clips.index(clip), min(remaining, clip.duration_s)
            remaining -= clip.duration_s
        return -1, 0.0

    def start_of(self, index: int) -> float:
        """Where a clip begins in the finished montage, in seconds."""
        total = 0.0
        for i, clip in enumerate(self.clips):
            if i == index:
                return total
            if clip.enabled and clip.duration_ms > 0:
                total += clip.duration_s
        return total

    def needs_encode(self) -> bool:
        """True when the timeline asks for something a stream copy cannot do.

        Fades and music both require re-encoding: a copy passes packets through
        untouched, so there is nothing to fade or mix into.
        """
        if self.bgm is not None or self.fade_in_ms or self.fade_out_ms:
            return True
        return any(c.fade_in_ms or c.fade_out_ms for c in self.active)
