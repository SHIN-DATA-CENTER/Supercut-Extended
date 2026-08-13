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

from PySide6.QtCore import (QObject, QSettings, QSize, Qt, QThread, QTimer,
                            Signal)
from PySide6.QtGui import QAction, QActionGroup, QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFrame,
    QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QProgressBar, QPushButton, QRadioButton, QScrollArea, QSizePolicy,
    QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QTabWidget,
    QVBoxLayout, QWidget,
)

from .. import __version__, updater
from ..encoder import (DEFAULT_QUALITY, QUALITY_TIERS, EncoderSpec,
                       available_encoders, estimate_rate, group_by_engine,
                       vendor_label)
from ..library import matches_with_highlights, read_matches
from ..model import (FPS_PRESETS, HIGHLIGHT_KINDS, RESOLUTION_PRESETS, Clip,
                     Framing, Match, Media, Timeline)
from ..probe import MediaInfo, detect_black_bars, probe
from ..render import (RenderError, RenderJob, RenderOptions, render_each,
                      render_many)
from ..segments import build_segments, total_duration_s
from . import icons
from .about_dialog import AboutDialog
from .controls import (NoScrollComboBox, NoScrollDoubleSpinBox,
                       NoScrollSpinBox)
from .editor import EditorWindow
from .i18n import event_label, fmt_duration, language, set_language, tr
from .player import VideoPlayer
from .style import (ACCENT_HI, TEXT, TEXT_DIM, TEXT_FAINT, build_style,
                    checkbox_style)
from .timeline import (Recording, TimelineControls, TimelineWidget,
                       event_color)
from .update_dialog import UpdateCheck, UpdateDialog


class LibraryLoader(QThread):
    loaded = Signal(list)
    failed = Signal(str)

    def run(self) -> None:
        try:
            self.loaded.emit(matches_with_highlights(read_matches()))
        except Exception as exc:
            self.failed.emit(str(exc))


class BarDetector(QThread):
    """Runs cropdetect off the GUI thread -- it decodes several seconds of video."""

    done = Signal(object)       # (left, right, top, bottom), or None if it failed

    def __init__(self, path: Path, at_s: float) -> None:
        super().__init__()
        self._path, self._at = path, at_s

    def run(self) -> None:
        try:
            self.done.emit(detect_black_bars(self._path, self._at))
        except Exception:
            self.done.emit(None)


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
        self._media_index = 0

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
        AboutDialog(self).exec()

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
        self.timeline.seekRequested.connect(self._seek_axis)
        self.timeline_controls = TimelineControls(self.timeline)

        self.legend = QLabel("")
        self.legend.setTextFormat(Qt.RichText)
        self.legend.setStyleSheet(f"color: {TEXT_DIM};")

        return card(self.player, self.timeline, self.timeline_controls, self.legend,
                    spacing=8, margins=(12, 12, 12, 10))

    def _build_settings(self) -> QWidget:
        """One card holding every setting, scrolled internally.

        Earlier this was a stack of separate cards inside a scroll area, which put the
        settings scrollbar *outside* the card borders while the match table's scrollbar
        sat *inside* its own border -- two bars at two different x positions. Wrapping
        the scroll area in a single card puts both bars just inside a frame at the same
        offset. The section headers already do the visual separating that the separate
        cards used to.
        """
        # --- events
        self.kinds_layout = QGridLayout()
        self.kinds_layout.setSpacing(6)
        events_tab = [block(self.kinds_layout)]

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
        timing_tab = [block(self.use_defaults, trow, caption(tr("timing.hint")))]

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
        # The preset had no connection at all, so the estimate never moved when it
        # changed. _on_codec_changed refills this combo with signals blocked.
        self.preset_combo.currentIndexChanged.connect(self._recompute)

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

        mode_block = block(
            self.mode_encode, caption(tr("out.mode.encode.desc")),
            self.mode_copy, caption(tr("out.mode.copy.desc")))

        speed_quality_block = block(
            self._field(tr("out.preset"), self.preset_combo),
            self._field(tr("out.quality"), self.quality_combo))

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

        shape_block = block(
            caption(tr("out.shape")),
            self.shape_combine, self.shape_separate)

        self.output_caption = QLabel(tr("out.file"))
        self.output_caption.setObjectName("fieldLabel")
        ocol = QVBoxLayout()
        ocol.setSpacing(3)
        ocol.addWidget(self.output_caption)
        ocol.addLayout(orow)

        # Wider spacing than the 4-8px used inside each sub-block, so "mode",
        # "processing", "quality type" and the preset/quality row read as distinct
        # questions rather than one dense list of radio buttons.
        output_tab = [block(
            mode_block,
            engine_block,
            codec_block,
            speed_quality_block,
            self._field(tr("out.audio"), self.audio_combo),
            self._build_framing_block(),
            spacing=14)]
        dest_tab = [block(shape_block, ocol, spacing=14)]

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        # Tabs along the bottom, the way OBS puts them: the panel is a tall narrow
        # column, so the tab strip sits next to the action buttons it leads to
        # rather than floating above a long scroll.
        self.tabs.setTabPosition(QTabWidget.South)
        for key, icon_name, contents in (
                ("group.events", "Interface/Filter", events_tab),
                ("group.timing", "Interface/Slider_01", timing_tab),
                ("group.output", "Interface/Download", output_tab),
                ("group.dest", "File/Folder_Open", dest_tab)):
            self.tabs.addTab(self._settings_tab(contents), icons.icon(icon_name, TEXT_DIM, 15),
                             tr(key))

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
        self.edit_btn = QPushButton(tr("editor.open"))
        self.edit_btn.setIcon(icons.icon("Edit/Edit_Pencil_01", TEXT, 16))
        self.edit_btn.setIconSize(QSize(16, 16))
        self.edit_btn.setEnabled(False)
        self.edit_btn.clicked.connect(self._open_editor)

        self.reveal_btn = QPushButton(tr("btn.reveal"))
        self.reveal_btn.setIcon(icons.icon("File/Folder_Open", TEXT, 16))
        self.reveal_btn.setIconSize(QSize(16, 16))
        self.reveal_btn.setEnabled(False)
        self.reveal_btn.clicked.connect(self._reveal_output)

        brow = QHBoxLayout()
        brow.addWidget(self.build_btn)
        brow.addWidget(self.cancel_btn)
        brow.addWidget(self.edit_btn)
        brow.addStretch(1)
        brow.addWidget(self.reveal_btn)

        holder = QWidget()
        outer = QVBoxLayout(holder)
        outer.setContentsMargins(12, 6, 6, 12)
        outer.setSpacing(10)
        # Zero right margin: each tab's scrollbar then sits flush against the card's
        # inner border, exactly where the table's own scrollbar sits inside its frame,
        # so the two bars line up on the same pixel column.
        outer.addWidget(card(self.tabs, margins=(10, 8, 0, 10)), 1)
        outer.addWidget(card(self.summary, self.progress, brow))

        self._populate_encoders()
        return holder

    @staticmethod
    def _settings_tab(contents: list[QWidget]) -> QWidget:
        """One tab: its own scroll area, so a long tab does not stretch the others.

        Each tab scrolls independently rather than the whole panel scrolling as one
        column -- which is the point of splitting them up. Horizontal scrolling stays
        off: everything has to fit the column width, and hiding a control behind a
        sideways scrollbar is never the right answer.
        """
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(2, 2, 8, 2)
        lay.setSpacing(16)
        for widget in contents:
            lay.addWidget(widget)
        lay.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(inner)
        return scroll

    def _build_framing_block(self) -> QWidget:
        """Output resolution, black-bar crop and the stretch filter.

        Crop is in source pixels off each edge rather than a target aspect ratio,
        because the bars a capture bakes in are not always symmetric and not always the
        exact 4:3 pillar people expect. The detect button fills these in from the
        footage, so nobody has to count pixels by hand.
        """
        self.fps_combo = self._combo()
        for label, value in FPS_PRESETS:
            self.fps_combo.addItem(label, value)
        self.fps_combo.currentIndexChanged.connect(self._on_framing_changed)

        self.res_combo = self._combo()
        for label, w, h in RESOLUTION_PRESETS:
            self.res_combo.addItem(label, (w, h))
        self.res_combo.addItem(tr("frame.custom"), "custom")
        self.res_combo.currentIndexChanged.connect(self._on_resolution_changed)

        self.res_w = self._px_spin(3840 * 2, 1920)
        self.res_h = self._px_spin(2160 * 2, 1080)
        for s in (self.res_w, self.res_h):
            s.setMinimum(16)
            s.setSingleStep(2)
            s.valueChanged.connect(self._on_framing_changed)
        self.custom_row = QWidget()
        crow = QHBoxLayout(self.custom_row)
        crow.setContentsMargins(0, 0, 0, 0)
        crow.setSpacing(6)
        crow.addLayout(self._field(tr("frame.width"), self.res_w))
        crow.addLayout(self._field(tr("frame.height"), self.res_h))
        self.custom_row.setVisible(False)

        crop_caption = QLabel(tr("frame.crop"))
        crop_caption.setObjectName("fieldLabel")
        crop_grid = QGridLayout()
        crop_grid.setSpacing(6)
        self.crop_spins: dict[str, QSpinBox] = {}
        for i, (key, name) in enumerate((
                ("crop_left", "frame.crop.left"), ("crop_right", "frame.crop.right"),
                ("crop_top", "frame.crop.top"), ("crop_bottom", "frame.crop.bottom"))):
            spin = self._px_spin(4000, 0)
            spin.valueChanged.connect(self._on_framing_changed)
            self.crop_spins[key] = spin
            crop_grid.addLayout(self._field(tr(name), spin), i // 2, i % 2)

        self.detect_btn = QPushButton(tr("frame.detect"))
        self.detect_btn.setIcon(icons.icon("Interface/Filter", TEXT, 16))
        self.detect_btn.setIconSize(QSize(16, 16))
        self.detect_btn.clicked.connect(self._detect_bars)

        # No per-widget stylesheet: this is an ordinary option like "use defaults" and
        # takes the global look. checkbox_style() is for the coloured per-event boxes.
        self.stretch_box = QCheckBox(tr("frame.stretch"))
        self.stretch_box.toggled.connect(self._on_framing_changed)

        self.frame_note = caption(tr("frame.note"))
        self.frame_note.setVisible(False)

        # A field label, not caption(): captions are indented to sit under a radio
        # button, which left this heading hanging off to the right of every other
        # sub-heading in the column.
        title = QLabel(tr("frame.title"))
        title.setObjectName("fieldLabel")
        return block(
            title,
            self._field(tr("frame.resolution"), self.res_combo),
            self.custom_row,
            self._field(tr("frame.fps"), self.fps_combo),
            caption(tr("frame.fps.desc")),
            crop_caption, crop_grid, self.detect_btn,
            self.stretch_box, caption(tr("frame.stretch.desc")),
            self.frame_note,
            spacing=6)

    @staticmethod
    def _px_spin(hi: int, val: int) -> QSpinBox:
        spin = NoScrollSpinBox(minimum=0, maximum=hi, suffix=" px")
        spin.setValue(val)
        spin.setMinimumWidth(64)
        spin.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        return spin

    @staticmethod
    def _combo(fixed: bool = True) -> QComboBox:
        """A combo that shrinks with its column instead of demanding its widest item.

        Left at the default AdjustToContents, one long entry such as
        "OBS Audio Handler (aac, 2ch)" sets a minimum width for the whole panel and
        forces a horizontal scrollbar.
        """
        combo = NoScrollComboBox()
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
        s = NoScrollDoubleSpinBox(suffix=" s", minimum=lo, maximum=hi,
                                  singleStep=0.5)
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

    # -- framing ------------------------------------------------------------
    def _on_resolution_changed(self) -> None:
        data = self.res_combo.currentData()
        custom = data == "custom"
        self.custom_row.setVisible(custom)
        if not custom and isinstance(data, tuple) and data[0]:
            # Seed the custom boxes from the preset just left, so switching to Custom
            # starts from what was on screen instead of a stale 1920x1080.
            self.res_w.blockSignals(True)
            self.res_h.blockSignals(True)
            self.res_w.setValue(int(data[0]))
            self.res_h.setValue(int(data[1]))
            self.res_w.blockSignals(False)
            self.res_h.blockSignals(False)
        self._on_framing_changed()

    def _current_fps(self) -> float | None:
        return self.fps_combo.currentData()

    def _current_framing(self) -> Framing:
        data = self.res_combo.currentData()
        if data == "custom":
            width, height = self.res_w.value(), self.res_h.value()
        else:
            width, height = data if isinstance(data, tuple) else (None, None)
        return Framing(
            width=width, height=height,
            stretch=self.stretch_box.isChecked(),
            **{k: s.value() for k, s in self.crop_spins.items()})

    def _on_framing_changed(self) -> None:
        framing = self._current_framing()
        # Stretching only means something once there is a frame to stretch into.
        self.stretch_box.setEnabled(framing.resizes)
        self.frame_note.setVisible(framing.active)
        self.player.set_framing(framing)
        self._recompute()

    def _detect_bars(self) -> None:
        """Read the black borders off the previewed recording and fill the crop boxes."""
        media = self._media
        if media is None or media.path is None:
            self.statusBar().showMessage(tr("frame.detect_nomedia"), 5000)
            return
        self.detect_btn.setEnabled(False)
        self.detect_btn.setText(tr("frame.detecting"))
        # Sample the middle: the opening seconds are often a dark loading screen,
        # which would read as border and crop the whole picture away.
        at = (self._info.duration_s / 2.0) if self._info else 0.0
        worker = BarDetector(Path(media.path), at)
        worker.done.connect(self._on_bars_detected, Qt.QueuedConnection)
        worker.finished.connect(lambda: setattr(self, "_detector", None))
        self._detector = worker      # a QThread that goes out of scope is destroyed
        worker.start()

    def _on_bars_detected(self, bars: object) -> None:
        self.detect_btn.setEnabled(True)
        self.detect_btn.setText(tr("frame.detect"))
        if bars is None:
            self.statusBar().showMessage(tr("frame.detect_fail"), 6000)
            return
        left, right, top, bottom = bars
        if not any((left, right, top, bottom)):
            self.statusBar().showMessage(tr("frame.detect_none"), 6000)
            return
        for key, value in (("crop_left", left), ("crop_right", right),
                           ("crop_top", top), ("crop_bottom", bottom)):
            self.crop_spins[key].blockSignals(True)
            self.crop_spins[key].setValue(value)
            self.crop_spins[key].blockSignals(False)
        self._on_framing_changed()
        self.statusBar().showMessage(
            tr("frame.detected", left=left, right=right, top=top, bottom=bottom), 8000)

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

        # Stored as "source"/"custom"/"1920x1080" rather than a combo index, so adding
        # a preset later does not silently change what an existing install restores.
        # The custom sizes go in first: _on_resolution_changed overwrites them from
        # whichever preset is current, and would otherwise wipe a restored custom size.
        self.res_w.setValue(int(s.value("res_w", 1920)))
        self.res_h.setValue(int(s.value("res_h", 1080)))
        res = str(s.value("resolution", "source"))
        if res != "source":
            i = self.res_combo.findData("custom")
            if res != "custom":
                try:
                    w, h = (int(v) for v in res.split("x", 1))
                except ValueError:
                    w = h = 0
                exact = self.res_combo.findData((w, h))
                if exact >= 0:
                    i = exact
                elif w and h:
                    self.res_w.setValue(w)
                    self.res_h.setValue(h)
            self.res_combo.setCurrentIndex(i)
        for key, spin in self.crop_spins.items():
            spin.setValue(int(s.value(key, 0)))
        self.stretch_box.setChecked(s.value("stretch", "false") == "true")
        fps = float(s.value("fps", 0) or 0)
        i = self.fps_combo.findData(fps if fps else None)
        if i >= 0:
            self.fps_combo.setCurrentIndex(i)
        # Not _on_framing_changed: the custom width/height row also has to be shown or
        # hidden to match the preset that was just restored.
        self._on_resolution_changed()
        self._on_mode_changed()

    def _save_settings(self) -> None:
        s = self.settings
        s.setValue("geometry", self.saveGeometry())
        s.setValue("mode", "copy" if self.mode_copy.isChecked() else "encode")
        s.setValue("shape", "separate" if self.shape_separate.isChecked() else "combine")
        s.setValue("engine", "gpu" if self.engine_gpu.isChecked() else "cpu")
        s.setValue("codec", "hevc" if self.codec_hevc.isChecked() else "h264")
        s.setValue("preset", self.preset_combo.currentData() or "")
        s.setValue("quality", self.quality_combo.currentData() or DEFAULT_QUALITY)
        s.setValue("use_defaults", "true" if self.use_defaults.isChecked() else "false")
        s.setValue("pre", self.pre_spin.value())
        s.setValue("post", self.post_spin.value())
        s.setValue("gap", self.gap_spin.value())
        s.setValue("volume", self.player.volume.value())
        data = self.res_combo.currentData()
        s.setValue("resolution", "custom" if data == "custom"
                   else f"{data[0]}x{data[1]}" if data and data[0] else "source")
        s.setValue("res_w", self.res_w.value())
        s.setValue("res_h", self.res_h.value())
        for key, spin in self.crop_spins.items():
            s.setValue(key, spin.value())
        s.setValue("stretch", "true" if self.stretch_box.isChecked() else "false")
        s.setValue("fps", self.fps_combo.currentData() or 0)

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
        if not match.playable_medias:
            return
        self._match = match
        self._show_media(0)

    def _resume_at(self, seconds: float, play: bool) -> None:
        self.player.seek(seconds)
        if play:
            self.player.player.play()



    def _show_media(self, index: int, seek_s: float | None = None,
                    play: bool = False) -> None:
        match = self._match
        medias = match.playable_medias if match else []
        if not (0 <= index < len(medias)):
            return
        media = medias[index]
        if media.path is None:
            return

        same = media is self._media
        self._media = media
        self._media_index = index
        try:
            self._info = probe(media.path)
        except Exception as exc:
            self._info = None
            self.statusBar().showMessage(tr("status.probe_fail", err=exc), 8000)
            return

        self.header.setText(match.label())
        total = len(match.playable_medias)
        counter = f"    ({index + 1}/{total})" if total > 1 else ""
        self.subheader.setText(
            f"{media.path.name}    {self._info.width}x{self._info.height} "
            f"{self._info.fps:.0f}fps    {fmt_duration(self._info.duration_s)}{counter}")

        if same:
            if seek_s is not None:
                self._resume_at(seek_s, play)
        else:
            # The start position rides along with load(): a seek issued before the
            # media reports itself loaded is discarded.
            self.player.load(media.path, start_s=seek_s or 0.0, play=play)
        try:
            self._rebuild_kind_boxes()
            self._rebuild_audio(self._info)
            self._refresh_default_output()
            self._recompute()
        except Exception as exc:
            self.statusBar().showMessage(tr("status.load_fail", err=exc), 15000)
            raise

    def _target_medias(self) -> list[tuple[Match, Media]]:
        """Every recording Build will act on, in the order it will be joined.

        A match is not always one file. Outplayed's Highlight capture mode writes a
        separate recording per highlight, so a single VALORANT match here holds ten
        of them -- and every code path used to take playable_medias[0], which is why
        only the first one ever came out.
        """
        out: list[tuple[Match, Media]] = []
        for match in self._target_matches():
            for media in match.playable_medias:
                if media.path is not None:
                    out.append((match, media))
        return out

    def _target_event_counts(self) -> dict[str, int]:
        """Events per kind across everything Build will act on, not just the preview.

        With several matches ticked the tallies have to add up over all of them --
        counting only the previewed recording made the numbers disagree with what was
        actually about to be rendered.
        """
        counts: dict[str, int] = {}
        for _match, media in self._target_medias():
            for kind, n in media.event_counts().items():
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
        marks = "　".join(
            f"<span style='color:{event_color(k).name()}'>&#9632;</span> {event_label(k)}"
            for k in shown)
        # The zoom keys are not discoverable on a bare strip, and a 30 minute capture
        # is unreadable at 1x, so the legend carries the hint.
        self.legend.setText(
            f"{marks}<br><span style='color:{TEXT_FAINT}'>{tr('timeline.zoom_hint')}</span>")

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
        sources = [media.path for _m, media in self._target_medias()]
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
        """Player time is inside one file; the strip runs across all of them.

        Playing past the end of a recording moves on to the next, so a match made of
        ten highlight files plays through as one piece.
        """
        recs = self.timeline.recordings()
        i = self._media_index
        if not (0 <= i < len(recs)):
            self.timeline.set_playhead(seconds)
            return
        self.timeline.set_playhead(recs[i].offset_s + seconds)
        if seconds >= recs[i].duration_s - 0.05 and i + 1 < len(recs):
            self._show_media(i + 1, seek_s=0.0, play=True)

    def _seek_axis(self, axis_s: float) -> None:
        """Seek by shared-axis seconds, switching recordings when it crosses one."""
        index, local = self.timeline.locate(axis_s)
        if index < 0:
            return
        if index != self._media_index:
            self._show_media(index, seek_s=local)
        else:
            self.player.seek(local)

    def _jump_event(self, direction: int) -> None:
        """Step to the next/previous event, across recordings.

        Both the event times and the seek are in shared-axis seconds, so skipping
        forward off the end of one recording lands in the next one rather than
        clamping at its last frame.
        """
        times = self.timeline.event_times()
        if not times:
            return
        now = self._axis_position()
        if direction > 0:
            nxt = next((t for t in times if t > now + 0.25), times[-1])
        else:
            nxt = next((t for t in reversed(times) if t < now - 0.75), times[0])
        self._seek_axis(nxt)

    def _axis_position(self) -> float:
        """Where playback is on the shared axis."""
        recs = self.timeline.recordings()
        i = self._media_index
        offset = recs[i].offset_s if 0 <= i < len(recs) else 0.0
        return offset + self.player.position_s()

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
        spec = self._current_spec()
        if spec is None:
            return
        # Refilling the combo emits currentIndexChanged for every step, which would
        # re-run the estimate several times mid-rebuild. Silence it and recompute once
        # at the end instead.
        self.preset_combo.blockSignals(True)
        try:
            self.preset_combo.clear()
            for name in spec.presets:
                label = f"{tr(f'preset.{name}')} ({name})"
                if name == spec.default_preset:
                    label = tr("out.preset.recommended", name=label)
                self.preset_combo.addItem(label, name)
            i = self.preset_combo.findData(spec.default_preset)
            self.preset_combo.setCurrentIndex(i if i >= 0 else 0)
        finally:
            self.preset_combo.blockSignals(False)
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
        # The strip shows every recording of this match end to end, so the montage
        # can be read as one piece instead of one file at a time.
        recordings = []
        for media in (self._match.playable_medias if self._match else []):
            if media.path is None:
                continue
            previewed = media is self._media
            duration = (self._info.duration_s if previewed and self._info
                        else media.duration_s)
            segs = self._segments if previewed else build_segments(
                media.events, kinds=kinds,
                pre_ms=None if use_defaults else self.pre_spin.value() * 1000,
                post_ms=None if use_defaults else self.post_spin.value() * 1000,
                duration_ms=media.duration_s * 1000 or None,
                gap_ms=self.gap_spin.value() * 1000)
            recordings.append(Recording(
                label=f"{len(recordings) + 1}",
                duration_s=duration,
                events=[e for e in media.events if not kinds or e.kind in kinds],
                segments=segs))
        self.timeline.set_recordings(recordings)

        # Rough realtime multiplier for the ETA, from encoder.estimate_rate() so the
        # preset, the quality and the output frame size all move it (see the measured
        # tables there). The previewed match's real geometry is the source size --
        # a batch of mixed resolutions is approximated by it.
        rate = estimate_rate(
            self._current_spec(),
            preset=self.preset_combo.currentData() or "",
            quality=self.quality_combo.currentData() or DEFAULT_QUALITY,
            mode="copy" if self.mode_copy.isChecked() else "encode",
            framing=self._current_framing(),
            source_size=(self._info.width, self._info.height),
        )
        targets = self._target_matches()

        if len(targets) > 1:
            # Estimated from the library's own durations rather than probing every
            # file: ffprobe on a few dozen recordings would stall the UI on each
            # keystroke. The worker probes properly before anything is encoded.
            n_events = n_segments = 0
            content = 0.0
            for _match, media in self._target_medias():
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
            self.edit_btn.setEnabled(bool(n_segments))
            return

        # Totals span every recording of this match, not just the previewed one:
        # a Highlight-mode match is several files, and the summary has to describe
        # what Build will actually produce.
        n_events = n_segments = 0
        content = 0.0
        for _match, media in self._target_medias():
            segs = (self._segments if media is self._media else build_segments(
                media.events, kinds=kinds,
                pre_ms=None if use_defaults else self.pre_spin.value() * 1000,
                post_ms=None if use_defaults else self.post_spin.value() * 1000,
                duration_ms=media.duration_s * 1000 or None,
                gap_ms=self.gap_spin.value() * 1000))
            n_events += sum(1 for e in media.events if e.kind in kinds)
            n_segments += len(segs)
            content += total_duration_s(segs)
        if n_segments:
            self.summary.setText(tr(
                "summary", events=n_events, segments=n_segments,
                length=fmt_duration(content), eta=content / rate))
        else:
            self.summary.setText(tr("summary.none"))
        self.build_btn.setEnabled(bool(n_segments) and self._thread is None)
        self.edit_btn.setEnabled(bool(n_segments))

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
        for match, media in self._target_medias():
            # One job per recording, not per match: a Highlight-mode match is several
            # files and all of them belong in the montage.
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
            framing=self._current_framing(),
            fps=self._current_fps(),
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

    # -- clip editor --------------------------------------------------------
    def _build_timeline(self) -> tuple[Timeline, dict[Path, float]]:
        """Seed an editable timeline from what Build would currently produce.

        One clip per *segment*, not per event: segments are what actually get cut, and
        build_segments() has already merged events whose windows overlap. Seeding per
        event would show clips that overlap in the source and double up footage.
        """
        kinds = self._selected_kinds()
        use_defaults = self.use_defaults.isChecked()
        timeline = Timeline()
        limits: dict[Path, float] = {}

        for match, media in self._target_medias():
            # The previewed recording has a real probe; the rest fall back to the
            # library's own duration, which is close enough to clamp a trim against.
            duration_ms = (self._info.duration_ms
                           if self._media is media and self._info
                           else media.duration_s * 1000.0)
            limits[Path(media.path)] = duration_ms
            segments = build_segments(
                media.events, kinds=kinds,
                pre_ms=None if use_defaults else self.pre_spin.value() * 1000,
                post_ms=None if use_defaults else self.post_spin.value() * 1000,
                duration_ms=duration_ms or None,
                gap_ms=self.gap_spin.value() * 1000,
            )
            for seg in segments:
                mid = (seg.start_ms + seg.end_ms) / 2.0
                near = min((e for e in media.events if e.kind in kinds),
                           key=lambda e: abs(e.time_ms - mid), default=None)
                kind = near.kind if near else None
                stamp = seg.start_ms / 1000.0
                timeline.clips.append(Clip(
                    source=Path(media.path),
                    source_start_ms=seg.start_ms,
                    source_end_ms=seg.end_ms,
                    label=f"{event_label(kind) if kind else 'clip'} "
                          f"{int(stamp // 60)}:{int(stamp % 60):02d}",
                    event_kind=kind,
                    event_time_ms=near.time_ms if near else None,
                ))
        return timeline, limits

    def _open_editor(self) -> None:
        timeline, limits = self._build_timeline()
        if not timeline.clips:
            QMessageBox.information(self, tr("err.noevents.title"),
                                    tr("err.noevents.body"))
            return
        spec = self._current_spec()
        if spec is None:
            QMessageBox.critical(self, tr("err.encoder.title"), tr("err.encoder.body"))
            return

        out = Path(self.output_edit.text().strip())
        if self._writing_separate() or not out.name:
            # The editor always produces one arranged montage, so a folder-shaped
            # output has no meaning here -- name a file inside it.
            base = out if out.suffix == "" else out.parent
            out = base / f"supercut-edited-{datetime.now():%Y%m%d-%H%M%S}.mp4"

        options = RenderOptions(
            encoder=spec,
            preset=self.preset_combo.currentData() or spec.default_preset,
            quality=self.quality_combo.currentData() or DEFAULT_QUALITY,
            audio=self.audio_combo.currentData() or "0",
            mode="copy" if self.mode_copy.isChecked() else "encode",
            framing=self._current_framing(),
            fps=self._current_fps(),
        )

        self._editor = EditorWindow(timeline, limits, out, options, self)
        self._editor.rendered.connect(self._on_editor_done, Qt.QueuedConnection)
        self._editor.show()

    def _on_editor_done(self, result) -> None:
        self._last_output = Path(result.output)
        self.reveal_btn.setEnabled(True)
        self.statusBar().showMessage(tr(
            "status.done", name=Path(result.output).name, secs=result.elapsed_s,
            speed=result.speed_x, mb=result.size_bytes / 1e6), 20000)

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
