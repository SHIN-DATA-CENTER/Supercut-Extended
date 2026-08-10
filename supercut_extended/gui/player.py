"""Video preview with transport controls.

There is no separate seek bar: the event timeline below the video *is* the seek bar.
That is how Outplayed presents it, and it means the playhead, the event markers and
the segments that will be cut all share one coordinate system -- so you can see the
clip boundaries against the footage instead of guessing.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, QTimer, QUrl, Qt, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QSizePolicy, QSlider, QVBoxLayout, QWidget,
)

from . import icons
from .i18n import tr
from .style import TEXT, TEXT_DIM


def fmt_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class VideoPlayer(QWidget):
    positionChanged = Signal(float)   # seconds
    durationChanged = Signal(float)   # seconds

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._duration_s = 0.0
        self._prime = False

        self.video = QVideoWidget()
        self.video.setMinimumHeight(300)
        self.video.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.video.setStyleSheet("background: #000;")

        self.audio_out = QAudioOutput()
        self.audio_out.setVolume(0.7)
        self.player = QMediaPlayer()
        self.player.setVideoOutput(self.video)
        self.player.setAudioOutput(self.audio_out)
        self.player.positionChanged.connect(self._on_position)
        self.player.durationChanged.connect(self._on_duration)
        self.player.playbackStateChanged.connect(self._on_state)
        self.player.mediaStatusChanged.connect(self._on_media_status)

        self._icon_play = icons.icon("Media/Play", TEXT, 18)
        self._icon_pause = icons.icon("Media/Pause", TEXT, 18)

        self.play_btn = self._transport("Media/Play", tr("player.play"), 40)
        self.play_btn.clicked.connect(self.toggle)
        self.prev_btn = self._transport("Media/Skip_Back", tr("player.prev"), 36)
        self.next_btn = self._transport("Media/Skip_Forward", tr("player.next"), 36)

        self.time_label = QLabel("0:00 / 0:00")
        self.time_label.setObjectName("timecode")

        self.volume_icon = QLabel()
        self.volume_icon.setPixmap(icons.pixmap("Media/Volume_Max", TEXT_DIM, 18))

        self.volume = QSlider(Qt.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(70)
        self.volume.setFixedWidth(110)
        self.volume.setToolTip(tr("player.volume"))
        self.volume.valueChanged.connect(self._on_volume)

        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(6)
        bar.addWidget(self.play_btn)
        bar.addWidget(self.prev_btn)
        bar.addWidget(self.next_btn)
        bar.addWidget(self.time_label)
        bar.addStretch(1)
        bar.addWidget(self.volume_icon)
        bar.addWidget(self.volume)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.addWidget(self.video, 1)
        lay.addLayout(bar)

    def _transport(self, icon_name: str, tip: str, width: int) -> QPushButton:
        btn = QPushButton()
        btn.setObjectName("transport")
        btn.setIcon(icons.icon(icon_name, TEXT, 18))
        btn.setIconSize(QSize(18, 18))
        btn.setFixedSize(width, 32)
        btn.setToolTip(tip)
        return btn

    def _on_volume(self, value: int) -> None:
        self.audio_out.setVolume(value / 100.0)
        # Swap the speaker glyph so muting is visible at a glance.
        name = "Media/Volume_Off" if value == 0 else (
            "Media/Volume_Min" if value < 45 else "Media/Volume_Max")
        self.volume_icon.setPixmap(icons.pixmap(name, TEXT_DIM, 18))

    # -- API ----------------------------------------------------------------
    def load(self, path: Path) -> None:
        """Load a file and show its first frame instead of a black rectangle.

        QMediaPlayer decodes nothing until playback starts, so selecting a match
        would otherwise leave an empty box. Nudging play->pause with the audio
        muted produces a poster frame without an audible blip.
        """
        self.stop()
        self._prime = True
        self.player.setSource(QUrl.fromLocalFile(str(path)))

    def _on_media_status(self, status) -> None:
        if not self._prime or status != QMediaPlayer.LoadedMedia:
            return
        self._prime = False
        volume = self.audio_out.volume()
        self.audio_out.setMuted(True)
        self.player.play()

        def settle() -> None:
            self.player.pause()
            self.player.setPosition(0)
            self.audio_out.setMuted(False)
            self.audio_out.setVolume(volume)

        QTimer.singleShot(120, settle)

    def stop(self) -> None:
        self.player.stop()
        self.player.setSource(QUrl())

    def toggle(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def seek(self, seconds: float) -> None:
        self.player.setPosition(int(max(0.0, seconds) * 1000))

    def position_s(self) -> float:
        return self.player.position() / 1000.0

    # -- signals ------------------------------------------------------------
    def _on_position(self, ms: int) -> None:
        secs = ms / 1000.0
        self.time_label.setText(f"{fmt_time(secs)} / {fmt_time(self._duration_s)}")
        self.positionChanged.emit(secs)

    def _on_duration(self, ms: int) -> None:
        self._duration_s = ms / 1000.0
        self.time_label.setText(
            f"{fmt_time(self.position_s())} / {fmt_time(self._duration_s)}")
        self.durationChanged.emit(self._duration_s)

    def _on_state(self, state) -> None:
        playing = state == QMediaPlayer.PlayingState
        self.play_btn.setIcon(self._icon_pause if playing else self._icon_play)
        self.play_btn.setToolTip(tr("player.pause") if playing else tr("player.play"))
