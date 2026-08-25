# StellwerkSim Bildfahrplan V0.2

Die neue Endanwenderanwendung ist von dem tkinter-Diagnosewerkzeug in
`Schnittstellentest/` getrennt. Sie verwendet dessen stabilen `STSLiveCollector`
und TCP-/XML-Client über eine thread-sichere Adapter-Schicht. Die Oberfläche liest
einmal pro Sekunde einen Snapshot; Netzwerkempfang und Collector-Verarbeitung
laufen außerhalb des Qt-Threads.

## Installation und Start

```bash
python -m pip install -r requirements.txt
python bildfahrplan_app.py
```

### Windows-Live-Start per Doppelklick

Nach dem Anlegen von `.venv` und der Installation der Abhängigkeiten genügt ein
Doppelklick auf `Bildfahrplan_Live_Start.bat`. Der Launcher verwendet immer
`.venv\Scripts\pythonw.exe` relativ zum Anwendungsordner und startet unmittelbar
den Live-Betrieb, ohne einen Offline-State zu laden.

Dafür muss StellwerkSim bereits laufen und seine Plugin-Schnittstelle unter
`127.0.0.1:3691` erreichbar sein. Fehlt die virtuelle Umgebung oder deren
`pythonw.exe`, zeigt der Launcher eine verständliche Fehlermeldung an.

Live werden standardmäßig `127.0.0.1:3691` sowie der persistente Collector-State
neben dem Diagnosetester verwendet. Ein vorhandener Zustand kann ohne Simulator
geöffnet werden:

```bash
python bildfahrplan_app.py --state Schnittstellentest/sts_collector_state.json
```

Ein anderes explizites Streckenprofil wird mit `--profile DATEI.json` gewählt.
Nur Namen aus `raw_names` werden zugeordnet. Unbekannte Namen werden ausgelassen;
es gibt insbesondere keine automatische Interpretation von Gleis-Suffixen.

## Umfang V0.2

Der Bildfahrplan zeichnet Plantrassen aus `original_schedule` und eine einfache
Projektion aus Planzeit plus aktueller Verspätung. Klassisch liegt die Strecke auf
der X-Achse und die nach unten zunehmende Zeit auf der Y-Achse; Halte werden als
vertikale Abschnitte und die aktuelle Simulationszeit als horizontale Linie
dargestellt. Lokbewegungen und Wagenparks werden nicht gezeichnet.

Der Tab **Strecke** wertet vollständige `<wege>`-Antworten konservativ aus. Ein
verlustfreier Raw-Graph wird strikt über exakte Namen mit Fahrplanpunkten
verankert. Eindeutig auflösbare, aufeinanderfolgende Fahrplanpunkte liefern
zählbare Pfad-Evidenz für einen getrennten, komprimierten OperationalRouteGraph.
Automatisch erzeugte Daten werden AID-spezifisch unter `config/generated/`
gespeichert; manuelle Streckenprofile bleiben davon unberührt.

Die relative X-Position stammt weiterhin aus einem expliziten linearen
RouteProfile/RoutePath. Echte Istzeit-Zuordnung, Auswahl-/Mapping-Editor,
Gleisbelegung, Konfliktmodell, metrische Kilometer und vollständige physische
Gleisrekonstruktion sind nicht Teil von V0.2.
