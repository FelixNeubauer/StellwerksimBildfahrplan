# StellwerkSim Bildfahrplan V0.3.5.1

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

## Umfang V0.3.5.1

Der Bildfahrplan zeichnet Plantrassen aus `original_schedule` und eine einfache
Projektion aus Planzeit plus aktueller Verspätung. Klassisch liegt die Strecke auf
der X-Achse und die nach unten zunehmende Zeit auf der Y-Achse; Halte werden als
vertikale Abschnitte und die aktuelle Simulationszeit als horizontale Linie
dargestellt. Lokbewegungen und Wagenparks werden nicht gezeichnet.

Der Tab **Strecke** leitet den sichtbaren betrieblichen Graphen ausschließlich
aus den unveränderlichen `original_schedule` normaler Züge ab. Exakte manuelle
Mappings haben Vorrang; anschließend dürfen explizite Beziehungen aus der
`bahnsteigliste`, mehrfach bestätigte Betriebsstellenkürzel und lokale
Sandwich-/Closed-Excursion-Fahrplanmuster Rawnamen gruppieren. Eine separate
RouteAxis-Ebene kann Ein-/Ausfahrt-Aliasse auf dieselbe X-Position abbilden,
ohne SchedulePoints oder OperatingPoints zu löschen. Ohne solche Evidenz bleibt jeder
Name als eigener virtueller Fahrplanpunkt erhalten. `<wege>` erzeugt weiterhin
einen verlustfreien Raw-Graph, bestimmt aber keine sichtbaren Betriebsstellen.
Automatisch erzeugte Daten werden AID-spezifisch unter `config/generated/`
gespeichert; manuelle Streckenprofile bleiben davon unberührt.

Manuelle Betriebsstellen-Cluster können unter
`config/operating_points/<aid>.json` abgelegt werden. Automatische Persistenz
liest diese Datei nur und überschreibt sie nicht.

Die relative X-Position stammt weiterhin aus einem expliziten linearen
RouteProfile/RoutePath. Echte Istzeit-Zuordnung, Auswahl-/Mapping-Editor,
Gleisbelegung, Konfliktmodell, metrische Kilometer und vollständige physische
Gleisrekonstruktion sind nicht Teil von V0.3.5.1. Die Streckenachse liegt oben und
ist fest; ausschließlich der auf 05:00–21:00 begrenzte Zeitbereich ist vertikal
zoom- und scrollbar. Zwischen den fünfsekündlichen STS-Abfragen interpoliert die
UI die Simulationszeit monoton und friert bei Verbindungsverlust konservativ ein.

ScheduleEdges bleiben rohe Folgenbeobachtungen. Eine nachgelagerte
Korridorrekonstruktion klassifiziert daraus `neighbour`, `skip`, `branch`,
`alternative_route`, `local_internal` oder `unresolved`. Stabil belegte
Zwischenpfade können seltenere Direktfolgen als Skip erklären; strukturelle
Hin-und-zurück-Muster liefern DirectionChange-Evidenz für Stichstreckenenden.
Seit V0.3.3 wird zuerst ein unveränderlicher Backbone aus aggregierter
Richtungs-, Fahrzeit-, Terminal- und optionaler Raw-Infrastrukturevidenz
gewählt. Erst danach dürfen übrige ScheduleEdges über ausschließlich bestätigte
Backbone-Kanten als Skip erklärt werden; zirkuläre Skip-Beweise sind damit
ausgeschlossen.

V0.3.4 bewertet lokale Dreiecks- und Between-Motive bereits vor der
Forest-Auswahl. Der diagnostizierbare BackboneScore kombiniert Fahrplan- und
Gegenrichtungssupport, Fahrzeitvergleiche, Raw-Adjazenz sowie positive und
negative Between-, Branch- und Terminal-Evidenz. Raw-Fortsetzungen können ein
scheinbares Fahrplanende als `observed_schedule_boundary` statt als echten
Terminal kennzeichnen; Raw-Elemente werden dabei weiterhin niemals zu sichtbaren
Betriebsstellen. Transitive Direktfolgen werden erst gegen den so festgelegten
Backbone als Skip klassifiziert.

V0.3.5 kann einen ausreichend belegten Stichstreckenast an einem synthetischen
Abzweig auf einer bestehenden Backbone-Kante befestigen. Der OperationalRouteGraph
splittet die Host-Kante, ohne `original_schedule` um künstliche Fahrplanpunkte zu
erweitern. Die relative Position stammt bevorzugt aus einer eindeutigen
Raw-Infrastrukturprojektion, andernfalls aus einer ausdrücklich nicht-metrischen
Fahrzeittriangulation. Herkunft, relative Auflösung und Konfidenz bleiben in der
Diagnose und im generierten Schema 8 sichtbar. V0.3.5.1 bewahrt dabei die
fachliche `topological_fraction` getrennt von einer ausschließlich grafischen
`display_fraction`, finalisiert Knotenrollen nach allen synthetischen Splits und
stabilisiert Raw-Fortsetzungsevidenz über komprimierte Anchorbereiche.
