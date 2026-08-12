"""Build the Windows installer from the PyInstaller one-folder output.

    python tools/build_installer.py

Expects `python -m PyInstaller supercut.spec` to have run first with ONEFILE = False,
so dist/SupercutExtended/ exists. The version comes from the package rather than being
typed into the .iss, so the installer can never disagree with the app about what it is.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from supercut_extended import __version__      # noqa: E402

ISS = ROOT / "installer" / "supercut.iss"
PAYLOAD = ROOT / "dist" / "SupercutExtended"
OUT_DIR = ROOT / "dist"

ISCC_CANDIDATES = (
    Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
    Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
)


def find_iscc() -> Path:
    for c in ISCC_CANDIDATES:
        if c.is_file():
            return c
    raise SystemExit(
        "Inno Setup 6 not found. Install it (winget install JRSoftware.InnoSetup) "
        "or edit ISCC_CANDIDATES.")


def main() -> int:
    if not (PAYLOAD / "SupercutExtended.exe").is_file():
        raise SystemExit(
            f"no one-folder build at {PAYLOAD}.\n"
            "Set ONEFILE = False in supercut.spec, then run:\n"
            "  python -m PyInstaller supercut.spec --noconfirm")
    if not (PAYLOAD / "_internal").is_dir():
        raise SystemExit(
            f"{PAYLOAD} has no _internal/. That is a one-file build; the installer "
            "packages the one-folder shape.")

    iscc = find_iscc()
    files = sum(1 for _ in PAYLOAD.rglob("*") if _.is_file())
    raw = sum(f.stat().st_size for f in PAYLOAD.rglob("*") if f.is_file())
    print(f"payload : {PAYLOAD}  ({files} files, {raw/1e6:.1f} MB)")
    print(f"version : {__version__}")

    result = subprocess.run(
        [str(iscc),
         f"/DAppVersion={__version__}",
         f"/DSourceDir={PAYLOAD}",
         f"/O{OUT_DIR}",
         str(ISS)],
        capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stdout[-4000:])
        print(result.stderr[-4000:])
        raise SystemExit(f"ISCC failed ({result.returncode})")

    setup = OUT_DIR / f"SupercutExtended-v{__version__}-setup.exe"
    if not setup.is_file():
        raise SystemExit(f"ISCC reported success but {setup} is missing")
    print(f"built   : {setup}  ({setup.stat().st_size/1e6:.1f} MB)")
    print(f"ratio   : {setup.stat().st_size / raw:.0%} of the uncompressed folder")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
