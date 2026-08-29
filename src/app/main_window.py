from PySide6 import QtCore, QtWidgets

from bildfahrplan.timeline import format_axis_time
from .tabs.bildfahrplan_tab import BildfahrplanTab
from .tabs.infrastructure_tab import InfrastructureTab
from .tabs.operating_points_tab import OperatingPointsTab
from .tabs.placeholders import PlaceholderTab
from .tabs.settings_tab import SettingsTab
from .settings import ApplicationSettingsStore
from .collector_adapter import REPOSITORY_ROOT


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, adapter, profile) -> None:
        super().__init__()
        self.adapter = adapter
        self.setWindowTitle("StellwerkSim Bildfahrplan V0.4.1")
        self.resize(1200, 760)
        self.tabs = QtWidgets.QTabWidget()
        self.settings_store = ApplicationSettingsStore(REPOSITORY_ROOT / "config")
        self.settings = SettingsTab(self.settings_store.load(), self.settings_store.save)
        self.infrastructure = InfrastructureTab(REPOSITORY_ROOT / "config")
        self.diagram = BildfahrplanTab(
            adapter, profile, lambda: self.infrastructure.graph,
            lambda: self.settings.settings, self.settings.set_live_follow_position,
        )
        self.settings.settingsChanged.connect(self.diagram.settings_changed)
        self.tabs.addTab(self.diagram, "Bildfahrplan")
        self.tabs.addTab(PlaceholderTab("Gleisbelegung – folgt in einer späteren Version"), "Gleisbelegung")
        self.infrastructure_index = self.tabs.addTab(self.infrastructure, "Strecke")
        self.operating_points = OperatingPointsTab(REPOSITORY_ROOT / "config")
        self.tabs.addTab(self.operating_points, "Gleise / Ortszuordnung")
        self.tabs.addTab(self.settings, "Einstellungen")
        self._previous_tab = self.tabs.currentWidget()
        self.tabs.currentChanged.connect(self._tab_changed)
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
        self.operating_points.refresh(snapshot)
        self._update_infrastructure_gate()
        self.infrastructure.refresh(snapshot)

    def _update_infrastructure_gate(self) -> None:
        state = self.operating_points.assignment_completeness()
        self.tabs.setTabEnabled(self.infrastructure_index, state.is_complete)
        if state.is_complete:
            reason = ""
        elif not state.initialized:
            reason = "Strecke ist erst verfügbar, wenn Gleis- und Einfahrtsdaten vorliegen."
        else:
            entries = state.unassigned_entry_count + state.empty_entry_point_count
            reason = (f"Noch {state.unassigned_platform_count} Gleise und {entries} Einfahrten "
                      "nicht vollständig zugeordnet.")
        self.tabs.setTabToolTip(self.infrastructure_index, reason)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        for index in range(self.tabs.count()):
            flush = getattr(self.tabs.widget(index), "flush_pending_save", None)
            if flush:
                flush()
        self.adapter.close()
        super().closeEvent(event)

    def _tab_changed(self, index: int) -> None:
        flush = getattr(self._previous_tab, "flush_pending_save", None)
        if flush:
            flush()
        self._previous_tab = self.tabs.widget(index)
