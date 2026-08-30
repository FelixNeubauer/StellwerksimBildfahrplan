from __future__ import annotations

import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets

from bildfahrplan.profile import RouteProfile
from bildfahrplan.timeline import (
    BoundaryEndpoint, RouteInstanceProjection, RouteInstanceProjectionPoint,
    build_route_instance_train_segments, format_axis_time,
)
from bildfahrplan.navigation import (
    TIME_MAX, TIME_MIN, centered_time_range, clamp_time_range, live_follow_time_range, time_bounds,
)
from bildfahrplan.x_axis import (
    BildfahrplanXAxisLayout, bildfahrplan_configuration_signature, build_bildfahrplan_x_axis,
)
from bildfahrplan.decorations import (
    StationHeaderLayout, build_route_plot_segments, build_station_header_layout,
    build_time_axis_ticks, build_time_grid, choose_time_tick_interval,
)
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
        self._static_items = []
        self._grid_items = []
        self._now_items = []
        self._train_items = {}
        self._train_labels = {}
        self._service_render_signatures = {}
        self._service_had_segments = {}
        self._grid_signature = None
        self._time_tick_signature = None
        self._x_source_signature = None
        self._route_projections_cache = ()
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
        self.left_time_axis = time_axis
        self.right_time_axis = right_time_axis
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
        configuration_signature = bildfahrplan_configuration_signature(graph) if graph is not None else ()
        x_source_signature = (configuration_signature, tuple(
            (node_id, graph.nodes[node_id].display_name)
            for item in graph.bildfahrplan_routes
            for route in (graph.defined_routes.get(item.route_id),) if route is not None
            for node_id in route.ordered_node_ids
            if node_id in graph.nodes
        )) if graph is not None else ()
        if x_source_signature != self._x_source_signature:
            self._x_source_signature = x_source_signature
            self._x_layout = (build_bildfahrplan_x_axis(graph) if graph is not None
                              else BildfahrplanXAxisLayout((), ()))
            self._route_projections_cache = self._route_projections(graph, self._x_layout)
            self._rebuild_static_items()
            self._station_header_signature = None
            self._grid_signature = None
        x_layout = self._x_layout
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
            self._update_live_items()
            return
        self._last_trace_signature = trace_signature
        self._update_train_items(snapshot.services, settings, int(reference))
        if self._initial_view:
            self.show_route()
            self.plot.setYRange(minimum, maximum, padding=0)
            self._initial_view = False
        self._update_station_header()
        if self.live_follow.isChecked():
            self._apply_live_follow()
        if not self.live_follow.isChecked():
            self._clamp_y_range()
        self._update_y_dependent_decorations()
        self._update_live_items()

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
        self._update_y_dependent_decorations()

    def _rebuild_static_items(self) -> None:
        for item, _segment in self._static_items:
            self.plot.removeItem(item)
        self._static_items = []
        for item in self._now_items:
            self.plot.removeItem(item)
        self._now_items = []
        segments = build_route_plot_segments(self._x_layout, 0, 1, ())
        pens = {"station_grid": pg.mkPen("#555555", width=1),
                "frame": pg.mkPen("#A0A0A0", width=2)}
        for segment in segments:
            item = pg.PlotDataItem(pen=pens[segment.kind])
            item.setZValue(5 if segment.kind == "frame" else -10)
            self.plot.addItem(item, ignoreBounds=True)
            self._static_items.append((item, segment))
        for route in self._x_layout.routes:
            item = pg.PlotDataItem(pen=pg.mkPen("#D32F2F", width=2))
            item.setZValue(20)
            self.plot.addItem(item, ignoreBounds=True)
            self._now_items.append(item)
        if self._x_layout.routes:
            if self.empty_notice.scene() is not None:
                self.plot.removeItem(self.empty_notice)
        elif self.empty_notice.scene() is None:
            self.plot.addItem(self.empty_notice)
        self._grid_signature = None

    def _update_y_dependent_decorations(self) -> None:
        start, end = self.plot.getViewBox().viewRange()[1]
        for item, segment in self._static_items:
            if segment.kind == "station_grid":
                item.setData((segment.x1, segment.x2), (start, end))
            elif segment.y1 == segment.y2:
                y = start if segment.y1 == 0 else end
                item.setData((segment.x1, segment.x2), (y, y))
            else:
                item.setData((segment.x1, segment.x2), (start, end))
        grid = build_time_grid(start, end)
        signature = (tuple((line.time, line.kind) for line in grid),
                     tuple(route.instance_id for route in self._x_layout.routes))
        if signature != self._grid_signature:
            self._grid_signature = signature
            for item in self._grid_items:
                self.plot.removeItem(item)
            self._grid_items = []
            pens = {
                "grid_five_minute": pg.mkPen("#606060", width=0.7, style=QtCore.Qt.PenStyle.DotLine),
                "grid_quarter_hour": pg.mkPen("#707070", width=1.0),
                "grid_full_hour": pg.mkPen("#858585", width=1.6),
            }
            for segment in build_route_plot_segments(self._x_layout, start, end, grid):
                if not segment.kind.startswith("grid_"):
                    continue
                item = pg.PlotDataItem((segment.x1, segment.x2), (segment.y1, segment.y2),
                                       pen=pens[segment.kind])
                item.setZValue(-10)
                self.plot.addItem(item, ignoreBounds=True)
                self._grid_items.append(item)
        self._update_time_axis_ticks(start, end)

    def _update_time_axis_ticks(self, start: float, end: float) -> None:
        metrics = QtGui.QFontMetrics(self.font())
        interval = choose_time_tick_interval(
            end - start, self.plot.getViewBox().height(), metrics.height())
        ticks = build_time_axis_ticks(start, end, interval)
        signature = (interval, ticks)
        if signature == self._time_tick_signature:
            return
        self._time_tick_signature = signature
        values = [(value, format_axis_time(value, False)) for value in ticks]
        self.left_time_axis.setTicks([values])
        self.right_time_axis.setTicks([values])

    def _update_live_items(self) -> None:
        if not self._x_layout.routes:
            self.empty_notice.setPos(0.5, sum(self.plot.getViewBox().viewRange()[1]) / 2)
        for item, route in zip(self._now_items, self._x_layout.routes):
            item.setData((route.start_x, route.end_x), (self._now_value, self._now_value))

    def _update_train_items(self, services, settings, reference: int) -> None:
        rendered = 0
        seen_zids = set()
        for service in services:
            seen_zids.add(service.zid)
            color = settings.train_color(rendered)
            service_signature = (
                self._x_source_signature, color, service.current_delay,
                getattr(service, "origin", None), getattr(service, "destination", None),
                tuple((p.planned_name, p.planned_arrival, p.planned_departure)
                      for p in service.original_schedule),
            )
            if self._service_render_signatures.get(service.zid) == service_signature:
                rendered += int(self._service_had_segments.get(service.zid, False))
                continue
            self._service_render_signatures[service.zid] = service_signature
            segments = build_route_instance_train_segments(
                service, self._route_projections_cache, reference)
            self._service_had_segments[service.zid] = bool(segments)
            active = set()
            for segment in segments:
                for kind, points, pen in (
                    ("planned", segment.planned,
                     pg.mkPen(color, width=1, style=QtCore.Qt.PenStyle.DashLine)),
                    ("projected", segment.projected, pg.mkPen(color, width=2)),
                ):
                    key = (segment.zid, segment.instance_id, kind)
                    active.add(key)
                    item = self._train_items.get(key)
                    if item is None:
                        item = pg.PlotDataItem()
                        self.plot.addItem(item)
                        self._train_items[key] = item
                    item.setData([p.position for p in points], [p.time_seconds for p in points], pen=pen)
                label_key = (segment.zid, segment.instance_id)
                active.add((*label_key, "label"))
                label = self._train_labels.get(label_key)
                if label is None:
                    label = pg.TextItem(color=color, anchor=(0, 1))
                    self.plot.addItem(label)
                    self._train_labels[label_key] = label
                label.setText(segment.label, color=color)
                point = segment.projected[len(segment.projected) // 2]
                label.setPos(point.position, point.time_seconds)
            rendered += int(bool(segments))
            for key in tuple(self._train_items):
                if key[0] == service.zid and key not in active:
                    self.plot.removeItem(self._train_items.pop(key))
            for key in tuple(self._train_labels):
                if key[0] == service.zid and (*key, "label") not in active:
                    self.plot.removeItem(self._train_labels.pop(key))
        for zid in set(self._service_render_signatures) - seen_zids:
            self._service_render_signatures.pop(zid, None)
            self._service_had_segments.pop(zid, None)
        for key in tuple(self._train_items):
            if key[0] not in seen_zids:
                self.plot.removeItem(self._train_items.pop(key))
        for key in tuple(self._train_labels):
            if key[0] not in seen_zids:
                self.plot.removeItem(self._train_labels.pop(key))

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
    def _route_projections(graph, layout: BildfahrplanXAxisLayout) -> tuple[RouteInstanceProjection, ...]:
        if graph is None:
            return ()
        result = []
        for route_span in layout.routes:
            points = []
            endpoints = []
            for index, position in enumerate(route_span.nodes):
                node = graph.nodes.get(position.node_id)
                if node is None:
                    continue
                metadata = node.metadata
                names = tuple(dict.fromkeys((
                    *metadata.get("raw_names", ()), *metadata.get("target_raw_members", ()),
                )))
                points.append(RouteInstanceProjectionPoint(
                    route_span.instance_id, route_span.route_id, position.node_id,
                    position.x, position.label, names,
                ))
                if index in (0, len(route_span.nodes) - 1) and node.node_type == "entry":
                    endpoint_names = tuple(dict.fromkeys((*names, node.display_name)))
                    endpoints.append(BoundaryEndpoint(position.node_id, position.x, endpoint_names))
            if points:
                result.append(RouteInstanceProjection(
                    route_span.instance_id, route_span.route_id, tuple(points), tuple(endpoints)))
        return tuple(result)

    def _clamp_y_range(self) -> None:
        start, end = clamp_time_range(
            *self.plot.getViewBox().viewRange()[1], reference=self._now_value,
        )
        current = self.plot.getViewBox().viewRange()[1]
        if abs(current[0] - start) > 0.001 or abs(current[1] - end) > 0.001:
            self.plot.setYRange(start, end, padding=0)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        self.show_route()
        super().resizeEvent(event)
        self._station_header_signature = None
        self._update_station_header()
        self._update_live_items()
        self._update_y_dependent_decorations()

    def changeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().changeEvent(event)
        if event.type() == QtCore.QEvent.Type.FontChange and hasattr(self, "station_header"):
            self._station_header_signature = None
            self._update_station_header()
