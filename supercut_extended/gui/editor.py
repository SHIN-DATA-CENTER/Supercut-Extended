"""The clip editor: a separate window for arranging, trimming and scoring a montage.

Kept out of the main window on purpose. A real timeline needs width to be usable, and
the main window's left column is already carrying the match list and every render
setting -- squeezing an editor in there would make both worse.

The editor owns a Timeline (see model.Timeline). The main window seeds it from the
segments it already computed, hands over the source durations so trims can be clamped
to real footage, and gets a finished file back.
"""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QObject, QSize, Qt, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDoubleSpinBox, QFileDialog, QFrame, QHBoxLayout, QLabel,
    QListWidget, QMessageBox, QProgressBar, QPushButton, QScrollArea, QSlider,
    QSplitter, QVBoxLayout, QWidget,
)

from ..model import Bgm, Clip, Timeline
from ..render import RenderError, RenderOptions, RenderResult, render_timeline
from . import icons
from .clip_timeline import ClipTimelineWidget, timecode
from .i18n import event_label, tr
from .player import VideoPlayer
from .style import build_style


class TimelineWorker(QObject):
    progressed = Signal(float, str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, timeline: Timeline, output: Path, options: RenderOptions):
        super().__init__()
        self._args = (timeline, output, options)
        self.cancel = threading.Event()

    def run(self) -> None:
        timeline, output, options = self._args
        try:
            result = render_timeline(
                timeline, output, options,
                progress=lambda f, m: self.progressed.emit(f, m),
                cancel=self.cancel,
            )
            self.finished.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))


def _spin(lo: float, hi: float, val: float, suffix: str = " s",
          step: float = 0.1) -> QDoubleSpinBox:
    s = QDoubleSpinBox(minimum=lo, maximum=hi, singleStep=step, suffix=suffix)
    s.setDecimals(2)
    s.setValue(val)
    s.setMinimumWidth(92)
    return s


def _section(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("sectionLabel")
    return label


def _field_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("fieldLabel")
    return label


class EditorWindow(QDialog):
    """Arrange clips, trim them, add fades and music, then render."""

    rendered = Signal(object)       # RenderResult, so the main window can offer Reveal

    def __init__(self, timeline: Timeline, limits: dict[Path, float],
                 output: Path, options: RenderOptions,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("editor.title"))
        self.setStyleSheet(build_style())
        self.setWindowIcon(icons.app_icon())
        self.resize(1180, 940)
        # A dialog that is not modal: the main window stays usable while editing.
        self.setModal(False)
        self.setWindowFlag(Qt.Window, True)

        self._timeline = timeline
        self._limits = limits
        self._output = output
        self._options = options
        self._thread: QThread | None = None
        self._worker: TimelineWorker | None = None
        self._loading = False
        self._preview_index = -1
        self._loaded_source: Path | None = None

        self._build()
        self.track.set_timeline(self._timeline, self._limits)
        self._refresh_list()
        self._sync_bgm_panel()
        self._refresh_summary()

    def _sync_bgm_panel(self) -> None:
        """Show the music the timeline already has, rather than assuming there is none.

        The panel's defaults describe an empty timeline; a timeline handed in with a
        track already set would otherwise read "(none)" while the track bar shows it.
        """
        bgm = self._timeline.bgm
        self._loading = True
        try:
            if bgm is None:
                self.bgm_label.setText(tr("editor.bgm_none"))
                return
            self.bgm_label.setText(Path(bgm.path).name)
            self.vol.setValue(int(round(bgm.volume * 100)))
            self.bgm_in.setValue(bgm.fade_in_ms / 1000.0)
            self.bgm_out.setValue(bgm.fade_out_ms / 1000.0)
            self.bgm_loop.setChecked(bgm.loop)
        finally:
            self._loading = False

    # -- construction -------------------------------------------------------
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        # Top row: clip list | preview | inspector, as an editor lays them out.
        top = QSplitter(Qt.Horizontal)
        top.addWidget(self._build_clip_list())
        top.addWidget(self._build_monitor())
        top.addWidget(self._build_inspector())
        top.setStretchFactor(0, 0)
        top.setStretchFactor(1, 1)
        top.setStretchFactor(2, 0)
        top.setSizes([210, 700, 280])

        self.track = ClipTimelineWidget()
        self.track.changed.connect(self._on_track_changed)
        self.track.selected.connect(self._on_select)
        self.track.scrubbed.connect(self._on_scrub)
        scroll = QScrollArea()
        scroll.setWidget(self.track)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        bottom = QWidget()
        blay = QVBoxLayout(bottom)
        blay.setContentsMargins(0, 0, 0, 0)
        blay.setSpacing(6)
        blay.addLayout(self._build_toolbar())
        blay.addWidget(scroll, 1)

        split = QSplitter(Qt.Vertical)
        split.addWidget(top)
        split.addWidget(bottom)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setSizes([430, 300])
        outer.addWidget(split, 1)

        self.summary = QLabel("")
        self.summary.setObjectName("summary")
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        outer.addWidget(self.summary)
        outer.addWidget(self.progress)

        self.render_btn = QPushButton(tr("editor.render"))
        self.render_btn.setObjectName("primary")
        self.render_btn.setIcon(icons.icon("Edit/Layers", "#ffffff", 17))
        self.render_btn.setIconSize(QSize(17, 17))
        self.render_btn.clicked.connect(self._start_render)
        self.cancel_btn = QPushButton(tr("btn.cancel"))
        self.cancel_btn.setVisible(False)
        self.cancel_btn.clicked.connect(self._cancel)
        close_btn = QPushButton(tr("editor.close"))
        close_btn.clicked.connect(self.close)

        row = QHBoxLayout()
        row.addWidget(self.render_btn)
        row.addWidget(self.cancel_btn)
        row.addStretch(1)
        row.addWidget(close_btn)
        outer.addLayout(row)

    def _build_clip_list(self) -> QWidget:
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 6, 0)
        lay.setSpacing(6)
        lay.addWidget(_section(tr("editor.clips")))
        self.clip_list = QListWidget()
        self.clip_list.currentRowChanged.connect(self._on_list_row)
        lay.addWidget(self.clip_list, 1)
        return box

    def _build_monitor(self) -> QWidget:
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(6, 0, 6, 0)
        lay.setSpacing(6)
        self.player = VideoPlayer()
        self.player.positionChanged.connect(self._on_position)
        lay.addWidget(self.player, 1)
        self.tc_label = QLabel("00:00.00 / 00:00.00")
        self.tc_label.setAlignment(Qt.AlignCenter)
        self.tc_label.setObjectName("summary")
        lay.addWidget(self.tc_label)
        note = QLabel(tr("editor.preview_note"))
        note.setObjectName("captionLabel")
        note.setWordWrap(True)
        lay.addWidget(note)
        return box

    def _build_inspector(self) -> QWidget:
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(6, 0, 4, 0)
        lay.setSpacing(7)
        lay.addWidget(_section(tr("editor.inspector")))

        self.clip_label = QLabel(tr("editor.noselection"))
        self.clip_label.setWordWrap(True)
        lay.addWidget(self.clip_label)

        self.enabled_box = QCheckBox(tr("editor.enabled"))
        self.enabled_box.toggled.connect(self._on_enabled)
        lay.addWidget(self.enabled_box)

        for key, attr in (("editor.fade_in", "fade_in_ms"),
                          ("editor.fade_out", "fade_out_ms")):
            lay.addWidget(_field_label(tr(key)))
            spin = _spin(0.0, 5.0, 0.0)
            spin.valueChanged.connect(
                lambda v, a=attr: self._set_clip_attr(a, v * 1000.0))
            lay.addWidget(spin)
            setattr(self, f"clip_{attr}", spin)

        lay.addWidget(_field_label(tr("editor.length")))
        self.len_spin = _spin(0.2, 600.0, 0.0)
        self.len_spin.valueChanged.connect(self._on_length)
        lay.addWidget(self.len_spin)

        self.remove_btn = QPushButton(tr("editor.remove"))
        self.remove_btn.clicked.connect(self._remove_clip)
        lay.addWidget(self.remove_btn)

        lay.addSpacing(8)
        lay.addWidget(_section(tr("editor.bgm")))
        self.bgm_label = QLabel(tr("editor.bgm_none"))
        self.bgm_label.setWordWrap(True)
        lay.addWidget(self.bgm_label)
        brow = QHBoxLayout()
        pick = QPushButton(tr("editor.bgm_pick"))
        pick.clicked.connect(self._pick_bgm)
        clear = QPushButton(tr("editor.bgm_clear"))
        clear.clicked.connect(self._clear_bgm)
        brow.addWidget(pick)
        brow.addWidget(clear)
        lay.addLayout(brow)

        lay.addWidget(_field_label(tr("editor.volume")))
        self.vol = QSlider(Qt.Horizontal)
        self.vol.setRange(0, 100)
        self.vol.setValue(25)
        self.vol.valueChanged.connect(self._on_bgm_change)
        lay.addWidget(self.vol)

        grid = QHBoxLayout()
        self.bgm_in = _spin(0.0, 10.0, 1.0)
        self.bgm_out = _spin(0.0, 10.0, 1.0)
        for w in (self.bgm_in, self.bgm_out):
            w.valueChanged.connect(self._on_bgm_change)
        grid.addWidget(self.bgm_in)
        grid.addWidget(self.bgm_out)
        lay.addLayout(grid)

        self.bgm_loop = QCheckBox(tr("editor.loop"))
        self.bgm_loop.setChecked(True)
        self.bgm_loop.toggled.connect(self._on_bgm_change)
        lay.addWidget(self.bgm_loop)
        lay.addStretch(1)

        holder = QScrollArea()
        holder.setWidget(box)
        holder.setWidgetResizable(True)
        holder.setFrameShape(QFrame.NoFrame)
        return holder

    def _build_toolbar(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(6)
        hint = QLabel(tr("editor.hint"))
        hint.setObjectName("captionLabel")
        row.addWidget(hint, 1)
        row.addWidget(_field_label(tr("editor.zoom")))
        out_btn = QPushButton("\u2212")
        out_btn.setFixedWidth(34)
        out_btn.clicked.connect(lambda: self.track.set_zoom(self.track.zoom() * 0.8))
        in_btn = QPushButton("+")
        in_btn.setFixedWidth(34)
        in_btn.clicked.connect(lambda: self.track.set_zoom(self.track.zoom() * 1.25))
        fit_btn = QPushButton(tr("editor.fit"))
        fit_btn.clicked.connect(self.track.fit)
        for b in (out_btn, in_btn, fit_btn):
            row.addWidget(b)
        return row

    # -- clip editing -------------------------------------------------------
    def _current(self) -> Clip | None:
        i = self.track.selected_index()
        clips = self._timeline.clips
        return clips[i] if 0 <= i < len(clips) else None

    # -- preview ------------------------------------------------------------
    def _cue(self, index: int, play: bool = False,
             seek_s: float | None = None) -> None:
        """Point the preview at a clip, loading its recording only if it changed.

        Reloading the same file would restart it from zero and stutter every time the
        selection moves between clips cut from one recording.
        """
        clips = self._timeline.clips
        if not (0 <= index < len(clips)):
            return
        clip = clips[index]
        self._preview_index = index
        source = Path(clip.source)
        if source != self._loaded_source:
            self.player.load(source)
            self._loaded_source = source
        self.player.seek(clip.start_s if seek_s is None else seek_s)
        self.track.set_playhead_seconds(
            self._timeline.start_of(index)
            + (0.0 if seek_s is None else max(0.0, seek_s - clip.start_s)))
        if play:
            self.player.toggle()

    def _on_scrub(self, seconds: float) -> None:
        """Jump anywhere in the montage by dragging the ruler.

        The sequence only exists as the clip list, so a position along it has to be
        translated back into "which clip, and how far into its recording".
        """
        index, offset = self._timeline.locate(seconds)
        if index < 0:
            return
        clip = self._timeline.clips[index]
        self._cue(index, seek_s=clip.start_s + offset)

    def _refresh_list(self) -> None:
        """Mirror the timeline into the clip list without echoing signals back."""
        self._loading = True
        try:
            self.clip_list.clear()
            for i, clip in enumerate(self._timeline.clips):
                kind = event_label(clip.event_kind) if clip.event_kind else "clip"
                mark = "" if clip.enabled else "  (除外)"
                self.clip_list.addItem(
                    f"{i + 1:>2}. {kind}   {timecode(clip.duration_s)}{mark}")
            row = self.track.selected_index()
            if 0 <= row < self.clip_list.count():
                self.clip_list.setCurrentRow(row)
        finally:
            self._loading = False

    def _on_list_row(self, row: int) -> None:
        if self._loading or row < 0:
            return
        self.track.select(row)

    def _sequence_seconds(self, index: int, source_seconds: float) -> float:
        clip = self._timeline.clips[index]
        return self._timeline.start_of(index) + max(0.0, source_seconds - clip.start_s)

    def _on_position(self, seconds: float) -> None:
        """Run the clips as one sequence: at a clip's out point, jump to the next.

        This is what makes the preview show the montage rather than the recording --
        the arrangement only exists here, since nothing has been rendered yet.
        """
        i = self._preview_index
        clips = self._timeline.clips
        if not (0 <= i < len(clips)):
            return
        clip = clips[i]
        ms = seconds * 1000.0
        at = self._sequence_seconds(i, seconds)
        self.track.set_playhead_seconds(at)
        self.tc_label.setText(
            f"{timecode(at)} / {timecode(self._timeline.duration_s)}")

        if ms < clip.source_end_ms:
            return
        for j in range(i + 1, len(clips)):
            if clips[j].enabled and clips[j].duration_ms > 0:
                self._cue(j)
                return
        self.player.stop()
        self.track.set_playhead_seconds(self._timeline.duration_s, visible=False)

    def _on_select(self, index: int) -> None:
        if index >= 0:
            self._cue(index)
        clip = self._current()
        # Writing the widgets fires their signals; _loading stops that being read back
        # as a user edit and clobbering the clip we are only displaying.
        self._loading = True
        try:
            enabled = clip is not None
            for w in (self.enabled_box, self.clip_fade_in_ms, self.clip_fade_out_ms,
                      self.len_spin, self.remove_btn):
                w.setEnabled(enabled)
            if clip is None:
                self.clip_label.setText(tr("editor.noselection"))
                return
            kind = event_label(clip.event_kind) if clip.event_kind else "clip"
            self.clip_label.setText(f"{index + 1}. {kind}  ({clip.duration_s:.2f}s)")
            self.enabled_box.setChecked(clip.enabled)
            self.clip_fade_in_ms.setValue(clip.fade_in_ms / 1000.0)
            self.clip_fade_out_ms.setValue(clip.fade_out_ms / 1000.0)
            self.len_spin.setValue(clip.duration_s)
        finally:
            self._loading = False

    def _set_clip_attr(self, attr: str, value: float) -> None:
        clip = self._current()
        if clip is None or self._loading:
            return
        setattr(clip, attr, value)
        self._refresh_list()
        self.track.update()
        self._refresh_summary()

    def _on_enabled(self, on: bool) -> None:
        self._set_clip_attr("enabled", on)

    def _on_length(self, seconds: float) -> None:
        """Numeric trim, as an exact alternative to dragging the clip's edge."""
        clip = self._current()
        if clip is None or self._loading:
            return
        limit = self._limits.get(Path(clip.source))
        end = clip.source_start_ms + seconds * 1000.0
        if limit is not None:
            end = min(end, limit)
        clip.source_end_ms = end
        self.track.set_timeline(self._timeline, self._limits)
        self._refresh_list()
        self._select_again()
        self._refresh_summary()

    def _remove_clip(self) -> None:
        i = self.track.selected_index()
        if 0 <= i < len(self._timeline.clips):
            del self._timeline.clips[i]
            self.track.set_timeline(self._timeline, self._limits)
            self._refresh_list()
            self._refresh_summary()

    def _select_again(self) -> None:
        self.track.update()

    def _on_track_changed(self) -> None:
        self._refresh_list()
        self._on_select(self.track.selected_index())
        self._refresh_summary()

    # -- bgm ----------------------------------------------------------------
    def _pick_bgm(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, tr("editor.bgm_pick"), "",
            "Audio/Video (*.mp3 *.m4a *.aac *.wav *.flac *.ogg *.opus *.mp4)")
        if not path:
            return
        self._timeline.bgm = Bgm(
            path=Path(path), volume=self.vol.value() / 100.0,
            fade_in_ms=self.bgm_in.value() * 1000.0,
            fade_out_ms=self.bgm_out.value() * 1000.0,
            loop=self.bgm_loop.isChecked())
        self.bgm_label.setText(Path(path).name)
        self.track.update()
        self._refresh_summary()

    def _clear_bgm(self) -> None:
        self._timeline.bgm = None
        self.bgm_label.setText(tr("editor.bgm_none"))
        self.track.update()
        self._refresh_summary()

    def _on_bgm_change(self) -> None:
        bgm = self._timeline.bgm
        if bgm is None or self._loading:
            return
        bgm.volume = self.vol.value() / 100.0
        bgm.fade_in_ms = self.bgm_in.value() * 1000.0
        bgm.fade_out_ms = self.bgm_out.value() * 1000.0
        bgm.loop = self.bgm_loop.isChecked()
        self.track.update()
        self._refresh_summary()

    # -- render -------------------------------------------------------------
    def _refresh_summary(self) -> None:
        n = len(self._timeline.active)
        note = tr("editor.will_encode") if (
            self._timeline.needs_encode() and self._options.mode == "copy") else ""
        self.summary.setText(
            tr("editor.summary", clips=n, length=self._timeline.duration_s) + note)
        self.render_btn.setEnabled(n > 0 and self._thread is None)

    def _start_render(self) -> None:
        if self._thread is not None or not self._timeline.active:
            return
        self._thread = QThread(self)
        self._worker = TimelineWorker(self._timeline, self._output, self._options)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        # render_timeline fans work out over a thread pool, so these cross threads Qt
        # does not own -- queue them explicitly (same reasoning as the main window).
        self._worker.progressed.connect(self._on_progress, Qt.QueuedConnection)
        self._worker.finished.connect(self._on_done, Qt.QueuedConnection)
        self._worker.failed.connect(self._on_failed, Qt.QueuedConnection)
        self._thread.start()

        self.progress.setVisible(True)
        self.progress.setValue(0)
        self.render_btn.setEnabled(False)
        self.cancel_btn.setVisible(True)

    def _on_progress(self, frac: float, msg: str) -> None:
        self.progress.setValue(int(frac * 100))
        self.progress.setFormat(f"{msg}  -  %p%")

    def _teardown(self) -> None:
        if self._thread:
            self._thread.quit()
            self._thread.wait(4000)
        self._thread = None
        self._worker = None
        self.cancel_btn.setVisible(False)
        self._refresh_summary()

    def _on_done(self, result: RenderResult) -> None:
        self._teardown()
        self.progress.setValue(100)
        self.rendered.emit(result)
        QMessageBox.information(self, tr("editor.title"),
                                tr("editor.done", name=Path(result.output).name))

    def _on_failed(self, message: str) -> None:
        self._teardown()
        self.progress.setVisible(False)
        if "cancel" in message.lower():
            return
        QMessageBox.critical(self, tr("err.render.title"), message)

    def _cancel(self) -> None:
        if self._worker:
            self._worker.cancel.set()

    def closeEvent(self, event) -> None:
        self.player.stop()
        if self._worker:
            self._worker.cancel.set()
        self._teardown()
        super().closeEvent(event)
