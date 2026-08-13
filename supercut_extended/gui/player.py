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

from PySide6.QtCore import QPointF, QRectF, QSize, QSizeF, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QTransform
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem
from PySide6.QtWidgets import (
    QFrame, QGraphicsItem, QGraphicsScene, QGraphicsView, QHBoxLayout, QLabel,
    QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from ..model import Framing
from . import icons
from .controls import NoScrollSlider
from .i18n import tr
from .style import ACCENT, ACCENT_HI, BORDER, TEXT, TEXT_DIM

# The unplayed track and the "you would seek here" shade behind the pointer.
TRACK_BG = "#2a3346"
TRACK_HOVER = "#3d4a63"


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

    Nothing outside that frame is painted. The widget is almost always a different
    shape from the video, and filling the leftover with black drew bars that are not
    in the footage and not in the output -- so the surrounding area is left
    transparent and the window's own background shows through instead. Black is
    painted *inside* the frame only, where padding really will be encoded.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        self._scene = QGraphicsScene()
        super().__init__(self._scene, parent)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setRenderHint(QPainter.SmoothPixmapTransform)
        # No background brush and a transparent viewport: the parent paints here.
        self.setBackgroundBrush(QBrush(Qt.NoBrush))
        self.setStyleSheet("background: transparent; border: none;")
        self.viewport().setAutoFillBackground(False)

        # The output frame itself, painted black underneath the picture. When no
        # framing is set the video covers it exactly, so nothing shows; when framing
        # pads, this is the padding that really does get encoded.
        self._backdrop = self._scene.addRect(
            QRectF(0, 0, 16, 9), QPen(Qt.NoPen), QBrush(QColor("#000000")))
        self._backdrop.setZValue(-10)

        # The video hangs off a clipping parent rather than sitting in the scene.
        # setSceneRect only decides what is *scrolled to*, not what is painted: a
        # cropped source is positioned with a negative offset, so the part being
        # cropped away carried on being drawn outside the frame and reappeared as
        # black bars whenever the widget was wider than the output.
        self._clip = self._scene.addRect(
            QRectF(0, 0, 16, 9), QPen(Qt.NoPen), QBrush(Qt.NoBrush))
        self._clip.setFlag(QGraphicsItem.GraphicsItemFlag.ItemClipsChildrenToShape,
                           True)

        self.item = QGraphicsVideoItem()
        # The item's rect is driven directly, so it must not letterbox inside itself.
        self.item.setAspectRatioMode(Qt.IgnoreAspectRatio)
        self.item.setParentItem(self._clip)

        # A hairline marking the output frame, shown only when framing is actually
        # doing something -- otherwise it would just trace the edge of the picture.
        self._edge = self._scene.addRect(
            QRectF(0, 0, 16, 9), QPen(QColor(BORDER), 0), QBrush(Qt.NoBrush))
        self._edge.setZValue(10)
        self._edge.setVisible(False)

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
        self._backdrop.setRect(frame)
        self._clip.setRect(frame)
        self._edge.setRect(frame)
        self._edge.setVisible(fr.active)
        self._scene.setSceneRect(frame)
        self.fitInView(frame, Qt.KeepAspectRatio)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # fitInView is a one-shot transform, so the frame has to be re-fitted whenever
        # the widget changes size or the video would stay at its old scale.
        if self._native.width() > 0:
            self.fitInView(self._scene.sceneRect(), Qt.KeepAspectRatio)


class SeekBar(QWidget):
    """A thin scrubber that thickens under the pointer, as video players do.

    Not a QSlider: a slider's groove and handle are a fixed size, and the whole point
    here is that the bar is unobtrusive until you reach for it. Drawing it directly is
    also what allows the hover time to be shown at the pointer rather than as a
    tooltip that appears half a second late.
    """

    seeked = Signal(float)        # seconds, released
    scrubbing = Signal(bool)      # dragging started / finished

    HEIGHT = 20                   # generous hit area; the bar itself is thinner
    IDLE_H = 3.0
    HOVER_H = 6.0
    KNOB_R = 6.5

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(self.HEIGHT)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self._duration = 0.0
        self._position = 0.0
        self._hover_x: float | None = None
        self._dragging = False
        self._grow = 0.0          # 0 = idle, 1 = fully expanded

        # Animating the thickness rather than snapping it is most of what makes this
        # read as a video player instead of a progress bar.
        self._anim = QTimer(self)
        self._anim.setInterval(16)
        self._anim.timeout.connect(self._step)

    # -- state --------------------------------------------------------------
    def set_duration(self, seconds: float) -> None:
        self._duration = max(0.0, seconds)
        self.update()

    def set_position(self, seconds: float) -> None:
        if self._dragging:
            return            # never fight the pointer while it is being dragged
        self._position = max(0.0, seconds)
        self.update()

    def is_dragging(self) -> bool:
        return self._dragging

    # -- geometry -----------------------------------------------------------
    def _track(self) -> QRectF:
        h = self.IDLE_H + (self.HOVER_H - self.IDLE_H) * self._grow
        return QRectF(self.KNOB_R, (self.height() - h) / 2.0,
                      max(1.0, self.width() - 2 * self.KNOB_R), h)

    def _fraction_at(self, x: float) -> float:
        track = self._track()
        if track.width() <= 0:
            return 0.0
        return min(1.0, max(0.0, (x - track.left()) / track.width()))

    def _fraction(self) -> float:
        if self._duration <= 0:
            return 0.0
        return min(1.0, max(0.0, self._position / self._duration))

    # -- animation ----------------------------------------------------------
    def _target_grow(self) -> float:
        return 1.0 if (self.underMouse() or self._dragging) else 0.0

    def _step(self) -> None:
        target = self._target_grow()
        self._grow += (target - self._grow) * 0.35
        if abs(target - self._grow) < 0.01:
            self._grow = target
            self._anim.stop()
        self.update()

    def _animate(self) -> None:
        if not self._anim.isActive():
            self._anim.start()

    # -- events -------------------------------------------------------------
    def enterEvent(self, event) -> None:
        super().enterEvent(event)
        self._animate()

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        self._hover_x = None
        self._animate()

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.LeftButton or self._duration <= 0:
            return
        self._dragging = True
        self.scrubbing.emit(True)
        self._hover_x = event.position().x()
        self._position = self._fraction_at(self._hover_x) * self._duration
        self.update()

    def mouseMoveEvent(self, event) -> None:
        self._hover_x = event.position().x()
        if self._dragging and self._duration > 0:
            self._position = self._fraction_at(self._hover_x) * self._duration
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.LeftButton or not self._dragging:
            return
        self._dragging = False
        self.scrubbing.emit(False)
        if self._duration > 0:
            self.seeked.emit(self._fraction_at(event.position().x()) * self._duration)
        self._animate()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        track = self._track()
        radius = track.height() / 2.0

        p.setPen(Qt.NoPen)
        p.setBrush(QColor(TRACK_BG))
        p.drawRoundedRect(track, radius, radius)

        # Where the pointer would seek to, drawn behind the played portion.
        if self._hover_x is not None and self._duration > 0:
            hover = QRectF(track)
            hover.setWidth(track.width() * self._fraction_at(self._hover_x))
            p.setBrush(QColor(TRACK_HOVER))
            p.drawRoundedRect(hover, radius, radius)

        played = QRectF(track)
        played.setWidth(track.width() * self._fraction())
        p.setBrush(QColor(ACCENT))
        p.drawRoundedRect(played, radius, radius)

        if self._grow > 0.01 and self._duration > 0:
            r = self.KNOB_R * self._grow
            cx = track.left() + track.width() * self._fraction()
            p.setBrush(QColor(ACCENT_HI))
            p.drawEllipse(QPointF(cx, track.center().y()), r, r)
        p.end()

    def hover_seconds(self) -> float | None:
        if self._hover_x is None or self._duration <= 0:
            return None
        return self._fraction_at(self._hover_x) * self._duration


class VideoPlayer(QWidget):
    positionChanged = Signal(float)   # seconds
    durationChanged = Signal(float)   # seconds

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._duration_s = 0.0
        self._prime = False
        # Optional hooks so a caller whose real timeline is not the source file can
        # own what the readout and the scrubber mean. The editor uses them: its
        # sequence is 5 minutes cut out of a 33 minute recording, and showing the
        # recording's clock made the montage's own length impossible to read.
        #   time_map(source_seconds) -> (position, duration) to display
        #   seek_map(position)       -> caller handles the seek
        self.time_map = None
        self.seek_map = None
        self._pending_seek = 0.0
        self._pending_play = False

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

        # Deliberately NOT called `seek`: that name is already the method callers use
        # (main_window wires timeline.seekRequested straight to player.seek), and an
        # attribute of the same name would silently replace it.
        self.seek_bar = SeekBar()
        self.seek_bar.seeked.connect(self._on_seek_requested)

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
        lay.setSpacing(2)
        lay.addWidget(self.video, 1)
        lay.addWidget(self.seek_bar)
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

    def load(self, path: Path, start_s: float = 0.0, play: bool = False) -> None:
        """Load a file, land on `start_s`, and optionally keep playing.

        QMediaPlayer decodes nothing until playback starts, so selecting a match
        would otherwise leave an empty box. Nudging play->pause with the audio
        muted produces a poster frame without an audible blip.

        The start position has to be carried through rather than seeked afterwards:
        a seek issued before the media reports itself loaded is discarded, so
        `load(); seek(x)` silently left playback sitting at 0. That is why moving to
        a clip cut from a different recording used to restart the source from the
        beginning -- and stop.
        """
        self.stop()
        self._prime = True
        self._pending_seek = max(0.0, start_s)
        self._pending_play = play
        self.player.setSource(QUrl.fromLocalFile(str(path)))

    def _on_media_status(self, status) -> None:
        if not self._prime or status != QMediaPlayer.LoadedMedia:
            return
        self._prime = False
        volume = self.audio_out.volume()
        self.audio_out.setMuted(True)
        self.player.play()

        def settle() -> None:
            self.player.setPosition(int(self._pending_seek * 1000))
            if not self._pending_play:
                self.player.pause()
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

    def pause(self) -> None:
        """Stop advancing but keep the media loaded.

        Distinct from stop(), which drops the source: after that the widget goes black
        and nothing can be played again until something reloads it.
        """
        self.player.pause()

    def _on_seek_requested(self, seconds: float) -> None:
        if self.seek_map is not None:
            self.seek_map(seconds)
        else:
            self.seek(seconds)

    def _displayed(self, source_seconds: float) -> tuple[float, float]:
        if self.time_map is not None:
            return self.time_map(source_seconds)
        return source_seconds, self._duration_s

    def position_s(self) -> float:
        return self.player.position() / 1000.0

    # -- signals ------------------------------------------------------------
    def _refresh_readout(self, source_seconds: float) -> None:
        position, duration = self._displayed(source_seconds)
        self.time_label.setText(f"{fmt_time(position)} / {fmt_time(duration)}")
        self.seek_bar.set_duration(duration)
        self.seek_bar.set_position(position)

    def _on_position(self, ms: int) -> None:
        secs = ms / 1000.0
        self._refresh_readout(secs)
        self.positionChanged.emit(secs)

    def _on_duration(self, ms: int) -> None:
        self._duration_s = ms / 1000.0
        self._refresh_readout(self.position_s())
        self.durationChanged.emit(self._duration_s)

    def _on_state(self, state) -> None:
        playing = state == QMediaPlayer.PlayingState
        self.play_btn.setIcon(self._icon_pause if playing else self._icon_play)
        self.play_btn.setToolTip(tr("player.pause") if playing else tr("player.play"))
