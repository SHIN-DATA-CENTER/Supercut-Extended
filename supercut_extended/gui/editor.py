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
    QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QHBoxLayout, QLabel,
    QMessageBox, QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSlider,
    QVBoxLayout, QWidget,
)

from ..model import Bgm, Clip, Timeline
from ..render import RenderError, RenderOptions, RenderResult, render_timeline
from . import icons
from .clip_timeline import ClipTimelineWidget
from .i18n import event_label, tr
from .style import ACCENT_HI, TEXT, build_style


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
        self.resize(1180, 660)
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

        self._build()
        self.track.set_timeline(self._timeline, self._limits)
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
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(10)

        hint = QLabel(tr("editor.hint"))
        hint.setObjectName("captionLabel")
        hint.setWordWrap(True)
        outer.addWidget(hint)

        self.track = ClipTimelineWidget()
        self.track.changed.connect(self._on_track_changed)
        self.track.selected.connect(self._on_select)
        scroll = QScrollArea()
        scroll.setWidget(self.track)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setMinimumHeight(200)
        scroll.setMaximumHeight(260)
        outer.addWidget(scroll)
        outer.addStretch(1)

        outer.addLayout(self._build_clip_panel())
        outer.addLayout(self._build_bgm_panel())

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

    def _build_clip_panel(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)

        self.clip_label = QLabel(tr("editor.noselection"))
        self.clip_label.setMinimumWidth(180)
        row.addWidget(self.clip_label)

        self.enabled_box = QCheckBox(tr("editor.enabled"))
        self.enabled_box.toggled.connect(self._on_enabled)
        row.addWidget(self.enabled_box)

        for key, attr in (("editor.fade_in", "fade_in_ms"),
                          ("editor.fade_out", "fade_out_ms")):
            row.addWidget(QLabel(tr(key)))
            spin = _spin(0.0, 5.0, 0.0)
            spin.valueChanged.connect(
                lambda v, a=attr: self._set_clip_attr(a, v * 1000.0))
            row.addWidget(spin)
            setattr(self, f"clip_{attr}", spin)

        row.addWidget(QLabel(tr("editor.length")))
        self.len_spin = _spin(0.2, 600.0, 0.0)
        self.len_spin.valueChanged.connect(self._on_length)
        row.addWidget(self.len_spin)

        self.remove_btn = QPushButton(tr("editor.remove"))
        self.remove_btn.clicked.connect(self._remove_clip)
        row.addWidget(self.remove_btn)
        row.addStretch(1)
        return row

    def _build_bgm_panel(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(QLabel(tr("editor.bgm")))

        self.bgm_label = QLabel(tr("editor.bgm_none"))
        self.bgm_label.setMinimumWidth(180)
        row.addWidget(self.bgm_label)

        pick = QPushButton(tr("editor.bgm_pick"))
        pick.clicked.connect(self._pick_bgm)
        row.addWidget(pick)
        clear = QPushButton(tr("editor.bgm_clear"))
        clear.clicked.connect(self._clear_bgm)
        row.addWidget(clear)

        row.addWidget(QLabel(tr("editor.volume")))
        self.vol = QSlider(Qt.Horizontal)
        self.vol.setRange(0, 100)
        self.vol.setValue(25)
        self.vol.setFixedWidth(120)
        self.vol.valueChanged.connect(self._on_bgm_change)
        row.addWidget(self.vol)

        row.addWidget(QLabel(tr("editor.fade_in")))
        self.bgm_in = _spin(0.0, 10.0, 1.0)
        self.bgm_in.valueChanged.connect(self._on_bgm_change)
        row.addWidget(self.bgm_in)
        row.addWidget(QLabel(tr("editor.fade_out")))
        self.bgm_out = _spin(0.0, 10.0, 1.0)
        self.bgm_out.valueChanged.connect(self._on_bgm_change)
        row.addWidget(self.bgm_out)

        self.bgm_loop = QCheckBox(tr("editor.loop"))
        self.bgm_loop.setChecked(True)
        self.bgm_loop.toggled.connect(self._on_bgm_change)
        row.addWidget(self.bgm_loop)
        row.addStretch(1)
        return row

    # -- clip editing -------------------------------------------------------
    def _current(self) -> Clip | None:
        i = self.track.selected_index()
        clips = self._timeline.clips
        return clips[i] if 0 <= i < len(clips) else None

    def _on_select(self, index: int) -> None:
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
        self._select_again()
        self._refresh_summary()

    def _remove_clip(self) -> None:
        i = self.track.selected_index()
        if 0 <= i < len(self._timeline.clips):
            del self._timeline.clips[i]
            self.track.set_timeline(self._timeline, self._limits)
            self._refresh_summary()

    def _select_again(self) -> None:
        self.track.update()

    def _on_track_changed(self) -> None:
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
        if self._worker:
            self._worker.cancel.set()
        self._teardown()
        super().closeEvent(event)
