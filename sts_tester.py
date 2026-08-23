"""Kleines Diagnosewerkzeug fuer die StellwerkSim-Plugin-Schnittstelle."""

from __future__ import annotations

import queue
import re
import socket
import threading
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 3691
ENCODING = "utf-8"


class LineXMLFramer:
    """Teilt den TCP-Byte-Strom in die laut Protokoll zeilenweisen XML-Pakete."""

    def __init__(self) -> None:
        self.buffer = bytearray()

    def feed(self, data: bytes) -> list[bytes]:
        self.buffer.extend(data)
        messages: list[bytes] = []
        while True:
            newline = self.buffer.find(b"\n")
            if newline < 0:
                break
            message = bytes(self.buffer[:newline])
            del self.buffer[: newline + 1]
            if message.endswith(b"\r"):
                message = message[:-1]
            if message.strip():
                messages.append(message)
        return messages

    def remainder(self) -> bytes:
        return bytes(self.buffer)


@dataclass(frozen=True)
class TrainInfo:
    name: str
    zid: str


@dataclass(frozen=True)
class ProtocolParseResult:
    """Ergebnis einer Protokollzeile; ``element`` gibt es erst am Containerende."""

    state: str
    element: ET.Element | None = None
    error: str | None = None


class StellwerkSimProtocolParser:
    """Setzt zeilenweise uebertragene XML-Container generisch zusammen.

    StellwerkSim terminiert Protokollzeilen mit LF, aber eine Antwort kann aus
    einem offenen Container, vielen Kindzeilen und einer Schlusszeile bestehen.
    ElementTree darf daher erst das zusammengesetzte Dokument erhalten.
    """

    _OPENING_TAG = re.compile(rb"^\s*<([A-Za-z_][\w.:-]*)(?:\s[^<>]*)?>\s*$")
    _CLOSING_TAG = re.compile(rb"^\s*</([A-Za-z_][\w.:-]*)\s*>\s*$")

    def __init__(self) -> None:
        self._container_lines: list[bytes] = []
        self._container_tag: bytes | None = None
        self.trains: list[TrainInfo] = []

    @property
    def in_container(self) -> bool:
        return bool(self._container_lines)

    def reset(self) -> None:
        """Verwirft nur eine unvollstaendige Antwort, etwa nach einem Reconnect."""
        self._container_lines.clear()
        self._container_tag = None

    def feed_line(self, raw: bytes) -> ProtocolParseResult:
        if self._container_lines:
            self._container_lines.append(raw)
            document = b"\n".join(self._container_lines)
            try:
                element = ET.fromstring(document)
            except (ET.ParseError, ValueError) as exc:
                # Solange kein End-Tag vorliegt, ist ein unvollstaendiges
                # Dokument der erwartete Zustand und kein Parserfehler.
                closing = self._CLOSING_TAG.match(raw)
                if closing is None or closing.group(1) != self._container_tag:
                    return ProtocolParseResult("pending")
                self._container_lines.clear()
                self._container_tag = None
                return ProtocolParseResult("error", error=str(exc))
            self._container_lines.clear()
            self._container_tag = None
            self._store_response_data(element)
            return ProtocolParseResult("complete", element=element)

        try:
            element = ET.fromstring(raw)
        except (ET.ParseError, ValueError) as exc:
            # Eine syntaktisch gueltige, nicht selbstschliessende Startzeile
            # beginnt eine mehrzeilige Antwort (zugliste, wege, ...).
            opening = self._OPENING_TAG.match(raw)
            if opening and not raw.rstrip().endswith(b"/>"):
                self._container_lines.append(raw)
                self._container_tag = opening.group(1)
                return ProtocolParseResult("pending")
            return ProtocolParseResult("error", error=str(exc))
        self._store_response_data(element)
        return ProtocolParseResult("complete", element=element)

    def _store_response_data(self, element: ET.Element) -> None:
        if element.tag != "zugliste":
            return
        trains: list[TrainInfo] = []
        for train in element.iter("zug"):
            zid = train.get("zid")
            if not zid:
                continue
            name = train.get("name") or train.get("zugname") or train.get("nummer") or "Unbenannter Zug"
            trains.append(TrainInfo(name=name, zid=zid))
        # Erst die abgeschlossene Antwort ersetzt die bisherige Auswahl.
        self.trains = trains


@dataclass(frozen=True)
class ClientEvent:
    kind: str
    data: object = None


class StellwerkSimClient:
    """Thread-sicherer, von tkinter unabhaengiger TCP-Client."""

    def __init__(self, event_callback: Callable[[ClientEvent], None]) -> None:
        self.event_callback = event_callback
        self._socket: socket.socket | None = None
        self._send_lock = threading.Lock()
        self._stop = threading.Event()
        self._generation = 0

    @property
    def connected(self) -> bool:
        return self._socket is not None and not self._stop.is_set()

    def connect(self, host: str, port: int) -> None:
        if self.connected:
            raise RuntimeError("Es besteht bereits eine Verbindung.")
        sock = socket.create_connection((host, port), timeout=8)
        sock.settimeout(1.0)
        self._socket = sock
        self._stop.clear()
        self._generation += 1
        generation = self._generation
        threading.Thread(
            target=self.receive_loop,
            args=(sock, generation),
            name="StellwerkSim-Empfang",
            daemon=True,
        ).start()

    def disconnect(self) -> None:
        self._stop.set()
        sock, self._socket = self._socket, None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()

    def send_xml(self, xml_text: str) -> None:
        """Sendet genau den Text plus die fuer das Protokoll notwendige LF-Grenze."""
        sock = self._socket
        if sock is None or self._stop.is_set():
            raise ConnectionError("Nicht mit StellwerkSim verbunden.")
        # Vor dem Senden pruefen, aber die Originalschreibweise nicht veraendern.
        ET.fromstring(xml_text)
        payload = xml_text.encode(ENCODING) + b"\n"
        with self._send_lock:
            sock.sendall(payload)

    def receive_loop(self, sock: socket.socket, generation: int) -> None:
        framer = LineXMLFramer()
        try:
            while not self._stop.is_set() and generation == self._generation:
                try:
                    data = sock.recv(65536)
                except socket.timeout:
                    continue
                if not data:
                    rest = framer.remainder()
                    if rest:
                        self.event_callback(ClientEvent("raw_incomplete", rest))
                    self.event_callback(ClientEvent("closed", "Gegenstelle hat die Verbindung geschlossen."))
                    return
                for raw_message in framer.feed(data):
                    self.event_callback(ClientEvent("received", raw_message))
        except OSError as exc:
            if not self._stop.is_set():
                self.event_callback(ClientEvent("error", f"Netzwerkfehler beim Empfang: {exc}"))
        finally:
            if generation == self._generation:
                self._stop.set()
                self._socket = None


class StellwerkSimTesterGUI:
    EVENT_TYPES = (
        ("Einfahrt", "einfahrt"),
        ("Ausfahrt", "ausfahrt"),
        ("Ankunft", "ankunft"),
        ("Abfahrt", "abfahrt"),
        ("Rothalt", "rothalt"),
        ("Wurde gruen", "wurdegruen"),
        ("Kuppeln", "kuppeln"),
        ("Fluegeln", "fluegeln"),
    )

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("StellwerkSim Schnittstellen-Tester")
        self.root.geometry("1100x820")
        self.events: queue.Queue[ClientEvent] = queue.Queue()
        self.client = StellwerkSimClient(self.events.put)
        self.status_var = tk.StringVar(value="Nicht verbunden")
        self.host_var = tk.StringVar(value=DEFAULT_HOST)
        self.port_var = tk.StringVar(value=str(DEFAULT_PORT))
        self.zid_var = tk.StringVar()
        self.train_var = tk.StringVar()
        self.train_ids: dict[str, str] = {}
        self.protocol_parser = StellwerkSimProtocolParser()
        self.event_vars: dict[str, tk.BooleanVar] = {}
        self._build_gui()
        self.root.after(80, self._process_events)
        self.root.protocol("WM_DELETE_WINDOW", self._close)

    def _build_gui(self) -> None:
        outer = ttk.Frame(self.root, padding=8)
        outer.pack(fill=tk.BOTH, expand=True)

        connection = ttk.LabelFrame(outer, text="1. Verbindung", padding=6)
        connection.pack(fill=tk.X)
        ttk.Label(connection, text="Host:").pack(side=tk.LEFT)
        ttk.Entry(connection, textvariable=self.host_var, width=18).pack(side=tk.LEFT, padx=(3, 10))
        ttk.Label(connection, text="Port:").pack(side=tk.LEFT)
        ttk.Entry(connection, textvariable=self.port_var, width=7).pack(side=tk.LEFT, padx=(3, 10))
        ttk.Button(connection, text="Verbinden", command=self.connect).pack(side=tk.LEFT, padx=2)
        ttk.Button(connection, text="Trennen", command=self.disconnect).pack(side=tk.LEFT, padx=2)
        ttk.Label(connection, textvariable=self.status_var).pack(side=tk.LEFT, padx=14)

        commands = ttk.LabelFrame(outer, text="2. Registrierung und allgemeine Abfragen", padding=6)
        commands.pack(fill=tk.X, pady=(6, 0))
        ttk.Button(commands, text="Plugin registrieren", command=self.register).pack(side=tk.LEFT, padx=2)
        for label, command in (
            ("Anlageninfo", "<anlageninfo />"),
            ("Simulationszeit", '<simzeit sender="sts_tester" />'),
            ("Zugliste", "<zugliste />"),
            ("Bahnsteigliste", "<bahnsteigliste />"),
            ("Wege / Fahrwege", "<wege />"),
        ):
            ttk.Button(commands, text=label, command=lambda value=command: self.send(value)).pack(side=tk.LEFT, padx=2)

        train = ttk.LabelFrame(outer, text="3. Zugbezogene Abfragen", padding=6)
        train.pack(fill=tk.X, pady=(6, 0))
        ttk.Label(train, text="ZID:").pack(side=tk.LEFT)
        ttk.Entry(train, textvariable=self.zid_var, width=12).pack(side=tk.LEFT, padx=4)
        ttk.Button(train, text="Zugdetails", command=lambda: self.send_for_train("zugdetails")).pack(side=tk.LEFT, padx=2)
        ttk.Button(train, text="Zugfahrplan", command=lambda: self.send_for_train("zugfahrplan")).pack(side=tk.LEFT, padx=2)
        ttk.Label(train, text="Zug aus Zugliste:").pack(side=tk.LEFT, padx=(15, 3))
        self.train_combo = ttk.Combobox(train, textvariable=self.train_var, state="readonly", width=35)
        self.train_combo.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.train_combo.bind("<<ComboboxSelected>>", self._select_train)

        event_box = ttk.LabelFrame(outer, text="4. Ereignisse (gelten fuer die eingetragene ZID)", padding=6)
        event_box.pack(fill=tk.X, pady=(6, 0))
        for label, event_type in self.EVENT_TYPES:
            variable = tk.BooleanVar()
            self.event_vars[event_type] = variable
            ttk.Checkbutton(event_box, text=label, variable=variable).pack(side=tk.LEFT, padx=3)
        ttk.Button(event_box, text="Ausgewählte abonnieren", command=self.subscribe_events).pack(side=tk.RIGHT, padx=2)

        raw = ttk.LabelFrame(outer, text="5. Eigenes XML senden", padding=6)
        raw.pack(fill=tk.X, pady=(6, 0))
        self.raw_input = ScrolledText(raw, height=4, wrap=tk.NONE)
        self.raw_input.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.raw_input.insert("1.0", "<zugliste />")
        ttk.Button(raw, text="XML senden", command=self.send_raw).pack(side=tk.LEFT, padx=7)

        log_header = ttk.Frame(outer)
        log_header.pack(fill=tk.X, pady=(6, 2))
        ttk.Label(log_header, text="6. Kommunikation / Debug-Log").pack(side=tk.LEFT)
        ttk.Button(log_header, text="Log speichern", command=self.save_log).pack(side=tk.RIGHT, padx=2)
        ttk.Button(log_header, text="Log leeren", command=self.clear_log).pack(side=tk.RIGHT, padx=2)
        self.log = ScrolledText(outer, wrap=tk.NONE, state=tk.DISABLED, font=("Consolas", 10))
        self.log.pack(fill=tk.BOTH, expand=True)

    def _log(self, category: str, text: str) -> None:
        self.log.configure(state=tk.NORMAL)
        self.log.insert(tk.END, f"[{datetime.now():%H:%M:%S}] {category}\n{text}\n\n")
        self.log.see(tk.END)
        self.log.configure(state=tk.DISABLED)

    def connect(self) -> None:
        host = self.host_var.get().strip()
        try:
            port = int(self.port_var.get())
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            self._log("FEHLER", "Der Port muss eine Zahl zwischen 1 und 65535 sein.")
            return
        self.status_var.set("Verbindung wird aufgebaut …")
        threading.Thread(target=self._connect_worker, args=(host, port), daemon=True).start()

    def _connect_worker(self, host: str, port: int) -> None:
        try:
            self.client.connect(host, port)
        except (OSError, RuntimeError) as exc:
            self.events.put(ClientEvent("connect_error", f"Verbindung zu {host}:{port} fehlgeschlagen: {exc}"))
        else:
            self.events.put(ClientEvent("connected", f"Verbunden mit {host}:{port}"))

    def disconnect(self) -> None:
        was_connected = self.client.connected
        self.client.disconnect()
        self.status_var.set("Nicht verbunden")
        if was_connected:
            self._log("VERBINDUNG", "Verbindung getrennt.")

    def register(self) -> None:
        self.send(
            '<register name="STS Schnittstellen Tester" autor="Test" version="0.1" '
            'protokoll="1" text="Testprogramm für die StellwerkSim Plugin-Schnittstelle" />'
        )

    def send(self, xml_text: str) -> bool:
        try:
            self.client.send_xml(xml_text)
        except ET.ParseError as exc:
            self._log("FEHLER – UNGÜLTIGES XML", str(exc))
            return False
        except (ConnectionError, OSError) as exc:
            self._log("FEHLER – SENDEN", str(exc))
            return False
        self._log("SEND >> (RAW, UTF-8; LF folgt)", xml_text)
        return True

    def send_raw(self) -> None:
        value = self.raw_input.get("1.0", "end-1c")
        if not value.strip():
            self._log("FEHLER", "Das XML-Eingabefeld ist leer.")
            return
        self.send(value)

    def _validated_zid(self) -> str | None:
        zid = self.zid_var.get().strip()
        if not zid:
            self._log("FEHLER – UNGÜLTIGE ZID", "Bitte eine ZID eingeben oder einen Zug auswählen.")
            return None
        if not zid.isdecimal():
            self._log("FEHLER – UNGÜLTIGE ZID", "Die ZID muss aus Ziffern bestehen.")
            return None
        return zid

    def send_for_train(self, command: str) -> None:
        if zid := self._validated_zid():
            self.send(f'<{command} zid="{zid}" />')

    def subscribe_events(self) -> None:
        zid = self._validated_zid()
        if not zid:
            return
        selected = [name for name, variable in self.event_vars.items() if variable.get()]
        if not selected:
            self._log("FEHLER", "Bitte mindestens einen Ereignistyp auswählen.")
            return
        for event_type in selected:
            self.send(f'<ereignis zid="{zid}" art="{event_type}" />')

    def _select_train(self, _event: object = None) -> None:
        if zid := self.train_ids.get(self.train_var.get()):
            self.zid_var.set(zid)

    def _process_events(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                self._handle_event(event)
        except queue.Empty:
            pass
        self.root.after(80, self._process_events)

    def _handle_event(self, event: ClientEvent) -> None:
        if event.kind == "connected":
            self.protocol_parser.reset()
            self.status_var.set("Verbunden")
            self._log("VERBINDUNG", str(event.data))
        elif event.kind in {"closed", "error", "connect_error"}:
            self.status_var.set(str(event.data) if event.kind != "closed" else "Nicht verbunden")
            self._log("VERBINDUNG / FEHLER", str(event.data))
        elif event.kind == "raw_incomplete":
            raw = bytes(event.data)
            self._log("RAW RECEIVE – UNVOLLSTÄNDIG", self._decode(raw))
        elif event.kind == "received":
            self._handle_received(bytes(event.data))

    @staticmethod
    def _decode(raw: bytes) -> str:
        # backslashreplace bewahrt auch bei fehlerhafter UTF-8-Kodierung jedes Byte sichtbar.
        return raw.decode(ENCODING, errors="backslashreplace")

    def _handle_received(self, raw: bytes) -> None:
        text = self._decode(raw)
        self._log(f"RECEIVE << RAW ({len(raw)} Bytes)", text)
        result = self.protocol_parser.feed_line(raw)
        if result.state == "pending":
            self._log("XML-CONTAINER", "Mehrzeilige Antwort wird gesammelt.")
            return
        if result.state == "error":
            self._log("PARSE-FEHLER", f"{result.error}\nHexdump: {raw.hex(' ')}")
            return
        element = result.element
        assert element is not None
        parsed_lines = [f"Tag: {element.tag}", f"Attribute: {element.attrib!r}"]
        try:
            ET.indent(element, space="  ")
            parsed_lines.extend(("Formatiertes XML:", ET.tostring(element, encoding="unicode")))
        except (TypeError, ValueError) as exc:
            parsed_lines.append(f"Formatierung fehlgeschlagen: {exc}")
        self._log("PARSED RECEIVE", "\n".join(parsed_lines))
        if element.tag == "status":
            code = element.get("code", "ohne Code")
            message = "".join(element.itertext()).strip() or "Keine Beschreibung übermittelt."
            try:
                is_error = int(code) >= 400
            except ValueError:
                is_error = False
            category = "SERVER-FEHLER" if is_error else "SERVER-STATUS"
            self._log(category, f"Status {code}: {message}")
        if element.tag == "zugliste":
            self._update_trains()

    def _update_trains(self) -> None:
        found: dict[str, str] = {}
        for train in self.protocol_parser.trains:
            label = f"{train.name} — ZID {train.zid}"
            if label in found:
                label = f"{label} ({len(found) + 1})"
            found[label] = train.zid
        self.train_ids = found
        self.train_combo["values"] = list(found)
        if found:
            self._log("KOMFORTFUNKTION", f"{len(found)} Züge für die Auswahlliste erkannt.")

    def clear_log(self) -> None:
        """Leert nur das sichtbare Log, auch wenn das Widget gesperrt ist."""
        self.log.configure(state=tk.NORMAL)
        self.log.delete("1.0", tk.END)
        self.log.configure(state=tk.DISABLED)

    def save_log(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Kommunikationslog speichern",
            defaultextension=".txt",
            filetypes=(("Textdateien", "*.txt"), ("Alle Dateien", "*.*")),
        )
        if not path:
            return
        try:
            Path(path).write_text(self.log.get("1.0", "end-1c"), encoding="utf-8")
        except OSError as exc:
            messagebox.showerror("Speichern fehlgeschlagen", str(exc), parent=self.root)
        else:
            self._log("LOG", f"Log gespeichert: {path}")

    def _close(self) -> None:
        self.client.disconnect()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    StellwerkSimTesterGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
