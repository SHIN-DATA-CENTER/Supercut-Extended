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

from dataclasses import dataclass, field

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor, QFont, QFontMetrics, QLinearGradient, QPainter, QPainterPath, QPen,
)
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QScrollBar,
                               QSizePolicy, QToolTip, QWidget)

from ..model import GameEvent, Segment
from . import icons
from .i18n import event_label, tr
from .style import TEXT_DIM

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


@dataclass
class Recording:
    """One source file on the shared axis.

    A match is not always one file -- Highlight capture writes one per highlight --
    so the strip lays them end to end and the axis runs across the whole set. Times
    inside `events` and `segments` stay relative to their own file; `offset_s` is
    where that file starts on the shared axis.
    """

    label: str
    duration_s: float
    events: list[GameEvent] = field(default_factory=list)
    segments: list[Segment] = field(default_factory=list)
    offset_s: float = 0.0

    @property
    def end_s(self) -> float:
        return self.offset_s + self.duration_s


BLOCK_GAP_PX = 6         # visual break between recordings, never part of the axis


class TimelineWidget(QWidget):
    seekRequested = Signal(float)     # seconds
    viewChanged = Signal()            # zoom or scroll position moved

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._duration_s = 0.0
        self._events: list[GameEvent] = []
        self._segments: list[Segment] = []
        self._playhead_s = 0.0
        self._hover_x: float | None = None
        # Zoom shows a window of the recording instead of all of it. A 30 minute
        # capture squeezes an hour of kills into a few hundred pixels, so at 1x the
        # markers overlap into a solid bar and there is no way to see a single event.
        self._zoom = 1.0
        self._view_start_s = 0.0
        self._pan_from: tuple[float, float] | None = None
        self._recordings: list[Recording] = []
        self.setMinimumHeight(int(SEG_H + EV_H + RULER_H + PAD * 2 + 6))
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    # -- data ---------------------------------------------------------------
    def set_data(self, duration_s: float, events: list[GameEvent],
                 segments: list[Segment]) -> None:
        """Single-recording form, kept for callers that only ever have one file."""
        self.set_recordings([Recording("", max(0.0, duration_s), list(events),
                                       list(segments))])

    def set_recordings(self, recordings: list[Recording]) -> None:
        """Lay several source files end to end on one axis."""
        offset = 0.0
        laid: list[Recording] = []
        for rec in recordings:
            laid.append(Recording(rec.label, max(0.0, rec.duration_s), rec.events,
                                  rec.segments, offset))
            offset += max(0.0, rec.duration_s)

        first = abs(self._duration_s - offset) > 0.01
        self._recordings = laid
        self._duration_s = offset
        self._events = [e for r in laid for e in r.events]
        self._segments = [s for r in laid for s in r.segments]
        if first:
            # A different recording: start from the whole thing rather than keeping a
            # window that pointed into the previous one.
            self._zoom = 1.0
            self._view_start_s = 0.0
        self._clamp_view()
        self.update()

    def set_playhead(self, seconds: float) -> None:
        if abs(seconds - self._playhead_s) < 0.02:
            return
        self._playhead_s = seconds
        # While zoomed, playback would otherwise run straight out of the window and
        # leave you watching an empty strip.
        if self._zoom > 1.0:
            span = self.view_span_s()
            if not (self._view_start_s <= seconds <= self._view_start_s + span):
                self._view_start_s = seconds - span / 2.0
                self._clamp_view()
        self.update()

    def event_times(self) -> list[float]:
        """Every event on the shared axis, so jumping works across recordings."""
        return sorted(t for t, _e in self.global_events())

    def global_events(self) -> list[tuple[float, GameEvent]]:
        """(axis seconds, event). Event times are stored relative to their own file."""
        return [(rec.offset_s + e.time_ms / 1000.0, e)
                for rec in self._recordings for e in rec.events]

    def global_segments(self) -> list[tuple[float, float]]:
        return [(rec.offset_s + seg.start_s, rec.offset_s + seg.end_ms / 1000.0)
                for rec in self._recordings for seg in rec.segments]

    def locate(self, axis_s: float) -> tuple[int, float]:
        """Axis seconds -> (recording index, seconds into that recording)."""
        for i, rec in enumerate(self._recordings):
            if axis_s < rec.end_s or i == len(self._recordings) - 1:
                return i, max(0.0, min(axis_s - rec.offset_s, rec.duration_s))
        return -1, 0.0

    def recordings(self) -> list[Recording]:
        return list(self._recordings)

    def _blocks_in_view(self) -> list[Recording]:
        """Only the recordings the current window actually touches.

        At high zoom a match of ten files is mostly off screen, and drawing all of
        them is wasted work on every repaint -- and every mouse move repaints.
        """
        start = self._view_start_s
        end = start + self.view_span_s()
        return [rec for rec in self._recordings
                if rec.end_s >= start and rec.offset_s <= end]

    # -- zoom ---------------------------------------------------------------
    def zoom(self) -> float:
        return self._zoom

    def view_start_s(self) -> float:
        return self._view_start_s

    def view_span_s(self) -> float:
        """How many seconds are on screen."""
        if self._duration_s <= 0:
            return 1.0
        return self._duration_s / self._zoom

    def _clamp_view(self) -> None:
        span = self.view_span_s()
        self._view_start_s = max(0.0, min(self._view_start_s,
                                          max(0.0, self._duration_s - span)))
        self.viewChanged.emit()

    def set_zoom(self, zoom: float, anchor_s: float | None = None) -> None:
        """Zoom, putting `anchor_s` -- the playhead unless told otherwise -- in the
        middle of the window.

        Centred rather than "keep it wherever it happened to be": the playhead is the
        thing being looked at, and preserving its old fraction of the window leaves it
        drifting toward an edge with every step until it falls off.
        """
        self._zoom = max(1.0, min(zoom, 400.0))
        if anchor_s is None:
            anchor_s = self._playhead_s
        anchor_s = max(0.0, min(anchor_s, self._duration_s))
        self._view_start_s = anchor_s - self.view_span_s() / 2.0
        self._clamp_view()
        self.update()

    def set_view_start(self, seconds: float) -> None:
        self._view_start_s = seconds
        self._clamp_view()
        self.update()

    def pan_seconds(self, delta_s: float) -> None:
        self._view_start_s += delta_s
        self._clamp_view()
        self.update()

    def reset_zoom(self) -> None:
        self._zoom = 1.0
        self._view_start_s = 0.0
        self.viewChanged.emit()
        self.update()

    # -- geometry -----------------------------------------------------------
    def _track(self) -> QRectF:
        return QRectF(PAD, PAD, max(1.0, self.width() - PAD * 2),
                      self.height() - PAD * 2)

    def _x_for(self, seconds: float) -> float:
        r = self._track()
        if self._duration_s <= 0:
            return r.left()
        span = self.view_span_s()
        return r.left() + ((seconds - self._view_start_s) / span) * r.width()

    def _seconds_for(self, x: float) -> float:
        r = self._track()
        if r.width() <= 0 or self._duration_s <= 0:
            return 0.0
        frac = (x - r.left()) / r.width()
        secs = self._view_start_s + frac * self.view_span_s()
        return max(0.0, min(self._duration_s, secs))

    # -- painting -----------------------------------------------------------
    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self._track()
        seg_top = r.top()
        ev_top = seg_top + SEG_H + 6
        ruler_top = ev_top + EV_H + 2

        p.fillRect(self.rect(), QColor("#0d1017"))

        # --- lanes, one block per recording so the file boundaries are visible
        for rec in self._blocks_in_view():
            x0 = self._x_for(rec.offset_s)
            x1 = max(x0 + 2.0, self._x_for(rec.end_s) - BLOCK_GAP_PX)
            block = QPainterPath()
            block.addRoundedRect(QRectF(x0, seg_top, x1 - x0, SEG_H), 5, 5)
            p.fillPath(block, QColor("#191e2b"))

        for start_s, end_s in self.global_segments():
            x0 = self._x_for(start_s)
            x1 = self._x_for(end_s)
            rect = QRectF(x0, seg_top, max(2.0, x1 - x0), SEG_H)
            grad = QLinearGradient(rect.topLeft(), rect.bottomLeft())
            grad.setColorAt(0.0, QColor("#60a5fa"))
            grad.setColorAt(1.0, QColor("#2563eb"))
            bar = QPainterPath()
            bar.addRoundedRect(rect, 4, 4)
            p.fillPath(bar, grad)

        for rec in self._blocks_in_view():
            x0 = self._x_for(rec.offset_s)
            x1 = max(x0 + 2.0, self._x_for(rec.end_s) - BLOCK_GAP_PX)
            lane = QPainterPath()
            lane.addRoundedRect(QRectF(x0, ev_top, x1 - x0, EV_H), 5, 5)
            p.fillPath(lane, QColor("#141824"))
            if len(self._recordings) > 1 and x1 - x0 > 46:
                p.setPen(QColor("#55617a"))
                p.drawText(QRectF(x0 + 5, seg_top - 1, x1 - x0 - 8, SEG_H),
                           Qt.AlignVCenter | Qt.AlignLeft, rec.label)
                p.setPen(Qt.NoPen)

        for axis_s, ev in self.global_events():
            x = self._x_for(axis_s)
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
            # Step from the visible span, not the whole recording: at 20x a 30 minute
            # capture would otherwise still be labelled every 5 minutes, i.e. never.
            span = self.view_span_s()
            step = self._nice_step(span)
            t = max(0.0, (self._view_start_s // step) * step)
            end = min(self._duration_s, self._view_start_s + span)
            while t <= end:
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
        for step in (1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800):
            if duration_s / step <= 12:
                return float(step)
        return 3600.0

    # -- interaction --------------------------------------------------------
    def mouseMoveEvent(self, event) -> None:
        if self._duration_s <= 0:
            return
        x = event.position().x()
        if self._pan_from is not None:
            from_x, from_start = self._pan_from
            r = self._track()
            per_px = self.view_span_s() / max(1.0, r.width())
            self._view_start_s = from_start - (x - from_x) * per_px
            self._clamp_view()
            self.update()
            return
        self._hover_x = x
        secs = self._seconds_for(x)

        # Dragging scrubs, matching every video player people already use.
        if event.buttons() & Qt.LeftButton:
            self.seekRequested.emit(secs)

        # Scale the grab radius to what is on screen, not to the whole recording:
        # zoomed in, a fixed fraction of the total would cover the entire window.
        tolerance = max(self.view_span_s() * 0.006, 0.15)
        near = [(t, e) for t, e in self.global_events() if abs(t - secs) < tolerance]
        if near:
            lines = []
            for t, e in near[:6]:
                lines.append(f"{event_label(e.kind)}  {int(t) // 60}:{t % 60:05.2f}")
            QToolTip.showText(event.globalPosition().toPoint(), "\n".join(lines), self)
        else:
            QToolTip.hideText()
        self.update()

    def leaveEvent(self, _event) -> None:
        self._hover_x = None
        self.update()

    def wheelEvent(self, event) -> None:
        """Ctrl = zoom at the pointer, Alt = pan. Same keys as the clip editor."""
        if self._duration_s <= 0:
            event.ignore()
            return
        mods = event.modifiers()
        delta = event.angleDelta().y() or event.angleDelta().x()
        if mods & Qt.ControlModifier:
            # Deliberately NOT the pointer: zoom is anchored on the playhead
            # everywhere, so the wheel and the buttons agree about what stays put.
            self.set_zoom(self._zoom * (1.25 if delta > 0 else 0.8))
            event.accept()
        elif mods & Qt.AltModifier:
            self.pan_seconds(-delta / 120.0 * self.view_span_s() * 0.15)
            event.accept()
        else:
            event.ignore()

    def mouseDoubleClickEvent(self, event) -> None:
        """Back to the whole recording -- the way out of being lost while zoomed."""
        if event.button() == Qt.LeftButton:
            self.reset_zoom()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MiddleButton and self._duration_s > 0:
            self._pan_from = (event.position().x(), self._view_start_s)
            self.setCursor(Qt.ClosedHandCursor)
            return
        if event.button() == Qt.LeftButton and self._duration_s > 0:
            self.seekRequested.emit(self._seconds_for(event.position().x()))

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MiddleButton and self._pan_from is not None:
            self._pan_from = None
            self.setCursor(Qt.PointingHandCursor)


class TimelineControls(QWidget):
    """Scrollbar and zoom buttons for a TimelineWidget.

    The wheel modifiers alone are not discoverable: someone who never reads the hint
    line has no way to know the strip zooms at all. These give the same three actions
    a visible home, and the scrollbar doubles as a map of where in the recording the
    visible window sits.
    """

    STEPS = 100.0        # scrollbars are integral; work in centiseconds

    def __init__(self, view: TimelineWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._view = view
        self._syncing = False

        self.bar = QScrollBar(Qt.Horizontal)
        self.bar.setFixedHeight(11)
        self.bar.setSingleStep(int(self.STEPS))
        self.bar.valueChanged.connect(self._on_scroll)

        self.factor = QLabel("1.0x")
        self.factor.setObjectName("captionLabel")
        self.factor.setMinimumWidth(38)
        self.factor.setAlignment(Qt.AlignCenter)

        self.out_btn = self._button("Edit/Remove_Minus", tr("editor.zoom_out"))
        self.out_btn.clicked.connect(lambda: self._zoom_by(0.8))
        self.in_btn = self._button("Edit/Add_Plus", tr("editor.zoom_in"))
        self.in_btn.clicked.connect(lambda: self._zoom_by(1.25))
        self.fit_btn = QPushButton(tr("editor.fit"))
        self.fit_btn.setFixedHeight(22)
        # Enough room for the label at either language; without a floor the row
        # squeezes it until the text is clipped.
        self.fit_btn.setMinimumWidth(78)
        self.fit_btn.setStyleSheet("padding: 0 8px;")
        self.fit_btn.clicked.connect(view.reset_zoom)

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.addWidget(self.bar, 1)
        row.addWidget(self.out_btn)
        row.addWidget(self.factor)
        row.addWidget(self.in_btn)
        row.addWidget(self.fit_btn)

        view.viewChanged.connect(self.sync)
        self.sync()

    @staticmethod
    def _button(icon_name: str, tip: str) -> QPushButton:
        btn = QPushButton()
        btn.setObjectName("transport")
        btn.setIcon(icons.icon(icon_name, TEXT_DIM, 14))
        btn.setIconSize(QSize(14, 14))
        btn.setFixedSize(26, 22)
        btn.setToolTip(tip)
        return btn

    def _zoom_by(self, factor: float) -> None:
        self._view.set_zoom(self._view.zoom() * factor)

    def _on_scroll(self, value: int) -> None:
        if self._syncing:
            return
        self._view.set_view_start(value / self.STEPS)

    def sync(self) -> None:
        """Mirror the widget's window onto the scrollbar, without echoing back."""
        view = self._view
        duration = view._duration_s
        span = view.view_span_s()
        hidden = max(0.0, duration - span)
        self._syncing = True
        try:
            self.bar.setRange(0, int(hidden * self.STEPS))
            self.bar.setPageStep(max(1, int(span * self.STEPS)))
            self.bar.setValue(int(view.view_start_s() * self.STEPS))
        finally:
            self._syncing = False
        # Nothing to scroll at 1x; leave the bar in place but inert so the row does
        # not change height as the zoom changes.
        self.bar.setEnabled(hidden > 0.001)
        self.factor.setText(f"{view.zoom():.1f}x")
        self.out_btn.setEnabled(view.zoom() > 1.0)
        self.fit_btn.setEnabled(view.zoom() > 1.0)
