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
