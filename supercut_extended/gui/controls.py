"""Input widgets that do not change value when you scroll past them.

Every settings panel in this app lives inside a QScrollArea, and Qt's combo boxes,
spin boxes and sliders all react to the wheel whether or not they are focused. The
result is that scrolling down to reach a control silently rewrites the ones you
passed on the way -- moving the encoder preset, the quality or the output
resolution without a click and without anything to undo it.

Ignoring the event rather than swallowing it is what makes the panel still scroll:
an ignored wheel event propagates to the parent, which is the scroll area.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QDoubleSpinBox, QSlider, QSpinBox


class _WheelGuard:
    """Wheel only adjusts the value once the control has been focused deliberately."""

    def wheelEvent(self, event) -> None:  # noqa: D102 - Qt override
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


def _guarded(base):
    """Build a wheel-guarded subclass of `base` that never takes focus by wheel."""

    class Guarded(_WheelGuard, base):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            # StrongFocus, not the default WheelFocus: otherwise the first scroll
            # focuses the widget and every scroll after it changes the value.
            self.setFocusPolicy(Qt.StrongFocus)

    Guarded.__name__ = f"NoScroll{base.__name__}"
    Guarded.__qualname__ = Guarded.__name__
    return Guarded


NoScrollComboBox = _guarded(QComboBox)
NoScrollSpinBox = _guarded(QSpinBox)
NoScrollDoubleSpinBox = _guarded(QDoubleSpinBox)
NoScrollSlider = _guarded(QSlider)
