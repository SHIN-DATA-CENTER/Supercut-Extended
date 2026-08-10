"""Event timeline that doubles as the video scrubber.

Three stacked lanes over one shared time axis:

    segments  the blocks that will actually be exported
    events    every detected moment, coloured by kind
    ruler     time labels

Because the playhead lives on the same axis, you can watch the video and see exactly
where the cut boundaries fall -- which is the thing Outplayed's own UI gives you and
a bare CLI cannot.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor, QFont, QFontMetrics, QLinearGradient, QPainter, QPainterPath, QPen,
)
from PySide6.QtWidgets import QSizePolicy, QToolTip, QWidget

from ..model import GameEvent, Segment
from .i18n import event_label

EVENT_COLORS: dict[str, str] = {
    "kill": "#4ade80",
    "ace": "#fbbf24",
    "knockdown": "#2dd4bf",
    "knocked_out": "#fb923c",
    "assist": "#60a5fa",
    "victory": "#c084fc",
    "death": "#f87171",
    "respawned": "#94a3b8",
    "revived": "#a3a3a3",
}
DEFAULT_COLOR = "#94a3b8"

SEG_H = 22.0
EV_H = 34.0
RULER_H = 16.0
PAD = 10.0


def event_color(kind: str) -> QColor:
    return QColor(EVENT_COLORS.get(kind, DEFAULT_COLOR))


class TimelineWidget(QWidget):
    seekRequested = Signal(float)     # seconds

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._duration_s = 0.0
        self._events: list[GameEvent] = []
        self._segments: list[Segment] = []
        self._playhead_s = 0.0
        self._hover_x: float | None = None
        self.setMinimumHeight(int(SEG_H + EV_H + RULER_H + PAD * 2 + 6))
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    # -- data ---------------------------------------------------------------
    def set_data(self, duration_s: float, events: list[GameEvent],
                 segments: list[Segment]) -> None:
        self._duration_s = max(0.0, duration_s)
        self._events = list(events)
        self._segments = list(segments)
        self.update()

    def set_playhead(self, seconds: float) -> None:
        if abs(seconds - self._playhead_s) < 0.02:
            return
        self._playhead_s = seconds
        self.update()

    def event_times(self) -> list[float]:
        return sorted(e.time_ms / 1000.0 for e in self._events)

    # -- geometry -----------------------------------------------------------
    def _track(self) -> QRectF:
        return QRectF(PAD, PAD, max(1.0, self.width() - PAD * 2),
                      self.height() - PAD * 2)

    def _x_for(self, seconds: float) -> float:
        r = self._track()
        if self._duration_s <= 0:
            return r.left()
        return r.left() + (seconds / self._duration_s) * r.width()

    def _seconds_for(self, x: float) -> float:
        r = self._track()
        if r.width() <= 0 or self._duration_s <= 0:
            return 0.0
        frac = (x - r.left()) / r.width()
        return max(0.0, min(self._duration_s, frac * self._duration_s))

    # -- painting -----------------------------------------------------------
    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self._track()
        seg_top = r.top()
        ev_top = seg_top + SEG_H + 6
        ruler_top = ev_top + EV_H + 2

        p.fillRect(self.rect(), QColor("#0d1017"))

        # --- segment lane
        base = QPainterPath()
        base.addRoundedRect(QRectF(r.left(), seg_top, r.width(), SEG_H), 5, 5)
        p.fillPath(base, QColor("#191e2b"))

        for seg in self._segments:
            x0 = self._x_for(seg.start_s)
            x1 = self._x_for(seg.end_ms / 1000.0)
            rect = QRectF(x0, seg_top, max(2.0, x1 - x0), SEG_H)
            grad = QLinearGradient(rect.topLeft(), rect.bottomLeft())
            grad.setColorAt(0.0, QColor("#60a5fa"))
            grad.setColorAt(1.0, QColor("#2563eb"))
            bar = QPainterPath()
            bar.addRoundedRect(rect, 4, 4)
            p.fillPath(bar, grad)

        # --- event lane
        lane = QPainterPath()
        lane.addRoundedRect(QRectF(r.left(), ev_top, r.width(), EV_H), 5, 5)
        p.fillPath(lane, QColor("#141824"))

        for ev in self._events:
            x = self._x_for(ev.time_ms / 1000.0)
            c = event_color(ev.kind)
            p.setPen(QPen(c, 2.0))
            p.drawLine(QPointF(x, ev_top + 5), QPointF(x, ev_top + EV_H - 5))
            p.setPen(Qt.NoPen)
            p.setBrush(c)
            p.drawEllipse(QPointF(x, ev_top + 5), 2.4, 2.4)
        p.setBrush(Qt.NoBrush)

        # --- ruler
        p.setPen(QPen(QColor("#64748b"), 1))
        font = QFont(self.font())
        font.setPointSizeF(max(7.0, font.pointSizeF() - 1.5))
        p.setFont(font)
        fm = QFontMetrics(font)
        if self._duration_s > 0:
            step = self._nice_step(self._duration_s)
            t = 0.0
            while t <= self._duration_s:
                x = self._x_for(t)
                label = f"{int(t) // 60}:{int(t) % 60:02d}"
                w = fm.horizontalAdvance(label)
                if x - w / 2 >= r.left() and x + w / 2 <= r.right():
                    p.setPen(QPen(QColor("#2b3446"), 1))
                    p.drawLine(QPointF(x, ruler_top), QPointF(x, ruler_top + 3))
                    p.setPen(QPen(QColor("#6b7a90"), 1))
                    p.drawText(QPointF(x - w / 2, ruler_top + RULER_H), label)
                t += step

        # --- hover guide
        if self._hover_x is not None:
            p.setPen(QPen(QColor("#3f4a5f"), 1, Qt.DashLine))
            p.drawLine(QPointF(self._hover_x, seg_top),
                       QPointF(self._hover_x, ev_top + EV_H))

        # --- playhead
        if self._duration_s > 0:
            x = self._x_for(self._playhead_s)
            p.setPen(QPen(QColor("#f8fafc"), 2))
            p.drawLine(QPointF(x, seg_top - 3), QPointF(x, ev_top + EV_H + 3))
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#f8fafc"))
            head = QPainterPath()
            head.moveTo(x - 5, seg_top - 9)
            head.lineTo(x + 5, seg_top - 9)
            head.lineTo(x, seg_top - 2)
            head.closeSubpath()
            p.fillPath(head, QColor("#f8fafc"))
        p.end()

    @staticmethod
    def _nice_step(duration_s: float) -> float:
        for step in (15, 30, 60, 120, 300, 600, 900, 1800):
            if duration_s / step <= 12:
                return float(step)
        return 3600.0

    # -- interaction --------------------------------------------------------
    def mouseMoveEvent(self, event) -> None:
        if self._duration_s <= 0:
            return
        x = event.position().x()
        self._hover_x = x
        secs = self._seconds_for(x)

        # Dragging scrubs, matching every video player people already use.
        if event.buttons() & Qt.LeftButton:
            self.seekRequested.emit(secs)

        tolerance = max(self._duration_s * 0.004, 0.5)
        near = [e for e in self._events
                if abs(e.time_ms / 1000.0 - secs) < tolerance]
        if near:
            lines = []
            for e in near[:6]:
                t = e.time_ms / 1000.0
                lines.append(f"{event_label(e.kind)}  {int(t) // 60}:{t % 60:05.2f}")
            QToolTip.showText(event.globalPosition().toPoint(), "\n".join(lines), self)
        else:
            QToolTip.hideText()
        self.update()

    def leaveEvent(self, _event) -> None:
        self._hover_x = None
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self._duration_s > 0:
            self.seekRequested.emit(self._seconds_for(event.position().x()))
