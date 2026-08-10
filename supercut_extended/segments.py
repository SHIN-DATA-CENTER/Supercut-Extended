"""Turn detected events into the merged segment list Outplayed would produce.

Verified in Phase 0: applying window -> sort -> merge to the events of montage
``640f8811`` reproduces the exact ``segmentsJson`` Outplayed passed to
IOPlugin.createMontage, to 0.0000 ms across all three segments.
"""

from __future__ import annotations

from typing import Iterable, Sequence

from .model import GameEvent, Segment


def build_segments(
    events: Iterable[GameEvent],
    *,
    kinds: Sequence[str] | None = None,
    pre_ms: float | None = None,
    post_ms: float | None = None,
    duration_ms: float | None = None,
    gap_ms: float = 0.0,
) -> list[Segment]:
    """Merge each event's [t-pre, t+post] window into non-overlapping segments.

    Args:
        kinds: keep only these event kinds; ``None`` keeps everything.
        pre_ms/post_ms: override the per-event defaults from Outplayed. ``None`` uses
            each event's own ``timing.past``/``timing.future``, which vary by game and
            by event type.
        duration_ms: clamp to the media length so we never seek past the end.
        gap_ms: also merge segments separated by less than this, which avoids very
            short cuts between two nearby kills.
    """
    wanted = set(kinds) if kinds else None
    windows: list[tuple[float, float]] = []
    for ev in events:
        if wanted is not None and ev.kind not in wanted:
            continue
        start, end = ev.window(pre_ms, post_ms)
        if duration_ms is not None:
            start = max(0.0, min(start, duration_ms))
            end = max(0.0, min(end, duration_ms))
        else:
            start = max(0.0, start)
        if end > start:
            windows.append((start, end))

    if not windows:
        return []

    windows.sort()
    merged: list[list[float]] = [list(windows[0])]
    for start, end in windows[1:]:
        if start - merged[-1][1] <= gap_ms:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])

    return [Segment(start_ms=a, end_ms=b) for a, b in merged]


def total_duration_s(segments: Sequence[Segment]) -> float:
    return sum(s.duration_s for s in segments)
