"""Application palette and stylesheet.

The stylesheet is built at runtime rather than being a constant, because the check
marks, radio dots, combo chevrons and spin carets are all rendered from the coolicons
SVG set and referenced by file path. See icons.css_icon.
"""

from __future__ import annotations

from . import icons

BG          = "#0b0e14"
SURFACE     = "#12161f"
SURFACE_2   = "#171c28"
BORDER      = "#232a39"
BORDER_SOFT = "#1b2130"
TEXT        = "#e6edf6"
TEXT_DIM    = "#8b98ad"
TEXT_FAINT  = "#5a6577"
ACCENT      = "#3b82f6"
ACCENT_HI   = "#60a5fa"
GOOD        = "#4ade80"


def build_style() -> str:
    """Compose the stylesheet, wiring in recoloured icons for the control glyphs."""
    def u(name: str, color: str, size: int = 18) -> str:
        path = icons.css_icon(name, color, size)
        return f"image: url({path});" if path else ""

    check_on = u("Interface/Checkbox_Check", "#ffffff", 20)
    check_off = u("Interface/Checkbox_Unchecked", TEXT_FAINT, 20)
    check_off_hover = u("Interface/Checkbox_Unchecked", TEXT_DIM, 20)
    radio_on = u("Interface/Radio_Fill", ACCENT_HI, 20)
    radio_off = u("Interface/Radio_Unchecked", TEXT_FAINT, 20)
    radio_off_hover = u("Interface/Radio_Unchecked", TEXT_DIM, 20)
    chevron = u("Arrow/Chevron_Down", TEXT_DIM, 16)
    caret_up = u("Arrow/Caret_Up_SM", TEXT_DIM, 14)
    caret_down = u("Arrow/Caret_Down_SM", TEXT_DIM, 14)

    return f"""
/* Only the window paints the base colour. Giving every QWidget a background makes
   labels, checkboxes and layouts stamp the window colour on top of the lighter card
   they sit in, which shows up as a faint rectangle around each piece of text. */
QMainWindow, QDialog {{ background: {BG}; }}
QWidget {{ color: {TEXT};
    font-family: "Segoe UI", "Yu Gothic UI", "Meiryo", sans-serif; font-size: 13px; }}
QLabel, QCheckBox, QRadioButton {{ background: transparent; }}
QScrollArea, QScrollArea > QWidget, QScrollArea > QWidget > QWidget {{
    background: transparent; }}
QSplitter {{ background: {BG}; }}

QLabel#h1 {{ font-size: 19px; font-weight: 700; color: {TEXT}; }}
QLabel#h2 {{ font-size: 12px; color: {TEXT_DIM}; }}
QLabel#summary {{ font-size: 13px; font-weight: 600; color: {TEXT}; }}
QLabel#timecode {{ font-family: Consolas, "Courier New", monospace;
    color: {TEXT_DIM}; padding-left: 8px; }}
QLabel#sectionLabel {{ color: {TEXT}; font-weight: 700; font-size: 12px;
    letter-spacing: 0.5px; }}
QLabel#fieldLabel {{ color: {TEXT_DIM}; font-size: 12px; }}
QLabel#captionLabel {{ color: {TEXT_DIM}; font-size: 12px;
    padding-left: 28px; margin-top: -2px; }}
QFrame#sectionRule {{ background: {BORDER}; border: 0; max-height: 1px; }}

QFrame#card {{ background: {SURFACE}; border: 1px solid {BORDER_SOFT};
    border-radius: 10px; }}

QSplitter::handle {{ background: {BORDER_SOFT}; }}
QSplitter::handle:horizontal {{ width: 4px; }}
QSplitter::handle:vertical {{ height: 4px; }}

QTableWidget {{ background: {SURFACE}; border: 1px solid {BORDER_SOFT};
    border-radius: 10px; gridline-color: transparent; outline: none; }}
QTableWidget::item {{ padding: 7px 6px; border: 0; }}
QTableWidget::item:selected {{ background: {ACCENT}; color: #ffffff; }}
/* The tick column. A checkable item is drawn by the view, not by a QCheckBox, so the
   QCheckBox::indicator rules below do not reach it -- without these it falls back to
   the native Windows check box and looks nothing like the rest of the app. */
QTableWidget::indicator {{ width: 20px; height: 20px; border: 0;
    background: transparent; }}
QTableWidget::indicator:unchecked {{ {check_off} }}
QTableWidget::indicator:unchecked:hover {{ {check_off_hover} }}
QTableWidget::indicator:checked {{ {check_on} }}
QHeaderView::section {{ background: transparent; color: {TEXT_DIM}; border: 0;
    border-bottom: 1px solid {BORDER}; padding: 8px 6px; font-weight: 700;
    font-size: 11px; }}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 7px;
    padding: 6px 9px; selection-background-color: {ACCENT}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
    border-color: {ACCENT}; }}
QLineEdit#search {{ padding-left: 32px; }}

QComboBox::drop-down {{ border: 0; width: 22px; }}
QComboBox::down-arrow {{ width: 14px; height: 14px; {chevron} }}
QComboBox QAbstractItemView {{ background: {SURFACE_2}; border: 1px solid {BORDER};
    selection-background-color: {ACCENT}; outline: none; padding: 4px; }}

QSpinBox, QDoubleSpinBox {{ padding-right: 22px; }}
QSpinBox::up-button, QDoubleSpinBox::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    subcontrol-origin: border; width: 20px; border: 0; background: transparent; }}
QSpinBox::up-button, QDoubleSpinBox::up-button {{ subcontrol-position: top right; }}
QSpinBox::down-button, QDoubleSpinBox::down-button {{ subcontrol-position: bottom right; }}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{
    background: #232c3d; border-radius: 4px; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ width: 12px; height: 12px; {caret_up} }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ width: 12px; height: 12px; {caret_down} }}

QPushButton {{ background: {SURFACE_2}; border: 1px solid {BORDER};
    border-radius: 7px; padding: 8px 15px; font-weight: 600; }}
QPushButton:hover {{ background: #1f2637; border-color: #33405a; }}
QPushButton:disabled {{ color: {TEXT_FAINT}; background: #141822;
    border-color: {BORDER_SOFT}; }}
QPushButton#primary {{ background: {ACCENT}; border-color: {ACCENT_HI}; color: #ffffff; }}
QPushButton#primary:hover {{ background: {ACCENT_HI}; }}
QPushButton#primary:disabled {{ background: #1b2536; border-color: {BORDER};
    color: {TEXT_FAINT}; }}
QPushButton#transport {{ padding: 0; border-radius: 7px; }}
QPushButton#transport:hover {{ background: #222b3d; }}

/* Editor tool palette: icon-only squares. The checked tool is filled with the accent
   so which tool is armed is readable at a glance, the way an NLE shows it. */
QPushButton#toolButton {{ padding: 0; border-radius: 8px; background: transparent;
    border: 1px solid transparent; }}
QPushButton#toolButton:hover {{ background: {SURFACE_2}; border-color: {BORDER}; }}
QPushButton#toolButton:checked {{ background: {ACCENT}; border-color: {ACCENT_HI}; }}
QPushButton#toolButton:checked:hover {{ background: {ACCENT_HI}; }}
QPushButton#toolButton:disabled {{ background: transparent;
    border-color: transparent; }}

QProgressBar {{ background: {SURFACE_2}; border: 1px solid {BORDER};
    border-radius: 8px; height: 22px; text-align: center; color: {TEXT}; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 7px; }}

QCheckBox, QRadioButton {{ spacing: 8px; padding: 3px; background: transparent; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 20px; height: 20px;
    border: 0; background: transparent; }}
QCheckBox::indicator:unchecked {{ {check_off} }}
QCheckBox::indicator:unchecked:hover {{ {check_off_hover} }}
QCheckBox::indicator:checked {{ {check_on} }}
QRadioButton::indicator:unchecked {{ {radio_off} }}
QRadioButton::indicator:unchecked:hover {{ {radio_off_hover} }}
QRadioButton::indicator:checked {{ {radio_on} }}

QSlider::groove:horizontal {{ height: 4px; background: {BORDER}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {TEXT}; width: 12px; height: 12px;
    margin: -5px 0; border-radius: 6px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}

QScrollArea {{ border: 0; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: #2b3446; border-radius: 5px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #3a465d; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QStatusBar {{ background: {SURFACE}; color: {TEXT_DIM};
    border-top: 1px solid {BORDER_SOFT}; }}
QToolTip {{ background: {SURFACE_2}; color: {TEXT}; border: 1px solid {BORDER};
    padding: 5px; border-radius: 5px; }}
QMenuBar {{ background: {BG}; }}
QMenuBar::item {{ padding: 5px 10px; }}
QMenuBar::item:selected {{ background: {SURFACE_2}; border-radius: 5px; }}
QMenu {{ background: {SURFACE_2}; border: 1px solid {BORDER}; padding: 5px; }}
QMenu::item {{ padding: 6px 24px 6px 10px; border-radius: 5px; }}
QMenu::item:selected {{ background: {ACCENT}; }}
"""


def checkbox_style(color: str) -> str:
    """Per-event-kind checkbox: label and check mark share the event's colour."""
    on = icons.css_icon("Interface/Checkbox_Check", color, 20)
    off = icons.css_icon("Interface/Checkbox_Unchecked", TEXT_FAINT, 20)
    rules = [f"QCheckBox {{ color: {color}; }}"]
    if on:
        rules.append(f"QCheckBox::indicator:checked {{ image: url({on}); }}")
    if off:
        rules.append(f"QCheckBox::indicator:unchecked {{ image: url({off}); }}")
    return "\n".join(rules)
