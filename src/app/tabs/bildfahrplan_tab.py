from __future__ import annotations

import pyqtgraph as pg
from PySide6 import QtCore, QtWidgets

from bildfahrplan.profile import RouteProfile
from bildfahrplan.profile import OperatingPoint
from bildfahrplan.timeline import NOW_LINE_ANGLE, build_trace, format_axis_time
from bildfahrplan.navigation import TIME_MAX, TIME_MIN, centered_time_range, clamp_time_range, time_bounds
from bildfahrplan.x_axis import BildfahrplanXAxisLayout, build_bildfahrplan_x_axis


class TimeAxis(pg.AxisItem):
    def tickStrings(self, values, scale, spacing):  # noqa: N802 - Qt/pyqtgraph API
        return [format_axis_time(value, spacing < 60) for value in values]


class BildfahrplanTab(QtWidgets.QWidget):
    def __init__(self, adapter, profile: RouteProfile, graph_provider=lambda: None, parent=None) -> None:
        super().__init__(parent)
        self.adapter = adapter
        self.profile = profile
        self._graph_provider = graph_provider
        self._x_layout = BildfahrplanXAxisLayout((), ())
        self._initial_view = True
        self._last_trace_signature = None
        controls = QtWidgets.QHBoxLayout()
        controls.addWidget(QtWidgets.QLabel(f"Streckenprofil: {profile.name}"))
        controls.addStretch()
        center = QtWidgets.QPushButton("Auf aktuelle Zeit zentrieren")
        center.clicked.connect(self.center_now)
        controls.addWidget(center)
        time_axis = TimeAxis(orientation="left")
        right_time_axis = TimeAxis(orientation="right")
        route_axis = pg.AxisItem(orientation="top")
        self.plot = pg.PlotWidget(axisItems={
            "left": time_axis, "right": right_time_axis, "top": route_axis,
        })
        self.plot.getPlotItem().showAxis("top")
        self.plot.getPlotItem().showAxis("right")
        self.plot.getPlotItem().hideAxis("bottom")
        self.plot.setLabel("top", "Strecke (relative Position)")
        self.plot.setLabel("left", "Simulations-/Fahrplanzeit")
        self.plot.showGrid(x=True, y=True, alpha=0.25)
        self.route_axis = route_axis
        # In pyqtgraph bedeutet invertY(True): groessere Fahrplanzeiten liegen unten.
        self.plot.getViewBox().invertY(True)
        self.plot.setMouseEnabled(x=False, y=True)
        self.plot.getViewBox().setLimits(yMin=TIME_MIN, yMax=TIME_MAX, minYRange=60, maxYRange=TIME_MAX - TIME_MIN)
        self.now_line = pg.InfiniteLine(angle=NOW_LINE_ANGLE, movable=False, pen=pg.mkPen("#d32f2f", width=2))
        self.plot.addItem(self.now_line)
        self.empty_notice = pg.TextItem(
            "Noch keine Strecken für den Bildfahrplan konfiguriert.", anchor=(0.5, 0.5), color="#666666",
        )

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
        graph = self._graph_provider()
        x_layout = build_bildfahrplan_x_axis(graph) if graph is not None else BildfahrplanXAxisLayout((), ())
        layout_signature = tuple(
            (route.instance_id, route.start_x, route.end_x,
             tuple((node.node_id, node.x, node.label) for node in route.nodes))
            for route in x_layout.routes
        )
        trace_signature = (layout_signature, tuple(
            (service.zid, service.current_delay,
             tuple((p.planned_name, p.planned_arrival, p.planned_departure)
                   for p in service.original_schedule))
            for service in snapshot.services
        ))
        if trace_signature == self._last_trace_signature:
            self._clamp_y_range()
            return
        self._last_trace_signature = trace_signature
        self._x_layout = x_layout
        self.plot.clear()
        self.plot.addItem(self.now_line)
        ticks = [(node.x, node.label) for route in x_layout.routes for node in route.nodes]
        self.route_axis.setTicks([ticks])
        display_profile = self._display_profile(graph, x_layout)
        if not x_layout.routes:
            self.empty_notice.setPos(0.5, sum(self.plot.getViewBox().viewRange()[1]) / 2)
            self.plot.addItem(self.empty_notice)
        colors = ("#1565c0", "#2e7d32", "#6a1b9a", "#ef6c00", "#00838f")
        rendered = 0
        for service in snapshot.services:
            trace = build_trace(service, display_profile, int(reference))
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
        # Die normierte Geometrie füllt unabhängig von Pixelbreite und Resize
        # stets die eine gemeinsame ViewBox; horizontal bleibt Zoom deaktiviert.
        self.plot.setXRange(0.0, 1.0, padding=0)

    @staticmethod
    def _display_profile(graph, layout: BildfahrplanXAxisLayout) -> RouteProfile:
        """Provisorische Brücke für bestehende Trassen, keine neue Projektion."""
        points = []
        if graph is not None:
            for route in layout.routes:
                for position in route.nodes:
                    node = graph.nodes.get(position.node_id)
                    metadata = node.metadata if node is not None else {}
                    raw_names = tuple(dict.fromkeys((
                        *metadata.get("raw_names", ()), *metadata.get("target_raw_members", ()),
                    )))
                    points.append(OperatingPoint(position.node_id, position.label, position.x, raw_names))
        return RouteProfile("Konfigurierte Bildfahrplan-Strecken", tuple(points))

    def _clamp_y_range(self) -> None:
        start, end = clamp_time_range(
            *self.plot.getViewBox().viewRange()[1], reference=self.now_line.value(),
        )
        self.plot.setYRange(start, end, padding=0)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        self.show_route()
        super().resizeEvent(event)
