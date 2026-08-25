"""Thread-sichere Adapter-Schicht zwischen stabilem Collector und Qt."""

from __future__ import annotations

import copy
import queue
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from .simtime import SimTimeInterpolator

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DIAGNOSTIC_DIR = REPOSITORY_ROOT / "Schnittstellentest"
if str(DIAGNOSTIC_DIR) not in sys.path:
    sys.path.insert(0, str(DIAGNOSTIC_DIR))

from sts_collector import STSLiveCollector  # noqa: E402
from sts_tester import ClientEvent, StellwerkSimClient, StellwerkSimProtocolParser  # noqa: E402


@dataclass(frozen=True)
class CollectorSnapshot:
    connected: bool
    status: str
    simtime: int | None
    sim_day: int
    facility_name: str | None
    aid: int | None
    services: tuple[object, ...]
    infrastructure_documents: tuple[str, ...]
    display_simtime: float | None
    display_simtime_running: bool


class CollectorAdapter:
    """Besitzt Collector und Netzwerkworker; Qt liest nur unveraenderliche Snapshots."""

    def __init__(self, state_path: str | Path, offline: bool = False) -> None:
        self._lock = threading.RLock()
        self.collector = STSLiveCollector(state_path)
        self.offline = offline
        self.status = "Offline-State geladen" if offline else "Bereit"
        self._events: queue.Queue[ClientEvent | None] = queue.Queue()
        self._client = StellwerkSimClient(self._events.put)
        self._parser = StellwerkSimProtocolParser()
        self._worker: threading.Thread | None = None
        self._poller: threading.Thread | None = None
        self._stop = threading.Event()
        self._display_clock = SimTimeInterpolator()
        self._last_display_sync: tuple[int, int] | None = None

    def start(self, host: str = "127.0.0.1", port: int = 3691) -> None:
        if self.offline:
            return
        self._stop.clear()
        self._worker = threading.Thread(target=self._run, name="Bildfahrplan-Collector", daemon=True)
        self._worker.start()
        threading.Thread(target=self._connect, args=(host, port), name="STS-Verbindung", daemon=True).start()

    def _connect(self, host: str, port: int) -> None:
        try:
            self._client.connect(host, port)
        except (OSError, RuntimeError) as exc:
            self._events.put(ClientEvent("connect_error", str(exc)))
            return
        self._events.put(ClientEvent("connected", f"{host}:{port}"))

    def _run(self) -> None:
        while not self._stop.is_set():
            event = self._events.get()
            if event is None:
                return
            if event.kind == "connected":
                with self._lock:
                    self.status = "Collector aktiv"
                self._send('<register name="StellwerkSim Bildfahrplan" autor="StellwerkSimBildfahrplan" '
                           'version="0.3.1" protokoll="1" text="Live-Bildfahrplan" />')
                for command in self.collector.startup_commands("bildfahrplan"):
                    self._send(command)
                self._poller = threading.Thread(target=self._poll_simtime, name="STS-Simzeit", daemon=True)
                self._poller.start()
            elif event.kind == "received":
                result = self._parser.feed_line(bytes(event.data))
                if result.state == "complete" and result.element is not None:
                    raw = (result.raw_document or b"").decode("utf-8", errors="backslashreplace")
                    with self._lock:
                        commands = self.collector.process(result.element, raw)
                    for command in commands:
                        self._send(command)
            elif event.kind in {"closed", "error", "connect_error"}:
                with self._lock:
                    self.status = f"Verbindung beendet: {event.data}"

    def _send(self, command: str) -> None:
        try:
            self._client.send_xml(command)
        except (ConnectionError, OSError):
            return
        time.sleep(0.05)

    def _poll_simtime(self) -> None:
        while not self._stop.wait(5):
            self._send('<simzeit sender="bildfahrplan" />')

    def snapshot(self) -> CollectorSnapshot:
        with self._lock:
            sync = ((self.collector.simtime, self.collector._sim_day)
                    if self.collector.simtime is not None else None)
            if sync is not None and sync != self._last_display_sync:
                self._display_clock.synchronize(*sync)
                self._last_display_sync = sync
            connected = self._client.connected
            display_simtime, running = self._display_clock.value(connected)
            return CollectorSnapshot(
                connected=connected, status=self.status, simtime=self.collector.simtime,
                sim_day=self.collector._sim_day, facility_name=self.collector.session.name,
                aid=self.collector.session.aid, services=tuple(copy.deepcopy(list(self.collector.services.values()))),
                infrastructure_documents=tuple(
                    raw for raw in self.collector.raw_xml
                    if raw.lstrip().startswith(("<wege", "<bahnsteigliste"))
                ),
                display_simtime=display_simtime, display_simtime_running=running,
            )

    def close(self) -> None:
        self._stop.set()
        self._client.disconnect()
        self._events.put(None)
