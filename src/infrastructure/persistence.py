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
        "schema_version": 7, "aid": aid, "facility": facility,
        "raw": {"nodes": [asdict(item) for item in raw.nodes.values()],
                "edges": [asdict(item) for item in raw.edges],
                "platform_evidence": [asdict(item) for item in platforms]},
        "schedule": ({"nodes": [asdict(item) for item in schedule.nodes.values()],
                      "edges": [asdict(item) for item in schedule.edges.values()]}
                     if schedule else {"nodes": [], "edges": []}),
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
            "direction_changes": [asdict(item) for item in corridor.direction_changes],
            "terminal_evidence": [asdict(item) for item in corridor.terminal_evidence.values()],
            "travel_time_stats": [asdict(item) for item in corridor.travel_time_stats.values()],
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
            "component_roles": corridor.component_roles,
        } if corridor else {"edges": [], "backbone_edges": [], "backbone_candidates": [],
                            "node_roles": {}, "direction_changes": [], "terminal_evidence": [],
                            "travel_time_stats": [], "between_evidence": [], "triangle_resolutions": [],
                            "raw_adjacency_evidence": [], "backbone_scores": [], "synthetic_junctions": [],
                            "branch_attachments": [], "junction_position_estimates": [], "component_roles": {}}),
        "derived": {"anchors": [asdict(item) for item in anchors.values()],
                    "operational_nodes": [asdict(item) for item in operational.nodes.values()],
                    "operational_edges": [asdict(item) for item in operational.edges]},
    }
    target.write_text(json.dumps(
        payload, ensure_ascii=False, indent=2,
        default=lambda value: sorted(value) if isinstance(value, set) else str(value),
    ), encoding="utf-8")
    return target
