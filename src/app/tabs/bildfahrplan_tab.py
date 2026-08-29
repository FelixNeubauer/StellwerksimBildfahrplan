from __future__ import annotations

import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets

from bildfahrplan.profile import RouteProfile
from bildfahrplan.profile import OperatingPoint
from bildfahrplan.timeline import (
    BoundaryEndpoint, BoundaryRouteProjection, build_trace, extend_trace_to_boundaries,
    format_axis_time,
)
from bildfahrplan.navigation import (
    TIME_MAX, TIME_MIN, centered_time_range, clamp_time_range, live_follow_time_range, time_bounds,
)
from bildfahrplan.x_axis import (
    BildfahrplanXAxisLayout, bildfahrplan_configuration_signature, build_bildfahrplan_x_axis,
)
from bildfahrplan.decorations import StationHeaderLayout, build_route_plot_segments, build_station_header_layout
from app.settings import ApplicationSettings


class TimeAxis(pg.AxisItem):
    def tickStrings(self, values, scale, spacing):  # noqa: N802 - Qt/pyqtgraph API
        return [format_axis_time(value, spacing < 60) for value in values]


class StationHeaderWidget(QtWidgets.QWidget):
    """Echte, vom Plot getrennte und dadurch nicht geclippte Label-Fläche."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.label_layout = StationHeaderLayout((), 0)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed)
        self.setFixedHeight(0)

    def set_label_layout(self, label_layout: StationHeaderLayout) -> None:
        if label_layout == self.label_layout:
            return
        self.label_layout = label_layout
        self.setFixedHeight(label_layout.global_header_height)
        self.updateGeometry()
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().paintEvent(event)
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.TextAntialiasing)
        painter.setPen(self.palette().color(QtGui.QPalette.ColorRole.WindowText))
        metrics = QtGui.QFontMetrics(self.font())
        bottom = self.height() - 6
        for route in self.label_layout.routes:
            for label in route.labels:
                if label.rotation:
                    painter.save()
                    painter.translate(label.pixel_x, bottom)
                    painter.rotate(label.rotation)
                    painter.drawText(QtCore.QRectF(0, -metrics.height() * label.anchor_x,
                                                   metrics.horizontalAdvance(label.text), metrics.height()),
                                     QtCore.Qt.AlignmentFlag.AlignVCenter, label.text)
                    painter.restore()
                else:
                    width = metrics.horizontalAdvance(label.text)
                    painter.drawText(QtCore.QPointF(label.pixel_x - width * label.anchor_x,
                                                    bottom - metrics.descent()), label.text)
                painter.drawLine(QtCore.QPointF(label.pixel_x, self.height() - 3),
                                 QtCore.QPointF(label.pixel_x, self.height()))


class BildfahrplanTab(QtWidgets.QWidget):
    def __init__(self, adapter, profile: RouteProfile, graph_provider=lambda: None,
                 settings_provider=ApplicationSettings, save_live_position=lambda _value: None,
                 parent=None) -> None:
        super().__init__(parent)
        self.adapter = adapter
        self.profile = profile
        self._graph_provider = graph_provider
        self._settings_provider = settings_provider
        self._save_live_position = save_live_position
        self._x_layout = BildfahrplanXAxisLayout((), ())
        self._decoration_items = []
        self._station_header_signature = None
        self._now_value = 0.0
        self._initial_view = True
        self._last_trace_signature = None
        controls = QtWidgets.QHBoxLayout()
        controls.addWidget(QtWidgets.QLabel(f"Streckenprofil: {profile.name}"))
        controls.addStretch()
        center = QtWidgets.QPushButton("Auf aktuelle Zeit zentrieren")
        center.clicked.connect(self.center_now)
        controls.addWidget(center)
        self.live_follow = QtWidgets.QCheckBox("Live mitbewegen")
        self.live_follow.setChecked(False)
        controls.addWidget(self.live_follow)
        controls.addWidget(QtWidgets.QLabel("Position:"))
        self.live_position = QtWidgets.QSpinBox()
        self.live_position.setRange(0, 100)
        self.live_position.setSuffix(" %")
        self.live_position.setValue(self._settings_provider().live_follow_position_percent)
        self.live_position.valueChanged.connect(self._save_live_position)
        controls.addWidget(self.live_position)
        time_axis = TimeAxis(orientation="left")
        right_time_axis = TimeAxis(orientation="right")
        route_axis = pg.AxisItem(orientation="top")
        self.plot = pg.PlotWidget(axisItems={
            "left": time_axis, "right": right_time_axis, "top": route_axis,
        })
        self.plot.getPlotItem().hideAxis("top")
        self.plot.getPlotItem().showAxis("right")
        self.plot.getPlotItem().hideAxis("bottom")
        self.plot.setLabel("left", "Simulations-/Fahrplanzeit")
        # Das globale pyqtgraph-Grid würde durch RouteGaps laufen. Rahmen und
        # Hilfslinien werden daher pro RouteDisplaySpan gezeichnet.
        self.plot.showGrid(x=False, y=False)
        self.route_axis = route_axis
        self.route_axis.setStyle(showValues=False)
        self.route_axis.setPen(None)
        # In pyqtgraph bedeutet invertY(True): groessere Fahrplanzeiten liegen unten.
        self.plot.getViewBox().invertY(True)
        self.plot.setMouseEnabled(x=False, y=True)
        self.plot.getViewBox().setLimits(yMin=TIME_MIN, yMax=TIME_MAX, minYRange=60, maxYRange=TIME_MAX - TIME_MIN)
        self.empty_notice = pg.TextItem(
            "Noch keine Strecken für den Bildfahrplan konfiguriert.", anchor=(0.5, 0.5), color="#666666",
        )

        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(controls)
        self.station_header = StationHeaderWidget()
        layout.addWidget(self.station_header)
        layout.addWidget(self.plot)
        self.plot.getViewBox().sigYRangeChanged.connect(self._y_range_changed)

    @QtCore.Slot(object)
    def refresh(self, snapshot) -> None:
        # Die 1-Hz-Uhr aktualisiert primaer nur Zeitlinie und Statusanzeige.
        reference = snapshot.display_simtime
        if reference is None:
            reference = snapshot.sim_day * 86400 + (snapshot.simtime or 0) / 1000
        self._now_value = reference
        minimum, maximum = time_bounds(reference)
        if self.live_follow.isChecked():
            # Live-Follow darf bei 0/100 % über die normalen Tagesränder
            # hinausschieben. Andernfalls klemmt ViewBox die angeforderte Range,
            # bevor setYRange sie anwenden kann.
            self.plot.getViewBox().setLimits(
                yMin=reference - 86400, yMax=reference + 86400,
                minYRange=60, maxYRange=2 * 86400,
            )
        else:
            self.plot.getViewBox().setLimits(
                yMin=minimum, yMax=maximum, minYRange=60, maxYRange=maximum - minimum,
            )
        graph = self._graph_provider()
        x_layout = build_bildfahrplan_x_axis(graph) if graph is not None else BildfahrplanXAxisLayout((), ())
        configuration_signature = bildfahrplan_configuration_signature(graph) if graph is not None else ()
        layout_signature = tuple(
            (route.instance_id, route.start_x, route.end_x,
             tuple((node.node_id, node.x, node.label) for node in route.nodes))
            for route in x_layout.routes
        )
        settings = self._settings_provider()
        trace_signature = (configuration_signature, layout_signature,
                           settings.train_color_mode, settings.single_train_color, tuple(
            (service.zid, service.current_delay, getattr(service, "origin", None),
             getattr(service, "destination", None),
             tuple((p.planned_name, p.planned_arrival, p.planned_departure)
                   for p in service.original_schedule))
            for service in snapshot.services
        ))
        if trace_signature == self._last_trace_signature:
            if self.live_follow.isChecked():
                self._apply_live_follow()
            else:
                self._clamp_y_range()
            self._render_decorations()
            return
        self._last_trace_signature = trace_signature
        self._x_layout = x_layout
        self.plot.clear()
        self._decoration_items = []
        self.route_axis.setTicks([])
        display_profile = self._display_profile(graph, x_layout)
        boundary_routes = self._boundary_routes(graph, x_layout)
        if not x_layout.routes:
            self.empty_notice.setPos(0.5, sum(self.plot.getViewBox().viewRange()[1]) / 2)
            self.plot.addItem(self.empty_notice)
        rendered = 0
        for service in snapshot.services:
            trace = build_trace(service, display_profile, int(reference))
            if trace is None:
                continue
            trace = extend_trace_to_boundaries(service, trace, boundary_routes)
            color = settings.train_color(rendered)
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
        self._update_station_header()
        if self.live_follow.isChecked():
            self._apply_live_follow()
        if not self.live_follow.isChecked():
            self._clamp_y_range()
        self._render_decorations()

    def center_now(self) -> None:
        value = self._now_value
        current = tuple(self.plot.getViewBox().viewRange()[1])
        start, end = centered_time_range(value, current)
        self.plot.setYRange(start, end, padding=0)

    def _apply_live_follow(self) -> None:
        current = tuple(self.plot.getViewBox().viewRange()[1])
        start, end = live_follow_time_range(self._now_value, current, self.live_position.value())
        self.plot.setYRange(start, end, padding=0)

    @QtCore.Slot(object)
    def settings_changed(self, _settings) -> None:
        self._last_trace_signature = None

    def _y_range_changed(self, *_args) -> None:
        self._render_decorations()

    def _render_decorations(self) -> None:
        for item in self._decoration_items:
            try:
                self.plot.removeItem(item)
            except RuntimeError:
                pass
        self._decoration_items = []
        if not self._x_layout.routes:
            return
        start, end = self.plot.getViewBox().viewRange()[1]
        axis_height = max(1.0, self.plot.getViewBox().height())
        tick_levels = self.plot.getAxis("left").tickValues(start, end, axis_height)
        ticks = tuple(tick_levels[0][1]) if tick_levels else ()
        pens = {
            "time_grid": pg.mkPen("#707070", width=1, style=QtCore.Qt.PenStyle.DotLine),
            "station_grid": pg.mkPen("#555555", width=1),
            "frame": pg.mkPen("#A0A0A0", width=2),
        }
        for segment in build_route_plot_segments(self._x_layout, start, end, ticks):
            item = pg.PlotDataItem((segment.x1, segment.x2), (segment.y1, segment.y2),
                                   pen=pens[segment.kind])
            item.setZValue(5 if segment.kind == "frame" else -10)
            self.plot.addItem(item, ignoreBounds=True)
            self._decoration_items.append(item)
        for route in self._x_layout.routes:
            now = pg.PlotDataItem((route.start_x, route.end_x), (self._now_value, self._now_value),
                                  pen=pg.mkPen("#D32F2F", width=2))
            now.setZValue(20)
            self.plot.addItem(now, ignoreBounds=True)
            self._decoration_items.append(now)

    def _update_station_header(self) -> None:
        font = self.station_header.font()
        signature = (
            self.plot.width(), font.family(), font.pointSizeF(), font.weight(),
            tuple((route.instance_id, route.start_x, route.end_x,
                   tuple((node.node_id, node.x, node.label) for node in route.nodes))
                  for route in self._x_layout.routes),
        )
        if signature == self._station_header_signature:
            return
        self._station_header_signature = signature
        metrics = QtGui.QFontMetrics(font)
        label_layout = build_station_header_layout(
            self._x_layout,
            lambda x: self.plot.getViewBox().mapViewToScene(QtCore.QPointF(x, 0)).x(),
            lambda text: (metrics.horizontalAdvance(text), metrics.height()),
        )
        self.station_header.set_label_layout(label_layout)

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

    @staticmethod
    def _boundary_routes(graph, layout: BildfahrplanXAxisLayout) -> tuple[BoundaryRouteProjection, ...]:
        if graph is None:
            return ()
        result = []
        for route_span in layout.routes:
            endpoint_positions = (route_span.nodes[0], route_span.nodes[-1])
            endpoints = []
            for position in endpoint_positions:
                node = graph.nodes.get(position.node_id)
                if node is None or node.node_type != "entry":
                    continue
                metadata = node.metadata
                names = tuple(dict.fromkeys((
                    *metadata.get("raw_names", ()), *metadata.get("target_raw_members", ()),
                    node.display_name,
                )))
                endpoints.append(BoundaryEndpoint(position.node_id, position.x, names))
            if endpoints:
                result.append(BoundaryRouteProjection(
                    route_span.instance_id, tuple(item.x for item in route_span.nodes),
                    tuple(endpoints),
                ))
        return tuple(result)

    def _clamp_y_range(self) -> None:
        start, end = clamp_time_range(
            *self.plot.getViewBox().viewRange()[1], reference=self._now_value,
        )
        self.plot.setYRange(start, end, padding=0)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        self.show_route()
        super().resizeEvent(event)
        self._station_header_signature = None
        self._update_station_header()
        self._render_decorations()

    def changeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().changeEvent(event)
        if event.type() == QtCore.QEvent.Type.FontChange and hasattr(self, "station_header"):
            self._station_header_signature = None
            self._update_station_header()
