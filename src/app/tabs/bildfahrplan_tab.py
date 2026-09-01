from __future__ import annotations

import pyqtgraph as pg
from PySide6 import QtCore, QtWidgets

from bildfahrplan.profile import RouteProfile
from bildfahrplan.timeline import NOW_LINE_ANGLE, build_trace, format_axis_time
from bildfahrplan.navigation import TIME_MAX, TIME_MIN, centered_time_range, clamp_time_range, time_bounds


class TimeAxis(pg.AxisItem):
    def tickStrings(self, values, scale, spacing):  # noqa: N802 - Qt/pyqtgraph API
        return [format_axis_time(value, spacing < 60) for value in values]


class BildfahrplanTab(QtWidgets.QWidget):
    def __init__(self, adapter, profile: RouteProfile, parent=None) -> None:
        super().__init__(parent)
        self.adapter = adapter
        self.profile = profile
        self._initial_view = True
        self._last_trace_signature = None
        controls = QtWidgets.QHBoxLayout()
        controls.addWidget(QtWidgets.QLabel(f"Streckenprofil: {profile.name}"))
        controls.addStretch()
        center = QtWidgets.QPushButton("Auf aktuelle Zeit zentrieren")
        center.clicked.connect(self.center_now)
        controls.addWidget(center)
        time_axis = TimeAxis(orientation="left")
        route_axis = pg.AxisItem(orientation="top")
        self.plot = pg.PlotWidget(axisItems={"left": time_axis, "top": route_axis})
        self.plot.getPlotItem().showAxis("top")
        self.plot.getPlotItem().hideAxis("bottom")
        self.plot.setLabel("top", "Strecke (relative Position)")
        self.plot.setLabel("left", "Simulations-/Fahrplanzeit")
        self.plot.showGrid(x=True, y=True, alpha=0.25)
        route_axis.setTicks([list(profile.ticks.items())])
        # In pyqtgraph bedeutet invertY(True): groessere Fahrplanzeiten liegen unten.
        self.plot.getViewBox().invertY(True)
        self.plot.setMouseEnabled(x=False, y=True)
        self.plot.getViewBox().setLimits(yMin=TIME_MIN, yMax=TIME_MAX, minYRange=60, maxYRange=TIME_MAX - TIME_MIN)
        self.now_line = pg.InfiniteLine(angle=NOW_LINE_ANGLE, movable=False, pen=pg.mkPen("#d32f2f", width=2))
        self.plot.addItem(self.now_line)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(controls)
        layout.addWidget(self.plot)

    @QtCore.Slot(object)
    def refresh(self, snapshot) -> None:
        # Die 1-Hz-Uhr aktualisiert primaer nur Zeitlinie und Statusanzeige.
        reference = snapshot.display_simtime
        if reference is None:
            reference = snapshot.sim_day * 86400 + (snapshot.simtime or 0) / 1000
        self.now_line.setValue(reference)
        minimum, maximum = time_bounds(reference)
        self.plot.getViewBox().setLimits(
            yMin=minimum, yMax=maximum, minYRange=60, maxYRange=maximum - minimum,
        )
        trace_signature = tuple(
            (service.zid, service.current_delay,
             tuple((p.planned_name, p.planned_arrival, p.planned_departure)
                   for p in service.original_schedule))
            for service in snapshot.services
        )
        if trace_signature == self._last_trace_signature:
            self._clamp_y_range()
            return
        self._last_trace_signature = trace_signature
        self.plot.clear()
        self.plot.addItem(self.now_line)
        colors = ("#1565c0", "#2e7d32", "#6a1b9a", "#ef6c00", "#00838f")
        rendered = 0
        for service in snapshot.services:
            trace = build_trace(service, self.profile, int(reference))
            if trace is None:
                continue
            color = colors[rendered % len(colors)]
            self.plot.plot([p.position for p in trace.planned], [p.time_seconds for p in trace.planned],
                           pen=pg.mkPen(color, width=1, style=QtCore.Qt.PenStyle.DashLine))
            self.plot.plot([p.position for p in trace.projected], [p.time_seconds for p in trace.projected],
                           pen=pg.mkPen(color, width=2))
            label_point = trace.projected[len(trace.projected) // 2]
            label = pg.TextItem(trace.label, color=color, anchor=(0, 1))
            label.setPos(label_point.position, label_point.time_seconds)
            self.plot.addItem(label)
            rendered += 1
        if self._initial_view:
            self.show_route()
            self.plot.setYRange(minimum, maximum, padding=0)
            self._initial_view = False
        self._clamp_y_range()

    def center_now(self) -> None:
        value = self.now_line.value()
        current = tuple(self.plot.getViewBox().viewRange()[1])
        start, end = centered_time_range(value, current)
        self.plot.setYRange(start, end, padding=0)

    def show_route(self) -> None:
        positions = list(self.profile.ticks)
        if positions:
            self.plot.setXRange(min(positions), max(positions), padding=0.08)

    def _clamp_y_range(self) -> None:
        start, end = clamp_time_range(
            *self.plot.getViewBox().viewRange()[1], reference=self.now_line.value(),
        )
        self.plot.setYRange(start, end, padding=0)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        self.show_route()
        super().resizeEvent(event)
