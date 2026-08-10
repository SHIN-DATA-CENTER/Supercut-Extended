"""Update checking and self-update against GitHub Releases.

Only the standard library is used -- adding `requests` for one JSON GET would drag a
dependency into the PyInstaller bundle for no benefit.

The check is deliberately quiet: a laptop on a hotel wifi should never see a dialog
because api.github.com timed out. Any network or parse failure returns None and the
app carries on.

Applying an update on Windows cannot overwrite the running executable, so the actual
swap is delegated to a small batch script that waits for this process to exit first.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import __version__

# ---------------------------------------------------------------------------
# The repository releases are published to. Clearing this string disables update
# checking entirely rather than hammering a 404. The environment variable wins,
# which is handy for testing against a fork.
# ---------------------------------------------------------------------------
GITHUB_REPO = "SHIN-DATA-CENTER/Supercut-Extended"

API_TIMEOUT = 8
USER_AGENT = f"SupercutExtended/{__version__}"
EXE_NAME = "SupercutExtended.exe"


def repo() -> str:
    return os.environ.get("SUPERCUT_GITHUB_REPO", "").strip() or GITHUB_REPO.strip()


def enabled() -> bool:
    return "/" in repo()


@dataclass(frozen=True)
class Release:
    version: tuple[int, ...]
    tag: str
    name: str
    notes: str
    page_url: str
    asset_url: str | None
    asset_name: str | None
    asset_size: int = 0

    @property
    def label(self) -> str:
        return self.name or self.tag


def parse_version(text: str) -> tuple[int, ...]:
    """"v1.2.3" / "1.2" / "1.0.0-beta" -> comparable tuple of ints.

    Pre-release suffixes are ignored for ordering; a tag that carries no digits at
    all sorts as (0,), i.e. never newer than a real release.
    """
    cleaned = text.strip().lstrip("vV")
    parts = re.findall(r"\d+", cleaned.split("-")[0].split("+")[0])
    return tuple(int(p) for p in parts) or (0,)


def is_newer(candidate: tuple[int, ...], current: tuple[int, ...]) -> bool:
    """Compare zero-padded so 1.2 and 1.2.0 are equal rather than 1.2 being older."""
    width = max(len(candidate), len(current))
    pad = lambda t: t + (0,) * (width - len(t))  # noqa: E731
    return pad(candidate) > pad(current)


def current_version() -> tuple[int, ...]:
    return parse_version(__version__)


def _api(url: str) -> dict | list | None:
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT,
                      "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(request, timeout=API_TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None


def fetch_latest() -> Release | None:
    """Return the newest published release, or None if unavailable/not newer."""
    if not enabled():
        return None
    data = _api(f"https://api.github.com/repos/{repo()}/releases/latest")
    if not isinstance(data, dict) or data.get("draft"):
        return None

    tag = str(data.get("tag_name") or "")
    if not tag:
        return None

    asset_url = asset_name = None
    asset_size = 0
    for asset in data.get("assets") or []:
        name = str(asset.get("name") or "")
        if name.lower().endswith(".zip"):
            asset_url = asset.get("browser_download_url")
            asset_name = name
            asset_size = int(asset.get("size") or 0)
            break

    return Release(
        version=parse_version(tag),
        tag=tag,
        name=str(data.get("name") or ""),
        notes=str(data.get("body") or ""),
        page_url=str(data.get("html_url") or f"https://github.com/{repo()}/releases"),
        asset_url=asset_url,
        asset_name=asset_name,
        asset_size=asset_size,
    )


def check() -> Release | None:
    """The newest release, but only when it is actually newer than what is running."""
    release = fetch_latest()
    if release and is_newer(release.version, current_version()):
        return release
    return None


# -- applying an update -----------------------------------------------------

def can_self_update(release: Release) -> bool:
    """Self-update only makes sense for the packaged build with a zip attached."""
    return bool(getattr(sys, "frozen", False) and release.asset_url)


def app_dir() -> Path:
    return Path(sys.executable).parent


def download(url: str, dest: Path,
             progress: Callable[[int, int], None] | None = None) -> Path:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as fh:
            while True:
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                fh.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
    return dest


def _find_payload_root(extracted: Path) -> Path | None:
    """Locate the directory holding the exe -- releases often nest one level."""
    if (extracted / EXE_NAME).is_file():
        return extracted
    for child in extracted.iterdir():
        if child.is_dir() and (child / EXE_NAME).is_file():
            return child
    return None


def stage(zip_path: Path) -> Path:
    """Unpack the downloaded zip and verify it really contains the application."""
    staging = Path(tempfile.mkdtemp(prefix="supercut_update_"))
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(staging)
    payload = _find_payload_root(staging)
    if payload is None:
        raise RuntimeError(
            f"the downloaded archive does not contain {EXE_NAME}")
    return payload


def apply_and_restart(payload: Path) -> None:
    """Hand the swap to a detached batch file, then the caller must exit.

    Windows keeps the running exe locked, so the copy has to happen after we are
    gone. robocopy /E merges the new build over the old one; exit codes below 8 are
    success in robocopy's unusual scheme.

    The wait loop watches this process's own PID, but a one-file build also has a
    bootloader parent that holds the .exe open until slightly after we exit. Retrying
    for a few seconds (/R /W) rides out that window instead of failing the update.
    """
    target = app_dir()
    script = Path(tempfile.gettempdir()) / "supercut_apply_update.bat"
    exe = target / EXE_NAME

    script.write_text(
        "@echo off\r\n"
        "setlocal\r\n"
        f'echo Updating Supercut Extended...\r\n'
        f'"%SystemRoot%\\System32\\timeout.exe" /t 2 /nobreak >nul\r\n'
        f':wait\r\n'
        f'tasklist /FI "PID eq {os.getpid()}" 2>nul | find "{os.getpid()}" >nul\r\n'
        f'if not errorlevel 1 (\r\n'
        f'  "%SystemRoot%\\System32\\timeout.exe" /t 1 /nobreak >nul\r\n'
        f'  goto wait\r\n'
        f')\r\n'
        f'robocopy "{payload}" "{target}" /E /IS /IT /R:10 /W:1 /NFL /NDL /NJH /NJS >nul\r\n'
        f'if errorlevel 8 (\r\n'
        f'  echo Update failed. & pause & exit /b 1\r\n'
        f')\r\n'
        f'start "" "{exe}"\r\n'
        f'rmdir /s /q "{payload.parent}" >nul 2>&1\r\n'
        f'del "%~f0" >nul 2>&1\r\n',
        encoding="utf-8",
    )

    creation = 0
    if os.name == "nt":
        creation = subprocess.CREATE_NEW_CONSOLE | subprocess.DETACHED_PROCESS
    subprocess.Popen(["cmd", "/c", str(script)], creationflags=creation,
                     close_fds=True, cwd=str(Path(tempfile.gettempdir())))
