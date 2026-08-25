from __future__ import annotations

import pyqtgraph as pg
from PySide6 import QtCore, QtWidgets

from bildfahrplan.profile import RouteProfile
from bildfahrplan.timeline import build_trace, format_axis_time


class TimeAxis(pg.AxisItem):
    def tickStrings(self, values, scale, spacing):  # noqa: N802 - Qt/pyqtgraph API
        return [format_axis_time(value, spacing < 60) for value in values]


class BildfahrplanTab(QtWidgets.QWidget):
    def __init__(self, adapter, profile: RouteProfile, parent=None) -> None:
        super().__init__(parent)
        self.adapter = adapter
        self.profile = profile
        self._initial_view = True
        controls = QtWidgets.QHBoxLayout()
        controls.addWidget(QtWidgets.QLabel(f"Streckenprofil: {profile.name}"))
        controls.addStretch()
        center = QtWidgets.QPushButton("Auf aktuelle Zeit zentrieren")
        center.clicked.connect(self.center_now)
        controls.addWidget(center)
        route = QtWidgets.QPushButton("Gesamte Strecke anzeigen")
        route.clicked.connect(self.show_route)
        controls.addWidget(route)

        axis = TimeAxis(orientation="bottom")
        self.plot = pg.PlotWidget(axisItems={"bottom": axis})
        self.plot.setLabel("bottom", "Simulations-/Fahrplanzeit")
        self.plot.setLabel("left", "Betriebsstelle")
        self.plot.showGrid(x=True, y=True, alpha=0.25)
        self.plot.getPlotItem().getAxis("left").setTicks([list(profile.ticks.items())])
        self.now_line = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen("#d32f2f", width=2))
        self.plot.addItem(self.now_line)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(controls)
        layout.addWidget(self.plot)

    @QtCore.Slot(object)
    def refresh(self, snapshot) -> None:
        # 1-Hz-Neuaufbau ist bewusst von der Eventfrequenz entkoppelt.
        self.plot.clear()
        self.plot.addItem(self.now_line)
        reference = snapshot.sim_day * 86400 + (snapshot.simtime or 0) / 1000
        self.now_line.setValue(reference)
        colors = ("#1565c0", "#2e7d32", "#6a1b9a", "#ef6c00", "#00838f")
        rendered = 0
        for service in snapshot.services:
            trace = build_trace(service, self.profile, int(reference))
            if trace is None:
                continue
            color = colors[rendered % len(colors)]
            self.plot.plot([p.time_seconds for p in trace.planned], [p.position for p in trace.planned],
                           pen=pg.mkPen(color, width=1, style=QtCore.Qt.PenStyle.DashLine))
            self.plot.plot([p.time_seconds for p in trace.projected], [p.position for p in trace.projected],
                           pen=pg.mkPen(color, width=2))
            label_point = trace.projected[len(trace.projected) // 2]
            label = pg.TextItem(trace.label, color=color, anchor=(0, 1))
            label.setPos(label_point.time_seconds, label_point.position)
            self.plot.addItem(label)
            rendered += 1
        if self._initial_view:
            self.center_now()
            self.show_route()
            self._initial_view = False

    def center_now(self) -> None:
        value = self.now_line.value()
        self.plot.setXRange(value - 30 * 60, value + 90 * 60, padding=0)

    def show_route(self) -> None:
        positions = list(self.profile.ticks)
        if positions:
            self.plot.setYRange(min(positions), max(positions), padding=0.08)

