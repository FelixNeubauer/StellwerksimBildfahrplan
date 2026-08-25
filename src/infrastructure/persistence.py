"""AID-spezifische Persistenz automatisch erzeugter Graphdaten."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from .model import OperationalRouteGraph, PlatformEvidence, RawInfrastructureGraph, RouteAnchor


def save_generated_graph(directory: str | Path, aid: int, raw: RawInfrastructureGraph,
                         anchors: dict[str, RouteAnchor], operational: OperationalRouteGraph,
                         platforms: tuple[PlatformEvidence, ...] = (), **facility: str | None) -> Path:
    """Schreibt nur unter ``generated`` und beruehrt keine manuelle Config."""
    target = Path(directory) / "generated" / f"{aid}_graph.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1, "aid": aid, "facility": facility,
        "raw": {"nodes": [asdict(item) for item in raw.nodes.values()],
                "edges": [asdict(item) for item in raw.edges],
                "platform_evidence": [asdict(item) for item in platforms]},
        "derived": {"anchors": [asdict(item) for item in anchors.values()],
                    "operational_nodes": [asdict(item) for item in operational.nodes.values()],
                    "operational_edges": [asdict(item) for item in operational.edges]},
    }
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return target
