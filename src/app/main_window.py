from PySide6 import QtCore, QtWidgets

from bildfahrplan.timeline import format_axis_time
from .tabs.bildfahrplan_tab import BildfahrplanTab
from .tabs.infrastructure_tab import InfrastructureTab
from .tabs.operating_points_tab import OperatingPointsTab
from .tabs.placeholders import PlaceholderTab
from .collector_adapter import REPOSITORY_ROOT


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, adapter, profile) -> None:
        super().__init__()
        self.adapter = adapter
        self.setWindowTitle("StellwerkSim Bildfahrplan V0.3.5")
        self.resize(1200, 760)
        self.tabs = QtWidgets.QTabWidget()
        self.diagram = BildfahrplanTab(adapter, profile)
        self.tabs.addTab(self.diagram, "Bildfahrplan")
        self.tabs.addTab(PlaceholderTab("Gleisbelegung – folgt in einer späteren Version"), "Gleisbelegung")
        self.infrastructure = InfrastructureTab(REPOSITORY_ROOT / "config")
        self.tabs.addTab(self.infrastructure, "Strecke")
        self.operating_points = OperatingPointsTab(REPOSITORY_ROOT / "config")
        self.tabs.addTab(self.operating_points, "Gleise / Ortszuordnung")
        self.tabs.addTab(PlaceholderTab("Allgemeine Einstellungen – folgen in einer späteren Version"), "Einstellungen")
        self.setCentralWidget(self.tabs)
        self.connection = QtWidgets.QLabel()
        self.facility = QtWidgets.QLabel()
        self.clock = QtWidgets.QLabel()
        for widget in (self.connection, self.facility, self.clock):
            self.statusBar().addPermanentWidget(widget)
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(1000)
        self.refresh()

    def refresh(self) -> None:
        snapshot = self.adapter.snapshot()
        self.connection.setText(f"Verbindung: {'verbunden' if snapshot.connected else 'offline'} · {snapshot.status}")
        self.facility.setText(f"Stellwerk: {snapshot.facility_name or 'unbekannt'} · AID: {snapshot.aid or '–'}")
        suffix = "" if snapshot.display_simtime_running else " (synchronisiert)"
        self.clock.setText(
            f"Simzeit: {format_axis_time(snapshot.display_simtime, True)}{suffix}"
            if snapshot.display_simtime is not None else "Simzeit: –"
        )
        self.diagram.refresh(snapshot)
        self.infrastructure.refresh(snapshot)
        self.operating_points.refresh(snapshot)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        self.adapter.close()
        super().closeEvent(event)
