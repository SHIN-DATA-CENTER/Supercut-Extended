"""Run a real self-update against a fake install, and check the files actually swap.

Written after a shipped release turned out to be unupdatable: apply_and_restart()
combined CREATE_NEW_CONSOLE with DETACHED_PROCESS, which CreateProcess rejects with
ERROR_INVALID_PARAMETER (87). Checking that the download and the zip were fine said
nothing about it -- the failure was in the last step, launching the swap script.

So this drives the real batch file against a throwaway directory and waits for the
bytes on disk to change.

    python tools/test_updater.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from supercut_extended import updater      # noqa: E402

failures: list[str] = []


def expect(cond: bool, label: str, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}: {label}" + (f"  [{detail}]" if detail else ""))
    if not cond:
        failures.append(label)


def main() -> int:
    print("-- process creation flags --")
    # The bug itself, in isolation: this is what the dialog surfaced as WinError 87.
    if os.name == "nt":
        bad = subprocess.CREATE_NEW_CONSOLE | subprocess.DETACHED_PROCESS
        try:
            subprocess.Popen(["cmd", "/c", "exit"], creationflags=bad).wait(10)
            expect(False, "CREATE_NEW_CONSOLE|DETACHED_PROCESS is still rejected "
                          "(if this passes, the pair became legal -- re-check)")
        except OSError as exc:
            expect(getattr(exc, "winerror", 0) == 87,
                   "the old flag pair really is what raised WinError 87",
                   f"winerror={getattr(exc, 'winerror', '?')}")

    print("-- staging a zip --")
    work = Path(tempfile.mkdtemp(prefix="supercut_upd_test_"))
    zip_path = work / "payload.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(updater.EXE_NAME, "NEW BUILD")
        zf.writestr("SupercutExtended-cli.exe", "NEW CLI")
        zf.writestr("extra.txt", "shipped alongside")
    payload = updater.stage(zip_path)
    expect((payload / updater.EXE_NAME).is_file(), "stage() found the exe in the zip")

    # A zip that nests the build one directory down, as release archives often do.
    nested_zip = work / "nested.zip"
    with zipfile.ZipFile(nested_zip, "w") as zf:
        zf.writestr(f"SupercutExtended-v9/{updater.EXE_NAME}", "NEW BUILD")
    expect(updater.stage(nested_zip).name.startswith("SupercutExtended-v9"),
           "stage() looks one level down too")

    bad_zip = work / "bad.zip"
    with zipfile.ZipFile(bad_zip, "w") as zf:
        zf.writestr("readme.txt", "no exe here")
    try:
        updater.stage(bad_zip)
        expect(False, "a zip without the exe is rejected")
    except RuntimeError:
        expect(True, "a zip without the exe is rejected")

    print("-- the real swap, against a fake install --")
    install = work / "install"
    install.mkdir()
    (install / updater.EXE_NAME).write_text("OLD BUILD")
    (install / "SupercutExtended-cli.exe").write_text("OLD CLI")
    (install / "settings.local").write_text("keep me")

    # apply_and_restart() targets app_dir() and waits on this process's PID. Point it
    # at the fake install, and give it a PID that is already gone so it proceeds at
    # once instead of waiting for this test to exit.
    dead = subprocess.Popen(["cmd", "/c", "exit"])
    dead.wait()
    real_app_dir, real_getpid = updater.app_dir, os.getpid
    updater.app_dir = lambda: install
    os.getpid = lambda: dead.pid
    try:
        updater.apply_and_restart(payload)
    finally:
        updater.app_dir, os.getpid = real_app_dir, real_getpid

    deadline = time.time() + 45
    while time.time() < deadline:
        if (install / updater.EXE_NAME).read_text() == "NEW BUILD":
            break
        time.sleep(0.5)

    expect((install / updater.EXE_NAME).read_text() == "NEW BUILD",
           "the exe was actually replaced",
           (install / updater.EXE_NAME).read_text())
    expect((install / "SupercutExtended-cli.exe").read_text() == "NEW CLI",
           "the CLI exe was replaced too")
    expect((install / "extra.txt").is_file(), "new files in the payload arrive")
    expect((install / "settings.local").read_text() == "keep me",
           "files not in the payload are left alone (robocopy /E merges, not mirrors)")

    print("\n" + ("updater OK" if not failures
                  else f"{len(failures)} CHECK(S) FAILED: {failures}"))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
