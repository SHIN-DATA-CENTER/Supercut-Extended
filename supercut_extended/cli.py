"""Command line interface.

The GUI drives exactly the same core functions; this exists so the pipeline can be
exercised and verified without any UI in the way.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

from . import __version__
from .encoder import available_encoders, describe, pick_encoder
from .library import LibraryError, matches_with_highlights, read_matches
from .model import HIGHLIGHT_KINDS, Framing, Match, Media
from .probe import ProbeError, probe
from .render import RenderJob, RenderOptions, render_each, render_many
from .segments import build_segments, total_duration_s


def default_output_dir(source: Path) -> Path:
    """Outplayed's media root is <root>/<Game>/<session>/<file>.mp4."""
    parents = source.parents
    root = parents[2] if len(parents) >= 3 else source.parent
    return root / "Exports"


def filter_by_game(matches: list[Match], game: str | None) -> list[Match]:
    if not game:
        return matches
    needle = game.lower()
    return [m for m in matches if needle in m.game_name.lower()]


def resolve_match(matches: list[Match], selector: str) -> Match:
    """Row numbers are positions in the SAME filtered list `list` printed.

    So `build` must be given the same --game filter as `list`, otherwise use a match
    id, which is stable regardless of filtering.

    Careful with id *prefixes*: Outplayed numbers matches within a session, so a
    match id is "<32-char session hash>_<n>" and every match recorded in one sitting
    shares all but the last characters. A short prefix therefore matches several
    matches, and silently rendering the first of them is how you end up with the
    wrong footage -- so an ambiguous selector is refused rather than guessed.
    """
    if selector.isdigit():
        idx = int(selector) - 1
        if 0 <= idx < len(matches):
            return matches[idx]
        raise SystemExit(
            f"no match #{selector} (have 1..{len(matches)}). "
            "If you filtered `list` with --game, pass the same --game to `build`."
        )

    exact = [m for m in matches
             if selector in (m.match_id, m.original_match_id)]
    if len(exact) == 1:
        return exact[0]

    hits = [m for m in matches if m.match_id.startswith(selector)
            or m.original_match_id.startswith(selector)]
    if not hits:
        raise SystemExit(f"no match matching id {selector!r}")
    if len(hits) > 1:
        listing = "\n".join(
            f"  {m.match_id}  {m.started_at:%Y-%m-%d %H:%M}  {m.game_name}"
            for m in hits[:10])
        more = f"\n  ... and {len(hits) - 10} more" if len(hits) > 10 else ""
        raise SystemExit(
            f"{selector!r} matches {len(hits)} matches. Outplayed ids share a session "
            f"prefix, so pass enough to be unique (the trailing _<n> is what differs):"
            f"\n{listing}{more}"
        )
    return hits[0]


def cmd_encoders(_: argparse.Namespace) -> int:
    print("Usable encoders (probed by actually encoding frames):")
    print(describe())
    found = available_encoders()
    if found and not found[0].hardware:
        print("\nWARNING: no GPU encoder available - output would still be CPU-bound.")
    return 0


def _print_match_table(matches: list[Match]) -> None:
    print(f"{'#':>3}  {'when':<16} {'game':<18} {'dur':>7}  events")
    print("-" * 78)
    for i, m in enumerate(matches, 1):
        counts = m.event_counts()
        summary = " ".join(f"{k}:{v}" for k, v in sorted(counts.items())
                           if k in HIGHLIGHT_KINDS or v > 2) or "-"
        info = m.info or {}
        game = m.game_name[:18]
        extra = f" {info['map']}" if info.get("map") else ""
        print(f"{i:>3}  {m.started_at:%Y-%m-%d %H:%M}  {game:<18} "
              f"{m.duration_s / 60:>6.1f}m  {summary}{extra}")


def cmd_list(args: argparse.Namespace) -> int:
    matches = read_matches(args.db)
    playable = matches_with_highlights(matches)
    playable = filter_by_game(playable, args.game)[: args.limit]
    if not playable:
        print("No matches with highlights found.")
        return 1
    _print_match_table(playable)
    print(f"\n{len(playable)} shown. Use: supercut build <#> --events kill")
    return 0


def cmd_events(args: argparse.Namespace) -> int:
    matches = filter_by_game(matches_with_highlights(read_matches(args.db)), args.game)
    match = resolve_match(matches, args.match)
    print(f"{match.label()}   id={match.match_id}")
    for media in match.playable_medias:
        print(f"\n  media #{media.media_id}  {media.path}")
        print(f"    db duration {media.duration_s:.1f}s, {len(media.events)} events")
        for e in media.events:
            print(f"      {e.time_ms / 1000:9.3f}s  {e.kind:<12} "
                  f"pre={e.pre_ms:>6} post={e.post_ms:>6}"
                  + (f"  #{e.counter}" if e.counter else ""))
    return 0


def pick_medias(match: Match, media_id: int | None) -> list[Media]:
    """Every recording a match should be cut from, or just the one --media names.

    All of them by default. Outplayed's Highlight capture writes a separate file per
    highlight, so a match is often several recordings -- taking only the first is why
    a ten-clip VALORANT match used to produce one clip.
    """
    medias = [m for m in match.playable_medias if m.path is not None]
    if media_id is not None:
        medias = [m for m in medias if m.media_id == media_id]
    if not medias:
        raise SystemExit(f"no playable media with events for match {match.match_id}")
    return medias


def resolve_matches(matches: list[Match], selectors: list[str]) -> list[Match]:
    """Resolve every selector, drop duplicates, then order chronologically.

    Chronological order is what makes a combined montage watchable, and it keeps the
    result independent of the order the selectors happened to be typed in.
    """
    seen: set[str] = set()
    chosen: list[Match] = []
    for sel in selectors:
        match = resolve_match(matches, sel)
        if match.match_id not in seen:
            seen.add(match.match_id)
            chosen.append(match)
    return sorted(chosen, key=lambda m: m.start_time_ms)


def _framing_from_args(args: argparse.Namespace) -> Framing:
    """Build the output framing from --size / --crop / --stretch."""
    width = height = None
    if args.size:
        try:
            width, height = (int(v) for v in str(args.size).lower().split("x", 1))
        except ValueError:
            raise SystemExit(f"--size wants WIDTHxHEIGHT, not {args.size!r}")
    crops = [0, 0, 0, 0]
    if args.crop:
        parts = [p.strip() for p in str(args.crop).split(",")]
        if len(parts) not in (1, 2, 4):
            raise SystemExit("--crop takes 1, 2 or 4 numbers "
                             "(all / left,right / left,right,top,bottom)")
        try:
            nums = [int(p) for p in parts]
        except ValueError:
            raise SystemExit(f"--crop wants whole pixels, not {args.crop!r}")
        # 1 number crops every edge, 2 crops the sides -- the common pillarbox case.
        crops = nums * 4 if len(nums) == 1 else (nums + [0, 0]) if len(nums) == 2 \
            else nums
    if args.stretch and (width is None or height is None):
        raise SystemExit("--stretch needs --size: there is no frame to stretch into")
    return Framing(width=width, height=height, stretch=args.stretch,
                   crop_left=crops[0], crop_right=crops[1],
                   crop_top=crops[2], crop_bottom=crops[3])


def _framing_summary(framing: Framing) -> str:
    bits = []
    if framing.resizes:
        bits.append(f"{framing.width}x{framing.height}"
                    + (" stretched" if framing.stretch else " (fit)"))
    if framing.crops:
        bits.append(f"crop L{framing.crop_left} R{framing.crop_right} "
                    f"T{framing.crop_top} B{framing.crop_bottom}")
    return ", ".join(bits)


def cmd_build(args: argparse.Namespace) -> int:
    matches = filter_by_game(matches_with_highlights(read_matches(args.db)), args.game)
    selected = resolve_matches(matches, args.match)
    if args.media is not None and len(selected) > 1:
        raise SystemExit("--media only makes sense with a single match")

    kinds = ([k.strip() for k in args.events.split(",") if k.strip()]
             if args.events else None)
    stamp = f"{datetime.now():%Y%m%d-%H%M%S}"

    jobs: list[RenderJob] = []
    skipped: list[str] = []
    for match in selected:
        medias = pick_medias(match, args.media)
        if len(medias) > 1:
            print(f"{match.label()}: {len(medias)} recordings "
                  f"(--media to pick just one)")
        for media in medias:
            try:
                info = probe(media.path)
            except ProbeError as exc:
                raise SystemExit(str(exc))

            segments = build_segments(
                media.events,
                kinds=kinds,
                pre_ms=args.pre * 1000 if args.pre is not None else None,
                post_ms=args.post * 1000 if args.post is not None else None,
                duration_ms=info.duration_ms,
                gap_ms=args.gap * 1000,
            )
            if not segments:
                continue

            n_events = sum(1 for e in media.events if not kinds or e.kind in kinds)
            print(f"{match.label()}")
            print(f"  source   {media.path.name}")
            print(f"           {info.width}x{info.height} {info.fps:.0f}fps "
                  f"{info.duration_s:.1f}s  {len(info.audio)} audio track(s)")
            print(f"  events   {n_events} -> {len(segments)} segments, "
                  f"{total_duration_s(segments):.1f}s of content")

            if args.dry_run:
                for i, seg in enumerate(segments, 1):
                    print(f"    {i:>3}. {seg.start_s:9.3f}s -> "
                          f"{seg.end_ms / 1000:9.3f}s ({seg.duration_s:6.2f}s)")

            jobs.append(RenderJob(
                source=media.path, segments=segments, label=match.label(),
                output=(default_output_dir(media.path)
                        / f"{media.path.stem}-supercut-{stamp}.mp4"),
            ))

        if not any(j.source in {m.path for m in medias} for j in jobs):
            skipped.append(f"{match.label()} (has: "
                           f"{', '.join(sorted(match.event_counts())) or 'none'})")

    for note in skipped:
        print(f"skipped: no events matched {kinds} in {note}")
    if not jobs:
        raise SystemExit(f"no events matched {kinds} in any selected match")

    if len(jobs) > 1:
        total = sum(j.content_s for j in jobs)
        print(f"\n{len(jobs)} recordings -> "
              f"{sum(len(j.segments) for j in jobs)} segments, {total:.1f}s total")
    if args.dry_run:
        return 0

    spec = pick_encoder(args.encoder)
    if not spec.hardware:
        print(f"  WARNING: falling back to {spec.name} (CPU) - no GPU encoder usable")

    framing = _framing_from_args(args)
    opts = RenderOptions(
        encoder=spec, preset=args.preset or spec.default_preset,
        quality=args.quality, max_rate_kbps=args.max_rate,
        audio=args.audio, workers=args.workers, mode=args.mode,
        framing=framing, fps=args.fps,
    )
    if args.fps and args.mode == "copy":
        # A stream copy passes packets through untouched, so there is nothing that
        # could change the rate. Say so rather than writing the source rate silently.
        raise SystemExit("--fps needs --mode encode (a stream copy cannot re-time)")
    if framing.active:
        print(f"  framing  {_framing_summary(framing)}")
    if opts.mode == "copy":
        print(f"  mode     copy (stream copy, no re-encode) audio={opts.audio}")
    else:
        print(f"  encoder  {spec.name} preset={opts.preset} cq={opts.quality} "
              f"audio={opts.audio} workers={opts.workers}")

    if args.separate:
        if args.output:
            out_dir = Path(args.output)
            for job in jobs:
                job.output = out_dir / Path(job.output).name
        print(f"  output   {len(jobs)} file(s), one per recording")
        for job in jobs:
            print(f"           {job.output}")
    else:
        combined = Path(args.output) if args.output else (
            default_output_dir(Path(jobs[0].source))
            / (f"{Path(jobs[0].source).stem}-supercut-{stamp}.mp4" if len(jobs) == 1
               else f"supercut-{len(jobs)}-matches-{stamp}.mp4")
        )
        print(f"  output   {combined}")
    print()

    last = [0.0]

    def on_progress(frac: float, msg: str) -> None:
        now = time.monotonic()
        if now - last[0] < 0.2 and frac < 1.0:
            return
        last[0] = now
        bar = "#" * int(frac * 34)
        sys.stdout.write(f"\r  [{bar:<34}] {frac * 100:5.1f}%  {msg[:44]:<44}")
        sys.stdout.flush()

    if args.separate:
        results = render_each(jobs, opts, progress=on_progress)
    else:
        results = [render_many(jobs, combined, opts, progress=on_progress)]
    sys.stdout.write("\r" + " " * 100 + "\r")

    for result in results:
        print(f"  done in {result.elapsed_s:.1f}s  "
              f"({result.speed_x:.1f}x realtime, {result.size_bytes / 1e6:.1f} MB)")
        print(f"  {result.output}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="supercut", description="GPU-accelerated Supercut for Outplayed recordings"
    )
    ap.add_argument("--db", type=Path, help="override the Outplayed IndexedDB path")
    # Worth having for its own sake, but the reason it exists is release checking:
    # a one-file build compresses its payload, so the version cannot be read out of
    # the .exe itself -- there was no way to confirm a built artifact was the version
    # it claimed to be.
    ap.add_argument("--version", action="version",
                    version=f"Supercut Extended {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("encoders", help="show which encoders actually work here")
    p.set_defaults(func=cmd_encoders)

    p = sub.add_parser("list", help="list matches that have highlights")
    p.add_argument("--game", help="filter by game name")
    p.add_argument("--limit", type=int, default=40)
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("events", help="show every detected event for a match")
    p.add_argument("match", help="row number from `list`, or a match id prefix")
    p.add_argument("--game", help="same filter you passed to `list`")
    p.set_defaults(func=cmd_events)

    p = sub.add_parser("build", help="render a supercut")
    p.add_argument("match", nargs="+",
                   help="one or more row numbers from `list`, or match id prefixes. "
                        "Several are combined into one file in chronological order "
                        "unless --separate is given")
    p.add_argument("--game", help="same filter you passed to `list`")
    p.add_argument("--events", default="kill",
                   help="comma separated kinds, or empty for all (default: kill)")
    p.add_argument("--pre", type=float, help="seconds before each event (override)")
    p.add_argument("--post", type=float, help="seconds after each event (override)")
    p.add_argument("--gap", type=float, default=0.0,
                   help="also merge segments closer than this many seconds")
    p.add_argument("--media", type=int, help="media id when a match has several")
    p.add_argument("--encoder", help="force an encoder (default: best GPU available)")
    p.add_argument("--preset", help="encoder preset (NVENC p1-p7)")
    p.add_argument("--quality", type=int, default=23, help="CQ/CRF, lower is better")
    p.add_argument("--max-rate", type=int, default=25000, help="ceiling in kbit/s")
    p.add_argument("--audio", default="0", help="track index, 'all', 'mix' or 'none'")
    p.add_argument("--size", help="output resolution, e.g. 1920x1080 "
                                  "(default: keep the source size)")
    p.add_argument("--crop", help="black bars to cut off, in source pixels: "
                                  "'240' (all edges), '240,240' (left,right) or "
                                  "'240,240,0,0' (left,right,top,bottom)")
    p.add_argument("--fps", type=float,
                   help="output frame rate, e.g. 30 / 60 / 120 "
                        "(default: keep the source rate)")
    p.add_argument("--stretch", action="store_true",
                   help="fill --size instead of padding, changing the aspect ratio "
                        "(for a 4:3 game recorded inside a 16:9 capture)")
    p.add_argument("--workers", type=int, default=2, help="parallel segment encodes")
    p.add_argument("--mode", choices=("encode", "copy"), default="encode",
                   help="encode = Outplayed-equivalent re-encode on GPU (default); "
                        "copy = no re-encode, near-instant, cuts snap to keyframes")
    p.add_argument("--separate", action="store_true",
                   help="write one supercut per match instead of combining them")
    p.add_argument("--output",
                   help="output file, or with --separate the directory to write into")
    p.add_argument("--dry-run", action="store_true", help="print segments and stop")
    p.set_defaults(func=cmd_build)

    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except LibraryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\ninterrupted")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
