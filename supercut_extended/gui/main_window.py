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
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, QSize, Qt, QThread, Signal
from PySide6.QtGui import QAction, QActionGroup, QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFrame,
    QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QProgressBar, QPushButton, QRadioButton, QScrollArea, QSizePolicy,
    QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .. import __version__, updater
from ..encoder import EncoderSpec, available_encoders, group_by_engine, vendor_label
from ..library import matches_with_highlights, read_matches
from ..model import HIGHLIGHT_KINDS, Match, Media
from ..probe import MediaInfo, probe
from ..render import RenderOptions, RenderResult, render
from ..segments import build_segments, total_duration_s
from . import icons
from .i18n import event_label, fmt_duration, language, set_language, tr
from .player import VideoPlayer
from .style import ACCENT_HI, TEXT, TEXT_DIM, build_style, checkbox_style
from .timeline import TimelineWidget, event_color
from .update_dialog import UpdateCheck, UpdateDialog

# CQ/QP/CRF-style quality values, all on the same "lower is better, bigger file"
# scale ffmpeg uses for -cq/-global_quality/-qp_i/-crf -- one shared friendly scale
# is enough because every encoder branch in encoder.video_args() already applies the
# same number without per-vendor rescaling.
QUALITY_TIERS = (18, 21, 23, 26, 30)
DEFAULT_QUALITY = 23


class LibraryLoader(QThread):
    loaded = Signal(list)
    failed = Signal(str)

    def run(self) -> None:
        try:
            self.loaded.emit(matches_with_highlights(read_matches()))
        except Exception as exc:
            self.failed.emit(str(exc))


class RenderWorker(QObject):
    progressed = Signal(float, str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, source: Path, segments, output: Path, options: RenderOptions):
        super().__init__()
        self._args = (source, segments, output, options)
        self.cancel = threading.Event()

    def run(self) -> None:
        source, segments, output, options = self._args
        try:
            result = render(
                source, segments, output, options,
                progress=lambda f, m: self.progressed.emit(f, m),
                cancel=self.cancel,
            )
            self.finished.emit(result)
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
        self.setWindowIcon(icons.icon("Edit/Layers", "#60a5fa", 64))

        self._matches: list[Match] = []
        self._match: Match | None = None
        self._media: Media | None = None
        self._info: MediaInfo | None = None
        self._kind_boxes: dict[str, QCheckBox] = {}
        self._segments: list = []
        self._thread: QThread | None = None
        self._worker: RenderWorker | None = None
        self._last_output: Path | None = None

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

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            [tr("col.when"), tr("col.game"), tr("col.length"), tr("col.highlights")])
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.itemSelectionChanged.connect(self._on_match_selected)
        lay.addWidget(self.table, 1)
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

        # Two plain-language questions -- "CPU or GPU", then "which quality type" --
        # instead of one combo box listing raw ffmpeg encoder names like
        # "h264_nvenc". The actual EncoderSpec is resolved from whichever pair of
        # radio buttons is checked (see _current_spec).
        engine_label = QLabel(tr("out.engine"))
        engine_label.setObjectName("fieldLabel")
        self.engine_gpu = QRadioButton(tr("out.engine.gpu"))
        self.engine_cpu = QRadioButton(tr("out.engine.cpu"))
        self.engine_gpu.toggled.connect(self._on_engine_changed)
        self.engine_detected = caption("")
        engine_block = block(
            engine_label, self.engine_gpu, self.engine_cpu, self.engine_detected,
            spacing=4)

        codec_label = QLabel(tr("out.codec"))
        codec_label.setObjectName("fieldLabel")
        self.codec_h264 = QRadioButton(tr("out.codec.h264"))
        self.codec_hevc = QRadioButton(tr("out.codec.hevc"))
        self.codec_h264.setChecked(True)
        self.codec_h264.toggled.connect(self._on_codec_changed)
        codec_block = block(
            codec_label,
            self.codec_h264, caption(tr("out.codec.h264.desc")),
            self.codec_hevc, caption(tr("out.codec.hevc.desc")),
            spacing=4)

        # Each item pairs a plain-language tier with the actual ffmpeg value in
        # parentheses ("Balanced (23)") -- the raw preset name / CQ number is still
        # what rides along as the item's data and gets passed to ffmpeg. Full-width
        # (not `fixed=False` side by side) because these run longer than a bare
        # "p4"/"23" and were getting elided in a shared half-width row.
        self.preset_combo = self._combo()
        self.quality_combo = self._combo()
        for cq in QUALITY_TIERS:
            label = f"{tr(f'quality.{cq}')} ({cq})"
            if cq == DEFAULT_QUALITY:
                label = tr("out.preset.recommended", name=label)
            self.quality_combo.addItem(label, cq)
        self.quality_combo.setCurrentIndex(QUALITY_TIERS.index(DEFAULT_QUALITY))
        self.quality_combo.currentIndexChanged.connect(self._recompute)

        self.audio_combo = self._combo()

        self.output_edit = QLineEdit()
        self.output_edit.setMinimumWidth(60)
        browse = QPushButton(tr("out.browse"))
        browse.setIcon(icons.icon("File/Folder_Open", TEXT, 16))
        browse.setIconSize(QSize(16, 16))
        browse.clicked.connect(self._browse_output)
        orow = QHBoxLayout()
        orow.setSpacing(6)
        orow.addWidget(self.output_edit, 1)
        orow.addWidget(browse)

        mode_block = block(
            self.mode_encode, caption(tr("out.mode.encode.desc")),
            self.mode_copy, caption(tr("out.mode.copy.desc")))

        speed_quality_block = block(
            self._field(tr("out.preset"), self.preset_combo),
            self._field(tr("out.quality"), self.quality_combo))

        # Wider spacing than the 4-8px used inside each sub-block, so "mode",
        # "processing", "quality type" and the preset/quality row read as distinct
        # questions rather than one dense list of radio buttons.
        lay.addWidget(block(
            section(tr("group.output"), "Interface/Download"),
            mode_block,
            engine_block,
            codec_block,
            speed_quality_block,
            self._field(tr("out.audio"), self.audio_combo),
            self._field(tr("out.file"), orow),
            spacing=14))

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
        self._groups: dict[tuple[bool, str], EncoderSpec] = {}
        try:
            specs = available_encoders()
        except Exception as exc:
            self._specs: list[EncoderSpec] = []
            self.engine_detected.setText(tr("encoder.missing"))
            self.engine_gpu.setEnabled(False)
            self.engine_cpu.setEnabled(False)
            self.codec_h264.setEnabled(False)
            self.codec_hevc.setEnabled(False)
            QMessageBox.critical(self, tr("err.ffmpeg.title"), str(exc))
            return

        self._specs = list(specs)
        self._groups = group_by_engine(specs)

        # PC-spec auto-detection: available_encoders() already probed this machine by
        # actually encoding a few frames with each candidate (see encoder.py), so
        # "recommended" just means "the best thing that was proven to work here".
        gpu_spec = self._groups.get((True, "h264")) or self._groups.get((True, "hevc"))
        if gpu_spec is not None:
            self.engine_detected.setText(
                tr("out.engine.detected.gpu", vendor=vendor_label(gpu_spec.name)))
            self.engine_gpu.setChecked(True)
        else:
            self.engine_detected.setText(tr("out.engine.detected.cpu_only"))
            self.engine_cpu.setChecked(True)

        if not specs:
            self.engine_detected.setText(tr("encoder.missing"))

        self._on_engine_changed()

        for w in (self.pre_spin, self.post_spin, self.gap_spin):
            w.valueChanged.connect(self._recompute)

    def _gpu_available(self) -> bool:
        return (True, "h264") in self._groups or (True, "hevc") in self._groups

    def _current_spec(self) -> EncoderSpec | None:
        codec = "hevc" if self.codec_hevc.isChecked() else "h264"
        return self._groups.get((self.engine_gpu.isChecked(), codec))

    def _apply_output_control_states(self) -> None:
        encoding = self.mode_encode.isChecked()
        gpu_ok = self._gpu_available()
        self.engine_gpu.setEnabled(encoding and gpu_ok)
        self.engine_gpu.setToolTip("" if gpu_ok else tr("out.engine.gpu_unavailable"))
        self.engine_cpu.setEnabled(encoding)
        gpu = self.engine_gpu.isChecked()
        has_h264 = (gpu, "h264") in self._groups
        has_hevc = (gpu, "hevc") in self._groups
        self.codec_h264.setEnabled(encoding and has_h264)
        self.codec_h264.setToolTip("" if has_h264 else tr("out.codec.unavailable"))
        self.codec_hevc.setEnabled(encoding and has_hevc)
        self.codec_hevc.setToolTip("" if has_hevc else tr("out.codec.unavailable"))
        self.preset_combo.setEnabled(encoding)
        self.quality_combo.setEnabled(encoding)

    # -- persistence --------------------------------------------------------
    def _restore_settings(self) -> None:
        s = self.settings
        geo = s.value("geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        if s.value("mode", "encode") == "copy":
            self.mode_copy.setChecked(True)
        engine = s.value("engine")
        if engine == "gpu" and self.engine_gpu.isEnabled():
            self.engine_gpu.setChecked(True)
        elif engine == "cpu":
            self.engine_cpu.setChecked(True)
        codec = s.value("codec")
        if codec == "hevc" and self.codec_hevc.isEnabled():
            self.codec_hevc.setChecked(True)
        elif codec == "h264":
            self.codec_h264.setChecked(True)
        preset = s.value("preset")
        if preset:
            i = self.preset_combo.findData(preset)
            if i >= 0:
                self.preset_combo.setCurrentIndex(i)
        quality = int(s.value("quality", DEFAULT_QUALITY))
        i = self.quality_combo.findData(quality)
        if i >= 0:
            self.quality_combo.setCurrentIndex(i)
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
        s.setValue("engine", "gpu" if self.engine_gpu.isChecked() else "cpu")
        s.setValue("codec", "hevc" if self.codec_hevc.isChecked() else "h264")
        s.setValue("preset", self.preset_combo.currentData() or "")
        s.setValue("quality", self.quality_combo.currentData() or DEFAULT_QUALITY)
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
        self.table.setRowCount(len(rows))
        for i, m in enumerate(rows):
            counts = m.event_counts()
            highlights = sum(v for k, v in counts.items() if k in HIGHLIGHT_KINDS)
            name = m.game_name
            if m.info.get("map"):
                name += f"  ·  {m.info['map']}"
            tip = f"{m.label()}\n" + ", ".join(
                f"{event_label(k)}: {v}" for k, v in sorted(counts.items()))
            cells = [m.started_at.strftime("%m-%d %H:%M"), name,
                     fmt_duration(m.duration_s), str(highlights)]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setToolTip(tip)
                if c == 3:
                    item.setForeground(QColor("#4ade80" if highlights else "#5a6577"))
                self.table.setItem(i, c, item)

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
            self._rebuild_kind_boxes(match)
            self._rebuild_audio(self._info)
            self.output_edit.setText(str(self._default_output(media.path)))
            self._recompute()
        except Exception as exc:
            self.statusBar().showMessage(tr("status.load_fail", err=exc), 15000)
            raise

    def _rebuild_kind_boxes(self, match: Match) -> None:
        while self.kinds_layout.count():
            item = self.kinds_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._kind_boxes.clear()

        counts = (self._media.event_counts() if self._media else {}) or match.event_counts()
        for i, (kind, n) in enumerate(sorted(counts.items(), key=lambda kv: -kv[1])):
            cb = QCheckBox(f"{event_label(kind)}  ({n})")
            cb.setChecked(kind in HIGHLIGHT_KINDS)
            cb.toggled.connect(self._recompute)
            cb.setStyleSheet(checkbox_style(event_color(kind).name()))
            self.kinds_layout.addWidget(cb, i // 2, i % 2)
            self._kind_boxes[kind] = cb

        self.legend.setText("　".join(
            f"<span style='color:{event_color(k).name()}'>&#9632;</span> {event_label(k)}"
            for k in counts))

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
        self._apply_output_control_states()
        self._recompute()

    def _on_engine_changed(self) -> None:
        gpu = self.engine_gpu.isChecked()
        # If the codec currently checked has no usable encoder under the engine we
        # just switched to, hop to whichever codec does -- never leave the panel
        # pointed at a combination available_encoders() never proved works.
        if self.codec_hevc.isChecked() and (gpu, "hevc") not in self._groups:
            self.codec_h264.setChecked(True)
        elif self.codec_h264.isChecked() and (gpu, "h264") not in self._groups \
                and (gpu, "hevc") in self._groups:
            self.codec_hevc.setChecked(True)
        self._apply_output_control_states()
        self._on_codec_changed()

    def _on_codec_changed(self) -> None:
        self.preset_combo.clear()
        spec = self._current_spec()
        if spec is None:
            return
        for name in spec.presets:
            label = f"{tr(f'preset.{name}')} ({name})"
            if name == spec.default_preset:
                label = tr("out.preset.recommended", name=label)
            self.preset_combo.addItem(label, name)
        i = self.preset_combo.findData(spec.default_preset)
        self.preset_combo.setCurrentIndex(i if i >= 0 else 0)
        self._recompute()

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

        content = total_duration_s(self._segments)
        n_events = sum(1 for e in self._media.events if e.kind in kinds)
        if self._segments:
            # Rough realtime multipliers for the ETA, not a guarantee: ~120x for
            # stream copy, ~4.3x for GPU re-encode (measured on an RTX 3070 at p4),
            # ~1.0x for libx264 on the CPU (Outplayed's own baseline) and roughly
            # half that again for libx265, which is markedly slower per frame.
            spec = self._current_spec()
            if self.mode_copy.isChecked():
                rate = 120.0
            elif spec is None or spec.hardware:
                rate = 4.3
            elif spec.codec == "hevc":
                rate = 0.5
            else:
                rate = 1.0
            self.summary.setText(tr(
                "summary", events=n_events, segments=len(self._segments),
                length=fmt_duration(content), eta=content / rate))
        else:
            self.summary.setText(tr("summary.none"))
        self.build_btn.setEnabled(bool(self._segments) and self._thread is None)

    def _browse_output(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, tr("out.file"), self.output_edit.text() or "", "MP4 (*.mp4)")
        if path:
            self.output_edit.setText(path)

    # -- render -------------------------------------------------------------
    def _start_render(self) -> None:
        if not self._media or not self._segments or self._thread is not None:
            return
        out = Path(self.output_edit.text().strip())
        if not out.name:
            QMessageBox.warning(self, tr("err.output.title"), tr("err.output.body"))
            return
        spec = self._current_spec()
        if spec is None:
            QMessageBox.critical(self, tr("err.encoder.title"), tr("err.encoder.body"))
            return

        options = RenderOptions(
            encoder=spec,
            preset=self.preset_combo.currentData() or spec.default_preset,
            quality=self.quality_combo.currentData() or DEFAULT_QUALITY,
            audio=self.audio_combo.currentData() or "0",
            mode="copy" if self.mode_copy.isChecked() else "encode",
        )

        self._thread = QThread(self)
        self._worker = RenderWorker(self._media.path, list(self._segments), out, options)
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
        self.build_btn.setEnabled(bool(self._segments))

    def _on_done(self, result: RenderResult) -> None:
        self._teardown_thread()
        self.progress.setValue(100)
        self._last_output = result.output
        self.reveal_btn.setEnabled(True)
        self.statusBar().showMessage(tr(
            "status.done", name=result.output.name, secs=result.elapsed_s,
            speed=result.speed_x, mb=result.size_bytes / 1e6), 20000)

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
