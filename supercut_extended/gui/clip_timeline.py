"""The editing timeline: a ruler, named tracks, and clips laid out in play order.

Different animal from timeline.TimelineWidget, which shows *one recording* on a real
time axis and doubles as a seek bar. Here the x axis is the OUTPUT sequence: clips sit
end to end in the order they will play, so dragging one past another reorders the
montage and dragging a clip's edge trims it.

Three interactions share the mouse, decided by where the press lands:

    on the ruler                                       -> scrub the whole sequence
    within EDGE_GRAB px of a clip's left/right border  -> trim that end
    anywhere else on a clip                            -> reorder

Trimming is clamped to the source recording's real length, which the caller supplies:
a clip may only be pulled outward as far as footage actually exists.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from ..model import Timeline
from .style import ACCENT, ACCENT_HI, BORDER, SURFACE, SURFACE_2, TEXT, TEXT_DIM
from .timeline import event_color

EDGE_GRAB = 7           # px from a clip border that starts a trim instead of a move
MIN_CLIP_MS = 200.0     # never let a trim collapse a clip to nothing
HEADER_W = 64           # track-name column down the left
RULER_H = 24
CLIP_TOP = RULER_H + 10
CLIP_H = 74
BGM_TOP = CLIP_TOP + CLIP_H + 8
BGM_H = 32
MIN_PX_PER_S = 4.0
PLAYHEAD = "#f87171"


def timecode(seconds: float) -> str:
    """h:mm:ss.cc -- the montage is short, so centiseconds are the useful precision."""
    seconds = max(0.0, seconds)
    m, s = divmod(seconds, 60)
    h, m = divmod(int(m), 60)
    body = f"{m:02d}:{s:05.2f}"
    return f"{h}:{body}" if h else body


class ClipTimelineWidget(QWidget):
    """Drag to reorder, drag an edge to trim, drag the ruler to scrub."""

    changed = Signal()                  # the timeline was mutated
    selected = Signal(int)              # clip index, or -1
    scrubbed = Signal(float)            # seconds into the whole sequence
    split = Signal(int, float)          # clip index, ms into that clip's source
    aboutToChange = Signal()            # a mutation is starting -- snapshot it

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(BGM_TOP + BGM_H + 16)
        self.setMouseTracking(True)
        self._timeline = Timeline()
        self._limits: dict[Path, float] = {}     # source -> real duration in ms
        self._zoom = 1.0
        self._index = -1                          # selected clip
        self._mode = ""                           # "", "move", "trim-l", "trim-r", "scrub"
        self._drag_from = 0.0
        self._drag_origin: tuple[float, float] = (0.0, 0.0)
        self._insert_at = -1
        self._play_s = 0.0                        # playhead, in sequence seconds
        self._show_play = False
        # While a clip is being dragged it is drawn as a floating copy that chases the
        # cursor instead of snapping to it. The lag is what makes the drag read as
        # picking the clip up rather than teleporting it.
        self._ghost_x = 0.0                       # where the copy is drawn now
        self._ghost_to = 0.0                      # where the cursor says it should be
        self._ghost_lift = 0.0                    # 0..1, how far it has risen
        self._grab_dx = 0.0                       # cursor offset inside the clip
        self._tool = "select"                     # "select" or "cut"
        self._cut_x: float | None = None
        self._ease = QTimer(self)
        self._ease.setInterval(16)
        self._ease.timeout.connect(self._step_ghost)

    def _step_ghost(self) -> None:
        """Ease the floating copy toward the cursor, and settle it when it arrives."""
        dragging = self._mode == "move"
        target_lift = 1.0 if dragging else 0.0
        self._ghost_x += (self._ghost_to - self._ghost_x) * 0.28
        self._ghost_lift += (target_lift - self._ghost_lift) * 0.25
        if not dragging and self._ghost_lift < 0.02:
            self._ghost_lift = 0.0
            self._ease.stop()
        self.update()

    # -- data ---------------------------------------------------------------
    def set_timeline(self, timeline: Timeline, limits: dict[Path, float]) -> None:
        self._timeline = timeline
        self._limits = limits
        self._index = -1
        self._rescale()
        self.selected.emit(-1)
        self.update()

    def set_tool(self, tool: str) -> None:
        self._tool = tool
        self.setCursor(Qt.CrossCursor if tool == "cut" else Qt.ArrowCursor)
        self.update()

    def tool(self) -> str:
        return self._tool

    def selected_index(self) -> int:
        return self._index

    def select(self, index: int) -> None:
        self._index = index
        self.selected.emit(index)
        self.update()

    def set_playhead_seconds(self, seconds: float, visible: bool = True) -> None:
        self._play_s = max(0.0, seconds)
        self._show_play = visible
        self.update()

    def set_zoom(self, zoom: float) -> None:
        self._zoom = max(0.2, min(zoom, 20.0))
        self._rescale()
        self.update()

    def zoom(self) -> float:
        return self._zoom

    def fit(self) -> None:
        self._zoom = 1.0
        self._rescale()
        self.update()

    def _rescale(self) -> None:
        total = max(1.0, self._timeline.duration_s)
        self.setMinimumWidth(int(HEADER_W + total * self._px_per_s()) + 30)

    def _px_per_s(self) -> float:
        total = max(1.0, self._timeline.duration_s)
        viewport = (self.parent().width() if self.parent() else 900) - HEADER_W - 40
        fit = max(MIN_PX_PER_S, viewport / total)
        return fit * self._zoom

    # -- geometry -----------------------------------------------------------
    def _rects(self) -> list[tuple[int, QRectF]]:
        out = []
        x = float(HEADER_W)
        pps = self._px_per_s()
        for i, clip in enumerate(self._timeline.clips):
            w = max(6.0, clip.duration_s * pps)
            out.append((i, QRectF(x, CLIP_TOP, w, CLIP_H)))
            x += w + 2
        return out

    def _x_for_seconds(self, seconds: float) -> float:
        return HEADER_W + seconds * self._px_per_s()

    def _seconds_for_x(self, x: float) -> float:
        return max(0.0, (x - HEADER_W) / self._px_per_s())

    def _hit(self, pos: QPointF) -> tuple[int, str]:
        if pos.y() <= RULER_H:
            return -1, "scrub"
        for i, rect in self._rects():
            if not rect.adjusted(-2, 0, 2, 0).contains(pos):
                continue
            if pos.x() - rect.left() <= EDGE_GRAB:
                return i, "trim-l"
            if rect.right() - pos.x() <= EDGE_GRAB:
                return i, "trim-r"
            return i, "move"
        return -1, ""

    # -- painting -----------------------------------------------------------
    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(SURFACE))

        font = QFont(self.font())
        font.setPointSizeF(8.0)
        p.setFont(font)

        self._paint_ruler(p)
        self._paint_headers(p)

        clips = self._timeline.clips
        if not clips:
            p.setPen(QColor(TEXT_DIM))
            p.drawText(QRectF(HEADER_W, CLIP_TOP, 400, CLIP_H),
                       Qt.AlignVCenter | Qt.AlignLeft, "  クリップがありません")
            return

        for i, rect in self._rects():
            if self._mode == "move" and i == self._index:
                p.setPen(QPen(QColor(BORDER), 1, Qt.DashLine))
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(rect, 5, 5)
                continue
            self._paint_clip(p, clips[i], rect, i == self._index)

        if self._ghost_lift > 0.01 and 0 <= self._index < len(clips):
            self._paint_ghost(p, clips[self._index])

        if self._mode == "move" and self._insert_at >= 0:
            x = float(HEADER_W)
            pps = self._px_per_s()
            for i, clip in enumerate(clips):
                if i == self._insert_at:
                    break
                x += max(6.0, clip.duration_s * pps) + 2
            p.setPen(QPen(QColor(ACCENT_HI), 3))
            p.drawLine(x - 1, CLIP_TOP - 6, x - 1, BGM_TOP + BGM_H + 4)

        self._paint_bgm(p)
        if self._tool == 'cut' and self._cut_x is not None:
            p.setPen(QPen(QColor('#fbbf24'), 1, Qt.DashLine))
            p.drawLine(self._cut_x, CLIP_TOP - 6, self._cut_x, CLIP_TOP + CLIP_H + 6)
        self._paint_playhead(p)

    def _paint_ruler(self, p: QPainter) -> None:
        total = max(1.0, self._timeline.duration_s)
        p.fillRect(QRectF(0, 0, self.width(), RULER_H), QColor(SURFACE_2))
        p.setPen(QPen(QColor(BORDER), 1))
        p.drawLine(0, RULER_H, self.width(), RULER_H)

        pps = self._px_per_s()
        # Aim for a label roughly every 90px, snapped to a readable interval.
        for step in (0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300):
            if step * pps >= 90:
                break
        p.setPen(QColor(TEXT_DIM))
        t = 0.0
        while t <= total + step:
            x = self._x_for_seconds(t)
            p.drawLine(x, RULER_H - 6, x, RULER_H)
            p.drawText(QRectF(x + 3, 2, 80, RULER_H - 4),
                       Qt.AlignVCenter | Qt.AlignLeft, timecode(t))
            half = self._x_for_seconds(t + step / 2)
            p.drawLine(half, RULER_H - 3, half, RULER_H)
            t += step

    def _paint_headers(self, p: QPainter) -> None:
        p.fillRect(QRectF(0, RULER_H, HEADER_W, self.height() - RULER_H),
                   QColor(SURFACE_2))
        p.setPen(QPen(QColor(BORDER), 1))
        p.drawLine(HEADER_W, RULER_H, HEADER_W, self.height())
        p.setPen(QColor(TEXT_DIM))
        p.drawText(QRectF(0, CLIP_TOP, HEADER_W - 8, CLIP_H),
                   Qt.AlignVCenter | Qt.AlignRight, "V1")
        p.drawText(QRectF(0, BGM_TOP, HEADER_W - 8, BGM_H),
                   Qt.AlignVCenter | Qt.AlignRight, "BGM")

    def _paint_clip(self, p: QPainter, clip, rect: QRectF, selected: bool) -> None:
        colour = QColor(event_color(clip.event_kind or ""))
        if not clip.enabled:
            colour.setAlpha(60)

        path = QPainterPath()
        path.addRoundedRect(rect, 5, 5)
        p.fillPath(path, QColor(SURFACE_2).lighter(112))

        cap = QPainterPath()
        cap.addRoundedRect(QRectF(rect.left(), rect.top(), rect.width(), 5), 3, 3)
        p.fillPath(cap, colour)

        p.setPen(QPen(QColor(ACCENT_HI if selected else BORDER), 2 if selected else 1))
        p.drawPath(path)

        # Fade ramps, drawn as the triangles an editor would show.
        pps = self._px_per_s()
        p.setPen(QPen(QColor(TEXT), 1))
        if clip.fade_in_ms:
            w = min(rect.width(), clip.fade_in_ms / 1000.0 * pps)
            p.drawLine(rect.left(), rect.bottom() - 2, rect.left() + w, rect.top() + 7)
        if clip.fade_out_ms:
            w = min(rect.width(), clip.fade_out_ms / 1000.0 * pps)
            p.drawLine(rect.right() - w, rect.top() + 7, rect.right(), rect.bottom() - 2)

        if rect.width() > 40:
            p.setPen(QColor(TEXT if clip.enabled else TEXT_DIM))
            p.drawText(rect.adjusted(6, 9, -6, 0), Qt.AlignTop | Qt.AlignLeft,
                       clip.label or (clip.event_kind or "clip"))
            p.setPen(QColor(TEXT_DIM))
            p.drawText(rect.adjusted(6, 0, -6, -6), Qt.AlignBottom | Qt.AlignLeft,
                       timecode(clip.duration_s))
        if not clip.enabled:
            p.setPen(QPen(QColor(TEXT_DIM), 1, Qt.DashLine))
            p.drawLine(rect.left() + 4, rect.center().y(),
                       rect.right() - 4, rect.center().y())

    def _paint_ghost(self, p: QPainter, clip) -> None:
        """The clip being dragged, floating above the track under the cursor."""
        pps = self._px_per_s()
        w = max(6.0, clip.duration_s * pps)
        lift = self._ghost_lift
        rect = QRectF(self._ghost_x, CLIP_TOP - 10 * lift, w, CLIP_H)

        p.save()
        p.setOpacity(0.30 * lift)
        shadow = QPainterPath()
        shadow.addRoundedRect(rect.adjusted(3, 6, 3, 8), 6, 6)
        p.fillPath(shadow, QColor("#000000"))
        p.setOpacity(0.88 * lift)

        path = QPainterPath()
        path.addRoundedRect(rect, 6, 6)
        p.fillPath(path, QColor(SURFACE_2).lighter(125))
        cap = QPainterPath()
        cap.addRoundedRect(QRectF(rect.left(), rect.top(), rect.width(), 5), 3, 3)
        p.fillPath(cap, QColor(event_color(clip.event_kind or "")))
        p.setPen(QPen(QColor(ACCENT_HI), 2))
        p.drawPath(path)
        if rect.width() > 40:
            p.setPen(QColor(TEXT))
            p.drawText(rect.adjusted(6, 9, -6, 0), Qt.AlignTop | Qt.AlignLeft,
                       clip.label or (clip.event_kind or "clip"))
        p.restore()

    def _paint_bgm(self, p: QPainter) -> None:
        total = max(1.0, self._timeline.duration_s)
        rect = QRectF(HEADER_W, BGM_TOP, total * self._px_per_s(), BGM_H)
        bgm = self._timeline.bgm
        if bgm is None:
            p.setPen(QPen(QColor(BORDER), 1, Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(rect, 4, 4)
            return
        path = QPainterPath()
        path.addRoundedRect(rect, 4, 4)
        p.fillPath(path, QColor(ACCENT).darker(170))
        p.setPen(QPen(QColor(ACCENT), 1))
        p.drawPath(path)
        p.setPen(QColor(TEXT))
        p.drawText(rect.adjusted(8, 0, -8, 0), Qt.AlignVCenter | Qt.AlignLeft,
                   f"♪ {Path(bgm.path).name}   {bgm.volume:.0%}")

    def _paint_playhead(self, p: QPainter) -> None:
        if not self._show_play:
            return
        x = self._x_for_seconds(self._play_s)
        p.setPen(QPen(QColor(PLAYHEAD), 2))
        p.drawLine(x, RULER_H - 6, x, BGM_TOP + BGM_H + 4)
        head = QPainterPath()
        head.moveTo(x - 5, RULER_H - 10)
        head.lineTo(x + 5, RULER_H - 10)
        head.lineTo(x, RULER_H - 2)
        head.closeSubpath()
        p.fillPath(head, QColor(PLAYHEAD))

    # -- interaction --------------------------------------------------------
    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return
        index, mode = self._hit(event.position())
        if self._tool == "cut" and index >= 0 and mode != "scrub":
            rect = dict(self._rects())[index]
            clip = self._timeline.clips[index]
            into = (event.position().x() - rect.left()) / max(1.0, rect.width())
            self.split.emit(index, clip.source_start_ms + into * clip.duration_ms)
            return
        if mode == "scrub":
            self._mode = "scrub"
            self.scrubbed.emit(self._seconds_for_x(event.position().x()))
            return
        self._index = index
        self.selected.emit(index)
        if index < 0:
            self._mode = ""
            self.update()
            return
        clip = self._timeline.clips[index]
        self.aboutToChange.emit()
        self._mode = mode
        self._drag_from = event.position().x()
        self._drag_origin = (clip.source_start_ms, clip.source_end_ms)
        self._insert_at = index
        if mode == "move":
            rect = dict(self._rects())[index]
            self._grab_dx = event.position().x() - rect.left()
            self._ghost_x = self._ghost_to = rect.left()
            self._ease.start()
        self.update()

    def mouseMoveEvent(self, event) -> None:
        pos = event.position()
        if self._tool == "cut":
            i, mode = self._hit(pos)
            self._cut_x = pos.x() if (i >= 0 and mode != "scrub") else None
            self.update()
            if not self._mode:
                return
        if not self._mode:
            _i, mode = self._hit(pos)
            self.setCursor(Qt.SizeHorCursor if mode.startswith("trim")
                           else (Qt.PointingHandCursor if mode == "scrub"
                                 else (Qt.OpenHandCursor if mode else Qt.ArrowCursor)))
            return

        if self._mode == "scrub":
            self.scrubbed.emit(self._seconds_for_x(pos.x()))
            return

        clip = self._timeline.clips[self._index]
        delta_ms = (pos.x() - self._drag_from) / self._px_per_s() * 1000.0
        start0, end0 = self._drag_origin

        if self._mode == "trim-l":
            clip.source_start_ms = max(
                0.0, min(start0 + delta_ms, clip.source_end_ms - MIN_CLIP_MS))
        elif self._mode == "trim-r":
            limit = self._limits.get(Path(clip.source), end0 + 10_000)
            clip.source_end_ms = min(
                limit, max(end0 + delta_ms, clip.source_start_ms + MIN_CLIP_MS))
        else:
            self._insert_at = self._slot_at(pos.x())
            self._ghost_to = pos.x() - self._grab_dx
        self._rescale()
        self.update()
        if self._mode.startswith("trim"):
            self.changed.emit()

    def _slot_at(self, x: float) -> int:
        pps = self._px_per_s()
        edge = float(HEADER_W)
        for i, clip in enumerate(self._timeline.clips):
            w = max(6.0, clip.duration_s * pps)
            if x < edge + w / 2:
                return i
            edge += w + 2
        return len(self._timeline.clips) - 1

    def mouseReleaseEvent(self, _event) -> None:
        if self._mode == "move" and 0 <= self._insert_at < len(self._timeline.clips):
            if self._insert_at != self._index:
                self._timeline.move(self._index, self._insert_at)
                self._index = self._insert_at
                self.selected.emit(self._index)
                self.changed.emit()
        self._mode = ""
        self._insert_at = -1
        self._rescale()
        # Let the copy glide into its new home rather than vanishing mid-air.
        if 0 <= self._index < len(self._timeline.clips):
            landed = dict(self._rects()).get(self._index)
            if landed is not None:
                self._ghost_to = landed.left()
                self._ease.start()
        self.update()

    def wheelEvent(self, event) -> None:
        if event.modifiers() & Qt.ControlModifier:
            self.set_zoom(self._zoom * (1.15 if event.angleDelta().y() > 0 else 0.87))
            event.accept()
        else:
            event.ignore()
