"""AID-spezifische Persistenz des autoritativen Streckengraphen."""

from __future__ import annotations

import json
from pathlib import Path

from .artifact_identity import SavedStellwerkIdentity, artifact_metadata, atomic_write_json
from .editable_topology import EditableTopologyGraph


class EditableTopologyGraphStore:
    SCHEMA_VERSION = 2
    ARTIFACT_TYPE = "editable_topology_graph"

    def __init__(self, config_directory: str | Path) -> None:
        self.directory = Path(config_directory) / "topology"

    def path_for(self, aid: int) -> Path:
        return self.directory / f"{aid}.json"

    @staticmethod
    def load_path(path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))

    def save(self, aid: int, stellwerk_name: str, graph: EditableTopologyGraph) -> Path:
        payload = {
            **artifact_metadata(SavedStellwerkIdentity(aid, stellwerk_name), self.ARTIFACT_TYPE,
                                self.SCHEMA_VERSION),
            **graph.to_dict(),
        }
        return atomic_write_json(self.path_for(aid), payload)
