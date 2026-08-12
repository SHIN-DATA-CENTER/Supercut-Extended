"""Video preview with transport controls.

There is no separate seek bar: the event timeline below the video *is* the seek bar.
That is how Outplayed presents it, and it means the playhead, the event markers and
the segments that will be cut all share one coordinate system -- so you can see the
clip boundaries against the footage instead of guessing.

The picture goes through a QGraphicsScene rather than a plain QVideoWidget, because the
preview has to show the *output* frame: cropped, resized and optionally stretched
exactly as the render will do it. A QVideoWidget can only letterbox a whole frame, so
the black bars a user is trying to remove would stay on screen right up until the file
was written.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRectF, QSize, QSizeF, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QTransform
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem
from PySide6.QtWidgets import (
    QFrame, QGraphicsScene, QGraphicsView, QHBoxLayout, QLabel, QPushButton,
    QSizePolicy, QSlider, QVBoxLayout, QWidget,
)

from ..model import Framing
from . import icons
from .controls import NoScrollSlider
from .i18n import tr
from .style import BORDER, TEXT, TEXT_DIM


def fmt_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class FramedVideoView(QGraphicsView):
    """Shows a video item through the output frame the render will produce.

    The scene rect *is* the output frame. The video item is scaled and positioned so
    that the kept part of the source lands inside it, which means what you see is
    literally the geometry ffmpeg is being asked for -- including the distortion when
    stretching is on.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        self._scene = QGraphicsScene()
        super().__init__(self._scene, parent)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setBackgroundBrush(QBrush(QColor("#000000")))
        self.setRenderHint(QPainter.SmoothPixmapTransform)

        self.item = QGraphicsVideoItem()
        # The item's rect is driven directly, so it must not letterbox inside itself.
        self.item.setAspectRatioMode(Qt.IgnoreAspectRatio)
        self._scene.addItem(self.item)

        # A hairline around the output frame: without it the padded area and the
        # widget's own background are both black and the frame edge is invisible.
        self._edge = self._scene.addRect(
            QRectF(0, 0, 16, 9), QPen(QColor(BORDER), 0), QBrush(Qt.NoBrush))
        self._edge.setZValue(10)

        self._framing = Framing()
        self._native = QSizeF(0, 0)
        self.item.nativeSizeChanged.connect(self._on_native)

    def _on_native(self, size: QSizeF) -> None:
        if size.width() > 0 and size.height() > 0:
            self._native = size
            self.apply()

    def set_framing(self, framing: Framing) -> None:
        self._framing = framing
        self.apply()

    def framing(self) -> Framing:
        return self._framing

    def output_size(self) -> tuple[int, int]:
        w, h = int(self._native.width()), int(self._native.height())
        if w <= 0 or h <= 0:
            return 0, 0
        return self._framing.output_size(w, h)

    def apply(self) -> None:
        """Lay the video item out inside the output frame, then fit that on screen."""
        nw, nh = int(self._native.width()), int(self._native.height())
        if nw <= 0 or nh <= 0:
            return
        fr = self._framing
        self.item.setSize(QSizeF(nw, nh))
        sx, sy, sw, sh = fr.source_rect(nw, nh)
        ow, oh = fr.output_size(nw, nh)

        if fr.resizes and fr.stretch:
            # Fill the frame and let the aspect ratio break -- that is the point.
            scale_x, scale_y = ow / sw, oh / sh
        else:
            scale_x = scale_y = min(ow / sw, oh / sh)
        # Centre the kept region in the frame; the leftovers are the black padding
        # that the render's pad filter would add.
        left = (ow - sw * scale_x) / 2.0
        top = (oh - sh * scale_y) / 2.0
        self.item.setTransform(QTransform.fromScale(scale_x, scale_y))
        self.item.setPos(left - sx * scale_x, top - sy * scale_y)

        frame = QRectF(0, 0, ow, oh)
        self._edge.setRect(frame)
        self._scene.setSceneRect(frame)
        self.fitInView(frame, Qt.KeepAspectRatio)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # fitInView is a one-shot transform, so the frame has to be re-fitted whenever
        # the widget changes size or the video would stay at its old scale.
        if self._native.width() > 0:
            self.fitInView(self._scene.sceneRect(), Qt.KeepAspectRatio)


class VideoPlayer(QWidget):
    positionChanged = Signal(float)   # seconds
    durationChanged = Signal(float)   # seconds

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._duration_s = 0.0
        self._prime = False

        self.video = FramedVideoView()
        self.video.setMinimumHeight(300)
        self.video.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self.audio_out = QAudioOutput()
        self.audio_out.setVolume(0.7)
        self.player = QMediaPlayer()
        self.player.setVideoOutput(self.video.item)
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

        # Says what the preview is actually showing, so a cropped or stretched frame
        # is never mistaken for the source.
        self.frame_label = QLabel("")
        self.frame_label.setObjectName("captionLabel")
        # "source size" framings only resolve once the real frame size is known.
        self.video.item.nativeSizeChanged.connect(
            lambda _s: self._refresh_frame_label())

        self.volume_icon = QLabel()
        self.volume_icon.setPixmap(icons.pixmap("Media/Volume_Max", TEXT_DIM, 18))

        self.volume = NoScrollSlider(Qt.Horizontal)
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
        bar.addSpacing(10)
        bar.addWidget(self.frame_label)
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
    def set_framing(self, framing: Framing) -> None:
        """Preview through the output frame the render would produce."""
        self.video.set_framing(framing)
        self._refresh_frame_label()

    def framing(self) -> Framing:
        return self.video.framing()

    def _refresh_frame_label(self) -> None:
        framing = self.video.framing()
        w, h = self.video.output_size()
        if not framing.active or not w:
            self.frame_label.setText("")
            return
        note = tr("frame.stretched") if (framing.stretch and framing.resizes) else ""
        self.frame_label.setText(f"{w}x{h}{note}")

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
