"""Diagnoseansicht fuer den konservativ abgeleiteten Streckengraphen."""

from __future__ import annotations

import json
from PySide6 import QtWidgets

from infrastructure import (
    InfrastructureGraphBuilder, OperatingPointResolver, RawInfrastructureGraph,
    SchedulePointGraph, parse_bahnsteigliste, parse_wege, save_generated_graph,
)


class InfrastructureTab(QtWidgets.QWidget):
    def __init__(self, generated_directory, parent=None) -> None:
        super().__init__(parent)
        self.generated_directory = generated_directory
        self._last_signature = None
        self.values: dict[str, QtWidgets.QLabel] = {}
        layout = QtWidgets.QFormLayout(self)
        rows = (
            ("schedule_nodes", "SchedulePointNodes"), ("schedule_edges", "ScheduleEdges"),
            ("operating_points", "erkannte OperatingPoints"),
            ("automatic", "automatisch gruppierte OperatingPoints"),
            ("manual", "manuell bestätigte OperatingPoints"),
            ("virtual", "virtuelle Fahrplanpunkte"),
            ("ungrouped", "nicht gruppierte SchedulePoints"),
            ("operational_nodes", "OperationalRouteNodes"),
            ("operational_edges", "OperationalRouteEdges"),
            ("branches", "Verzweigungsknoten"),
            ("raw_nodes", "Raw-Infrastruktur: Nodes"), ("raw_edges", "Raw-Infrastruktur: Edges"),
            ("raw_types", "Raw-Infrastruktur: Elementtypen"),
            ("anchors", "Raw-Infrastruktur: Anchor-Zuordnungen"),
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
        wege_xml = next((raw for raw in reversed(snapshot.infrastructure_documents)
                     if raw.lstrip().startswith("<wege")), None)
        try:
            raw_graph = parse_wege(wege_xml) if wege_xml else RawInfrastructureGraph()
            builder = InfrastructureGraphBuilder(raw_graph)
            schedule = SchedulePointGraph.from_services(snapshot.services)
            builder.resolve_names(schedule.nodes)
            platforms = ()
            platform_xml = next((raw for raw in reversed(snapshot.infrastructure_documents)
                                 if raw.lstrip().startswith("<bahnsteigliste")), None)
            if platform_xml:
                platforms = parse_bahnsteigliste(platform_xml)
            manual = {}
            if snapshot.aid is not None:
                manual_path = self.generated_directory / "operating_points" / f"{snapshot.aid}.json"
                if manual_path.exists():
                    manual = json.loads(manual_path.read_text(encoding="utf-8"))
            operating = OperatingPointResolver(platforms, manual).resolve(schedule)
            operational = operating.to_operational_graph()
            counts = {
                "schedule_nodes": len(schedule.nodes), "schedule_edges": len(schedule.edges),
                "operating_points": len(operating.nodes),
                "automatic": sum("bahnsteigliste" in p.evidence for p in operating.nodes.values()),
                "manual": sum(p.manual_confirmation for p in operating.nodes.values()),
                "virtual": sum(p.point_type in {"virtual_schedule_point", "entry_exit"}
                               for p in operating.nodes.values()),
                "ungrouped": sum(len(p.raw_names) == 1 and not p.manual_confirmation
                                 for p in operating.nodes.values()),
                "raw_nodes": len(raw_graph.nodes), "raw_edges": len(raw_graph.edges),
                "raw_types": len({p.element_type for p in raw_graph.nodes.values()}),
                "anchors": sum(a.resolution != "unresolved" for a in builder.anchors.values()),
                "operational_nodes": len(operational.nodes), "operational_edges": len(operational.edges),
                "branches": len(operating.branch_nodes),
            }
            for key, count in counts.items():
                self.values[key].setText(str(count))
            self.status.setText("Betrieblicher Graph aus original_schedule; <wege> dient nur als Raw-Evidenz.")
            if snapshot.aid is not None:
                save_generated_graph(
                    self.generated_directory, snapshot.aid, raw_graph, builder.anchors, operational, platforms,
                    schedule=schedule, operating=operating, name=snapshot.facility_name,
                )
        except (ValueError, StopIteration) as exc:
            self.status.setText(f"Graphdaten konnten nicht ausgewertet werden: {exc}")
