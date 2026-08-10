"""Read Outplayed's match library out of its IndexedDB.

Outplayed stores kill/knockdown/death events ONLY here -- there are no sidecar files
next to the videos, so this is the single source of truth. The database stays open for
the whole Outplayed session, so we snapshot the directory first (see winio) and parse
the copy offline. That keeps us read-only with respect to the live app.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Iterable

from .model import GameEvent, Match, Media
from .winio import snapshot_dir

OUTPLAYED_EXT_ID = "cghphpbjeabdkomiphingnegihoigeggcfphdofo"
MATCH_DB = "MediaDatabase"
MATCH_STORE = "matches"


class LibraryError(RuntimeError):
    pass


def default_indexeddb_path() -> Path:
    local = os.environ.get("LOCALAPPDATA", "")
    return (
        Path(local) / "Overwolf" / "CefBrowserCache" / "Default" / "IndexedDB"
        / f"overwolf-extension_{OUTPLAYED_EXT_ID}_0.indexeddb.leveldb"
    )


def _as_path(value) -> Path | None:
    if isinstance(value, str) and value.strip():
        return Path(value)
    return None


def _plain(value):
    """Coerce ccl's V8/Blink wrappers into plain Python."""
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    for attr in ("value", "timestamp"):
        if hasattr(value, attr):
            try:
                return _plain(getattr(value, attr))
            except Exception:
                pass
    return value


def _build_event(raw: dict) -> GameEvent | None:
    kind = raw.get("type") or raw.get("eventId")
    time_ms = raw.get("time", raw.get("eventTimeMs"))
    if not kind or time_ms is None:
        return None
    timing = raw.get("timing") or {}
    pre = raw.get("preMs", timing.get("past", 5000))
    post = raw.get("postMs", timing.get("future", 2000))
    return GameEvent(
        kind=str(kind),
        time_ms=float(time_ms),
        pre_ms=int(pre),
        post_ms=int(post),
        counter=raw.get("data"),
    )


def _build_media(raw: dict) -> Media:
    events = []
    for e in raw.get("events") or []:
        if isinstance(e, dict):
            ev = _build_event(e)
            if ev is not None:
                events.append(ev)
    events.sort(key=lambda e: e.time_ms)

    path = _as_path(raw.get("path"))
    original = _as_path(raw.get("originalPath"))
    # A media moved to "Recently Deleted" keeps its original location around; prefer
    # whichever actually exists on disk.
    if path is not None and not path.exists() and original is not None and original.exists():
        path, original = original, path

    # Plain recordings use a small integer id; montage-derived entries use a hex uuid.
    raw_id = raw.get("id")
    try:
        media_id: int | str = int(raw_id)
    except (TypeError, ValueError):
        media_id = str(raw_id or "")

    return Media(
        media_id=media_id,
        path=path,
        url=str(raw.get("url") or ""),
        kind=str(raw.get("type") or ""),
        start_s=float(raw.get("startTime") or 0.0),
        end_s=float(raw.get("endTime") or 0.0),
        events=events,
        audio_track_map=list(raw.get("audioTrackMap") or []),
        original_path=original,
        deleted_at_ms=raw.get("markedForDeletionAt"),
    )


def _build_match(raw: dict) -> Match | None:
    if not raw.get("id"):
        return None
    medias = [_build_media(m) for m in (raw.get("medias") or []) if isinstance(m, dict)]
    return Match(
        match_id=str(raw["id"]),
        session_id=str(raw.get("sessionId") or ""),
        original_match_id=str(raw.get("originalMatchId") or ""),
        game_id=int(raw.get("gameId") or 0),
        index=int(raw.get("index") or 0),
        capture_mode=str(raw.get("captureMode") or ""),
        start_time_ms=float(raw.get("startTime") or 0.0),
        end_time_ms=float(raw.get("endTime") or 0.0),
        info=dict(raw.get("info") or {}),
        medias=medias,
    )


def read_matches(db_path: Path | None = None) -> list[Match]:
    """Return every match Outplayed knows about, newest first.

    LevelDB retains superseded versions and tombstones, so records are folded by id
    with last-write-wins before being converted.
    """
    try:
        from ccl_chromium_reader import ccl_chromium_indexeddb
    except ImportError as exc:  # pragma: no cover
        raise LibraryError(
            "ccl_chromium_reader is required. Install with:\n"
            "  pip install \"ccl_chromium_reader @ "
            "git+https://github.com/cclgroupltd/ccl_chromium_reader.git\""
        ) from exc

    src = Path(db_path) if db_path else default_indexeddb_path()
    if not src.exists():
        raise LibraryError(f"Outplayed IndexedDB not found: {src}")

    tmp = Path(tempfile.mkdtemp(prefix="supercut_idb_"))
    try:
        work = snapshot_dir(src, tmp / "idb.leveldb")
        wrapper = ccl_chromium_indexeddb.WrappedIndexDB(work)

        latest: dict[str, dict] = {}
        for db_info in wrapper.database_ids:
            db = wrapper[db_info.dbid_no]
            if db.name != MATCH_DB or MATCH_STORE not in set(db.object_store_names):
                continue
            for rec in db[MATCH_STORE].iterate_records():
                value = _plain(rec.value)
                if isinstance(value, dict) and value.get("id"):
                    latest[str(value["id"])] = value

        matches = [m for m in (_build_match(v) for v in latest.values()) if m]
        matches.sort(key=lambda m: m.start_time_ms, reverse=True)
        return matches
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def matches_with_highlights(matches: Iterable[Match]) -> list[Match]:
    """Only matches that have both detected events and a video still on disk."""
    return [m for m in matches if m.playable_medias]
