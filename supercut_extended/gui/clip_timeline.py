"""The editing timeline: clips laid out in play order, draggable and trimmable.

This is a different animal from timeline.TimelineWidget, which shows *one recording*
on a real time axis and doubles as a seek bar. Here the x axis is the OUTPUT: clips sit
end to end in the order they will play, so dragging one past another reorders the
montage, and dragging a clip's edge trims it.

Two interactions share the mouse, decided by where the press lands:

    within EDGE_GRAB px of a clip's left/right border  -> trim that end
    anywhere else on the clip                          -> reorder

Trimming is clamped to the source recording's real length, which the caller supplies:
a clip may only be pulled outward as far as footage actually exists.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from ..model import Timeline
from .style import ACCENT, ACCENT_HI, BORDER, SURFACE, SURFACE_2, TEXT, TEXT_DIM
from .timeline import event_color

EDGE_GRAB = 7           # px from a clip border that starts a trim instead of a move
MIN_CLIP_MS = 200.0     # never let a trim collapse a clip to nothing
CLIP_TOP = 34
CLIP_H = 78
BGM_TOP = CLIP_TOP + CLIP_H + 14
BGM_H = 34
MIN_PX_PER_S = 4.0


class ClipTimelineWidget(QWidget):
    """Drag to reorder, drag an edge to trim."""

    changed = Signal()                  # the timeline was mutated
    selected = Signal(int)              # clip index, or -1

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(BGM_TOP + BGM_H + 18)
        self.setMouseTracking(True)
        self._timeline = Timeline()
        self._limits: dict[Path, float] = {}     # source -> real duration in ms
        self._zoom = 1.0
        self._index = -1                          # selected clip
        self._mode = ""                           # "", "move", "trim-l", "trim-r"
        self._drag_from = 0.0
        self._drag_origin: tuple[float, float] = (0.0, 0.0)
        self._insert_at = -1
        self._play_at: tuple[int, float] | None = None   # (clip index, 0..1 through it)

    def set_playhead(self, index: int, ratio: float) -> None:
        """Show where preview playback has reached, as a position inside one clip."""
        self._play_at = None if index < 0 else (index, max(0.0, min(1.0, ratio)))
        self.update()

    # -- data ---------------------------------------------------------------
    def set_timeline(self, timeline: Timeline, limits: dict[Path, float]) -> None:
        self._timeline = timeline
        self._limits = limits
        self._index = -1
        self._rescale()
        self.selected.emit(-1)
        self.update()

    def selected_index(self) -> int:
        return self._index

    def set_zoom(self, zoom: float) -> None:
        self._zoom = max(0.2, min(zoom, 12.0))
        self._rescale()
        self.update()

    def _rescale(self) -> None:
        """Width follows the content so the parent scroll area can pan it."""
        total = max(1.0, self._timeline.duration_s)
        self.setMinimumWidth(int(total * self._px_per_s()) + 40)

    def _px_per_s(self) -> float:
        total = max(1.0, self._timeline.duration_s)
        # Fit the viewport at zoom 1, then scale from there.
        fit = max(MIN_PX_PER_S, (self.parent().width() - 60) / total
                  if self.parent() else 60.0)
        return fit * self._zoom

    # -- geometry -----------------------------------------------------------
    def _rects(self) -> list[tuple[int, QRectF]]:
        """(clip index, rect) for every clip, laid out end to end."""
        out = []
        x = 20.0
        pps = self._px_per_s()
        for i, clip in enumerate(self._timeline.clips):
            w = max(6.0, clip.duration_s * pps)
            out.append((i, QRectF(x, CLIP_TOP, w, CLIP_H)))
            x += w + 2
        return out

    def _hit(self, pos: QPointF) -> tuple[int, str]:
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

        clips = self._timeline.clips
        if not clips:
            p.setPen(QColor(TEXT_DIM))
            p.drawText(self.rect(), Qt.AlignCenter, "クリップがありません")
            return

        rects = self._rects()
        font = QFont(self.font())
        font.setPointSizeF(8.5)
        p.setFont(font)

        for i, rect in rects:
            clip = clips[i]
            colour = QColor(event_color(clip.event_kind or ""))
            if not clip.enabled:
                colour.setAlpha(60)

            path = QPainterPath()
            path.addRoundedRect(rect, 6, 6)
            p.fillPath(path, QColor(SURFACE_2))

            # A coloured cap identifies which event the clip came from.
            cap = QRectF(rect.left(), rect.top(), rect.width(), 5)
            capped = QPainterPath()
            capped.addRoundedRect(cap, 3, 3)
            p.fillPath(capped, colour)

            selected = i == self._index
            p.setPen(QPen(QColor(ACCENT_HI if selected else BORDER), 2 if selected else 1))
            p.drawPath(path)

            if rect.width() > 34:
                p.setPen(QColor(TEXT if clip.enabled else TEXT_DIM))
                label = clip.label or (clip.event_kind or "clip")
                p.drawText(rect.adjusted(6, 10, -6, 0), Qt.AlignTop | Qt.AlignLeft,
                           label)
                p.setPen(QColor(TEXT_DIM))
                p.drawText(rect.adjusted(6, 0, -6, -8),
                           Qt.AlignBottom | Qt.AlignLeft, f"{clip.duration_s:.1f}s")
                if clip.fade_in_ms or clip.fade_out_ms:
                    p.drawText(rect.adjusted(0, 0, -6, -8),
                               Qt.AlignBottom | Qt.AlignRight, "fade")
            if not clip.enabled:
                p.setPen(QPen(QColor(TEXT_DIM), 1, Qt.DashLine))
                p.drawLine(rect.left() + 4, rect.center().y(),
                           rect.right() - 4, rect.center().y())

        # Where a dragged clip would land.
        if self._mode == "move" and self._insert_at >= 0:
            x = 20.0
            pps = self._px_per_s()
            for i, clip in enumerate(clips):
                if i == self._insert_at:
                    break
                x += max(6.0, clip.duration_s * pps) + 2
            p.setPen(QPen(QColor(ACCENT_HI), 3))
            p.drawLine(x - 1, CLIP_TOP - 6, x - 1, CLIP_TOP + CLIP_H + 6)

        # Preview playhead.
        if self._play_at is not None:
            idx, ratio = self._play_at
            for i, rect in rects:
                if i != idx:
                    continue
                x = rect.left() + rect.width() * ratio
                p.setPen(QPen(QColor("#f87171"), 2))
                p.drawLine(x, CLIP_TOP - 8, x, CLIP_TOP + CLIP_H + 8)
                break

        # BGM track.
        total_w = (rects[-1][1].right() - 20) if rects else 0
        bgm_rect = QRectF(20, BGM_TOP, max(40.0, total_w), BGM_H)
        p.setPen(QPen(QColor(BORDER), 1, Qt.DashLine))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(bgm_rect, 5, 5)
        p.setPen(QColor(TEXT_DIM))
        bgm = self._timeline.bgm
        if bgm is None:
            p.drawText(bgm_rect, Qt.AlignCenter, "BGM なし")
        else:
            p.fillPath(_rounded(bgm_rect), QColor(ACCENT).darker(160))
            p.setPen(QColor(TEXT))
            p.drawText(bgm_rect.adjusted(8, 0, -8, 0), Qt.AlignVCenter | Qt.AlignLeft,
                       f"♪ {Path(bgm.path).name}   音量 {bgm.volume:.0%}")

        p.setPen(QColor(TEXT_DIM))
        p.drawText(QRectF(20, 6, 400, 20), Qt.AlignVCenter | Qt.AlignLeft,
                   f"{len(self._timeline.active)} クリップ / "
                   f"{self._timeline.duration_s:.1f} 秒")

    # -- interaction --------------------------------------------------------
    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return
        index, mode = self._hit(event.position())
        self._index = index
        self.selected.emit(index)
        if index < 0:
            self._mode = ""
            self.update()
            return
        clip = self._timeline.clips[index]
        self._mode = mode
        self._drag_from = event.position().x()
        self._drag_origin = (clip.source_start_ms, clip.source_end_ms)
        self._insert_at = index
        self.update()

    def mouseMoveEvent(self, event) -> None:
        pos = event.position()
        if not self._mode:
            _i, mode = self._hit(pos)
            self.setCursor(Qt.SizeHorCursor if mode.startswith("trim")
                           else (Qt.OpenHandCursor if mode else Qt.ArrowCursor))
            return

        clip = self._timeline.clips[self._index]
        delta_ms = (pos.x() - self._drag_from) / self._px_per_s() * 1000.0
        start0, end0 = self._drag_origin

        if self._mode == "trim-l":
            limit = 0.0
            clip.source_start_ms = max(
                limit, min(start0 + delta_ms, clip.source_end_ms - MIN_CLIP_MS))
        elif self._mode == "trim-r":
            limit = self._limits.get(Path(clip.source), end0 + 10_000)
            clip.source_end_ms = min(
                limit, max(end0 + delta_ms, clip.source_start_ms + MIN_CLIP_MS))
        else:
            # Reorder: find which slot the pointer is over.
            self._insert_at = self._slot_at(pos.x())
        self._rescale()
        self.update()
        if self._mode.startswith("trim"):
            self.changed.emit()

    def _slot_at(self, x: float) -> int:
        pps = self._px_per_s()
        edge = 20.0
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
        self.update()

    def wheelEvent(self, event) -> None:
        if event.modifiers() & Qt.ControlModifier:
            self.set_zoom(self._zoom * (1.15 if event.angleDelta().y() > 0 else 0.87))
            event.accept()
        else:
            event.ignore()


def _rounded(rect: QRectF, r: float = 5.0) -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(rect, r, r)
    return path
