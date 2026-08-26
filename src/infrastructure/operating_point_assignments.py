"""Editierbare, AID-spezifische Ortszuordnungen ohne Qt-Abhaengigkeit."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .schedule_graph import OperatingPointGraph, station_key

_NATURAL_PART = re.compile(r"(\d+)")
_UNPREFIXED_TRACK = re.compile(r"^\d+(?:\s*[A-Za-zÄÖÜäöü]+)?(?:\s+.*)?$")


def natural_sort_key(value: str) -> tuple[tuple[int, object], ...]:
    """Case-insensitiver Sortierschluessel mit numerischen Teilstuecken."""
    return tuple((0, int(part)) if part.isdigit() else (1, part.casefold())
                 for part in _NATURAL_PART.split(value))


def is_unprefixed_numeric(raw_name: str) -> bool:
    """Erkennt nur die Auswahlgruppe, ohne irgendeine Gleissemantik abzuleiten."""
    return station_key(raw_name) is None and bool(_UNPREFIXED_TRACK.match(raw_name.strip()))


def related_selection(raw_names: Iterable[str], selected: Iterable[str]) -> set[str]:
    """Vereinigt Station-Key- bzw. unpraefixierte Auswahlgruppen."""
    keys = {station_key(name) for name in selected if station_key(name)}
    include_unprefixed = any(is_unprefixed_numeric(name) for name in selected)
    return {name for name in raw_names if station_key(name) in keys or
            (include_unprefixed and is_unprefixed_numeric(name))}


@dataclass
class EditableOperatingPoint:
    id: str
    display_name: str
    station_key: str | None = None
    removable: bool = False


@dataclass
class OperatingPointAssignments:
    """Ein eindeutiger Assignment-Layer ueber einem ableitbaren Auto-Graphen."""

    points: dict[str, EditableOperatingPoint] = field(default_factory=dict)
    assignments: dict[str, str] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)
    manual_assignments: dict[str, str] = field(default_factory=dict)
    explicitly_unassigned: set[str] = field(default_factory=set)
    manual_point_ids: set[str] = field(default_factory=set)
    all_raw_names: set[str] = field(default_factory=set)

    def rebuild(self, automatic: OperatingPointGraph, raw_names: Iterable[str],
                haltpunkt_names: Iterable[str], config: dict | None = None) -> None:
        """Wendet Automatik neu an; persistierte Nutzerentscheidungen gewinnen immer."""
        config = config or {}
        self.all_raw_names = {name for name in raw_names if name}
        haltepunkte = set(haltpunkt_names)
        self.points = {
            point.id: EditableOperatingPoint(point.id, point.display_name, station_key(point.display_name), False)
            for point in automatic.nodes.values()
        }
        self.assignments = {}
        self.sources = {}
        for raw_name, point_id in automatic.raw_to_operating_point.items():
            if raw_name not in self.all_raw_names:
                continue
            self.assignments[raw_name] = point_id
            self.sources[raw_name] = "self_haltpunkt" if raw_name in haltepunkte and (
                automatic.nodes[point_id].raw_names == (raw_name,)) else "automatic"

        point_data = config.get("operating_points", {})
        self.manual_point_ids = set(config.get("manual_point_ids", ()))
        for point_id, values in point_data.items():
            # Altes Schema: jeder konfigurierte Punkt war manuell bestaetigt.
            if values.get("removable", True) or not config.get("manual_point_ids"):
                self.manual_point_ids.add(point_id)
            self.points[point_id] = EditableOperatingPoint(
                point_id, values.get("display_name", point_id), values.get("station_key"),
                point_id in self.manual_point_ids,
            )

        persisted = dict(config.get("assignments", {}))
        # Rueckwaertskompatibilitaet mit den bisherigen raw_names-Clustern.
        for point_id, values in point_data.items():
            for raw_name in values.get("raw_names", values.get("members", ())):
                persisted.setdefault(raw_name, point_id)
        self.manual_assignments = {
            raw_name: point_id for raw_name, point_id in persisted.items()
            if raw_name in self.all_raw_names and point_id in self.points
        }
        self.explicitly_unassigned = set(config.get("unassigned", ())) & self.all_raw_names
        for raw_name in self.explicitly_unassigned:
            self.assignments.pop(raw_name, None); self.sources.pop(raw_name, None)
        for raw_name, point_id in self.manual_assignments.items():
            self.assignments[raw_name] = point_id; self.sources[raw_name] = "manual"

    @property
    def unassigned(self) -> set[str]:
        return self.all_raw_names - set(self.assignments)

    def assign(self, raw_names: Iterable[str], point_id: str) -> None:
        if point_id not in self.points:
            raise KeyError(point_id)
        for raw_name in set(raw_names) & self.all_raw_names:
            self.assignments[raw_name] = point_id
            self.sources[raw_name] = "manual"
            self.manual_assignments[raw_name] = point_id
            self.explicitly_unassigned.discard(raw_name)

    def remove_assignments(self, raw_names: Iterable[str]) -> None:
        for raw_name in set(raw_names) & self.all_raw_names:
            self.assignments.pop(raw_name, None); self.sources.pop(raw_name, None)
            self.manual_assignments.pop(raw_name, None)
            self.explicitly_unassigned.add(raw_name)

    def clear_editable_assignments(self) -> None:
        """Loest alles ausser den fachlich belegten Self-Haltepunkten."""
        for raw_name in tuple(self.assignments):
            if self.sources.get(raw_name) != "self_haltpunkt":
                self.remove_assignments((raw_name,))

    def add_point(self, display_name: str, key: str | None = None) -> str:
        base = re.sub(r"[^a-z0-9]+", "-", display_name.casefold()).strip("-") or "neu"
        point_id = f"manual:{base}"
        suffix = 2
        while point_id in self.points:
            point_id = f"manual:{base}-{suffix}"; suffix += 1
        self.points[point_id] = EditableOperatingPoint(point_id, display_name, key or None, True)
        self.manual_point_ids.add(point_id)
        return point_id

    def rename_point(self, point_id: str, display_name: str) -> None:
        point = self.points[point_id]
        if not point.removable:
            raise ValueError("Automatisch benoetigte Betriebsstellen sind nicht editierbar")
        point.display_name = display_name

    def delete_point(self, point_id: str) -> None:
        if point_id not in self.manual_point_ids:
            raise ValueError("Nur manuelle Betriebsstellen sind loeschbar")
        self.remove_assignments({name for name, owner in self.assignments.items() if owner == point_id})
        self.points.pop(point_id); self.manual_point_ids.discard(point_id)

    def to_config(self) -> dict:
        configured_ids = self.manual_point_ids | set(self.manual_assignments.values())
        return {
            "schema_version": 1,
            "operating_points": {
                point_id: {"display_name": self.points[point_id].display_name,
                           "station_key": self.points[point_id].station_key,
                           "raw_names": sorted((name for name, owner in self.manual_assignments.items()
                                                if owner == point_id), key=natural_sort_key),
                           "removable": point_id in self.manual_point_ids}
                for point_id in sorted(configured_ids)
            },
            "manual_point_ids": sorted(self.manual_point_ids),
            "assignments": dict(sorted(self.manual_assignments.items(), key=lambda item: natural_sort_key(item[0]))),
            "unassigned": sorted(self.explicitly_unassigned, key=natural_sort_key),
        }


class OperatingPointConfigStore:
    def __init__(self, config_directory: str | Path) -> None:
        self.directory = Path(config_directory) / "operating_points"

    def path_for(self, aid: int) -> Path:
        return self.directory / f"{aid}.json"

    def load(self, aid: int | None) -> dict:
        if aid is None or not self.path_for(aid).exists():
            return {}
        return json.loads(self.path_for(aid).read_text(encoding="utf-8"))

    def save(self, aid: int, model: OperatingPointAssignments) -> Path:
        target = self.path_for(aid)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(model.to_config(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(target)
        return target
