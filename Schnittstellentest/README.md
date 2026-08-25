# StellwerkSim Schnittstellen-Tester

Ein bewusst kleines Windows-Diagnoseprogramm für die XML-Plugin-Schnittstelle von
StellwerkSim. Es zeigt gesendete und empfangene Daten an, erstellt aber **keinen**
Bildfahrplan.

## Voraussetzungen und Start

- Python 3.9 oder neuer (empfohlen: aktuelles Python 3 für Windows)
- `tkinter` (in der normalen Windows-Python-Installation enthalten)
- Ein laufendes StellwerkSim mit verfügbarer Plugin-Schnittstelle

Es sind keine Python-Pakete von Drittanbietern erforderlich. Start im Projektordner:

```bash
python sts_tester.py
```

Unter Windows startet ein Doppelklick auf `sts_tester.pyw` dieselbe GUI ueber
`pythonw.exe` ohne zusaetzliches Konsolenfenster. Start- und unbehandelte
GUI-Fehler werden dabei in `sts_tester_error.log` protokolliert; das sichtbare
Kommunikations-/Debuglog bleibt unveraendert erhalten. Collector-Zustand und
Fehlerlog liegen stabil neben den Programmdateien, unabhaengig vom beim
Doppelklick verwendeten Windows-Arbeitsverzeichnis.

StellwerkSim verwendet standardmäßig `127.0.0.1:3691`. Je nach Simulatorzustand
muss die Plugin-Verbindung dort zunächst freigegeben werden.

## Bedienung

1. **Verbinden / Trennen** öffnet beziehungsweise schließt die TCP-Verbindung.
2. **Plugin registrieren** meldet das Testprogramm mit Protokollversion 1 an.
3. **Anlageninfo**, **Simulationszeit**, **Zugliste**, **Bahnsteigliste** und
   **Wege / Fahrwege** senden die entsprechenden parameterlosen XML-Abfragen.
4. Für **Zugdetails**, **Zugfahrplan** und Ereignisse muss eine numerische ZID
   eingetragen sein. Nach einer Zugliste können erkannte Züge im Dropdown gewählt
   werden. Ereignis-Abonnements gelten jeweils für diese ZID.
5. **Eigenes XML senden** validiert das XML, verändert den Inhalt aber nicht; für
   das zeilenbasierte Protokoll wird beim Senden lediglich ein LF angehängt.
6. Im Log erscheinen Raw-Nachrichten, Parsergebnis, Fehler und Verbindungsstatus.
   **Log speichern** schreibt den vollständigen Inhalt als UTF-8-Textdatei.
7. **Live-Collector starten** aktiviert optional den persistenten Datenkern. Er
   fragt neue reguläre Züge automatisch ab, abonniert deren Events und speichert
   den Zustand atomar in `sts_collector_state.json`. Negative ZIDs und `Lok ...`
   bleiben als separate temporäre Bewegungen erhalten. Die periodische Zugliste
   wird anhand der Simulationszeit (nicht der PC-Zeit) ausgelöst.

Der Collector ist in `sts_collector.py` bewusst unabhängig von Socket und GUI.
Seine Datenmodelle behalten rohe XML-Dokumente, Plan-/Istwerte, den ersten
Fahrplan, aktuelle Restfahrpläne, alle Raw-Events sowie deduplizierte fachliche
Events getrennt. Ortsauflösung erfolgt ausschließlich über explizite, später
korrigierbare `LocationResolver`-Mappings; unbekannte Namen werden nicht geraten.
Automatische Requests werden in der GUI mit kurzem Abstand serialisiert. Aktuelle
Fahrplaene werden an den Simzeit-Slots `:10`, `:30` und `:50` aktualisiert, die
vollstaendige Zugliste dagegen weiterhin nur etwa alle zwei Simulationsminuten.
Anlageninformationen binden den Zustand an eine AID; bei einem Wechsel wird die
alte JSON-Datei archiviert, statt Daten verschiedener Stellwerke zu vermischen.

Der Simulator bestätigt oder verwirft Kommandos selbst. Seine Status- und
Fehlerantworten werden deshalb absichtlich ungefiltert im Log angezeigt.

## Windows-EXE mit PyInstaller (optional)

```bash
python -m pip install pyinstaller
pyinstaller --onefile --windowed --name sts_tester sts_tester.py
```

Die EXE liegt anschließend unter `dist/sts_tester.exe`. Für eine übliche
python.org-Windows-Installation erkennt PyInstaller `tkinter` automatisch. Falls
eine stark reduzierte Python-Distribution ohne Tcl/Tk verwendet wird, muss zuerst
eine vollständige Python-Installation einschließlich Tcl/Tk installiert werden.

## Protokollhinweis

Die Plugin-Schnittstelle überträgt ein XML-Element je Zeile über TCP. Der Client
puffert deshalb unvollständige `recv()`-Fragmente und trennt mehrere gemeinsam
empfangene Nachrichten an LF beziehungsweise CRLF. Nutzdaten werden als UTF-8
gesendet und sowohl roh als auch zusätzlich geparst protokolliert.
