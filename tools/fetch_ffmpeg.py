"""Download the pinned FFmpeg build into vendor/ffmpeg/ for bundling into the exe.

FFmpeg itself is not committed to git (100+ MB, GPLv3 binary) -- this script
reproduces vendor/ffmpeg/ on a fresh checkout or a different PC. See
vendor/ffmpeg/NOTICE.txt for why this specific build was chosen (it is the only
official Windows build that has libx264, libx265, NVENC, QSV and AMF all enabled
at once) and what the GPLv3 redistribution requirements are.

    python tools/fetch_ffmpeg.py
"""

from __future__ import annotations

import hashlib
import sys
import urllib.request
import zipfile
from pathlib import Path

VERSION = "8.1.2"
URL = (
    "https://github.com/GyanD/codexffmpeg/releases/download/"
    f"{VERSION}/ffmpeg-{VERSION}-essentials_build.zip"
)
SHA256 = "db580001caa24ac104c8cb856cd113a87b0a443f7bdf47d8c12b1d740584a2ec"

ROOT = Path(__file__).resolve().parent.parent
DEST = ROOT / "vendor" / "ffmpeg"


def download(url: str, dest: Path) -> None:
    print(f"downloading {url}")
    with urllib.request.urlopen(url) as resp, open(dest, "wb") as f:
        total = int(resp.headers.get("Content-Length", 0))
        read = 0
        while chunk := resp.read(1 << 20):
            f.write(chunk)
            read += len(chunk)
            if total:
                sys.stdout.write(f"\r  {read / 1e6:6.1f} / {total / 1e6:.1f} MB")
                sys.stdout.flush()
    print()


def verify(path: Path, expected: str) -> None:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != expected:
        raise SystemExit(
            f"checksum mismatch for {path.name}\n  expected {expected}\n  got      {digest}\n"
            "The gyan.dev build was likely updated -- check "
            "https://github.com/GyanD/codexffmpeg/releases for the new version/hash "
            "and update tools/fetch_ffmpeg.py."
        )


def main() -> int:
    DEST.mkdir(parents=True, exist_ok=True)
    if (DEST / "ffmpeg.exe").exists() and (DEST / "ffprobe.exe").exists():
        print(f"already present: {DEST}")
        return 0

    zip_path = DEST / f"ffmpeg-{VERSION}-essentials_build.zip"
    download(URL, zip_path)
    verify(zip_path, SHA256)

    with zipfile.ZipFile(zip_path) as zf:
        prefix = f"ffmpeg-{VERSION}-essentials_build/"
        wanted = {
            f"{prefix}bin/ffmpeg.exe": DEST / "ffmpeg.exe",
            f"{prefix}bin/ffprobe.exe": DEST / "ffprobe.exe",
            f"{prefix}LICENSE": DEST / "LICENSE-FFmpeg-GPLv3.txt",
        }
        for member, out_path in wanted.items():
            with zf.open(member) as src, open(out_path, "wb") as dst:
                dst.write(src.read())

    zip_path.unlink()
    print(f"wrote {DEST / 'ffmpeg.exe'}, {DEST / 'ffprobe.exe'}, "
          f"{DEST / 'LICENSE-FFmpeg-GPLv3.txt'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
