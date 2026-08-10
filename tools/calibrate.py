"""Phase 0 calibration: prove that eventTimeMs is an offset into the media file.

Everything downstream depends on this. If eventTimeMs were an epoch timestamp, or an
offset into the match rather than the file, every segment we cut would be wrong.

The check: for each montage record, ffprobe the referenced media and assert that every
derived segment lands inside [0, duration]. An epoch value would overflow by ~56 years,
so this discriminates the two hypotheses decisively.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

DUMP = Path(__file__).resolve().parent.parent / "idb_dump.json"


def probe_duration(path: Path) -> float | None:
    if not path.exists():
        return None
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True,
    )
    try:
        return float(out.stdout.strip())
    except ValueError:
        return None


def main() -> int:
    data = json.loads(DUMP.read_text(encoding="utf-8"))
    raw = data["outplayed-idb/match-montage"]

    # LevelDB keeps superseded versions and tombstones; keep the last write per id.
    montages: dict[str, dict] = {}
    for rec in raw:
        if isinstance(rec, dict) and rec.get("id"):
            montages[rec["id"]] = rec
    montages = list(montages.values())
    print(f"match-montage: {len(raw)} raw rows -> {len(montages)} live records\n")

    durations: dict[str, float | None] = {}
    verdicts = Counter()

    for rec in montages:
        info = rec.get("info") or {}
        sc = info.get("instantSupercut") or {}
        events = sc.get("mediaEvents") or []
        if not events:
            verdicts["no-events"] += 1
            continue

        by_media: dict[str, list] = {}
        for ev in events:
            by_media.setdefault(ev["mediaPath"], []).append(ev)

        print(f"--- montage {rec['id'][:8]}  game={rec.get('gameId')} "
              f"map={info.get('map')} selected={sc.get('selectedEventIds')}")
        for media_path, evs in by_media.items():
            p = Path(media_path)
            if media_path not in durations:
                durations[media_path] = probe_duration(p)
            dur = durations[media_path]

            times = [e["eventTimeMs"] / 1000 for e in evs]
            pre = {e.get("preMs") for e in evs}
            post = {e.get("postMs") for e in evs}
            kinds = Counter(e["eventId"] for e in evs)

            if dur is None:
                verdicts["media-missing"] += 1
                status = "MEDIA MISSING"
            else:
                lo = min(t - max(pre) / 1000 for t in times)
                hi = max(t + max(post) / 1000 for t in times)
                inside = lo >= -1.0 and hi <= dur + 1.0
                verdicts["relative-ok" if inside else "OUT-OF-RANGE"] += 1
                status = "OK (inside media)" if inside else "*** OUT OF RANGE ***"

            print(f"      {p.name}")
            print(f"        duration={dur if dur else '?'}  events={len(evs)} {dict(kinds)}")
            print(f"        eventTimeMs range = {min(times):.1f}s .. {max(times):.1f}s")
            print(f"        preMs={pre} postMs={post}   -> {status}")
        print()

    print("=== verdict ===")
    for k, v in verdicts.items():
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
