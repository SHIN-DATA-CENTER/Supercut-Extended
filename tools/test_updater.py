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
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from supercut_extended import updater      # noqa: E402

failures: list[str] = []


def expect(cond: bool, label: str, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}: {label}" + (f"  [{detail}]" if detail else ""))
    if not cond:
        failures.append(label)


def contents(path: Path) -> str | None:
    """None while the file is missing or locked.

    robocopy replaces rather than rewrites, so there is a window where the target
    does not exist. Reading straight through that window makes this test fail on
    timing alone, which is exactly the kind of noise that gets a real failure
    waved away later.
    """
    try:
        return path.read_text()
    except OSError:
        return None


def run_swap(install: Path, payload: Path, settled: Callable[[], bool]) -> None:
    """Drive the real batch script against a fake install and wait for it to land.

    apply_and_restart() targets app_dir() and waits on this process's PID. Point it
    at the fake install, and give it a PID that is already gone so it proceeds at
    once instead of waiting for this test to exit.
    """
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
        if settled():
            break
        time.sleep(0.5)


def build_zip(path: Path, files: dict[str, str]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, body in files.items():
            zf.writestr(name, body)
    return path


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

    print("-- picking the asset out of a release --")
    # First-zip-wins was only ever correct because exactly one zip is attached.
    def asset(name: str) -> dict:
        return {"name": name, "browser_download_url": f"https://x/{name}"}

    expect(updater._pick_asset(
        [asset("SupercutExtended-cli.zip"), asset("SupercutExtended.zip")]
    )["name"] == "SupercutExtended.zip",
        "the app archive wins even when a CLI archive is listed first")
    expect(updater._pick_asset([asset("notes.txt"), asset("SupercutExtended.zip")])
           ["name"] == "SupercutExtended.zip", "non-zip attachments are ignored")
    expect(updater._pick_asset([asset("SupercutExtended.zip")])["name"]
           == "SupercutExtended.zip", "the single-zip case still works")
    expect(updater._pick_asset([asset("SupercutExtended-cli.zip")])["name"]
           == "SupercutExtended-cli.zip",
           "an only-looks-like-a-side-archive zip is still used, not refused")
    expect(updater._pick_asset([]) is None, "a release with no assets gives None")
    expect(updater._pick_asset(None) is None, "a missing assets list gives None")

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

    print("-- cleanup never escapes the staging directory --")
    # The bug this guards: stage() returns the staging dir itself when the exe sits at
    # the zip root, and the cleanup step deleted payload.parent -- i.e. all of %TEMP%.
    expect(updater._staging_root(payload) == payload,
           "a root-level payload cleans up itself, not its parent",
           f"{updater._staging_root(payload)}")
    nested = updater.stage(nested_zip)
    expect(updater._staging_root(nested) == nested.parent,
           "a nested payload cleans up the staging dir above it")
    expect(updater._staging_root(Path(tempfile.gettempdir()) / "somewhere") is None,
           "a path that did not come from stage() is never deleted")

    print("-- the real swap, against a fake install --")
    # A bystander directly in %TEMP%: if cleanup ever escapes again, this dies.
    bystander = Path(tempfile.gettempdir()) / "supercut_bystander_do_not_delete.txt"
    bystander.write_text("must survive the update")

    install = work / "install"
    install.mkdir()
    (install / updater.EXE_NAME).write_text("OLD BUILD")
    (install / "SupercutExtended-cli.exe").write_text("OLD CLI")
    (install / "settings.local").write_text("keep me")

    run_swap(install, payload,
             lambda: contents(install / updater.EXE_NAME) == "NEW BUILD")

    expect(contents(install / updater.EXE_NAME) == "NEW BUILD",
           "the exe was actually replaced",
           str(contents(install / updater.EXE_NAME)))
    expect(contents(install / "SupercutExtended-cli.exe") == "NEW CLI",
           "the CLI exe was replaced too")
    expect((install / "extra.txt").is_file(), "new files in the payload arrive")
    expect(contents(install / "settings.local") == "keep me",
           "files not in the payload are left alone (robocopy /E merges, not mirrors)")

    expect(bystander.is_file(),
           "an unrelated file in %TEMP% survived the update")
    expect(not payload.exists(), "the staging directory was cleaned up")
    expect(Path(tempfile.gettempdir()).is_dir(), "%TEMP% itself still exists")
    bystander.unlink(missing_ok=True)

    print("-- one-folder build: _internal is mirrored, the root is not --")
    # The whole point of the split copy. A one-folder build that only ever merges
    # keeps every .pyd and Qt plugin any past version shipped; a stale plugin across
    # a PySide6 bump is an import error at startup. _internal must lose the orphan,
    # and the install root must NOT -- the user's own files live there.
    onedir = work / "onedir_install"
    (onedir / "_internal").mkdir(parents=True)
    (onedir / updater.EXE_NAME).write_text("OLD BUILD")
    (onedir / "_internal" / "shared.pyd").write_text("OLD PYD")
    (onedir / "_internal" / "zzz_stale.pyd").write_text("ORPHAN FROM AN OLD VERSION")
    (onedir / "_internal" / "plugins").mkdir()
    (onedir / "_internal" / "plugins" / "gone.dll").write_text("ORPHAN IN A SUBDIR")
    (onedir / "mysettings.txt").write_text("user file, must survive")

    onedir_payload = updater.stage(build_zip(work / "onedir.zip", {
        updater.EXE_NAME: "NEW BUILD",
        "_internal/shared.pyd": "NEW PYD",
        "_internal/added.pyd": "ADDED",
    }))
    run_swap(onedir, onedir_payload,
             lambda: contents(onedir / updater.EXE_NAME) == "NEW BUILD"
             and not (onedir / "_internal" / "zzz_stale.pyd").exists())

    expect(contents(onedir / updater.EXE_NAME) == "NEW BUILD",
           "the exe was replaced in a one-folder install")
    expect(contents(onedir / "_internal" / "shared.pyd") == "NEW PYD",
           "_internal files are updated")
    expect((onedir / "_internal" / "added.pyd").is_file(),
           "new _internal files arrive")
    expect(not (onedir / "_internal" / "zzz_stale.pyd").exists(),
           "a stale file in _internal is purged")
    expect(not (onedir / "_internal" / "plugins" / "gone.dll").exists(),
           "the purge reaches subdirectories of _internal")
    expect(contents(onedir / "mysettings.txt") == "user file, must survive",
           "a user file next to the exe is NOT purged (the root still merges)")

    print("-- migrating a one-file install to a one-folder build --")
    # What every existing v1.3.x user hits on the release that switches to onedir.
    onefile = work / "onefile_install"
    onefile.mkdir()
    (onefile / updater.EXE_NAME).write_text("OLD BUILD")
    (onefile / "SupercutExtended-cli.exe").write_text("OLD CLI")

    migrate_payload = updater.stage(build_zip(work / "migrate.zip", {
        f"SupercutExtended/{updater.EXE_NAME}": "NEW BUILD",
        "SupercutExtended/SupercutExtended-cli.exe": "NEW CLI",
        "SupercutExtended/_internal/base_library.zip": "RUNTIME",
    }))
    run_swap(onefile, migrate_payload,
             lambda: contents(onefile / updater.EXE_NAME) == "NEW BUILD")

    expect(contents(onefile / updater.EXE_NAME) == "NEW BUILD",
           "a one-file install takes a one-folder payload")
    expect((onefile / "_internal" / "base_library.zip").is_file(),
           "_internal is created where there was none")
    expect(contents(onefile / "SupercutExtended-cli.exe") == "NEW CLI",
           "the old one-file CLI exe is overwritten, not orphaned")

    print("\n" + ("updater OK" if not failures
                  else f"{len(failures)} CHECK(S) FAILED: {failures}"))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
