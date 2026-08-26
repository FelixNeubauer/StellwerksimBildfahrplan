# StellwerkSim Bildfahrplan V0.3.5.6

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

## Umfang V0.3.5.6

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

High-confidence-Between-Entscheidungen werden als gemeinsames, deterministisches
Constraint-Set vor dem Maximum-Evidence-Forest angewandt: Kettenkanten sind
verbindlich, die transitive Direktkante wird zum Skip. Widersprüchliche
Constraints werden nicht durch Iterationsreihenfolge entschieden, sondern als
offene Topologiefrage gespeichert. Zugdetail-Ziele werden vor einer
Boundary-Frage gegen bekannte Betriebsstellen- und Plattformnamen aufgelöst;
interne Bezeichnungen wie `Gleis THD1` erzeugen daher keine künstliche
Außengrenze. Generierte Graphdaten verwenden Persistenzschema 10.
Automatisch erzeugte Daten werden AID-spezifisch unter `config/generated/`
gespeichert; manuelle Streckenprofile bleiben davon unberührt.

Manuelle Betriebsstellen-Cluster können unter
`config/operating_points/<aid>.json` abgelegt werden. Automatische Persistenz
liest diese Datei nur und überschreibt sie nicht.

Der Tab **Gleise / Ortszuordnung** bearbeitet diese AID-spezifische Konfiguration
jetzt direkt. Er trennt Betriebsstellen, zugeordnete und nicht zugeordnete
Originalnamen, unterstützt Mehrfachauswahl, natürliche Sortierung, Suche sowie
Station-Key-Auswahlhilfen und erhält manuelle Entscheidungen bei erneuter
automatischer Zuordnung. Die Automatik bleibt der vorhandene
`OperatingPointResolver`; der Editor fügt keine zweite Topologieheuristik hinzu.
„Alle Zuordnungen entfernen“ löst alle editierbaren Zuordnungen, erhält aber
fachlich durch `haltepunkt="true"` belegte Self-Zuordnungen wie `Martinszell →
Martinszell`. Manuelle Betriebsstellen, Zuordnungen und bewusst gelöste Namen
werden autoritativ unter `config/operating_points/<aid>.json` gespeichert;
generierte Graphdaten bleiben davon getrennt.

Ein expliziter Klick auf **Automatisch zuordnen** baut den automatischen
Editorzustand stets neu aus den aktuellen Live-Daten auf. Dabei ergänzt der
Editor mit dem bestehenden `station_key()` eindeutig präfixierte Namen auf
OperatingPoint-Ebene; dies ist ausdrücklich keine Aussage über physische
Gleisgleichheit. Positive manuelle Zuordnungen bleiben autoritativ, während
bewusst gelöste automatische Zuordnungen bei diesem expliziten Neuaufbau wieder
automatisch zugeordnet werden dürfen. Im normalen Live-Refresh bleiben solche
Unassignments dagegen erhalten. Rawnamen können per Mehrfachauswahl von rechts
oder aus der Mitte auf eine Betriebsstelle gezogen sowie aus der Mitte nach
rechts gelöst werden.

Der Editor hält zusätzlich einen vollständigen, quellengetrennten Snapshot
seines sichtbaren Zustands. Änderungen werden 30 Sekunden nach der letzten
Bearbeitung automatisch sowie beim Verlassen des Tabs und beim Beenden sofort
atomar gespeichert. Automatische Snapshot-Einträge bleiben automatisch; nur
der getrennte Override-Bereich ist autoritativ manuell.

Stellwerksbezogene JSON-Artefakte tragen gemeinsame Metadaten mit AID,
Stellwerkname, Artefakttyp und Speicherzeit. Beim Laden werden AID und Name
gemeinsam geprüft. Namensänderungen, mögliche AID-Wechsel und Legacy-Dateien
ohne Namen benötigen eine ausdrückliche Nutzerentscheidung; abgewählte oder
migrierte Altdateien werden unter einem `archive/`-Unterordner bewahrt.

Die relative X-Position stammt weiterhin aus einem expliziten linearen
RouteProfile/RoutePath. Echte Istzeit-Zuordnung, Auswahl-/Mapping-Editor,
Gleisbelegung, Konfliktmodell, metrische Kilometer und vollständige physische
Gleisrekonstruktion sind nicht Teil von V0.3.5.3. Die Streckenachse liegt oben und
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

V0.3.5.2 wendet hoch-konfidente Between-Entscheidungen als verbindliche
Topologiebedingung vor Branch- und Synthetic-Junction-Erkennung an. Transitive
Schedule-Folgen bleiben Rohbeobachtungen, werden im sichtbaren Graph aber als
Skip über die bestätigte Kette geführt. Bereits gespeicherte Zugdetails `von` und
`nach` können zusammen mit Gegenrichtung oder Raw-Connectoren nicht sichtbare
Außengrenzen belegen. Unzureichende Evidenz erzeugt keine zufällige Topologie,
sondern eine persistierbare `TopologyQuestion`; eine Frage-GUI ist weiterhin
nicht Bestandteil dieses Schritts. Die generierten Diagnosedaten verwenden
Schema 9.

V0.3.5.3 kompiliert alle High-Between-Befunde gemeinsam in Required-/Forbidden-
Constraints, bevor Union-Find normale Backbone-Kandidaten sieht. Konflikte
bleiben als `conflicting_between_constraints` offen. Ein vorgeschalteter
`ExternalTargetResolution` gleicht unveränderte `von`-/`nach`-Originaltexte mit
bekannten Mitgliedern derselben Betriebsstelle und sichtbaren Punkten ab, bevor
externe Connectoren oder Nutzerfragen abgeleitet werden. Diese zusätzlichen
Entscheidungsdaten werden in Schema 10 persistiert.

V0.3.5.4 bewertet Fahrzeitvergleiche an Zwischenhalten haltbewusst. Expliziter
Aufenthalt bleibt vom Movement getrennt; der in den Legs enthaltene Brems-/
Anfahranteil wird bei starkem bidirektionalem Through-or-Skip-Muster nicht mehr
als alleiniger Gegenbeweis behandelt. Massive Umwege bleiben negative Evidenz.
Zusätzlich kennzeichnet der Collector additiv die Herkunft des ersten
Fahrplancaptures. Unsichere Starts aus der initialen Zugliste liefern keine
Incoming-Boundary-Evidenz und keine vorschnellen Nutzerfragen, während das
vertrauenswürdige Fahrplanende weiterhin ausgewertet wird. Schema 11 persistiert
diese Vergleiche, Provenienz und zurückgestellte Beobachtungsfragen.

V0.3.5.5 trennt echte konsekutive Dreierfolgen desselben `original_schedule`
von lediglich serviceübergreifend kombinierbaren Kanten. Same-Service-Order
wird nach OperatingPoint-Kollaps als primäre lokale Reihenfolgeevidenz
aggregiert; startup-trunkierte Fahrpläne behalten dabei verlässliche interne
Reihenfolge, obwohl ihr erster Endpoint unsicher bleibt. Alle drei
Dreieckshypothesen werden einzeln diagnostiziert. Widersprüchliche echte
Sequenzen erzeugen eine TopologyQuestion, während Pairwise-only-Support ohne
weitere starke Raw-/Fahrzeitevidenz kein High-Between erzwingt. Schema 12
persistiert die geordneten Sequenzen, Triple-Aggregate und Hypothesen.

V0.3.5.6 schließt die automatische Topologiehärtung vor dem manuellen Editor
ab. Finale Knoten besitzen getrennte `topology_role`- und `boundary_role`-
Dimensionen, sodass etwa ein Verzweigungsknoten zugleich boundary-adjacent sein
kann. Ein durch Endpoint-, Raw- und Randlage belegter expliziter Schedule-
BoundaryNode verdrängt eine gleichbedeutende synthetische Dublette. Wiederholte,
aber wegen Startup-Trunkierung noch unzuverlässige externe Endpoints mit
Raw-Connector bleiben als `DeferredExternalBoundaryCandidate` erhalten und
können durch eine spätere vertrauenswürdige Beobachtung automatisch bestätigt
werden. Verbleibende Sonderfälle werden bewusst für den späteren Topology Editor
persistiert statt mit weiteren Heuristiken überbaut; Schema 13 enthält diese
Rollen, Deduplizierungen und Kandidaten.
