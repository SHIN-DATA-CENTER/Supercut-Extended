"""Drive the framing controls in the real main window, against the real library.

The point is the wiring, not the arithmetic (tools/test_framing.py covers that): a
crop box that never reaches RenderOptions, or a preset the settings file cannot
round-trip, would look completely fine on screen.

    python tools/test_framing_ui.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from supercut_extended.encoder import estimate_rate                 # noqa: E402
from supercut_extended.gui.main_window import MainWindow            # noqa: E402
from supercut_extended.model import RESOLUTION_PRESETS, Framing     # noqa: E402
from supercut_extended.render import _framing_filters               # noqa: E402
from supercut_extended.render import RenderOptions                  # noqa: E402

failures: list[str] = []


def expect(cond: bool, label: str, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}: {label}" + (f"  [{detail}]" if detail else ""))
    if not cond:
        failures.append(label)


def pick(win: MainWindow, label_start: str) -> None:
    for i in range(win.res_combo.count()):
        if win.res_combo.itemText(i).startswith(label_start):
            win.res_combo.setCurrentIndex(i)
            return
    raise AssertionError(f"no resolution item starting {label_start!r}")


def run(win: MainWindow) -> None:
    # Save/restore is exercised below, and QSettings is the user's real config. Point
    # the window at a throwaway INI first: otherwise running this test leaves the app
    # cropping 240px off every render, and the "defaults" checks below only pass on a
    # machine that has never run it.
    win.settings = QSettings(str(Path(tempfile.mkdtemp()) / "test.ini"),
                             QSettings.IniFormat)
    win.res_combo.setCurrentIndex(0)
    for spin in win.crop_spins.values():
        spin.setValue(0)
    win.stretch_box.setChecked(False)

    print("-- defaults leave the picture alone --")
    framing = win._current_framing()
    expect(not framing.active, "framing off by default", str(framing))
    expect(not win.custom_row.isVisible(), "custom size row hidden")
    expect(not win.stretch_box.isEnabled(),
           "stretch is meaningless without a target frame")

    print("-- a resolution preset --")
    pick(win, "1280 x 720")
    framing = win._current_framing()
    expect((framing.width, framing.height) == (1280, 720), "preset reaches the model",
           f"{framing.width}x{framing.height}")
    expect(win.stretch_box.isEnabled(), "stretch becomes available")
    expect(win.player.framing() == framing, "the preview follows the setting")

    print("-- custom size --")
    # The framing controls live on the output tab now. A widget on a tab that is not
    # current is not "visible" no matter how it is configured, so open it first --
    # which is what a user does before touching these anyway.
    win.tabs.setCurrentIndex(2)
    QApplication.processEvents()
    pick(win, "カスタム")
    expect(win.custom_row.isVisible(), "width/height row appears")
    expect((win.res_w.value(), win.res_h.value()) == (1280, 720),
           "custom starts from the preset just left",
           f"{win.res_w.value()}x{win.res_h.value()}")
    win.res_w.setValue(1600)
    win.res_h.setValue(900)
    expect(win._current_framing().output_size(1920, 1080) == (1600, 900),
           "custom size reaches the model")

    print("-- crop boxes --")
    win.crop_spins["crop_left"].setValue(240)
    win.crop_spins["crop_right"].setValue(240)
    framing = win._current_framing()
    expect(framing.source_rect(1920, 1080) == (240, 0, 1440, 1080),
           "crop reaches the model", str(framing.source_rect(1920, 1080)))
    filters = _framing_filters(RenderOptions(encoder=win._current_spec(),
                                             framing=framing),
                               win._info)
    expect(any(f.startswith("crop=") for f in filters),
           "the render builds a crop filter from it", ",".join(filters))

    print("-- stretch --")
    win.stretch_box.setChecked(True)
    opts = RenderOptions(encoder=win._current_spec(), framing=win._current_framing())
    filters = _framing_filters(opts, win._info)
    expect(not any("force_original_aspect_ratio" in f for f in filters),
           "stretching does not preserve the aspect ratio", ",".join(filters))
    expect(not any(f.startswith("pad=") for f in filters),
           "stretching does not pad")
    win.stretch_box.setChecked(False)
    filters = _framing_filters(
        RenderOptions(encoder=win._current_spec(), framing=win._current_framing()),
        win._info)
    expect(any(f.startswith("pad=") for f in filters),
           "without stretch it pads instead", ",".join(filters))

    print("-- the ETA moves with the output size --")
    spec = win._current_spec()
    args = dict(preset=spec.default_preset, quality=23, source_size=(1920, 1080))
    plain = estimate_rate(spec, framing=Framing(), **args)
    smaller = estimate_rate(spec, framing=Framing(width=1280, height=720), **args)
    bigger = estimate_rate(spec, framing=Framing(width=3840, height=2160), **args)
    expect(smaller > plain, "downscaling is estimated faster",
           f"{plain:.2f} -> {smaller:.2f}")
    expect(bigger < plain, "upscaling is estimated slower",
           f"{plain:.2f} -> {bigger:.2f}")

    print("-- settings round-trip --")
    for label, w, h in RESOLUTION_PRESETS[1:]:
        # Match on the whole label: "1080" alone hits the square preset before the
        # portrait one, and the round-trip then silently checks the same item twice.
        pick(win, label)
        before = win._current_framing()
        expect((before.width, before.height) == (w, h), f"selected: {label}",
               f"{before.width}x{before.height}")
        win._save_settings()
        win.res_combo.setCurrentIndex(0)
        for spin in win.crop_spins.values():
            spin.setValue(0)
        win._restore_settings()
        expect(win._current_framing() == before, f"restored: {label}",
               str(win._current_framing()))

    pick(win, "カスタム")
    win.res_w.setValue(1234)
    win.res_h.setValue(694)
    before = win._current_framing()
    win._save_settings()
    win.res_combo.setCurrentIndex(0)
    win._restore_settings()
    expect(win._current_framing() == before, "restored: custom size",
           str(win._current_framing()))

    print("-- passing framing to the render --")
    win.res_combo.setCurrentIndex(0)
    win.crop_spins["crop_left"].setValue(120)
    # _start_render needs a real output path; only the options matter here.
    expect(win._current_framing().crop_left == 120, "crop survives a preset reset")

    print("-- black bar detection is offered --")
    expect(win.detect_btn.isEnabled(), "detect button is live")


def main() -> int:
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()

    def go() -> None:
        if win.table.rowCount() == 0 or win._info is None:
            QTimer.singleShot(500, go)
            return
        try:
            run(win)
        finally:
            print("\n" + ("framing UI OK" if not failures
                          else f"{len(failures)} CHECK(S) FAILED: {failures}"))
            app.exit(1 if failures else 0)

    QTimer.singleShot(800, go)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
