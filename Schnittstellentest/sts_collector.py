"""Persistenter, GUI-unabhaengiger Datenkern fuer StellwerkSim-Livedaten.

Das Modul bewertet nur direkt gelieferte Attribute. Insbesondere findet hier
bewusst keine universelle Interpretation von Gleisnamen statt.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4


EVENT_TYPES = ("einfahrt", "ausfahrt", "ankunft", "abfahrt", "rothalt", "wurdegruen", "kuppeln", "fluegeln")
TRACK_RESOLUTIONS = {"exact", "section", "abstract", "unknown"}
RELATION_TYPES = {"continuation", "coupling", "splitting", "rename", "locomotive_runaround", "locomotive_change"}
SERVICE_KINDS = {"train", "locomotive_movement", "wagon_set", "unknown"}
LOGGER = logging.getLogger(__name__)
_FLAG_TOKEN = re.compile(r"([A-Z])(?:\[[^]]*\]|\([^)]*\))?")


def _bool(value: str | None) -> bool | None:
    if value is None:
        return None
    return value.lower() == "true"


def _int(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


@dataclass
class Location:
    raw_name: str
    operating_point: str | None = None
    physical_track: str | None = None
    track_section: str | None = None
    stop_position: str | None = None
    track_resolution: str = "unknown"
    location_type: str = "unknown"

    def __post_init__(self) -> None:
        if self.track_resolution not in TRACK_RESOLUTIONS:
            raise ValueError(f"Unbekannte Gleisaufloesung: {self.track_resolution}")


@dataclass
class SchedulePoint:
    raw_name: str
    current_name: str
    planned_name: str
    planned_arrival: str | None = None
    planned_departure: str | None = None
    flags_raw: str = ""
    hint_text: str | None = None
    operating_point: str | None = None
    physical_track: str | None = None
    track_section: str | None = None
    stop_position: str | None = None
    track_resolution: str = "unknown"
    raw_xml: str = ""


@dataclass
class TrainEvent:
    art: str
    zid: int
    train_name: str | None
    delay: int | None
    track: str | None
    planned_track: str | None
    visible: bool | None
    at_track: bool | None
    origin: str | None
    destination: str | None
    receive_timestamp: str
    simtime: int | None
    raw_xml: str


@dataclass
class ObservedScheduleRowTime:
    """Nur in der laufenden Sitzung beobachtete STS-Zeiten, in Simulationsminuten."""

    actual_arrival_minute: int | None = None
    actual_departure_minute: int | None = None


@dataclass
class ObservedTrainTimes:
    """GUI-unabhaengiger, nicht persistierter Zuordnungszustand einer ZID."""

    rows: dict[int, ObservedScheduleRowTime] = field(default_factory=dict)
    last_observed_original_index: int | None = None
    last_arrival_original_index: int | None = None
    last_event_type: str | None = None
    last_event_planned_track: str | None = None
    last_event_original_index: int | None = None


@dataclass
class TrainRelation:
    relation_type: str
    source_zid: int
    target_zid: int | None = None
    raw_evidence: str | None = None

    def __post_init__(self) -> None:
        if self.relation_type not in RELATION_TYPES:
            raise ValueError(f"Unbekannter Relationstyp: {self.relation_type}")


@dataclass
class DepartureState:
    track: str | None
    status: str = "in_progress"
    started_event: TrainEvent | None = None
    completed_event: TrainEvent | None = None


@dataclass
class SessionMetadata:
    session_id: str = field(default_factory=lambda: str(uuid4()))
    aid: int | None = None
    name: str | None = None
    region: str | None = None
    simbuild: str | None = None
    online: bool | None = None
    raw_xml: str | None = None


@dataclass
class TrainService:
    zid: int
    name: str
    current_delay: int | None = None
    current_track: str | None = None
    planned_track: str | None = None
    visible: bool | None = None
    at_track: bool | None = None
    origin: str | None = None
    destination: str | None = None
    status: str = "known"
    first_seen_simtime: int | None = None
    last_seen_simtime: int | None = None
    discovery_source: str = "unknown"
    discovered_simtime: int | None = None
    schedule_start_completeness: str = "unknown"
    schedule_end_completeness: str = "likely_complete"
    provenance_evidence: tuple[str, ...] = ()
    original_schedule: list[SchedulePoint] = field(default_factory=list)
    current_schedule: list[SchedulePoint] = field(default_factory=list)
    raw_events: list[TrainEvent] = field(default_factory=list)
    interpreted_events: list[TrainEvent] = field(default_factory=list)
    relations: list[TrainRelation] = field(default_factory=list)
    raw_details: list[str] = field(default_factory=list)
    raw_schedules: list[str] = field(default_factory=list)
    initialized: bool = False
    temporary_locomotive: bool = False
    service_kind: str = "unknown"
    departure_states: dict[str, DepartureState] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.service_kind not in SERVICE_KINDS:
            raise ValueError(f"Unbekannte Service-Kategorie: {self.service_kind}")


@dataclass
class TrainFamily:
    family_id: str
    service_zids: list[int] = field(default_factory=list)
    relations: list[TrainRelation] = field(default_factory=list)


class LocationResolver:
    """Korrigierbare Mapping-Schicht; ohne Mapping wird nichts erraten."""

    def __init__(self, mappings: dict[str, dict[str, Any]] | None = None) -> None:
        self.mappings = mappings or {}

    def resolve(self, raw_name: str) -> Location:
        values = self.mappings.get(raw_name, {})
        return Location(raw_name=raw_name, **values)


class STSLiveCollector:
    """Verarbeitet komplette Protokollelemente und liefert Folgekommandos.

    Netzwerkcode darf die von :meth:`process` gelieferten XML-Kommandos senden.
    Dadurch bleibt der Collector unabhaengig von Socket, Empfangsthread und GUI.
    """

    def __init__(self, storage_path: str | Path | None = None, resolver: LocationResolver | None = None,
                 clock: Callable[[], datetime] | None = None) -> None:
        self.storage_path = Path(storage_path) if storage_path else None
        self.resolver = resolver or LocationResolver()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.services: dict[int, TrainService] = {}
        self.families: dict[str, TrainFamily] = {}
        self.raw_xml: list[str] = []
        self.session = SessionMetadata()
        self.simtime: int | None = None
        self._last_train_list_simtime: int | None = None
        self._last_schedule_slot: tuple[int, int] | None = None
        self._sim_day = 0
        self._previous_simtime: int | None = None
        self._active_zids: set[int] = set()
        self._initial_train_list_requested = False
        self._has_received_train_list = False
        self._signal_stop_open: set[tuple[int, str | None]] = set()
        # Bewusst ausserhalb von TrainService: dieser diagnostische Zustand
        # darf niemals in Collector-State-Dateien oder die Projektion gelangen.
        self.observed_train_times: dict[int, ObservedTrainTimes] = {}
        self.messages: list[str] = []
        if self.storage_path and self.storage_path.exists():
            self.load()

    def startup_commands(self, sender: str = "sts_collector") -> list[str]:
        """Fordert jede Startressource genau einmal an.

        Die erste Simzeit-Antwort darf deshalb keine zweite Zugliste ausloesen.
        """
        self._initial_train_list_requested = True
        self._has_received_train_list = False
        return [
            "<anlageninfo />", f'<simzeit sender="{sender}" />',
            "<bahnsteigliste />", "<wege />", "<zugliste />",
        ]

    def drain_messages(self) -> list[str]:
        messages, self.messages = self.messages, []
        return messages

    def process(self, element: ET.Element, raw_xml: str | None = None) -> list[str]:
        raw = raw_xml if raw_xml is not None else ET.tostring(element, encoding="unicode")
        self.raw_xml.append(raw)
        commands: list[str] = []
        if element.tag == "simzeit":
            commands.extend(self._process_simtime(element))
        elif element.tag == "anlageninfo":
            self._process_facility(element, raw)
        elif element.tag == "zugliste":
            commands.extend(self._process_train_list(element))
        elif element.tag == "zugdetails":
            self._process_details(element, raw)
        elif element.tag == "zugfahrplan":
            self._process_schedule(element, raw)
        elif element.tag == "ereignis" and element.get("zid") is not None:
            self._process_event(element, raw)
        self.save()
        return commands

    def _process_simtime(self, element: ET.Element) -> list[str]:
        value = _int(element.get("zeit"))
        if value is None:
            return []
        if self._previous_simtime is not None and value < self._previous_simtime:
            self._sim_day += 1
        previous = self._previous_simtime
        self._previous_simtime = value
        self.simtime = value
        commands: list[str] = []
        slot = self._schedule_slot(value)
        crossed_slot = previous is not None and self._schedule_slot(previous) != slot
        exactly_on_slot = (value // 1000) % 20 == 10
        slot_key = (self._sim_day, slot)
        if (exactly_on_slot or crossed_slot) and slot_key != self._last_schedule_slot:
            self._last_schedule_slot = slot_key
            refresh_zids = [zid for zid in sorted(self._active_zids) if self._refresh_relevant(self.services[zid])]
            commands.extend(f'<zugfahrplan zid="{zid}" />' for zid in refresh_zids)
            if refresh_zids:
                self.messages.append(
                    f"Schedule-Refresh {self._format_simtime(value)}\n{len(refresh_zids)} aktive Services"
                )
        if self._last_train_list_simtime is None:
            if self._has_received_train_list:
                # Die initiale Zugliste kann vor ihrer zuvor angeforderten
                # Simzeitantwort eintreffen. Diese erste Zeit ist dann nur die
                # Basis fuer den Zwei-Minuten-Takt, kein neuer Initialrequest.
                self._last_train_list_simtime = value
            elif not self._initial_train_list_requested:
                self._last_train_list_simtime = value
                commands.append("<zugliste />")
        elif self._elapsed(self._last_train_list_simtime, value) >= 120_000:
            self._last_train_list_simtime = value
            commands.append("<zugliste />")
        return commands

    @staticmethod
    def _schedule_slot(value: int) -> int:
        """Index des letzten Slots 10/30/50 innerhalb des Simulationstags."""
        seconds = value // 1000
        return (seconds - 10) // 20

    @staticmethod
    def _format_simtime(value: int | None) -> str:
        if value is None:
            return "unbekannt"
        seconds = value // 1000
        return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"

    @staticmethod
    def _refresh_relevant(service: TrainService) -> bool:
        return service.zid > 0 and service.service_kind != "locomotive_movement"

    @staticmethod
    def _elapsed(previous: int, current: int) -> int:
        day = 24 * 60 * 60 * 1000
        return current - previous if current >= previous else current + day - previous

    def _process_train_list(self, element: ET.Element) -> list[str]:
        discovery_source = "initial_train_list" if not self._has_received_train_list else "periodic_train_list"
        active: set[int] = set()
        commands: list[str] = []
        for item in element.iter("zug"):
            zid = _int(item.get("zid"))
            if zid is None:
                continue
            active.add(zid)
            name = item.get("name") or item.get("zugname") or item.get("nummer") or "Unbenannter Zug"
            service = self.services.get(zid)
            if service is None:
                kind = self._classify_service(zid, name)
                temporary = kind == "locomotive_movement"
                service = self.services[zid] = TrainService(
                    zid=zid, name=name, first_seen_simtime=self.simtime,
                    discovery_source=discovery_source, discovered_simtime=self.simtime,
                    schedule_start_completeness=("possibly_truncated_at_startup"
                                                 if discovery_source == "initial_train_list"
                                                 else "likely_complete"),
                    schedule_end_completeness="likely_complete",
                    provenance_evidence=(discovery_source,),
                    temporary_locomotive=temporary, status="temporary_locomotive" if temporary else "active",
                    service_kind=kind,
                )
            service.name = name
            service.service_kind = self._classify_service(zid, name)
            service.temporary_locomotive = service.service_kind == "locomotive_movement"
            service.last_seen_simtime = self.simtime
            if not service.temporary_locomotive:
                service.status = "active"
                if not service.initialized:
                    service.initialized = True
                    commands.extend((f'<zugdetails zid="{zid}" />', f'<zugfahrplan zid="{zid}" />'))
                    commands.extend(f'<ereignis zid="{zid}" art="{art}" />' for art in EVENT_TYPES)
        for zid, service in self.services.items():
            if zid not in active and service.status == "active":
                service.status = "inactive_unknown"
        self._active_zids = active
        self._has_received_train_list = True
        if self.simtime is not None:
            self._last_train_list_simtime = self.simtime
        self._initial_train_list_requested = False
        return commands

    @staticmethod
    def _classify_service(zid: int, name: str) -> str:
        if zid < 0 or name.startswith("Lok "):
            return "locomotive_movement"
        if name.startswith("Wagen "):
            return "wagon_set"
        return "train" if name.strip() else "unknown"

    def _service(self, element: ET.Element) -> TrainService | None:
        zid = _int(element.get("zid"))
        if zid is None:
            return None
        if zid not in self.services:
            name = element.get("name") or "Unbenannter Zug"
            kind = self._classify_service(zid, name)
            self.services[zid] = TrainService(zid=zid, name=name, first_seen_simtime=self.simtime,
                                              discovery_source="related_service",
                                              discovered_simtime=self.simtime,
                                              schedule_start_completeness="unknown",
                                              schedule_end_completeness="likely_complete",
                                              provenance_evidence=("related_service",),
                                              temporary_locomotive=kind == "locomotive_movement", service_kind=kind)
        return self.services[zid]

    def _update_state(self, service: TrainService, element: ET.Element) -> None:
        service.name = element.get("name") or service.name
        service.current_delay = _int(element.get("verspaetung"))
        service.current_track = element.get("gleis")
        service.planned_track = element.get("plangleis")
        service.visible = _bool(element.get("sichtbar"))
        service.at_track = _bool(element.get("amgleis"))
        service.origin = element.get("von")
        service.destination = element.get("nach")
        service.last_seen_simtime = self.simtime

    def _process_details(self, element: ET.Element, raw: str) -> None:
        service = self._service(element)
        if service:
            self._update_state(service, element)
            service.raw_details.append(raw)

    def _process_schedule(self, element: ET.Element, raw: str) -> None:
        service = self._service(element)
        if not service:
            return
        points: list[SchedulePoint] = []
        for item in element.findall("gleis"):
            current = item.get("name", "")
            planned = item.get("plan", current)
            location = self.resolver.resolve(current)
            points.append(SchedulePoint(
                raw_name=current, current_name=current, planned_name=planned,
                planned_arrival=item.get("an"), planned_departure=item.get("ab"), flags_raw=item.get("flags", ""),
                hint_text=item.get("hinweistext"),
                operating_point=location.operating_point, physical_track=location.physical_track,
                track_section=location.track_section, stop_position=location.stop_position,
                track_resolution=location.track_resolution, raw_xml=ET.tostring(item, encoding="unicode"),
            ))
        self._record_track_changes(service, points)
        if not service.original_schedule:
            service.original_schedule = points
        service.current_schedule = points
        service.raw_schedules.append(raw)

    def _record_track_changes(self, service: TrainService, points: list[SchedulePoint]) -> None:
        previous = {(p.planned_name, p.planned_arrival, p.planned_departure): p for p in service.current_schedule}
        for point in points:
            old = previous.get((point.planned_name, point.planned_arrival, point.planned_departure))
            if old and old.current_name != point.current_name and old.planned_name == point.planned_name:
                self.messages.append(
                    "Gleisänderung erkannt\n"
                    f"{service.name} (ZID {service.zid})\n{old.current_name} → {point.current_name}\n"
                    f"Plan: {point.planned_name}"
                )

    def _process_event(self, element: ET.Element, raw: str) -> None:
        service = self._service(element)
        if not service:
            return
        self._update_state(service, element)
        event = TrainEvent(
            art=element.get("art", ""), zid=service.zid, train_name=element.get("name"),
            delay=_int(element.get("verspaetung")), track=element.get("gleis"),
            planned_track=element.get("plangleis"), visible=_bool(element.get("sichtbar")),
            at_track=_bool(element.get("amgleis")), origin=element.get("von"), destination=element.get("nach"),
            receive_timestamp=self.clock().isoformat(), simtime=self.simtime, raw_xml=raw,
        )
        service.raw_events.append(event)
        if event.art in {"ankunft", "abfahrt"}:
            self._observe_schedule_time(service, event)
        key = (service.zid, event.track)
        if event.art == "abfahrt":
            self._process_departure(service, event)
            return
        if event.art == "rothalt":
            if key in self._signal_stop_open:
                return
            self._signal_stop_open.add(key)
        elif event.art == "wurdegruen":
            self._signal_stop_open.discard(key)
        elif service.interpreted_events and self._event_identity(service.interpreted_events[-1]) == self._event_identity(event):
            return
        service.interpreted_events.append(event)

    @staticmethod
    def _schedule_identity(point: SchedulePoint) -> tuple[str, str | None, str | None]:
        return point.planned_name, point.planned_arrival, point.planned_departure

    def _remaining_original_indices(self, service: TrainService) -> tuple[int, ...]:
        """Ordnet den Restfahrplan wie die Tabellenansicht reihenfolgestabil zu."""
        original = tuple(map(self._schedule_identity, service.original_schedule))
        current = tuple(map(self._schedule_identity, service.current_schedule))
        if not current:
            return ()
        solutions: list[tuple[int, ...]] = []

        def match(current_index: int, start: int, chosen: tuple[int, ...]) -> None:
            if current_index == len(current):
                solutions.append(chosen)
                return
            for index in range(start, len(original)):
                if original[index] == current[current_index]:
                    match(current_index + 1, index + 1, (*chosen, index))

        match(0, 0, ())
        return max(solutions, key=lambda value: value[0]) if solutions else ()

    @staticmethod
    def _is_d_point(point: SchedulePoint) -> bool:
        return "D" in _FLAG_TOKEN.findall(point.flags_raw or "")

    def _observe_schedule_time(self, service: TrainService, event: TrainEvent) -> None:
        """Matcht echte Ankunft/Abfahrt konservativ nur per exaktem ``plangleis``."""
        state = self.observed_train_times.setdefault(service.zid, ObservedTrainTimes())
        planned = event.planned_track
        candidates = ([index for index, point in enumerate(service.original_schedule)
                       if point.planned_name == planned] if planned else [])
        usable = [index for index in candidates if not self._is_d_point(service.original_schedule[index])]
        selected: int | None = None
        reason = "missing_plangleis" if not planned else "no_candidate"

        if planned and candidates and not usable:
            reason = "d_point"
        elif usable:
            remaining = self._remaining_original_indices(service)
            remaining_matches = [index for index in remaining if index in usable]
            current_has_later = bool(
                state.last_event_original_index is not None
                and remaining_matches
                and remaining_matches[0] > state.last_event_original_index
            )
            if (state.last_event_type == event.art
                    and state.last_event_planned_track == planned
                    and state.last_event_original_index in usable
                    and not current_has_later):
                selected = state.last_event_original_index
                reason = "duplicate"
            elif (event.art == "abfahrt" and state.last_arrival_original_index in usable
                  and state.rows.get(state.last_arrival_original_index, ObservedScheduleRowTime()).actual_departure_minute
                  is None):
                selected = state.last_arrival_original_index
                reason = "matched_same_as_arrival"
            elif len(usable) == 1:
                selected = usable[0]
                reason = "matched_unique"
            else:
                forward = [index for index in usable
                           if state.last_observed_original_index is not None
                           and index > state.last_observed_original_index]
                if remaining_matches:
                    selected = remaining_matches[0]
                    reason = "matched_sequence"
                elif forward:
                    selected = forward[0]
                    reason = "matched_sequence"
                else:
                    reason = "ambiguous"

        changed = False
        if selected is not None and reason != "duplicate":
            row = state.rows.setdefault(selected, ObservedScheduleRowTime())
            attribute = ("actual_arrival_minute" if event.art == "ankunft"
                         else "actual_departure_minute")
            if getattr(row, attribute) is None:
                minute = self._sim_day * 24 * 60 + event.simtime // 60_000 if event.simtime is not None else None
                if minute is not None:
                    setattr(row, attribute, minute)
                    changed = True
                    state.last_observed_original_index = max(
                        selected, state.last_observed_original_index if state.last_observed_original_index is not None else -1)
                    if event.art == "ankunft":
                        state.last_arrival_original_index = selected
                    state.last_event_type = event.art
                    state.last_event_planned_track = planned
                    state.last_event_original_index = selected
            else:
                reason = "duplicate"

        selected_name = (service.original_schedule[selected].planned_name
                         if selected is not None else None)
        LOGGER.debug(
            "observed_train_time zid=%s train_name=%r event_type=%s gleis=%r plangleis=%r "
            "event_simtime=%r candidate_original_indices=%s selected_original_index=%r "
            "selected_planned_name=%r reason=%s changed=%s",
            service.zid, service.name, event.art, event.track, planned, event.simtime,
            candidates, selected, selected_name, reason, changed,
        )

    def _process_departure(self, service: TrainService, event: TrainEvent) -> None:
        key = event.track or ""
        state = service.departure_states.get(key)
        if state is not None and state.status == "completed" and event.at_track is False:
            return
        if state is None or state.status == "completed":
            state = DepartureState(track=event.track, started_event=event)
            service.departure_states[key] = state
            service.interpreted_events.append(event)
        if event.at_track is False:
            state.status = "completed"
            state.completed_event = event
            # Der eine fachliche Datensatz repraesentiert nach Abschluss das
            # tatsaechliche Verlassen, nicht einen vorherigen Heartbeat.
            for index in range(len(service.interpreted_events) - 1, -1, -1):
                candidate = service.interpreted_events[index]
                if candidate.art == "abfahrt" and candidate.track == event.track:
                    service.interpreted_events[index] = event
                    break
            self.messages.append(
                f"Abfahrtsvorgang abgeschlossen\n{service.name}\n{event.track or 'Gleis unbekannt'}\n"
                f"Simzeit: {self._format_simtime(event.simtime)}"
            )

    def _process_facility(self, element: ET.Element, raw: str) -> None:
        aid = _int(element.get("aid"))
        if self.session.aid is not None and aid is not None and self.session.aid != aid:
            old_aid = self.session.aid
            archive = self._archive_current_state(old_aid)
            self.services.clear()
            self.families.clear()
            self.raw_xml = [raw]
            self._active_zids.clear()
            self._last_train_list_simtime = None
            self._last_schedule_slot = None
            self._sim_day = 0
            self._previous_simtime = None
            self._signal_stop_open.clear()
            self.observed_train_times.clear()
            self.messages.append(
                f"Stellwerkwechsel erkannt: AID {old_aid} → {aid}. Alter Zustand archiviert: {archive}"
            )
        self.session = SessionMetadata(
            aid=aid, name=element.get("name"), region=element.get("region"),
            simbuild=element.get("simbuild"), online=_bool(element.get("online")), raw_xml=raw,
        )
        self.messages.append(f"Neue Stellwerk-Session\nAID: {aid}\nName: {self.session.name or 'unbekannt'}")

    def _archive_current_state(self, aid: int) -> str:
        if not self.storage_path or not self.storage_path.exists():
            return "nur im Speicher"
        archive = self.storage_path.with_name(
            f"{self.storage_path.stem}.aid-{aid}.{self.session.session_id}{self.storage_path.suffix}"
        )
        shutil.copy2(self.storage_path, archive)
        return str(archive)

    @staticmethod
    def _event_identity(event: TrainEvent) -> tuple[Any, ...]:
        return (event.art, event.zid, event.track, event.planned_track, event.at_track, event.delay)

    def save(self) -> None:
        if not self.storage_path:
            return
        data = {
            "schema_version": 2, "artifact_type": "collector_state",
            "aid": self.session.aid, "stellwerk_name": self.session.name,
            "stellwerk": {"aid": self.session.aid, "name": self.session.name},
            "saved_at": self.clock().isoformat(), "simtime": self.simtime,
            "last_train_list_simtime": self._last_train_list_simtime,
            "last_schedule_slot": self._last_schedule_slot, "sim_day": self._sim_day,
            "previous_simtime": self._previous_simtime, "active_zids": sorted(self._active_zids),
            "session": asdict(self.session),
            "services": [asdict(item) for item in self.services.values()],
            "families": [asdict(item) for item in self.families.values()], "raw_xml": self.raw_xml,
        }
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.storage_path.with_suffix(self.storage_path.suffix + ".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.storage_path)

    def load(self) -> None:
        data = json.loads(self.storage_path.read_text(encoding="utf-8"))  # type: ignore[union-attr]
        if data.get("schema_version", 1) not in {1, 2}:
            raise ValueError("Nicht unterstuetzte Collector-Dateiversion")
        self.simtime = data.get("simtime")
        self._last_train_list_simtime = data.get("last_train_list_simtime")
        slot = data.get("last_schedule_slot")
        self._last_schedule_slot = tuple(slot) if slot is not None else None
        self._sim_day = data.get("sim_day", 0)
        self._previous_simtime = data.get("previous_simtime")
        self._active_zids = set(data.get("active_zids", []))
        self.session = SessionMetadata(**data.get("session", {}))
        self.raw_xml = data.get("raw_xml", [])
        for raw_service in data.get("services", []):
            raw_service["original_schedule"] = [SchedulePoint(**p) for p in raw_service.get("original_schedule", [])]
            raw_service["current_schedule"] = [SchedulePoint(**p) for p in raw_service.get("current_schedule", [])]
            raw_service["raw_events"] = [TrainEvent(**e) for e in raw_service["raw_events"]]
            raw_service["interpreted_events"] = [TrainEvent(**e) for e in raw_service["interpreted_events"]]
            raw_service["relations"] = [TrainRelation(**r) for r in raw_service["relations"]]
            name, zid = raw_service.get("name", ""), raw_service["zid"]
            raw_service.setdefault("service_kind", self._classify_service(zid, name))
            raw_service["departure_states"] = {
                key: DepartureState(
                    **{**state,
                       "started_event": TrainEvent(**state["started_event"]) if state.get("started_event") else None,
                       "completed_event": TrainEvent(**state["completed_event"]) if state.get("completed_event") else None}
                ) for key, state in raw_service.get("departure_states", {}).items()
            }
            service = TrainService(**raw_service)
            self.services[service.zid] = service
        for raw_family in data.get("families", []):
            raw_family["relations"] = [TrainRelation(**r) for r in raw_family["relations"]]
            family = TrainFamily(**raw_family)
            self.families[family.family_id] = family
