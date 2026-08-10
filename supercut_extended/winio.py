"""Reading files that a running Windows process holds open.

Outplayed keeps its LevelDB open for the whole session. Python's open() requests only
FILE_SHARE_READ, which the running app denies, so we go straight to CreateFileW with
all three share flags -- the same thing .NET's FileShare.ReadWrite does.
"""

from __future__ import annotations

import os
from pathlib import Path

_CHUNK = 1 << 20


def read_shared(path: Path) -> bytes:
    """Read a file even while another process has it open for writing."""
    if os.name != "nt":
        return Path(path).read_bytes()

    import ctypes
    from ctypes import wintypes

    GENERIC_READ = 0x80000000
    SHARE_ALL = 0x1 | 0x2 | 0x4  # READ | WRITE | DELETE
    OPEN_EXISTING = 3
    FLAG_SEQUENTIAL = 0x08000000
    INVALID_HANDLE = ctypes.c_void_p(-1).value

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    k32.ReadFile.argtypes = [
        wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
    ]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]

    handle = k32.CreateFileW(
        str(path), GENERIC_READ, SHARE_ALL, None, OPEN_EXISTING, FLAG_SEQUENTIAL, None
    )
    if handle == INVALID_HANDLE:
        raise OSError(ctypes.get_last_error(), f"CreateFileW failed: {path}")
    try:
        chunks: list[bytes] = []
        buf = ctypes.create_string_buffer(_CHUNK)
        nread = wintypes.DWORD()
        while True:
            if not k32.ReadFile(handle, buf, _CHUNK, ctypes.byref(nread), None):
                raise OSError(ctypes.get_last_error(), f"ReadFile failed: {path}")
            if nread.value == 0:
                break
            chunks.append(buf.raw[: nread.value])
        return b"".join(chunks)
    finally:
        k32.CloseHandle(handle)


def snapshot_dir(src: Path, dst: Path) -> Path:
    """Copy a LevelDB directory out from under the running app.

    LOCK carries a byte-range lock and cannot be read at all, but it is a zero-byte
    sentinel that LevelDB only needs to exist -- so recreate it empty.
    """
    dst.mkdir(parents=True, exist_ok=True)
    for item in Path(src).iterdir():
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
