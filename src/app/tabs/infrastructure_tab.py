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
            ("station_key_clusters", "Station-Key-Cluster"),
            ("platform_relation_clusters", "PlatformRelation-Cluster"),
            ("internal_points_merged", "zusammengeführte interne Punkte"),
            ("sandwich_merges", "Sandwich-Merges"),
            ("closed_excursion_merges", "Closed-Excursion-Merges"),
            ("unprefixed_platform_clusters", "unpräfixierte Platform-Cluster"),
            ("route_axis_nodes", "RouteAxisNodes"),
            ("axis_branches", "Verzweigungen nach Axis-Kollaps"),
            ("conflicting_candidates", "konfliktbehaftete Kandidaten"),
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
        self.clusters = QtWidgets.QPlainTextEdit()
        self.clusters.setReadOnly(True)
        layout.addRow("OperatingPoint-Cluster", self.clusters)

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
            operating = OperatingPointResolver(platforms, manual, snapshot.aid).resolve(schedule)
            axis = operating.to_route_axis_graph()
            operational = operating.to_operational_graph()
            counts = {
                "schedule_nodes": len(schedule.nodes), "schedule_edges": len(schedule.edges),
                "operating_points": len(operating.nodes),
                "automatic": sum(
                    any(key in p.evidence for key in ("platform_relation", "same_station_key",
                                                       "schedule_sandwich", "closed_excursion"))
                    for p in operating.nodes.values()
                ),
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
                "route_axis_nodes": len(axis.nodes), "axis_branches": len(axis.branch_nodes),
                **operating.diagnostics,
            }
            for key, count in counts.items():
                self.values[key].setText(str(count))
            self.status.setText("Betrieblicher Graph aus original_schedule; <wege> dient nur als Raw-Evidenz.")
            self.clusters.setPlainText("\n\n".join(
                f"OperatingPoint {point.display_name}\n    " + "\n    ".join(point.raw_names)
                for point in sorted(operating.nodes.values(), key=lambda item: item.display_name)
            ))
            if snapshot.aid is not None:
                save_generated_graph(
                    self.generated_directory, snapshot.aid, raw_graph, builder.anchors, operational, platforms,
                    schedule=schedule, operating=operating, name=snapshot.facility_name,
                )
        except (ValueError, StopIteration) as exc:
            self.status.setText(f"Graphdaten konnten nicht ausgewertet werden: {exc}")
