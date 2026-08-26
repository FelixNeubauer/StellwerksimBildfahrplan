"""Diagnoseansicht fuer den konservativ abgeleiteten Streckengraphen."""

from __future__ import annotations

import json
from PySide6 import QtWidgets

from infrastructure import (
    CorridorGraphBuilder, InfrastructureGraphBuilder, OperatingPointResolver, RawInfrastructureGraph,
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
            ("neighbour_edges", "Neighbour-Edges"), ("skip_edges", "Skip-Edges"),
            ("branch_edges", "Branch-Edges"), ("alternative_route_edges", "Alternative Routes"),
            ("local_internal_edges", "Local-Internal-Edges"), ("unresolved_edges", "Unresolved-Edges"),
            ("branch_junctions", "Final Branch Junctions"),
            ("branch_terminals", "Final Branch Terminals"),
            ("direction_changes", "Direction Changes"), ("secondary_components", "Secondary Components"),
            ("backbone_edges", "BackboneEdges"), ("backbone_candidates", "Backbone Candidates"),
            ("travel_time_comparisons", "TravelTime Comparisons"),
            ("between_evidence", "Between-Evidence"), ("terminal_candidates", "Terminal Candidates"),
            ("triangle_resolutions", "Triangle Resolutions"),
            ("terminal_contradictions", "Terminal Contradictions"),
            ("synthetic_junctions", "Synthetic Junctions"),
            ("edge_attachments", "Edge Attachments"),
            ("node_attachments", "Node Attachments"),
            ("unresolved_attachments", "Unresolved Branch Attachments"),
            ("travel_time_junctions", "TravelTime Junction Estimates"),
            ("raw_junctions", "Raw Junction Estimates"),
            ("role_changes", "Role Changes After Finalization"),
            ("applied_between", "Applied High-Confidence Between"),
            ("between_constraints", "Between Constraints Detected"),
            ("between_conflicts", "Between Constraints Conflicting"),
            ("hidden_boundaries", "Hidden External Boundaries"),
            ("external_targets", "External Target Resolutions"),
            ("topology_questions", "Topology Questions Pending"),
            ("external_boundaries", "External Boundaries"),
            ("schedule_start_count", "Schedule Starts"), ("schedule_end_count", "Schedule Ends"),
            ("through_count", "Through Count"), ("reversal_count", "Reversal Count"),
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
                     tuple((service.zid, len(service.original_schedule),
                            getattr(service, "origin", None), getattr(service, "destination", None))
                           for service in snapshot.services))
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
            corridor = CorridorGraphBuilder(schedule, operating, raw_graph).build()
            operational = corridor.to_operational_graph()
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
                **{f"{kind}_edges": sum(edge.classification == kind for edge in corridor.edges.values())
                   for kind in ("neighbour", "skip", "branch", "alternative_route", "local_internal", "unresolved")},
                "branch_junctions": sum(role == "branch_junction" for role in corridor.node_roles.values()),
                "branch_terminals": sum(role == "branch_terminal" for role in corridor.node_roles.values()),
                "direction_changes": len(corridor.direction_changes),
                "secondary_components": sum(role == "secondary_component" for role in corridor.component_roles.values()),
                "backbone_edges": len(corridor.backbone_edges),
                "backbone_candidates": len(corridor.backbone_candidates),
                "travel_time_comparisons": len(corridor.travel_time_stats),
                "between_evidence": len(corridor.between_evidence),
                "triangle_resolutions": len(corridor.triangle_resolutions),
                "terminal_contradictions": sum(bool(item.contradicting_terminal_evidence)
                                                for item in corridor.terminal_evidence.values()),
                "synthetic_junctions": len(corridor.synthetic_junctions),
                "edge_attachments": sum(item.attachment_type == "edge"
                                        for item in corridor.branch_attachments.values()),
                "node_attachments": sum(item.attachment_type == "node"
                                        for item in corridor.branch_attachments.values()),
                "unresolved_attachments": sum(item.attachment_type == "unresolved"
                                              for item in corridor.branch_attachments.values()),
                "travel_time_junctions": sum(item.edge_fraction is not None
                                             for item in corridor.junction_position_estimates.values()),
                "raw_junctions": sum(item.raw_junction_node is not None
                                     for item in corridor.synthetic_junctions.values()),
                "role_changes": len(corridor.role_changes),
                "applied_between": len(corridor.applied_between_resolutions),
                "between_constraints": len(corridor.between_constraints),
                "between_conflicts": sum(item.status == "conflicting"
                                           for item in corridor.between_constraints.values()),
                "hidden_boundaries": len(corridor.synthetic_external_boundaries),
                "external_targets": len(corridor.external_target_resolutions),
                "topology_questions": sum(item.status == "needs_user_confirmation"
                                          for item in corridor.topology_questions.values()),
                "terminal_candidates": sum(item.classification == "terminal"
                                           for item in corridor.terminal_evidence.values()),
                "external_boundaries": sum(item.classification == "external_boundary"
                                           for item in corridor.terminal_evidence.values()),
                "schedule_start_count": sum(item.schedule_start_count for item in corridor.terminal_evidence.values()),
                "schedule_end_count": sum(item.schedule_end_count for item in corridor.terminal_evidence.values()),
                "through_count": sum(item.through_count for item in corridor.terminal_evidence.values()),
                "reversal_count": sum(item.reversal_count for item in corridor.terminal_evidence.values()),
            }
            for key, count in counts.items():
                self.values[key].setText(str(count))
            self.status.setText("Betrieblicher Graph aus original_schedule; <wege> dient nur als Raw-Evidenz.")
            self.clusters.setPlainText("\n\n".join(
                f"OperatingPoint {point.display_name}\n    " + "\n    ".join(point.raw_names)
                for point in sorted(operating.nodes.values(), key=lambda item: item.display_name)
            ) + "\n\n" + "\n".join(
                f"Backbone: {edge.source} ↔ {edge.target}\n  evidence: {edge.evidence}"
                for edge in corridor.backbone_edges.values()
            ) + "\n\n" + "\n".join(
                f"Triangle: {' / '.join(item.nodes)}\n  between: {item.between_candidate or 'rejected'}"
                f"\n  supports: {item.supporting_evidence}\n  contradictions: {item.contradicting_evidence}"
                for item in corridor.triangle_resolutions
            ) + "\n\n" + "\n".join(
                f"Terminal rejected: {item.node}\n  classification: {item.classification}"
                f"\n  contradictions: {item.contradicting_terminal_evidence}"
                for item in corridor.terminal_evidence.values() if item.contradicting_terminal_evidence
            ) + "\n\n" + "\n".join(
                f"Applied Between: {' → '.join(path)}\n  between: {path[1]}"
                f"\n  final action: {path[0]}–{path[2]} skip; chain edges retained"
                for path in corridor.applied_between_resolutions.values()
            ) + "\n\n" + "\n".join(
                f"Between constraint: {' → '.join(item.path)}\n  status: {item.status}"
                f"\n  required: {item.required_edges}\n  forbidden direct: {item.forbidden_transitive_edge}"
                f"\n  conflicts: {item.conflict_ids or 'none'}"
                for item in corridor.between_constraints.values()
            ) + "\n\n" + "\n".join(
                f"External target: {item.source_node} / {item.original_target!r}"
                f"\n  normalized candidate: {item.normalized_candidate}"
                f"\n  resolution: {item.classification}\n  matched node: {item.matched_node or 'none'}"
                f"\n  matched raw names: {item.matched_raw_names or 'none'}"
                f"\n  raw connector: {item.raw_connector or 'none'}"
                for item in corridor.external_target_resolutions.values()
            ) + "\n\n" + "\n".join(
                f"Hidden external boundary: {item.source_node} → {item.external_name}"
                f"\n  outgoing: {item.outgoing_observations}\n  incoming: {item.incoming_observations}"
                f"\n  raw connector: {item.raw_connector or 'not confirmed'}"
                f"\n  directionality: {item.directionality}\n  confidence: {item.confidence}"
                f"\n  evidence: {item.evidence}"
                for item in corridor.hidden_boundary_evidence.values()
            ) + "\n\n" + "\n".join(
                f"Topology question: {item.question_text}\n  status: {item.status}"
                f"\n  options: {item.options}\n  evidence: {item.evidence_summary}"
                for item in corridor.topology_questions.values()
            ) + "\n\n" + "\n".join(
                f"Synthetic junction: {item.display_name}\n  host edge: {' – '.join(item.host_edge)}"
                f"\n  branch: {item.branch_node}\n  attachment: edge"
                f"\n  topological fraction: {item.topological_fraction if item.topological_fraction is not None else 'unresolved'}"
                f"\n  topological source: {item.topological_position_source}"
                f"\n  display fraction: {item.display_fraction}"
                f"\n  display source: {item.display_position_source}\n  evidence: {item.evidence}"
                f"\n  topological confidence: {item.topological_confidence}"
                f"\n  raw support: {item.raw_junction_node or 'not confirmed'}"
                for item in corridor.synthetic_junctions.values()
            ) + "\n\n" + "\n".join(
                f"Role finalized: {node}\n  {change['pre_split_node_role']} → {change['final_node_role']}"
                f"\n  reason: {change['role_change_reason']}"
                for node, change in corridor.role_changes.items()
            ) + "\n\n" + "\n".join(
                f"Boundary evidence: {item.node}\n  starts: {item.schedule_start_count}"
                f"\n  ends: {item.schedule_end_count}\n  through: {item.through_count}"
                f"\n  raw outgoing corridors: {item.raw_outgoing_corridors}"
                f"\n  supporting: {item.evidence}"
                f"\n  contradictions: {item.contradicting_terminal_evidence}"
                f"\n  final role: {corridor.node_roles.get(item.node, 'not visible')}"
                for item in corridor.terminal_evidence.values()
                if item.classification != "candidate" or item.contradicting_terminal_evidence
            ) + "\n\n" + "\n".join(
                f"Skip: {edge.source} → {edge.target}\n  covered by: {' → '.join(edge.covered_path)}"
                for edge in corridor.edges.values() if edge.classification == "skip"
            ) + "\n\n" + "\n".join(
                f"Branch terminal: {item.terminal}\n  approach: {item.approach}\n  observations: {item.observations}"
                for item in corridor.direction_changes
            ))
            if snapshot.aid is not None:
                save_generated_graph(
                    self.generated_directory, snapshot.aid, raw_graph, builder.anchors, operational, platforms,
                    schedule=schedule, operating=operating, corridor=corridor, name=snapshot.facility_name,
                )
        except (ValueError, StopIteration) as exc:
            self.status.setText(f"Graphdaten konnten nicht ausgewertet werden: {exc}")
