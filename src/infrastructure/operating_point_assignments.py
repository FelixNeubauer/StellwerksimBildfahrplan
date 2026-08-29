"""Editierbare, AID-spezifische Ortszuordnungen ohne Qt-Abhaengigkeit."""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from hashlib import sha1
from pathlib import Path
from typing import Any, Iterable

from .model import RawInfrastructureGraph
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


ASSIGNABLE_KINDS = {"platform_or_haltpunkt", "schedule_point", "entry"}
TARGET_KINDS = {"operating_point", "entry_point"}


@dataclass(frozen=True)
class EntryInfrastructureElement:
    node_id: str
    element_type: str
    enr: str | None
    raw_name: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class EntryPoint:
    id: str
    display_name: str
    source: str = "wege"
    infrastructure_elements: tuple[EntryInfrastructureElement, ...] = ()
    evidence: tuple[str, ...] = ("wege_type_6_or_7",)
    boundary_evidence: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class AssignableRawItem:
    raw_name: str
    kind: str
    evidence: tuple[str, ...] = ()


class InvalidAssignment(ValueError):
    def __init__(self, raw_names: Iterable[str]) -> None:
        self.raw_names = tuple(raw_names)
        super().__init__(f"{len(self.raw_names)} ausgewählte Elemente können diesem Ziel nicht zugeordnet werden.")


def _entry_key(name: str) -> str:
    return unicodedata.normalize("NFC", name).strip().casefold()


def entry_point_id(name: str) -> str:
    key = _entry_key(name)
    slug = re.sub(r"[^a-z0-9]+", "-", key).strip("-") or "extern"
    return f"entry:wege:{slug}:{sha1(key.encode('utf-8')).hexdigest()[:10]}"


def entry_points_from_raw_graph(raw: RawInfrastructureGraph) -> dict[str, EntryPoint]:
    """Projiziert type-6/7-Shapes verlustfrei auf deduplizierte äußere Ziele."""
    grouped: dict[str, list] = {}
    labels: dict[str, str] = {}
    for node in raw.nodes.values():
        if str(node.element_type) not in {"6", "7"} or not node.raw_name:
            continue
        key = _entry_key(node.raw_name)
        grouped.setdefault(key, []).append(node); labels.setdefault(key, node.raw_name)
    result = {}
    for key, nodes in sorted(grouped.items()):
        label = labels[key]; identifier = entry_point_id(label)
        elements = tuple(EntryInfrastructureElement(
            node.id, str(node.element_type), node.enr, node.raw_name or "", dict(node.metadata))
            for node in sorted(nodes, key=lambda item: (item.enr or "", item.id)))
        result[identifier] = EntryPoint(identifier, label, infrastructure_elements=elements)
    return result


def can_assign_kind(assignable_kind: str, target_kind: str) -> bool:
    if assignable_kind not in ASSIGNABLE_KINDS or target_kind not in TARGET_KINDS:
        return False
    return target_kind == "entry_point" or assignable_kind != "entry"


@dataclass
class EditableOperatingPoint:
    id: str
    display_name: str
    station_key: str | None = None
    removable: bool = False


@dataclass(frozen=True)
class AssignmentCompleteness:
    unassigned_platform_count: int
    unassigned_entry_count: int
    empty_entry_point_count: int
    initialized: bool = True

    @property
    def is_complete(self) -> bool:
        return self.initialized and not (
            self.unassigned_platform_count or self.unassigned_entry_count
            or self.empty_entry_point_count)


@dataclass(frozen=True)
class TopologyEligibility:
    eligible: bool
    reason: str
    relevant_members: tuple[str, ...] = ()


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
    entry_points: dict[str, EntryPoint] = field(default_factory=dict)
    raw_items: dict[str, AssignableRawItem] = field(default_factory=dict)
    _automatic_members: dict[str, tuple[str, ...]] = field(default_factory=dict, repr=False)
    deleted_automatic_point_ids: set[str] = field(default_factory=set)
    deleted_automatic_identities: set[str] = field(default_factory=set)
    deleted_entry_point_ids: set[str] = field(default_factory=set)
    hidden_manual_point_ids: set[str] = field(default_factory=set)
    _hidden_manual_points: dict[str, EditableOperatingPoint] = field(default_factory=dict, repr=False)

    def rebuild(self, automatic: OperatingPointGraph, raw_names: Iterable[str],
                haltpunkt_names: Iterable[str], config: dict | None = None,
                *, respect_unassigned: bool = True,
                entry_points: dict[str, EntryPoint] | None = None,
                raw_item_kinds: dict[str, str] | None = None,
                automatic_entry_assignments: dict[str, str] | None = None) -> None:
        """Wendet Automatik neu an; persistierte Nutzerentscheidungen gewinnen immer."""
        config = config or {}
        snapshot = config.get("editor_snapshot", {})
        self.all_raw_names = {name for name in raw_names if name} | set(snapshot.get("raw_names", ()))
        haltepunkte = set(haltpunkt_names)
        configured_kinds = dict(config.get("raw_item_kinds", {}))
        configured_kinds.update({name: values.get("kind", "schedule_point")
                                 for name, values in snapshot.get("raw_items", {}).items()})
        configured_kinds.update(raw_item_kinds or {})
        kind_evidence = {"entry": ("confirmed_boundary",),
                         "platform_or_haltpunkt": ("bahnsteigliste",),
                         "schedule_point": ("original_schedule",)}
        self.raw_items = {}
        for name in self.all_raw_names:
            kind = configured_kinds.get(name, "schedule_point")
            evidence = tuple(snapshot.get("raw_items", {}).get(name, {}).get(
                "evidence", kind_evidence.get(kind, ())))
            self.raw_items[name] = AssignableRawItem(name, kind, evidence)
        self.deleted_automatic_point_ids = set(config.get("deleted_automatic_point_ids", ()))
        self.deleted_automatic_identities = set(config.get("deleted_automatic_identities", ()))
        self.deleted_entry_point_ids = set(config.get("deleted_entry_point_ids", ()))
        self.hidden_manual_point_ids = set(config.get("hidden_manual_point_ids", ()))
        self.entry_points = {key: value for key, value in (entry_points or {}).items()
                             if key not in self.deleted_entry_point_ids}
        configured_entries = dict(config.get("entry_points", {}))
        configured_entries.update(snapshot.get("entry_points", {}))
        for point_id, values in configured_entries.items():
            if point_id not in self.entry_points and point_id not in self.deleted_entry_point_ids:
                elements = tuple(EntryInfrastructureElement(**item)
                                 for item in values.get("infrastructure_elements", ()))
                self.entry_points[point_id] = EntryPoint(
                    point_id, values.get("display_name", point_id), values.get("source", "snapshot"),
                    elements, tuple(values.get("evidence", ())), tuple(values.get("boundary_evidence", ())))
        entry_names = {name for name, item in self.raw_items.items() if item.kind == "entry"}
        automatic_points = {
            point.id: EditableOperatingPoint(point.id, point.display_name, station_key(point.display_name), False)
            for point in automatic.nodes.values()
            if not point.raw_names or any(name not in entry_names for name in point.raw_names)
        }
        aliases = self._canonicalize_automatic_points(automatic_points, automatic)
        self.points = {key: value for key, value in automatic_points.items()
                       if aliases.get(key, key) == key and key not in self.deleted_automatic_point_ids
                       and self._point_identity(value) not in self.deleted_automatic_identities}
        self._automatic_members = {}
        for point in automatic.nodes.values():
            canonical = aliases.get(point.id, point.id)
            self._automatic_members[canonical] = tuple(dict.fromkeys(
                (*self._automatic_members.get(canonical, ()), *point.raw_names)))
        self.assignments = {}
        self.sources = {}
        for raw_name, point_id in automatic.raw_to_operating_point.items():
            point_id = aliases.get(point_id, point_id)
            if raw_name not in self.all_raw_names or self.raw_items[raw_name].kind == "entry" or point_id not in self.points:
                continue
            self.assignments[raw_name] = point_id
            self.sources[raw_name] = "self_haltpunkt" if raw_name in haltepunkte and (
                automatic.nodes[point_id].raw_names == (raw_name,)) else "automatic"

        self._extend_by_station_key(haltepunkte)
        for raw_name, point_id in (automatic_entry_assignments or {}).items():
            if raw_name in self.all_raw_names and point_id in self.entry_points:
                self.assignments[raw_name] = point_id; self.sources[raw_name] = "self_entry"

        for point_id, values in snapshot.get("operating_points", {}).items():
            point_id = aliases.get(point_id, point_id)
            if point_id in self.deleted_automatic_point_ids or point_id in self.hidden_manual_point_ids:
                continue
            automatic_point = automatic.nodes.get(point_id)
            if (automatic_point and automatic_point.raw_names
                    and all(name in entry_names for name in automatic_point.raw_names)
                    and values.get("source") != "manual"):
                continue
            # Der Snapshot ist eine Diagnose des damaligen Autozustands, keine
            # zweite autoritative Quelle. Nicht mehr reproduzierbare
            # automatische Ziele (z. B. ``schedule:TKS`` neben dem aktuellen
            # Resolver-Ziel ``TKS``) würden sonst als leere Schattenobjekte
            # wieder erscheinen.
            if (values.get("source") != "manual" and point_id not in self.points
                    and point_id not in automatic.nodes):
                continue
            self.points.setdefault(point_id, EditableOperatingPoint(
                point_id, values.get("display_name", point_id), values.get("station_key"),
                values.get("source") == "manual"))
        for raw_name, values in snapshot.get("assignments", {}).items():
            if raw_name not in self.assignments and raw_name in self.all_raw_names and values.get("target", values.get("operating_point")) in (self.points | self.entry_points):
                self.assignments[raw_name] = values.get("target", values.get("operating_point"))
                self.sources[raw_name] = values.get("source", "automatic")

        point_data = config.get("operating_points", {})
        self._hidden_manual_points = {}
        self.manual_point_ids = set(config.get("manual_point_ids", ()))
        has_manual_point_ids = "manual_point_ids" in config
        for point_id, values in point_data.items():
            # Alte, nicht typisierte Cluster bleiben konservativ manuell; als
            # automatic markierte Snapshot-Daten werden dagegen nur migriert.
            source = values.get("assignment_source")
            if values.get("removable", False) or (not has_manual_point_ids and source not in {
                    "automatic", "automatic_station_key", "self_haltpunkt"}):
                self.manual_point_ids.add(point_id)
            if point_id in self.hidden_manual_point_ids:
                self._hidden_manual_points[point_id] = EditableOperatingPoint(
                    point_id, values.get("display_name", point_id), values.get("station_key"), True)
                continue
            self.points[point_id] = EditableOperatingPoint(
                point_id, values.get("display_name", point_id), values.get("station_key"),
                point_id in self.manual_point_ids,
            )

        assignment_sources = config.get("assignment_sources", {})
        persisted = {
            raw_name: point_id for raw_name, point_id in config.get("assignments", {}).items()
            if assignment_sources.get(raw_name, "manual") in {"manual", "manual_entry", "imported", "manual_config"}
        }
        # Rueckwaertskompatibilitaet mit den bisherigen raw_names-Clustern.
        for point_id, values in point_data.items():
            source = values.get("assignment_source", "manual")
            for raw_name in values.get("raw_names", values.get("members", ())):
                raw_source = assignment_sources.get(raw_name, source)
                if raw_source in {"manual", "manual_entry", "imported", "manual_config"}:
                    persisted.setdefault(raw_name, point_id)
        self.manual_assignments = {
            raw_name: point_id for raw_name, point_id in persisted.items()
            if raw_name in self.all_raw_names and point_id in (self.points | self.entry_points)
        }
        self.explicitly_unassigned = (set(config.get("unassigned", ())) & self.all_raw_names
                                      if respect_unassigned else set())
        for raw_name in self.explicitly_unassigned:
            self.assignments.pop(raw_name, None); self.sources.pop(raw_name, None)
        for raw_name, point_id in self.manual_assignments.items():
            self.assignments[raw_name] = point_id
            self.sources[raw_name] = assignment_sources.get(
                raw_name, "manual_entry" if point_id in self.entry_points else "manual")

    @staticmethod
    def _canonicalize_automatic_points(points: dict[str, EditableOperatingPoint],
                                       automatic: OperatingPointGraph) -> dict[str, str]:
        """Merges only automatic targets with the same established station identity."""
        groups: dict[tuple[str, str], list[str]] = {}
        for point_id, point in points.items():
            keys = {station_key(name) for name in automatic.nodes[point_id].raw_names if station_key(name)}
            identity = ("station", next(iter(keys))) if len(keys) == 1 else (
                "name", unicodedata.normalize("NFC", point.display_name).strip().casefold())
            groups.setdefault(identity, []).append(point_id)
        aliases: dict[str, str] = {}
        for identifiers in groups.values():
            canonical = min(identifiers, key=lambda value: (
                value.startswith("schedule:"), value.startswith("station-key:"), natural_sort_key(value)))
            aliases.update({identifier: canonical for identifier in identifiers})
            points[canonical].station_key = points[canonical].station_key or next(
                (points[item].station_key for item in identifiers if points[item].station_key), None)
        return aliases

    def _extend_by_station_key(self, haltpunkt_names: set[str]) -> None:
        """Erweitert nur die Editor-Ortszuordnung, nicht den Topologiegraphen."""
        names_by_key: dict[str, set[str]] = {}
        for raw_name in self.all_raw_names:
            if self.raw_items.get(raw_name, AssignableRawItem(raw_name, "schedule_point")).kind == "entry":
                continue
            key = station_key(raw_name)
            if key and raw_name not in haltpunkt_names:
                names_by_key.setdefault(key, set()).add(raw_name)
        for key, names in names_by_key.items():
            if self._normalize_identity(key) in self.deleted_automatic_identities:
                continue
            candidate_ids = {
                self.assignments[name] for name in names if name in self.assignments
                and self.assignments[name] in self.points
                and (self.assignments[name] == key or
                     len(self._automatic_members.get(self.assignments[name], ())) > 1)
            }
            direct = key if key in self.points else None
            target = direct or (next(iter(candidate_ids)) if len(candidate_ids) == 1 else None)
            if target is None and len(names) > 1:
                target = f"station-key:{key}"
                self.points[target] = EditableOperatingPoint(target, key, key, False)
            if target is None:
                continue
            for raw_name in names:
                current = self.assignments.get(raw_name)
                if current is None or (self.sources.get(raw_name) == "automatic" and
                                       current.startswith("schedule:")):
                    self.assignments[raw_name] = target
                    self.sources[raw_name] = "automatic_station_key"

    @property
    def unassigned(self) -> set[str]:
        return self.all_raw_names - set(self.assignments)

    def target_kind(self, target_id: str) -> str:
        if target_id in self.points:
            return "operating_point"
        if target_id in self.entry_points:
            return "entry_point"
        raise KeyError(target_id)

    def can_assign(self, raw_name: str, target_id: str) -> bool:
        item = self.raw_items.get(raw_name)
        return bool(item and can_assign_kind(item.kind, self.target_kind(target_id)))

    def assign(self, raw_names: Iterable[str], point_id: str) -> None:
        target_kind = self.target_kind(point_id)
        selected = set(raw_names) & self.all_raw_names
        invalid = sorted((name for name in selected if not self.can_assign(name, point_id)), key=natural_sort_key)
        if invalid:
            raise InvalidAssignment(invalid)
        source = "manual_entry" if target_kind == "entry_point" else "manual"
        for raw_name in selected:
            self.assignments[raw_name] = point_id
            self.sources[raw_name] = source
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
            if self.sources.get(raw_name) not in {"self_haltpunkt", "self_entry"}:
                self.remove_assignments((raw_name,))

    def completeness(self) -> AssignmentCompleteness:
        unassigned_platforms = sum(
            item.kind == "platform_or_haltpunkt" and name not in self.assignments
            for name, item in self.raw_items.items())
        unassigned_entries = sum(
            item.kind == "entry" and name not in self.assignments
            for name, item in self.raw_items.items())
        empty_entries = sum(not any(owner == point_id for owner in self.assignments.values())
                            for point_id in self.entry_points)
        return AssignmentCompleteness(unassigned_platforms, unassigned_entries, empty_entries,
                                      bool(self.raw_items or self.entry_points))

    def topology_eligibility(self, target_id: str) -> TopologyEligibility:
        """Decides topology visibility from explicit assignment semantics, never resolver existence alone."""
        if target_id in self.entry_points:
            return TopologyEligibility(True, "active_entry_point", tuple(sorted(
                (name for name, owner in self.assignments.items() if owner == target_id),
                key=natural_sort_key)))
        if target_id not in self.points:
            return TopologyEligibility(False, "unknown_target")
        if target_id in self.manual_point_ids:
            return TopologyEligibility(True, "manual_operating_point")
        relevant: list[str] = []
        for raw_name, owner in self.assignments.items():
            if owner != target_id:
                continue
            item = self.raw_items.get(raw_name)
            source = self.sources.get(raw_name, "automatic")
            if source == "self_haltpunkt":
                relevant.append(raw_name)
            elif item and item.kind == "platform_or_haltpunkt":
                relevant.append(raw_name)
            elif item and item.kind == "entry" and source in {"manual", "manual_entry"}:
                relevant.append(raw_name)
            elif item and item.kind == "schedule_point" and source in {
                    "manual", "manual_config", "imported"}:
                relevant.append(raw_name)
        if relevant:
            return TopologyEligibility(True, "relevant_assignment",
                                       tuple(sorted(relevant, key=natural_sort_key)))
        return TopologyEligibility(False, "automatic_without_relevant_assignment")

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
        if point_id not in self.points:
            raise KeyError(point_id)
        self._release_target(point_id)
        if point_id in self.manual_point_ids:
            self.hidden_manual_point_ids.add(point_id)
            self._hidden_manual_points[point_id] = self.points[point_id]
        else:
            self.deleted_automatic_point_ids.add(point_id)
            self.deleted_automatic_identities.add(self._point_identity(self.points[point_id]))
        self.points.pop(point_id)

    def delete_entry_point(self, point_id: str) -> None:
        if point_id not in self.entry_points:
            raise KeyError(point_id)
        self._release_target(point_id)
        self.deleted_entry_point_ids.add(point_id)
        self.entry_points.pop(point_id)

    def _release_target(self, point_id: str) -> None:
        self.remove_assignments({name for name, owner in self.assignments.items() if owner == point_id})

    def delete_all_points(self) -> None:
        for point_id in tuple(self.points):
            self.delete_point(point_id)

    def delete_all_entry_points(self) -> None:
        for point_id in tuple(self.entry_points):
            self.delete_entry_point(point_id)

    def restore_automatic_targets(self) -> None:
        self.deleted_automatic_point_ids.clear()
        self.deleted_automatic_identities.clear()
        self.deleted_entry_point_ids.clear()

    @staticmethod
    def _point_identity(point: EditableOperatingPoint) -> str:
        return OperatingPointAssignments._normalize_identity(point.station_key or point.display_name)

    @staticmethod
    def _normalize_identity(value: str) -> str:
        return unicodedata.normalize("NFC", value).strip().casefold()

    def to_config(self) -> dict:
        configured_ids = self.manual_point_ids | (set(self.manual_assignments.values()) & set(self.points))
        configured_points = self.points | self._hidden_manual_points
        result = {
            "schema_version": 3,
            "entry_points": {
                point_id: {"display_name": point.display_name, "source": point.source,
                           "infrastructure_elements": [
                               {"node_id": item.node_id, "element_type": item.element_type,
                                "enr": item.enr, "raw_name": item.raw_name, "metadata": item.metadata}
                               for item in point.infrastructure_elements],
                           "evidence": list(point.evidence),
                           "boundary_evidence": list(point.boundary_evidence)}
                for point_id, point in sorted(self.entry_points.items())
            },
            "raw_item_kinds": {name: item.kind for name, item in sorted(self.raw_items.items())},
            "operating_points": {
                point_id: {"display_name": configured_points[point_id].display_name,
                           "station_key": configured_points[point_id].station_key,
                           "raw_names": sorted((name for name, owner in self.manual_assignments.items()
                                                if owner == point_id), key=natural_sort_key),
                           "removable": point_id in self.manual_point_ids}
                for point_id in sorted(configured_ids)
            },
            "manual_point_ids": sorted(self.manual_point_ids),
            "assignments": dict(sorted(self.manual_assignments.items(), key=lambda item: natural_sort_key(item[0]))),
            "assignment_sources": {name: self.sources.get(name, "manual")
                                   for name in sorted(self.manual_assignments, key=natural_sort_key)},
            "unassigned": sorted(self.explicitly_unassigned, key=natural_sort_key),
            "deleted_automatic_point_ids": sorted(self.deleted_automatic_point_ids),
            "deleted_automatic_identities": sorted(self.deleted_automatic_identities),
            "deleted_entry_point_ids": sorted(self.deleted_entry_point_ids),
            "hidden_manual_point_ids": sorted(self.hidden_manual_point_ids),
        }
        result["editor_snapshot"] = {
            "raw_names": sorted(self.all_raw_names, key=natural_sort_key),
            "raw_items": {name: {"kind": item.kind, "evidence": list(item.evidence)}
                          for name, item in sorted(self.raw_items.items())},
            "entry_points": result["entry_points"],
            "operating_points": {
                point_id: {"display_name": point.display_name, "station_key": point.station_key,
                           "source": "manual" if point_id in self.manual_point_ids else "automatic"}
                for point_id, point in sorted(self.points.items())
            },
            "assignments": {
                name: {"target": self.assignments.get(name),
                       "operating_point": self.assignments.get(name),
                       "source": self.sources.get(
                           name, "unassigned_manual_tombstone" if name in self.explicitly_unassigned
                           else "unassigned")}
                for name in sorted(self.all_raw_names, key=natural_sort_key)
            },
        }
        return result


class OperatingPointConfigStore:
    def __init__(self, config_directory: str | Path) -> None:
        self.directory = Path(config_directory) / "operating_points"

    def path_for(self, aid: int) -> Path:
        return self.directory / f"{aid}.json"

    def load_path(self, path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))

    def load(self, aid: int | None) -> dict:
        if aid is None or not self.path_for(aid).exists():
            return {}
        return self.load_path(self.path_for(aid))

    def save(self, aid: int, stellwerk_name: str, model: OperatingPointAssignments) -> Path:
        from .artifact_identity import SavedStellwerkIdentity, artifact_metadata, atomic_write_json
        payload = {**artifact_metadata(SavedStellwerkIdentity(aid, stellwerk_name), "operating_points", 3),
                   **model.to_config()}
        payload["schema_version"] = 3
        return atomic_write_json(self.path_for(aid), payload)
