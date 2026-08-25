"""Diagnoseansicht fuer den konservativ abgeleiteten Streckengraphen."""

from __future__ import annotations

from PySide6 import QtWidgets

from infrastructure import InfrastructureGraphBuilder, parse_bahnsteigliste, parse_wege, save_generated_graph


class InfrastructureTab(QtWidgets.QWidget):
    def __init__(self, generated_directory, parent=None) -> None:
        super().__init__(parent)
        self.generated_directory = generated_directory
        self._last_signature = None
        self.values: dict[str, QtWidgets.QLabel] = {}
        layout = QtWidgets.QFormLayout(self)
        rows = (
            ("raw_nodes", "Raw-Nodes"), ("raw_edges", "Raw-Edges"),
            ("anchors", "erkannte Anchors"), ("unresolved", "unresolved Anchors"),
            ("ambiguous", "ambiguous Anchors"),
            ("operational_nodes", "OperationalRouteNodes"),
            ("operational_edges", "OperationalRouteEdges"),
        )
        for key, title in rows:
            label = self.values[key] = QtWidgets.QLabel("0")
            layout.addRow(title, label)
        self.status = QtWidgets.QLabel("Noch keine <wege>-Antwort empfangen.")
        self.status.setWordWrap(True)
        layout.addRow("Status", self.status)

    def refresh(self, snapshot) -> None:
        signature = (snapshot.aid, snapshot.infrastructure_documents,
                     tuple((service.zid, len(service.original_schedule)) for service in snapshot.services))
        if signature == self._last_signature:
            return
        self._last_signature = signature
        wege = next((raw for raw in reversed(snapshot.infrastructure_documents)
                     if raw.lstrip().startswith("<wege")), None)
        if not wege:
            return
        try:
            raw_graph = parse_wege(wege)
            builder = InfrastructureGraphBuilder(raw_graph)
            schedules = [
                [point.planned_name or point.raw_name for point in service.original_schedule]
                for service in snapshot.services if service.original_schedule
            ]
            builder.resolve_names(name for schedule in schedules for name in schedule)
            for schedule in schedules:
                builder.observe_schedule(schedule)
            operational = builder.build_operational_graph()
            platforms = ()
            platform_xml = next((raw for raw in reversed(snapshot.infrastructure_documents)
                                 if raw.lstrip().startswith("<bahnsteigliste")), None)
            if platform_xml:
                platforms = parse_bahnsteigliste(platform_xml)
            counts = {
                "raw_nodes": len(raw_graph.nodes), "raw_edges": len(raw_graph.edges),
                "anchors": sum(a.resolution != "unresolved" for a in builder.anchors.values()),
                "unresolved": sum(a.resolution == "unresolved" for a in builder.anchors.values()),
                "ambiguous": sum(a.resolution == "ambiguous" for a in builder.anchors.values()),
                "operational_nodes": len(operational.nodes), "operational_edges": len(operational.edges),
            }
            for key, count in counts.items():
                self.values[key].setText(str(count))
            self.status.setText("Graph aus expliziten <wege>-Daten und Fahrplan-Evidenz aufgebaut.")
            if snapshot.aid is not None:
                save_generated_graph(
                    self.generated_directory, snapshot.aid, raw_graph, builder.anchors, operational, platforms,
                    name=snapshot.facility_name,
                )
        except (ValueError, StopIteration) as exc:
            self.status.setText(f"Graphdaten konnten nicht ausgewertet werden: {exc}")
