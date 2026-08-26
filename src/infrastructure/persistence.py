"""AID-spezifische Persistenz automatisch erzeugter Graphdaten."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from .model import OperationalRouteGraph, PlatformEvidence, RawInfrastructureGraph, RouteAnchor
from .schedule_graph import OperatingPointGraph, SchedulePointGraph
from .corridor import CorridorGraph


def save_generated_graph(directory: str | Path, aid: int, raw: RawInfrastructureGraph,
                         anchors: dict[str, RouteAnchor], operational: OperationalRouteGraph,
                         platforms: tuple[PlatformEvidence, ...] = (),
                         schedule: SchedulePointGraph | None = None,
                         operating: OperatingPointGraph | None = None,
                         corridor: CorridorGraph | None = None,
                         **facility: str | None) -> Path:
    """Schreibt nur unter ``generated`` und beruehrt keine manuelle Config."""
    target = Path(directory) / "generated" / f"{aid}_graph.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 13, "aid": aid, "facility": facility,
        "raw": {"nodes": [asdict(item) for item in raw.nodes.values()],
                "edges": [asdict(item) for item in raw.edges],
                "platform_evidence": [asdict(item) for item in platforms]},
        "schedule": ({"nodes": [asdict(item) for item in schedule.nodes.values()],
                      "edges": [asdict(item) for item in schedule.edges.values()],
                      "service_provenance": [
                          {"zid": zid, **asdict(item)} for zid, item in schedule.service_provenance.items()
                      ]}
                     if schedule else {"nodes": [], "edges": [], "service_provenance": []}),
        "operating_point_clustering": ({
            "nodes": [asdict(item) for item in operating.nodes.values()],
            "edges": [asdict(item) for item in operating.edges.values()],
            "raw_to_operating_point": operating.raw_to_operating_point,
        } if operating else {"nodes": [], "edges": [], "raw_to_operating_point": {}}),
        "manual_confirmation": [asdict(item) for item in (operating.nodes.values() if operating else ())
                                if item.manual_confirmation],
        "route_axis": ({
            "nodes": [asdict(item) for item in operating.to_route_axis_graph().nodes.values()],
            "edges": [asdict(item) for item in operating.to_route_axis_graph().edges.values()],
            "operating_to_axis": operating.to_route_axis_graph().operating_to_axis,
        } if operating else {"nodes": [], "edges": [], "operating_to_axis": {}}),
        "corridor": ({
            "edges": [asdict(item) for item in corridor.edges.values()],
            "backbone_edges": [asdict(item) for item in corridor.backbone_edges.values()],
            "backbone_candidates": [
                {"nodes": sorted(nodes), "evidence": evidence}
                for nodes, evidence in corridor.backbone_candidates.items()
            ],
            "node_roles": corridor.node_roles,
            "topology_roles": corridor.topology_roles,
            "boundary_roles": corridor.boundary_roles,
            "direction_changes": [asdict(item) for item in corridor.direction_changes],
            "terminal_evidence": [asdict(item) for item in corridor.terminal_evidence.values()],
            "travel_time_stats": [asdict(item) for item in corridor.travel_time_stats.values()],
            "halt_aware_travel_time_comparisons": [
                {"nodes": list(nodes), "comparison": asdict(item)}
                for nodes, item in corridor.halt_aware_time_comparisons.items()
            ],
            "intermediate_stop_or_skipped_point_evidence": [
                {"nodes": list(nodes), "evidence": asdict(item)}
                for nodes, item in corridor.intermediate_stop_or_skip_evidence.items()
            ],
            "ordered_schedule_sequence_evidence": [
                asdict(item) for item in corridor.ordered_schedule_sequences
            ],
            "same_service_triple_evidence": [
                {"nodes": list(nodes), "evidence": asdict(item)}
                for nodes, item in corridor.same_service_triple_evidence.items()
            ],
            "triangle_hypotheses": [asdict(item) for item in corridor.triangle_hypotheses],
            "between_evidence": [
                {"nodes": list(nodes), "evidence": evidence}
                for nodes, evidence in corridor.between_evidence.items()
            ],
            "triangle_resolutions": [asdict(item) for item in corridor.triangle_resolutions],
            "raw_adjacency_evidence": [asdict(item) for item in corridor.raw_adjacency_evidence.values()],
            "backbone_scores": [
                {"nodes": sorted(nodes), "score": asdict(score)}
                for nodes, score in corridor.backbone_scores.items()
            ],
            "synthetic_junctions": [asdict(item) for item in corridor.synthetic_junctions.values()],
            "branch_attachments": [asdict(item) for item in corridor.branch_attachments.values()],
            "junction_position_estimates": [
                {"junction": junction, "estimate": asdict(estimate)}
                for junction, estimate in corridor.junction_position_estimates.items()
            ],
            "final_node_roles": corridor.node_roles,
            "pre_split_node_roles": corridor.pre_split_node_roles,
            "role_changes": corridor.role_changes,
            "applied_between_resolutions": [
                {"direct_edge": sorted(edge), "covered_path": list(path),
                 "between_final_action": "transitive_direct_edge_is_skip"}
                for edge, path in corridor.applied_between_resolutions.items()
            ],
            "between_constraints": [asdict(item) for item in corridor.between_constraints.values()],
            "required_edges": sorted({edge for item in corridor.between_constraints.values()
                                      if item.status == "applied" for edge in item.required_edges}),
            "forbidden_transitive_edges": sorted({item.forbidden_transitive_edge
                                                   for item in corridor.between_constraints.values()
                                                   if item.status == "applied"}),
            "between_constraint_conflicts": [asdict(item) for item in corridor.between_constraints.values()
                                             if item.status == "conflicting"],
            "hidden_boundary_evidence": [asdict(item) for item in corridor.hidden_boundary_evidence.values()],
            "synthetic_external_boundaries": [
                asdict(item) for item in corridor.synthetic_external_boundaries.values()
            ],
            "explicit_external_boundaries": [
                asdict(item) for item in corridor.explicit_external_boundaries.values()
            ],
            "boundary_dedup_mapping": corridor.boundary_dedup_mapping,
            "deferred_external_boundary_candidates": [
                asdict(item) for item in corridor.deferred_external_boundary_candidates.values()
            ],
            "topology_questions": [asdict(item) for item in corridor.topology_questions.values()],
            "external_target_resolutions": [
                asdict(item) for item in corridor.external_target_resolutions.values()
            ],
            "internal_target_matches": [
                asdict(item) for item in corridor.external_target_resolutions.values()
                if item.classification == "same_operating_point_internal"
            ],
            "ignored_endpoint_observations": corridor.ignored_endpoint_observations,
            "deferred_questions": corridor.deferred_questions,
            "component_roles": corridor.component_roles,
        } if corridor else {"edges": [], "backbone_edges": [], "backbone_candidates": [],
                            "node_roles": {}, "topology_roles": {}, "boundary_roles": {},
                            "direction_changes": [], "terminal_evidence": [],
                            "travel_time_stats": [], "halt_aware_travel_time_comparisons": [],
                            "intermediate_stop_or_skipped_point_evidence": [],
                            "ordered_schedule_sequence_evidence": [],
                            "same_service_triple_evidence": [], "triangle_hypotheses": [],
                            "between_evidence": [], "triangle_resolutions": [],
                            "raw_adjacency_evidence": [], "backbone_scores": [], "synthetic_junctions": [],
                            "branch_attachments": [], "junction_position_estimates": [],
                            "final_node_roles": {}, "pre_split_node_roles": {}, "role_changes": {},
                            "applied_between_resolutions": [], "between_constraints": [],
                            "required_edges": [], "forbidden_transitive_edges": [],
                            "between_constraint_conflicts": [], "hidden_boundary_evidence": [],
                            "synthetic_external_boundaries": [], "explicit_external_boundaries": [],
                            "boundary_dedup_mapping": {}, "deferred_external_boundary_candidates": [],
                            "topology_questions": [],
                            "external_target_resolutions": [], "internal_target_matches": [],
                            "ignored_endpoint_observations": [], "deferred_questions": [],
                            "component_roles": {}}),
        "derived": {"anchors": [asdict(item) for item in anchors.values()],
                    "operational_nodes": [asdict(item) for item in operational.nodes.values()],
                    "operational_edges": [asdict(item) for item in operational.edges]},
    }
    target.write_text(json.dumps(
        payload, ensure_ascii=False, indent=2,
        default=lambda value: sorted(value) if isinstance(value, set) else str(value),
    ), encoding="utf-8")
    return target
