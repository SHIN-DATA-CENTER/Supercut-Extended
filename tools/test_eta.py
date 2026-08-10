"""Check that the estimated time actually responds to the preset and the quality.

Two things used to stop it moving: the estimate ignored both knobs, and the preset
combo had no signal connected at all, so changing it did not even trigger a redraw.
Both are easy to regress, and neither shows up in a render test.

    python tools/test_eta.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from supercut_extended.encoder import (DEFAULT_QUALITY, QUALITY_TIERS,  # noqa: E402
                                       CANDIDATES, estimate_rate)
from supercut_extended.gui.main_window import MainWindow            # noqa: E402

failures: list[str] = []


def expect(cond: bool, label: str, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}: {label}" + (f"  [{detail}]" if detail else ""))
    if not cond:
        failures.append(label)


def check_model() -> None:
    by = {s.name: s for s in CANDIDATES}
    print("-- speed model --")

    # Slower presets must never be estimated as faster.
    for name in ("h264_nvenc", "hevc_nvenc", "libx264", "libx265"):
        spec = by[name]
        rates = [estimate_rate(spec, preset=p, quality=DEFAULT_QUALITY)
                 for p in spec.presets]
        ordered = all(a >= b - 1e-9 for a, b in zip(rates, rates[1:]))
        expect(ordered, f"{name}: rate decreases as the preset gets slower",
               " ".join(f"{r:.2f}" for r in rates))
        expect(rates[0] > rates[-1] * 1.3,
               f"{name}: fastest preset is meaningfully faster than the slowest",
               f"{rates[0]:.2f} vs {rates[-1]:.2f}")

    # On the CPU, a coarser quality must encode faster; on a fixed-function GPU
    # encoder it legitimately does not move (measured), so only require "not slower".
    for name, must_change in (("libx264", True), ("libx265", True),
                              ("h264_nvenc", False), ("hevc_nvenc", False)):
        spec = by[name]
        rates = [estimate_rate(spec, preset=spec.default_preset, quality=q)
                 for q in QUALITY_TIERS]
        rising = all(a <= b + 1e-9 for a, b in zip(rates, rates[1:]))
        expect(rising, f"{name}: coarser quality is never slower",
               " ".join(f"{r:.2f}" for r in rates))
        if must_change:
            expect(rates[-1] > rates[0] * 1.1,
                   f"{name}: quality visibly changes the estimate",
                   f"{rates[0]:.2f} -> {rates[-1]:.2f}")

    expect(estimate_rate(by["h264_nvenc"], preset="p4", quality=23, mode="copy") > 50,
           "copy mode ignores the encoder settings")


def eta_of(win: MainWindow) -> float | None:
    m = re.search(r"([\d.]+)\s*秒", win.summary.text().split("·")[-1])
    return float(m.group(1)) if m else None


def select_biggest(win: MainWindow) -> None:
    """Preview the match with the most highlights.

    The ETA is printed as whole seconds, so a five-second job rounds every setting to
    the same number. Only a realistically sized job shows the difference.
    """
    rows = win._visible_matches()
    if not rows:
        return
    best = max(range(len(rows)),
               key=lambda i: sum(rows[i].event_counts().values()))
    win.table.selectRow(best)


def check_gui(win: MainWindow) -> None:
    print("-- GUI --")
    select_biggest(win)
    win.mode_encode.setChecked(True)
    win.engine_gpu.setChecked(True)
    if win.preset_combo.count() < 2:
        expect(False, "preset combo populated", str(win.preset_combo.count()))
        return

    base = eta_of(win)
    expect(base is not None, "summary shows an ETA", win.summary.text())

    fastest, slowest = 0, win.preset_combo.count() - 1
    win.preset_combo.setCurrentIndex(fastest)
    fast_eta = eta_of(win)
    win.preset_combo.setCurrentIndex(slowest)
    slow_eta = eta_of(win)
    expect(fast_eta is not None and slow_eta is not None and slow_eta > fast_eta,
           "changing the preset changes the ETA",
           f"{win.preset_combo.itemData(fastest)}={fast_eta}s "
           f"{win.preset_combo.itemData(slowest)}={slow_eta}s")

    # CPU: the quality must move it too.
    win.engine_cpu.setChecked(True)
    etas = []
    for i in range(win.quality_combo.count()):
        win.quality_combo.setCurrentIndex(i)
        etas.append(eta_of(win))
    expect(len(set(etas)) > 1, "on CPU, changing the quality changes the ETA",
           " ".join(str(e) for e in etas))


def main() -> int:
    app = QApplication(sys.argv)
    check_model()

    win = MainWindow()
    win.show()

    def go() -> None:
        if win.table.rowCount() == 0:
            QTimer.singleShot(500, go)
            return
        try:
            check_gui(win)
        finally:
            print("\n" + ("ETA behaviour OK" if not failures
                          else f"{len(failures)} CHECK(S) FAILED: {failures}"))
            app.exit(1 if failures else 0)

    QTimer.singleShot(800, go)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
