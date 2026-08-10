"""Phase 0 calibration: dump Outplayed's IndexedDB so we can confirm the event schema.

Outplayed keeps kill/knockdown/death events ONLY in IndexedDB -- there are no sidecar
files next to the videos. This script copies the LevelDB out from under the running
app (the .log write-ahead file is exclusively locked, but shared reads succeed) and
walks every object store.

Usage:
    python tools/dump_indexeddb.py                # summary of every store
    python tools/dump_indexeddb.py --store Media  # full records for matching stores
    python tools/dump_indexeddb.py --json out.json
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

from ccl_chromium_reader import ccl_chromium_indexeddb

OUTPLAYED_EXT_ID = "cghphpbjeabdkomiphingnegihoigeggcfphdofo"


def default_leveldb_path() -> Path:
    local = os.environ.get("LOCALAPPDATA", "")
    return (
        Path(local)
        / "Overwolf"
        / "CefBrowserCache"
        / "Default"
        / "IndexedDB"
        / f"overwolf-extension_{OUTPLAYED_EXT_ID}_0.indexeddb.leveldb"
    )


def read_shared(path: Path) -> bytes:
    """Read a file the running app holds open.

    Python's open() asks for FILE_SHARE_READ only, which fails against LevelDB's LOCK
    and the live .log. CreateFileW with all three share flags is what actually works
    (same trick as .NET's FileShare.ReadWrite).
    """
    if os.name != "nt":
        return path.read_bytes()

    import ctypes
    from ctypes import wintypes

    GENERIC_READ = 0x80000000
    SHARE_ALL = 0x00000001 | 0x00000002 | 0x00000004  # READ | WRITE | DELETE
    OPEN_EXISTING = 3
    INVALID_HANDLE = ctypes.c_void_p(-1).value

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]

    handle = kernel32.CreateFileW(
        str(path), GENERIC_READ, SHARE_ALL, None, OPEN_EXISTING, 0x80, None
    )
    if handle == INVALID_HANDLE:
        raise OSError(ctypes.get_last_error(), f"CreateFileW failed for {path}")
    try:
        chunks, buf, nread = [], ctypes.create_string_buffer(1 << 20), wintypes.DWORD()
        while True:
            if not kernel32.ReadFile(handle, buf, len(buf), ctypes.byref(nread), None):
                raise OSError(ctypes.get_last_error(), f"ReadFile failed for {path}")
            if nread.value == 0:
                break
            chunks.append(buf.raw[: nread.value])
        return b"".join(chunks)
    finally:
        kernel32.CloseHandle(handle)


def copy_locked_tree(src: Path, dst: Path) -> Path:
    """Snapshot a LevelDB directory that a running process holds open.

    LOCK carries a byte-range lock and cannot be read at all while Outplayed runs, but
    it is a zero-byte sentinel -- LevelDB only needs it to exist, so recreate it empty.
    """
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        if not item.is_file():
            continue
        try:
            (dst / item.name).write_bytes(read_shared(item))
        except OSError:
            if item.name == "LOCK" or item.stat().st_size == 0:
                (dst / item.name).write_bytes(b"")
            else:
                raise
    return dst


def jsonable(value, _depth: int = 0):
    """Coerce deserialised V8/Blink objects into something json.dumps can handle."""
    if _depth > 12:
        return "<max depth>"
    if isinstance(value, dict):
        return {str(k): jsonable(v, _depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(v, _depth + 1) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (bytes, bytearray)):
        return f"<bytes len={len(value)}>"
    # ccl returns custom wrappers for Date / undefined / etc.
    for attr in ("value", "timestamp"):
        if hasattr(value, attr):
            try:
                return jsonable(getattr(value, attr), _depth + 1)
            except Exception:
                pass
    return str(value)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=default_leveldb_path())
    ap.add_argument("--store", help="only dump stores whose name contains this (case-insensitive)")
    ap.add_argument("--limit", type=int, default=3, help="records to print per store")
    ap.add_argument("--json", type=Path, help="write every record to this JSON file")
    args = ap.parse_args()

    if not args.db.exists():
        print(f"IndexedDB not found: {args.db}", file=sys.stderr)
        return 1

    tmp = Path(tempfile.mkdtemp(prefix="supercut_idb_"))
    work = copy_locked_tree(args.db, tmp / "idb.leveldb")
    print(f"copied {args.db}\n    -> {work}\n")

    collected: dict[str, list] = {}
    try:
        wrapper = ccl_chromium_indexeddb.WrappedIndexDB(work)
        for db_info in wrapper.database_ids:
            db = wrapper[db_info.dbid_no]
            print(f"=== database: {db.name}  (origin={db_info.origin})")
            for store_name in db.object_store_names:
                if not store_name:
                    continue
                store = db[store_name]
                records = []
                for rec in store.iterate_records():
                    records.append(jsonable(rec.value))
                collected[f"{db.name}/{store_name}"] = records
                print(f"    store {store_name!r}: {len(records)} records")

                if args.store and args.store.lower() not in store_name.lower():
                    continue
                for rec in records[: args.limit]:
                    print(json.dumps(rec, ensure_ascii=False, indent=2)[:4000])
                    print("    ---")
            print()
    finally:
        if args.json:
            args.json.write_text(
                json.dumps(collected, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"wrote {args.json}")
        shutil.rmtree(tmp, ignore_errors=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
