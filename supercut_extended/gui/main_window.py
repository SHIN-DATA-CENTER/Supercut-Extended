"""Main window: preview the match, choose what counts as a highlight, render on GPU.

Layout mirrors how Outplayed presents a match -- video on top, the event timeline
directly beneath it on the same time axis -- so the clip boundaries can be checked
against the footage before spending any time encoding.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, QSize, Qt, QThread, Signal
from PySide6.QtGui import QAction, QActionGroup, QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFrame,
    QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QProgressBar, QPushButton, QRadioButton, QScrollArea, QSizePolicy,
    QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .. import __version__, updater
from ..encoder import EncoderSpec, available_encoders
from ..library import matches_with_highlights, read_matches
from ..model import HIGHLIGHT_KINDS, Match, Media
from ..probe import MediaInfo, probe
from ..render import (RenderError, RenderJob, RenderOptions, render_each,
                      render_many)
from ..segments import build_segments, total_duration_s
from . import icons
from .i18n import event_label, fmt_duration, language, set_language, tr
from .player import VideoPlayer
from .style import ACCENT_HI, TEXT, TEXT_DIM, build_style, checkbox_style
from .timeline import TimelineWidget, event_color
from .update_dialog import UpdateCheck, UpdateDialog


class LibraryLoader(QThread):
    loaded = Signal(list)
    failed = Signal(str)

    def run(self) -> None:
        try:
            self.loaded.emit(matches_with_highlights(read_matches()))
        except Exception as exc:
            self.failed.emit(str(exc))


@dataclass
class BatchPlan:
    """Everything a render needs, resolved off the GUI thread.

    Only the *parameters* are captured here, not the segments: turning events into
    segments needs the real duration of every source, and probing a few dozen files
    would block the UI. The worker does both.
    """

    items: list[tuple[Path, list, str]]      # source, events, label
    kinds: list[str]
    pre_ms: float | None
    post_ms: float | None
    gap_ms: float
    options: RenderOptions
    combine: bool
    output: Path                             # a file when combining, else a folder


class RenderWorker(QObject):
    progressed = Signal(float, str)
    finished = Signal(object)                # list[RenderResult]
    failed = Signal(str)

    def __init__(self, plan: BatchPlan):
        super().__init__()
        self._plan = plan
        self.cancel = threading.Event()

    def _jobs(self) -> list[RenderJob]:
        plan = self._plan
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        jobs: list[RenderJob] = []
        for source, events, label in plan.items:
            info = probe(source)
            segments = build_segments(
                events, kinds=plan.kinds,
                pre_ms=plan.pre_ms, post_ms=plan.post_ms,
                duration_ms=info.duration_ms, gap_ms=plan.gap_ms,
            )
            if not segments:
                continue
            jobs.append(RenderJob(
                source=source, segments=segments, label=label,
                output=plan.output / f"{source.stem}-supercut-{stamp}.mp4",
            ))
        return jobs

    def run(self) -> None:
        plan = self._plan
        try:
            jobs = self._jobs()
            if not jobs:
                raise RenderError(tr("err.noevents.body"))
            emit = lambda f, m: self.progressed.emit(f, m)  # noqa: E731
            if plan.combine:
                results = [render_many(jobs, plan.output, plan.options,
                                       progress=emit, cancel=self.cancel)]
            else:
                results = render_each(jobs, plan.options,
                                      progress=emit, cancel=self.cancel)
            self.finished.emit(results)
        except Exception as exc:
            self.failed.emit(str(exc))


def card(*children, spacing: int = 10, margins=(14, 12, 14, 12)) -> QFrame:
    frame = QFrame()
    frame.setObjectName("card")
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(*margins)
    lay.setSpacing(spacing)
    for c in children:
        if isinstance(c, QWidget):
            lay.addWidget(c)
        else:
            lay.addLayout(c)
    return frame


def block(*children, spacing: int = 8) -> QWidget:
    """A titled group inside the settings card -- no frame, just its own spacing."""
    holder = QWidget()
    lay = QVBoxLayout(holder)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(spacing)
    for c in children:
        if isinstance(c, QWidget):
            lay.addWidget(c)
        else:
            lay.addLayout(c)
    return holder


def section(text: str, icon_name: str = "") -> QWidget:
    """A section header: icon, title, then a rule filling the remaining width.

    The rule is what makes the groups read as separate blocks -- a bare bold label
    on a card of the same colour did not separate anything.
    """
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(7)

    if icon_name:
        glyph = QLabel()
        glyph.setPixmap(icons.pixmap(icon_name, ACCENT_HI, 15))
        glyph.setFixedWidth(15)
        row.addWidget(glyph)

    title = QLabel(text)
    title.setObjectName("sectionLabel")
    row.addWidget(title)

    rule = QFrame()
    rule.setFrameShape(QFrame.HLine)
    rule.setObjectName("sectionRule")
    rule.setFixedHeight(1)
    row.addWidget(rule, 1)

    holder = QWidget()
    holder.setLayout(row)
    holder.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
    return holder


def caption(text: str) -> QLabel:
    """Small dim explanatory line, e.g. under a radio button."""
    lbl = QLabel(text)
    lbl.setObjectName("captionLabel")
    lbl.setWordWrap(True)
    return lbl


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = QSettings("SupercutExtended", "gui")
        set_language(self.settings.value("language", "ja"))

        self.setWindowTitle(tr("app.title"))
        self.resize(1400, 900)
        self.setStyleSheet(build_style())
        self.setWindowIcon(icons.app_icon())

        self._matches: list[Match] = []
        self._match: Match | None = None
        self._media: Media | None = None
        self._info: MediaInfo | None = None
        self._kind_boxes: dict[str, QCheckBox] = {}
        self._segments: list = []
        self._thread: QThread | None = None
        self._worker: RenderWorker | None = None
        self._last_output: Path | None = None
        # Ticked matches, kept by id so search filtering never loses a selection.
        self._checked: set[str] = set()
        self._populating = False
        self._output_touched = False

        self._build_ui()
        self._restore_settings()
        self._load_library()
        self._start_update_check()

    # -- construction -------------------------------------------------------
    def _build_ui(self) -> None:
        self._build_menu()

        # Right side is built first: the settings pane wires signals that reach into
        # the timeline, so the preview widgets have to exist before it is created.
        right = self._build_right()
        left = self._build_left()
        left.setMinimumWidth(430)

        split = QSplitter(Qt.Horizontal)
        split.addWidget(left)
        split.addWidget(right)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([500, 900])
        self.setCentralWidget(split)
        self.statusBar().showMessage(tr("loading"))

    def _build_menu(self) -> None:
        view = self.menuBar().addMenu(tr("menu.view"))
        lang_menu = view.addMenu(tr("menu.language"))
        lang_menu.setIcon(icons.icon("Navigation/Globe", TEXT, 16))
        group = QActionGroup(self)
        for code, label in (("ja", "日本語"), ("en", "English")):
            act = QAction(label, self, checkable=True)
            act.setChecked(language() == code)
            act.triggered.connect(lambda _c, x=code: self._set_language(x))
            group.addAction(act)
            lang_menu.addAction(act)

        help_menu = self.menuBar().addMenu(tr("menu.help"))
        check = QAction(tr("menu.check_update"), self)
        check.setIcon(icons.icon("Arrow/Arrows_Reload_01", TEXT, 16))
        check.triggered.connect(lambda: self._start_update_check(manual=True))
        help_menu.addAction(check)
        about = QAction(tr("menu.about"), self)
        about.setIcon(icons.icon("Warning/Info", TEXT, 16))
        about.triggered.connect(self._show_about)
        help_menu.addAction(about)

    def _set_language(self, code: str) -> None:
        if code == language():
            return
        self.settings.setValue("language", code)
        QMessageBox.information(self, tr("menu.language"), tr("dlg.restart"))

    # -- updates ------------------------------------------------------------
    def _start_update_check(self, manual: bool = False) -> None:
        """Look for a newer release on GitHub.

        The automatic check is rate-limited to once a day and stays completely silent
        unless something newer exists -- GitHub allows only 60 unauthenticated calls
        an hour, and nobody wants a dialog on every launch.
        """
        if not updater.enabled():
            if manual:
                QMessageBox.information(self, tr("update.title"),
                                        tr("update.disabled"))
            return

        if not manual:
            last = float(self.settings.value("update/last_check", 0) or 0)
            if time.time() - last < 24 * 3600:
                return
        self.settings.setValue("update/last_check", time.time())

        if manual:
            self.statusBar().showMessage(tr("update.checking"), 8000)

        self._update_check = UpdateCheck()
        self._update_check.found.connect(
            lambda rel: self._on_update_found(rel, manual), Qt.QueuedConnection)
        self._update_check.none.connect(
            lambda: self._on_update_none(manual), Qt.QueuedConnection)
        self._update_check.start()

    def _on_update_found(self, release, manual: bool) -> None:
        if not manual and self.settings.value("update/skip", "") == release.tag:
            return
        dialog = UpdateDialog(release, self)
        dialog.setStyleSheet(self.styleSheet())
        dialog.exec()
        skipped = dialog.skipped_version()
        if skipped:
            self.settings.setValue("update/skip", skipped)

    def _on_update_none(self, manual: bool) -> None:
        if manual:
            self.statusBar().showMessage(
                tr("update.uptodate", version=__version__), 8000)

    def _show_about(self) -> None:
        QMessageBox.information(self, tr("menu.about"),
                                tr("about.body", version=__version__))

    def _build_left(self) -> QWidget:
        """Match list on top, every control beneath it.

        The right-hand side is kept purely for the footage and its timelines, so all
        the knobs live here where they can be scrolled independently of the video.
        """
        stack = QSplitter(Qt.Vertical)
        stack.addWidget(self._build_match_list())
        stack.addWidget(self._build_settings())
        stack.setStretchFactor(0, 2)
        stack.setStretchFactor(1, 3)
        stack.setSizes([330, 520])
        return stack

    def _build_match_list(self) -> QWidget:
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(12, 12, 6, 6)
        lay.setSpacing(10)

        self.search = QLineEdit(placeholderText=tr("search.placeholder"))
        self.search.setObjectName("search")
        self.search.textChanged.connect(self._refresh_match_table)
        glass = QLabel(self.search)
        glass.setPixmap(icons.pixmap("Interface/Search_Magnifying_Glass", TEXT_DIM, 16))
        glass.setStyleSheet("background: transparent; border: 0;")
        glass.move(9, 8)
        lay.addWidget(self.search)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            [tr("col.pick"), tr("col.when"), tr("col.game"),
             tr("col.length"), tr("col.highlights")])
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        # Row selection still drives the preview; the tick column is what decides
        # what gets built, so the two stay independent.
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Fixed)
        # 20px indicator plus the 6px item padding on each side.
        self.table.setColumnWidth(0, 34)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.table.itemSelectionChanged.connect(self._on_match_selected)
        self.table.itemChanged.connect(self._on_item_changed)
        lay.addWidget(self.table, 1)

        self.pick_label = QLabel(tr("sel.none"))
        self.pick_label.setObjectName("fieldLabel")
        self.pick_label.setToolTip(tr("sel.tip"))
        # No fixed height: QPushButton carries 8px of vertical padding, and forcing a
        # shorter box clips the text rather than tightening the button.
        all_btn = QPushButton(tr("sel.all"))
        all_btn.clicked.connect(lambda: self._set_all_checked(True))
        none_btn = QPushButton(tr("sel.clear"))
        none_btn.clicked.connect(lambda: self._set_all_checked(False))

        prow = QHBoxLayout()
        prow.setSpacing(6)
        prow.addWidget(self.pick_label, 1)
        prow.addWidget(all_btn)
        prow.addWidget(none_btn)
        lay.addLayout(prow)
        return box

    def _build_right(self) -> QWidget:
        """Footage only: the video, the scrub/segment lane and the event lane."""
        outer = QWidget()
        lay = QVBoxLayout(outer)
        lay.setContentsMargins(6, 12, 12, 12)
        lay.setSpacing(10)

        self.header = QLabel(tr("select.match"))
        self.header.setObjectName("h1")
        self.subheader = QLabel("")
        self.subheader.setObjectName("h2")
        lay.addWidget(self.header)
        lay.addWidget(self.subheader)
        lay.addWidget(self._build_preview(), 1)
        return outer

    def _build_preview(self) -> QWidget:
        self.player = VideoPlayer()
        self.player.positionChanged.connect(self._on_player_position)
        self.player.prev_btn.clicked.connect(lambda: self._jump_event(-1))
        self.player.next_btn.clicked.connect(lambda: self._jump_event(+1))

        self.timeline = TimelineWidget()
        self.timeline.seekRequested.connect(self.player.seek)

        self.legend = QLabel("")
        self.legend.setTextFormat(Qt.RichText)
        self.legend.setStyleSheet(f"color: {TEXT_DIM};")

        return card(self.player, self.timeline, self.legend, spacing=8,
                    margins=(12, 12, 12, 10))

    def _build_settings(self) -> QWidget:
        """One card holding every setting, scrolled internally.

        Earlier this was a stack of separate cards inside a scroll area, which put the
        settings scrollbar *outside* the card borders while the match table's scrollbar
        sat *inside* its own border -- two bars at two different x positions. Wrapping
        the scroll area in a single card puts both bars just inside a frame at the same
        offset. The section headers already do the visual separating that the separate
        cards used to.
        """
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        # Everything in here must fit the column width; growing sideways
        # and hiding controls behind a scrollbar is never the right answer.
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(0, 0, 8, 0)
        lay.setSpacing(18)

        # --- events
        self.kinds_layout = QGridLayout()
        self.kinds_layout.setSpacing(6)
        lay.addWidget(block(section(tr("group.events"), "Interface/Filter"),
                            self.kinds_layout))

        # --- timing
        self.use_defaults = QCheckBox(tr("timing.defaults"))
        self.use_defaults.setChecked(True)
        self.use_defaults.toggled.connect(self._on_use_defaults)

        self.pre_spin = self._spin(0.0, 60.0, 8.0)
        self.post_spin = self._spin(0.0, 60.0, 2.0)
        self.gap_spin = self._spin(0.0, 30.0, 0.0)

        # The settings column is narrow, so labels sit above their controls and the
        # three spin boxes share one row rather than trailing off the right edge.
        trow = QHBoxLayout()
        trow.setSpacing(6)
        for key, spin in ((("timing.before"), self.pre_spin),
                          (("timing.after"), self.post_spin),
                          (("timing.gap"), self.gap_spin)):
            cell = QVBoxLayout()
            cell.setSpacing(3)
            cap = QLabel(tr(key))
            cap.setObjectName("fieldLabel")
            cell.addWidget(cap)
            cell.addWidget(spin)
            trow.addLayout(cell)
        trow.addStretch(1)
        lay.addWidget(block(section(tr("group.timing"), "Interface/Slider_01"),
                            self.use_defaults, trow))

        # --- output
        self.mode_encode = QRadioButton(tr("out.mode.encode"))
        self.mode_copy = QRadioButton(tr("out.mode.copy"))
        self.mode_encode.setChecked(True)
        self.mode_encode.toggled.connect(self._on_mode_changed)

        self.encoder_combo = self._combo()
        self.preset_combo = self._combo(fixed=False)
        self.quality_spin = QSpinBox(minimum=10, maximum=40)
        self.quality_spin.setValue(23)
        self.encoder_combo.currentIndexChanged.connect(self._on_encoder_changed)

        erow = QHBoxLayout()
        erow.setSpacing(6)
        erow.addLayout(self._field(tr("out.preset"), self.preset_combo))
        erow.addLayout(self._field(tr("out.quality"), self.quality_spin))
        erow.addStretch(1)

        self.audio_combo = self._combo()

        self.output_edit = QLineEdit()
        self.output_edit.setMinimumWidth(60)
        # textEdited (not textChanged) fires only for real typing, so the app can keep
        # proposing defaults until the user takes the field over.
        self.output_edit.textEdited.connect(self._on_output_edited)
        browse = QPushButton(tr("out.browse"))
        browse.setIcon(icons.icon("File/Folder_Open", TEXT, 16))
        browse.setIconSize(QSize(16, 16))
        browse.clicked.connect(self._browse_output)
        orow = QHBoxLayout()
        orow.setSpacing(6)
        orow.addWidget(self.output_edit, 1)
        orow.addWidget(browse)

        self.shape_combine = QRadioButton(tr("out.shape.combine"))
        self.shape_combine.setChecked(True)
        self.shape_separate = QRadioButton(tr("out.shape.separate"))
        # No per-widget stylesheet: these are radios like the encode/copy pair above
        # and take their look from the global sheet. checkbox_style() would give them
        # the square check glyphs used by the per-event boxes.
        #
        # Both are connected, and only the rising edge acts. Qt unchecks the outgoing
        # radio first, so reacting to that would run while neither is checked yet and
        # read the old shape back.
        for w in (self.shape_combine, self.shape_separate):
            w.toggled.connect(self._on_shape_changed)
            w.setEnabled(False)     # only meaningful once several are ticked

        self.output_caption = QLabel(tr("out.file"))
        self.output_caption.setObjectName("fieldLabel")
        ocol = QVBoxLayout()
        ocol.setSpacing(3)
        ocol.addWidget(self.output_caption)
        ocol.addLayout(orow)

        lay.addWidget(block(
            section(tr("group.output"), "Interface/Download"),
            self.mode_encode, caption(tr("out.mode.encode.desc")),
            self.mode_copy, caption(tr("out.mode.copy.desc")),
            self._field(tr("out.encoder"), self.encoder_combo),
            erow,
            self._field(tr("out.audio"), self.audio_combo),
            caption(tr("out.shape")),
            self.shape_combine, self.shape_separate,
            ocol))

        # --- action
        self.summary = QLabel("")
        self.summary.setObjectName("summary")
        self.progress = QProgressBar()
        self.progress.setVisible(False)

        self.build_btn = QPushButton(tr("btn.build"))
        self.build_btn.setObjectName("primary")
        self.build_btn.setIcon(icons.icon("Edit/Layers", "#ffffff", 17))
        self.build_btn.setIconSize(QSize(17, 17))
        self.build_btn.setEnabled(False)
        self.build_btn.clicked.connect(self._start_render)
        self.cancel_btn = QPushButton(tr("btn.cancel"))
        self.cancel_btn.setIcon(icons.icon("Menu/Close_MD", TEXT, 16))
        self.cancel_btn.setIconSize(QSize(16, 16))
        self.cancel_btn.setVisible(False)
        self.cancel_btn.clicked.connect(self._cancel_render)
        self.reveal_btn = QPushButton(tr("btn.reveal"))
        self.reveal_btn.setIcon(icons.icon("File/Folder_Open", TEXT, 16))
        self.reveal_btn.setIconSize(QSize(16, 16))
        self.reveal_btn.setEnabled(False)
        self.reveal_btn.clicked.connect(self._reveal_output)

        brow = QHBoxLayout()
        brow.addWidget(self.build_btn)
        brow.addWidget(self.cancel_btn)
        brow.addStretch(1)
        brow.addWidget(self.reveal_btn)

        lay.addStretch(1)
        scroll.setWidget(inner)

        holder = QWidget()
        outer = QVBoxLayout(holder)
        outer.setContentsMargins(12, 6, 6, 12)
        outer.setSpacing(10)
        # Zero right margin: the scrollbar then sits flush against the card's inner
        # border, exactly where the table's own scrollbar sits inside its frame, so
        # the two bars line up on the same pixel column.
        outer.addWidget(card(scroll, margins=(14, 12, 0, 12)), 1)
        outer.addWidget(card(self.summary, self.progress, brow))

        self._populate_encoders()
        return holder

    @staticmethod
    def _combo(fixed: bool = True) -> QComboBox:
        """A combo that shrinks with its column instead of demanding its widest item.

        Left at the default AdjustToContents, one long entry such as
        "OBS Audio Handler (aac, 2ch)" sets a minimum width for the whole panel and
        forces a horizontal scrollbar.
        """
        combo = QComboBox()
        combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        combo.setMinimumContentsLength(6)
        if fixed:
            combo.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        return combo

    @staticmethod
    def _field(caption: str, control) -> QVBoxLayout:
        """A labelled control, stacked vertically to survive a narrow column."""
        box = QVBoxLayout()
        box.setSpacing(3)
        cap = QLabel(caption)
        cap.setObjectName("fieldLabel")
        box.addWidget(cap)
        if isinstance(control, QWidget):
            box.addWidget(control)
        else:
            box.addLayout(control)
        return box

    @staticmethod
    def _spin(lo: float, hi: float, val: float) -> QDoubleSpinBox:
        s = QDoubleSpinBox(suffix=" s", minimum=lo, maximum=hi, singleStep=0.5)
        s.setValue(val)
        s.setMinimumWidth(96)
        return s

    def _populate_encoders(self) -> None:
        try:
            specs = available_encoders()
        except Exception as exc:
            self._specs: list[EncoderSpec] = []
            self.encoder_combo.addItem(tr("encoder.missing"), "")
            QMessageBox.critical(self, tr("err.ffmpeg.title"), str(exc))
            return
        self._specs = list(specs)
        for spec in specs:
            self.encoder_combo.addItem(spec.label, spec.name)
        if not specs:
            self.encoder_combo.addItem(tr("encoder.missing"), "")
        elif not specs[0].hardware:
            self.statusBar().showMessage(tr("warn.cpu"), 10000)
        self._on_encoder_changed()

        for w in (self.pre_spin, self.post_spin, self.gap_spin):
            w.valueChanged.connect(self._recompute)

    # -- persistence --------------------------------------------------------
    def _restore_settings(self) -> None:
        s = self.settings
        geo = s.value("geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        if s.value("mode", "encode") == "copy":
            self.mode_copy.setChecked(True)
        if s.value("shape", "combine") == "separate":
            self.shape_separate.setChecked(True)
        enc = s.value("encoder")
        if enc:
            i = self.encoder_combo.findData(enc)
            if i >= 0:
                self.encoder_combo.setCurrentIndex(i)
        preset = s.value("preset")
        if preset and self.preset_combo.findText(preset) >= 0:
            self.preset_combo.setCurrentText(preset)
        self.quality_spin.setValue(int(s.value("quality", 23)))
        self.use_defaults.setChecked(s.value("use_defaults", "true") == "true")
        self.pre_spin.setValue(float(s.value("pre", 8.0)))
        self.post_spin.setValue(float(s.value("post", 2.0)))
        self.gap_spin.setValue(float(s.value("gap", 0.0)))
        self.player.volume.setValue(int(s.value("volume", 70)))
        self._on_mode_changed()

    def _save_settings(self) -> None:
        s = self.settings
        s.setValue("geometry", self.saveGeometry())
        s.setValue("mode", "copy" if self.mode_copy.isChecked() else "encode")
        s.setValue("shape", "separate" if self.shape_separate.isChecked() else "combine")
        s.setValue("encoder", self.encoder_combo.currentData() or "")
        s.setValue("preset", self.preset_combo.currentText())
        s.setValue("quality", self.quality_spin.value())
        s.setValue("use_defaults", "true" if self.use_defaults.isChecked() else "false")
        s.setValue("pre", self.pre_spin.value())
        s.setValue("post", self.post_spin.value())
        s.setValue("gap", self.gap_spin.value())
        s.setValue("volume", self.player.volume.value())

    # -- library ------------------------------------------------------------
    def _load_library(self) -> None:
        self._loader = LibraryLoader()
        self._loader.loaded.connect(self._on_library)
        self._loader.failed.connect(
            lambda m: QMessageBox.critical(self, tr("err.lib.title"), m))
        self._loader.start()

    def _on_library(self, matches: list) -> None:
        self._matches = matches
        self._refresh_match_table()
        self.statusBar().showMessage(tr("loaded", n=len(matches)), 6000)
        if matches:
            self.table.selectRow(0)

    def _visible_matches(self) -> list[Match]:
        needle = self.search.text().strip().lower()
        if not needle:
            return self._matches
        return [m for m in self._matches
                if needle in f"{m.game_name} {m.info.get('map', '')} "
                             f"{m.info.get('gameMode', '')}".lower()]

    def _refresh_match_table(self) -> None:
        rows = self._visible_matches()
        # Repopulating fires itemChanged for every cell; ignore those so filtering the
        # list never looks like the user ticking boxes.
        self._populating = True
        try:
            self.table.setRowCount(len(rows))
            for i, m in enumerate(rows):
                counts = m.event_counts()
                highlights = sum(v for k, v in counts.items() if k in HIGHLIGHT_KINDS)
                name = m.game_name
                if m.info.get("map"):
                    name += f"  ·  {m.info['map']}"
                tip = f"{m.label()}\n" + ", ".join(
                    f"{event_label(k)}: {v}" for k, v in sorted(counts.items()))

                pick = QTableWidgetItem()
                pick.setFlags((pick.flags() | Qt.ItemIsUserCheckable)
                              & ~Qt.ItemIsSelectable)
                pick.setCheckState(Qt.Checked if m.match_id in self._checked
                                   else Qt.Unchecked)
                pick.setToolTip(tr("sel.tip"))
                self.table.setItem(i, 0, pick)

                cells = [m.started_at.strftime("%m-%d %H:%M"), name,
                         fmt_duration(m.duration_s), str(highlights)]
                for c, text in enumerate(cells, start=1):
                    item = QTableWidgetItem(text)
                    item.setToolTip(tip)
                    if c == 4:
                        item.setForeground(
                            QColor("#4ade80" if highlights else "#5a6577"))
                    self.table.setItem(i, c, item)
        finally:
            self._populating = False
        self._update_pick_label()

    # -- multi-select -------------------------------------------------------
    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._populating or item.column() != 0:
            return
        rows = self._visible_matches()
        row = item.row()
        if not (0 <= row < len(rows)):
            return
        match_id = rows[row].match_id
        if item.checkState() == Qt.Checked:
            self._checked.add(match_id)
        else:
            self._checked.discard(match_id)
        self._update_pick_label()
        self._rebuild_kind_boxes()
        self._recompute()

    def _set_all_checked(self, checked: bool) -> None:
        """Apply to what the search currently shows, not the whole library."""
        rows = self._visible_matches()
        ids = {m.match_id for m in rows}
        if checked:
            self._checked |= ids
        else:
            self._checked -= ids
        self._populating = True
        try:
            for i in range(self.table.rowCount()):
                cell = self.table.item(i, 0)
                if cell is not None:
                    cell.setCheckState(Qt.Checked if checked else Qt.Unchecked)
        finally:
            self._populating = False
        self._update_pick_label()
        self._rebuild_kind_boxes()
        self._recompute()

    def _checked_matches(self) -> list[Match]:
        """Ticked matches in chronological order, whether or not they are visible."""
        return sorted((m for m in self._matches if m.match_id in self._checked),
                      key=lambda m: m.start_time_ms)

    def _target_matches(self) -> list[Match]:
        """What Build will act on: the ticked matches, else the previewed one."""
        chosen = self._checked_matches()
        if chosen:
            return chosen
        return [self._match] if self._match else []

    def _update_pick_label(self) -> None:
        n = len(self._checked)
        self.pick_label.setText(tr("sel.count", n=n) if n else tr("sel.none"))
        multi = n > 1
        for w in (self.shape_combine, self.shape_separate):
            w.setEnabled(multi)
        self._sync_output_field()

    # -- selection ----------------------------------------------------------
    def _on_match_selected(self) -> None:
        rows = self._visible_matches()
        idx = self.table.currentRow()
        if not (0 <= idx < len(rows)):
            return
        match = rows[idx]
        media = match.playable_medias[0] if match.playable_medias else None
        if media is None or media.path is None:
            return

        self._match, self._media = match, media
        try:
            self._info = probe(media.path)
        except Exception as exc:
            self._info = None
            self.statusBar().showMessage(tr("status.probe_fail", err=exc), 8000)
            return

        self.header.setText(match.label())
        self.subheader.setText(
            f"{media.path.name}    {self._info.width}x{self._info.height} "
            f"{self._info.fps:.0f}fps    {fmt_duration(self._info.duration_s)}")

        self.player.load(media.path)
        try:
            self._rebuild_kind_boxes()
            self._rebuild_audio(self._info)
            self._refresh_default_output()
            self._recompute()
        except Exception as exc:
            self.statusBar().showMessage(tr("status.load_fail", err=exc), 15000)
            raise

    def _target_event_counts(self) -> dict[str, int]:
        """Events per kind across everything Build will act on, not just the preview.

        With several matches ticked the tallies have to add up over all of them --
        counting only the previewed recording made the numbers disagree with what was
        actually about to be rendered.
        """
        counts: dict[str, int] = {}
        for match in self._target_matches():
            media = match.playable_medias[0] if match.playable_medias else None
            source = media.event_counts() if media else match.event_counts()
            for kind, n in source.items():
                counts[kind] = counts.get(kind, 0) + n
        return counts

    def _rebuild_kind_boxes(self) -> None:
        # Ticking another match rebuilds these boxes, so the kinds already chosen have
        # to be carried over or the selection would reset on every tick.
        previous = {kind: cb.isChecked() for kind, cb in self._kind_boxes.items()}
        while self.kinds_layout.count():
            item = self.kinds_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._kind_boxes.clear()

        counts = self._target_event_counts()
        for i, (kind, n) in enumerate(sorted(counts.items(), key=lambda kv: -kv[1])):
            cb = QCheckBox(f"{event_label(kind)}  ({n})")
            # setChecked before connecting, so restoring state does not fire _recompute
            # once per box while the panel is still being rebuilt.
            cb.setChecked(previous.get(kind, kind in HIGHLIGHT_KINDS))
            cb.toggled.connect(self._recompute)
            cb.setStyleSheet(checkbox_style(event_color(kind).name()))
            self.kinds_layout.addWidget(cb, i // 2, i % 2)
            self._kind_boxes[kind] = cb

        # The legend labels the timeline, which only ever shows the previewed
        # recording, so it stays tied to that rather than to the whole selection.
        shown = (self._media.event_counts() if self._media else counts) or counts
        self.legend.setText("　".join(
            f"<span style='color:{event_color(k).name()}'>&#9632;</span> {event_label(k)}"
            for k in shown))

    def _rebuild_audio(self, info: MediaInfo) -> None:
        self.audio_combo.clear()
        for track in info.audio:
            self.audio_combo.addItem(track.label(), str(track.index))
        if len(info.audio) > 1:
            self.audio_combo.addItem(tr("audio.all"), "all")
            self.audio_combo.addItem(tr("audio.mix"), "mix")
        if not info.audio:
            self.audio_combo.addItem(tr("audio.none"), "none")

    def _default_output(self, source: Path) -> Path:
        parents = source.parents
        root = parents[2] if len(parents) >= 3 else source.parent
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        return root / "Exports" / f"{source.stem}-supercut-{stamp}.mp4"

    def _refresh_default_output(self) -> None:
        """Propose an output path, unless the user has typed one of their own.

        With several matches ticked the name cannot follow a single recording, so a
        combined montage gets its own name and separate output gets just the folder.
        """
        if self._output_touched:
            return
        targets = self._target_matches()
        sources = [m.playable_medias[0].path for m in targets
                   if m.playable_medias and m.playable_medias[0].path]
        if not sources:
            return

        default = self._default_output(sources[0])
        if self._writing_separate():
            self.output_edit.setText(str(default.parent))
        elif len(sources) > 1:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            self.output_edit.setText(str(
                default.parent / f"supercut-{len(sources)}-matches-{stamp}.mp4"))
        else:
            self.output_edit.setText(str(default))

    # -- playback -----------------------------------------------------------
    def _on_player_position(self, seconds: float) -> None:
        self.timeline.set_playhead(seconds)

    def _jump_event(self, direction: int) -> None:
        times = self.timeline.event_times()
        if not times:
            return
        now = self.player.position_s()
        if direction > 0:
            nxt = next((t for t in times if t > now + 0.25), times[-1])
        else:
            nxt = next((t for t in reversed(times) if t < now - 0.75), times[0])
        self.player.seek(nxt)

    # -- settings changes ---------------------------------------------------
    def _on_use_defaults(self, checked: bool) -> None:
        self.pre_spin.setEnabled(not checked)
        self.post_spin.setEnabled(not checked)
        self._recompute()

    def _on_mode_changed(self) -> None:
        encoding = self.mode_encode.isChecked()
        for w in (self.encoder_combo, self.preset_combo, self.quality_spin):
            w.setEnabled(encoding)
        self._recompute()

    def _on_encoder_changed(self) -> None:
        self.preset_combo.clear()
        name = self.encoder_combo.currentData()
        spec = next((s for s in getattr(self, "_specs", []) if s.name == name), None)
        if spec is None:
            return
        self.preset_combo.addItems(list(spec.presets))
        self.preset_combo.setCurrentText(spec.default_preset)

    def _selected_kinds(self) -> list[str]:
        return [k for k, cb in self._kind_boxes.items() if cb.isChecked()]

    def _recompute(self) -> None:
        if not self._media or not self._info:
            return
        kinds = self._selected_kinds()
        use_defaults = self.use_defaults.isChecked()
        self._segments = build_segments(
            self._media.events,
            kinds=kinds,
            pre_ms=None if use_defaults else self.pre_spin.value() * 1000,
            post_ms=None if use_defaults else self.post_spin.value() * 1000,
            duration_ms=self._info.duration_ms,
            gap_ms=self.gap_spin.value() * 1000,
        )
        shown = [e for e in self._media.events if not kinds or e.kind in kinds]
        self.timeline.set_data(self._info.duration_s, shown, self._segments)

        # Measured on an RTX 3070: ~4.3x realtime re-encoding at p4, ~120x copying.
        rate = 120.0 if self.mode_copy.isChecked() else 4.3
        targets = self._target_matches()

        if len(targets) > 1:
            # Estimated from the library's own durations rather than probing every
            # file: ffprobe on a few dozen recordings would stall the UI on each
            # keystroke. The worker probes properly before anything is encoded.
            n_events = n_segments = 0
            content = 0.0
            for match in targets:
                media = match.playable_medias[0] if match.playable_medias else None
                if media is None:
                    continue
                segs = build_segments(
                    media.events, kinds=kinds,
                    pre_ms=None if use_defaults else self.pre_spin.value() * 1000,
                    post_ms=None if use_defaults else self.post_spin.value() * 1000,
                    duration_ms=media.duration_s * 1000 or None,
                    gap_ms=self.gap_spin.value() * 1000,
                )
                n_events += sum(1 for e in media.events if e.kind in kinds)
                n_segments += len(segs)
                content += total_duration_s(segs)
            if n_segments:
                self.summary.setText(tr(
                    "summary.multi", matches=len(targets), events=n_events,
                    segments=n_segments, length=fmt_duration(content),
                    eta=content / rate))
            else:
                self.summary.setText(tr("summary.none"))
            self.build_btn.setEnabled(bool(n_segments) and self._thread is None)
            return

        content = total_duration_s(self._segments)
        n_events = sum(1 for e in self._media.events if e.kind in kinds)
        if self._segments:
            self.summary.setText(tr(
                "summary", events=n_events, segments=len(self._segments),
                length=fmt_duration(content), eta=content / rate))
        else:
            self.summary.setText(tr("summary.none"))
        self.build_btn.setEnabled(bool(self._segments) and self._thread is None)

    def _on_output_edited(self, _text: str) -> None:
        self._output_touched = True

    def _writing_separate(self) -> bool:
        """True when the run will produce one file per match rather than one montage."""
        return len(self._checked) > 1 and self.shape_separate.isChecked()

    def _on_shape_changed(self, checked: bool) -> None:
        if not checked:
            return          # the radio being switched on reports the settled state
        self._sync_output_field()
        self._recompute()

    def _sync_output_field(self) -> None:
        """Swap the output box between a file path and a folder.

        Separate output writes several files, so a single filename would be
        meaningless -- the field becomes the folder they land in.
        """
        separate = self._writing_separate()
        self.output_caption.setText(tr("out.dir") if separate else tr("out.file"))
        if self._output_touched:
            # Respect a hand-typed path, but it still has to change shape.
            current = self.output_edit.text().strip()
            if current:
                path = Path(current)
                if separate and path.suffix.lower() == ".mp4":
                    self.output_edit.setText(str(path.parent))
                return
        self._refresh_default_output()

    def _browse_output(self) -> None:
        if self._writing_separate():
            folder = QFileDialog.getExistingDirectory(
                self, tr("out.dir"), self.output_edit.text() or "")
            if folder:
                self.output_edit.setText(folder)
            return
        path, _ = QFileDialog.getSaveFileName(
            self, tr("out.file"), self.output_edit.text() or "", "MP4 (*.mp4)")
        if path:
            self.output_edit.setText(path)

    # -- render -------------------------------------------------------------
    def _start_render(self) -> None:
        if self._thread is not None:
            return
        targets = self._target_matches()
        if not targets:
            return

        items: list[tuple[Path, list, str]] = []
        for match in targets:
            media = match.playable_medias[0] if match.playable_medias else None
            if media is not None and media.path is not None:
                items.append((media.path, list(media.events), match.label()))
        if not items:
            return

        separate = self._writing_separate()
        out = Path(self.output_edit.text().strip())
        if not str(out).strip() or (not separate and not out.name):
            QMessageBox.warning(
                self, tr("err.output.title"),
                tr("err.output.dir") if separate else tr("err.output.body"))
            return

        spec = next((s for s in self._specs
                     if s.name == self.encoder_combo.currentData()), None)
        if spec is None:
            QMessageBox.critical(self, tr("err.encoder.title"), tr("err.encoder.body"))
            return

        options = RenderOptions(
            encoder=spec,
            preset=self.preset_combo.currentText() or spec.default_preset,
            quality=self.quality_spin.value(),
            audio=self.audio_combo.currentData() or "0",
            mode="copy" if self.mode_copy.isChecked() else "encode",
        )
        use_defaults = self.use_defaults.isChecked()
        plan = BatchPlan(
            items=items,
            kinds=self._selected_kinds(),
            pre_ms=None if use_defaults else self.pre_spin.value() * 1000,
            post_ms=None if use_defaults else self.post_spin.value() * 1000,
            gap_ms=self.gap_spin.value() * 1000,
            options=options,
            combine=not separate,
            output=out,
        )

        self._thread = QThread(self)
        self._worker = RenderWorker(plan)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        # render() fans segments out over a ThreadPoolExecutor, so these are emitted
        # from plain worker threads Qt does not own. Queue them explicitly so the
        # slots always run on the GUI thread.
        self._worker.progressed.connect(self._on_progress, Qt.QueuedConnection)
        self._worker.finished.connect(self._on_done, Qt.QueuedConnection)
        self._worker.failed.connect(self._on_failed, Qt.QueuedConnection)
        self._thread.start()

        self.progress.setVisible(True)
        self.progress.setValue(0)
        self.build_btn.setEnabled(False)
        self.cancel_btn.setVisible(True)
        self.reveal_btn.setEnabled(False)
        self.statusBar().showMessage(tr("status.building"))

    def _on_progress(self, frac: float, msg: str) -> None:
        self.progress.setValue(int(frac * 100))
        self.progress.setFormat(f"{msg}  -  %p%")

    def _teardown_thread(self) -> None:
        if self._thread:
            self._thread.quit()
            self._thread.wait(4000)
        self._thread = None
        self._worker = None
        self.cancel_btn.setVisible(False)
        # _recompute owns the button's enabled state for both the single and the
        # multi-match case, so re-run it rather than guessing here.
        self._recompute()

    def _on_done(self, results: list) -> None:
        self._teardown_thread()
        self.progress.setValue(100)
        if not results:
            return
        self._last_output = results[-1].output
        self.reveal_btn.setEnabled(True)
        if len(results) == 1:
            result = results[0]
            self.statusBar().showMessage(tr(
                "status.done", name=result.output.name, secs=result.elapsed_s,
                speed=result.speed_x, mb=result.size_bytes / 1e6), 20000)
        else:
            self.statusBar().showMessage(tr(
                "status.done.multi", n=len(results),
                secs=sum(r.elapsed_s for r in results),
                mb=sum(r.size_bytes for r in results) / 1e6), 20000)

    def _on_failed(self, message: str) -> None:
        self._teardown_thread()
        self.progress.setVisible(False)
        if "cancel" in message.lower():
            self.statusBar().showMessage(tr("status.cancelled"), 5000)
        else:
            QMessageBox.critical(self, tr("err.render.title"), message)

    def _cancel_render(self) -> None:
        if self._worker:
            self._worker.cancel.set()
            self.statusBar().showMessage(tr("status.cancelling"), 3000)

    def _reveal_output(self) -> None:
        path = self._last_output
        if path and Path(path).exists():
            subprocess.Popen(["explorer", "/select,", str(Path(path))])
        elif path:
            os.startfile(Path(path).parent)  # noqa: S606

    def closeEvent(self, event) -> None:
        if self._worker:
            self._worker.cancel.set()
        self._teardown_thread()
        self.player.stop()
        self._save_settings()
        super().closeEvent(event)
