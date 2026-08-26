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
        "schema_version": 4, "aid": aid, "facility": facility,
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
            "node_roles": corridor.node_roles,
            "direction_changes": [asdict(item) for item in corridor.direction_changes],
            "component_roles": corridor.component_roles,
        } if corridor else {"edges": [], "node_roles": {}, "direction_changes": [], "component_roles": {}}),
        "derived": {"anchors": [asdict(item) for item in anchors.values()],
                    "operational_nodes": [asdict(item) for item in operational.nodes.values()],
                    "operational_edges": [asdict(item) for item in operational.edges]},
    }
    target.write_text(json.dumps(
        payload, ensure_ascii=False, indent=2,
        default=lambda value: sorted(value) if isinstance(value, set) else str(value),
    ), encoding="utf-8")
    return target
