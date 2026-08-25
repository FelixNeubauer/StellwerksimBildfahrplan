# StellwerkSim-Plugin-Schnittstelle – Arbeits- und Implementierungshandbuch

> **Zweck dieses Dokuments**  
> Dieses Handbuch fasst den bislang praktisch getesteten Wissensstand zur StellwerkSim-Plugin-Schnittstelle sowie die für unsere Tools relevanten Konventionen zu Zugläufen, Fahrplänen, Bahnsteigen, Gleisen und Haltepositionen zusammen.  
> Es ist bewusst als **Repository-Kontext für Codex und andere Entwickler** geschrieben.

## 0. Statuskennzeichnungen

Aussagen in diesem Dokument werden gedanklich in vier Klassen unterschieden:

- **OFFIZIELL** – aus der StellwerkSim-Plugin-Dokumentation bekannt.
- **IM LOG BESTÄTIGT** – im bisherigen Testprogramm praktisch beobachtet.
- **BETRIEBLICHE/STS-BEOBACHTUNG** – vom Nutzer aus StellwerkSim-Erfahrung bestätigt, aber nicht zwingend direkt aus dem XML-Log beweisbar.
- **DESIGNENTSCHEIDUNG** – empfohlene Implementierungsregel für unsere Tools.

Wenn eine Aussage nur eine Vermutung ist, muss sie als solche markiert werden. Codex soll aus diesem Handbuch **keine zusätzlichen Protokolleigenschaften erfinden**.

---

# 1. Schnittstellen-Grundlagen

## 1.1 Verbindung

- StellwerkSim stellt lokal eine TCP-Plugin-Schnittstelle bereit.
- Standardziel: `127.0.0.1`
- Standardport: `3691`
- Protokoll: XML-basierte Nachrichten über TCP.
- Vor normalen Abfragen muss sich das Plugin registrieren.

Beispiel:

```xml
<register
    name="STS Schnittstellen Tester"
    autor="Test"
    version="0.1"
    protokoll="1"
    text="Testprogramm für die StellwerkSim Plugin-Schnittstelle"
/>
```

Typische Serverantwort beim Verbindungsaufbau:

```xml
<status code="300">STS Plugin Interface, bitte anmelden.</status>
```

Nach erfolgreicher Registrierung:

```xml
<status code="220">Ok.</status>
```

## 1.2 Wichtig: TCP-Pakete sind keine XML-Dokumente

**IM LOG BESTÄTIGT**

Ein `recv()` entspricht nicht zwingend genau einer vollständigen fachlichen Antwort und eine einzelne LF-terminierte Protokollzeile ist nicht zwingend ein eigenständiges XML-Dokument.

Beispiel Zugliste:

```xml
<zugliste>
<zug zid="128001" name="Wagen IC 2084" />
<zug zid="72230" name="RE 32924" />
...
</zugliste>
```

Die Zeilen können einzeln eintreffen. Deshalb gilt:

- Rohdaten weiterhin zeilenweise protokollieren.
- XML-Container über mehrere Zeilen zusammensetzen.
- Öffnende Container-Tags wie `<zugliste>` oder `<zugfahrplan>` sind **keine Parsefehler**.
- Schließende Tags wie `</zugliste>` sind **keine eigenständigen XML-Dokumente**.
- Die Parserarchitektur muss auch fragmentierte `recv()`-Aufrufe tolerieren.
- GUI-Updates aus dem Netzwerkthread dürfen bei tkinter nur threadsicher, z. B. über `root.after(...)`, erfolgen.

Betroffene Container sind u. a.:

- `zugliste`
- `bahnsteigliste`
- verschachtelte `bahnsteig`-Blöcke
- `wege`
- `zugfahrplan`

---

# 2. Anlagen- und Zeitinformationen

## 2.1 Anlageninformation

Abfrage:

```xml
<anlageninfo />
```

Beispielantwort:

```xml
<anlageninfo
    simbuild="5851"
    name="Immenstadt 2010"
    online="true"
    region="Südbayern"
    aid="823"
/>
```

Relevante Felder:

- `simbuild`
- `name`
- `online`
- `region`
- `aid`

**DESIGNENTSCHEIDUNG**  
Die `aid` bzw. mindestens `name + aid` sollte verwendet werden, um stellwerksspezifische Konfigurationen zu laden.

## 2.2 Simulationszeit

Abfrage:

```xml
<simzeit sender="sts_tester" />
```

Beispiel:

```xml
<simzeit sender="sts_tester" zeit="55660126" />
```

Die bisherigen Werte verhalten sich wie Millisekunden seit Tagesbeginn.

**DESIGNENTSCHEIDUNG**

- Simulationszeit intern immer unabhängig von der PC-Uhr behandeln.
- Für einen Bildfahrplan eine aktuelle Zeitlinie aus der Simulationszeit zeichnen.
- Für Event-Historisierung den Event-Empfang mit Simulationszeit verknüpfen.
- Bei Tageswechsel Zeitachsen explizit normalisieren, damit `23:59 -> 00:01` nicht rückwärts springt.

---

# 3. Zugliste und Lebenszyklus von Fahrplänen

## 3.1 Zugliste

Abfrage:

```xml
<zugliste />
```

Antwort:

```xml
<zugliste>
    <zug zid="72230" name="RE 32924" />
    <zug zid="37854" name="RE 32741" />
    ...
</zugliste>
```

Jeder normale Fahrplan besitzt eine `zid` als interne Identifikation.

## 3.2 Aktualisierungsintervall

**BETRIEBLICHE/STS-BEOBACHTUNG + DESIGNENTSCHEIDUNG**

Die vollständige Zugliste muss nicht permanent abgefragt werden. Für unsere Tools genügt:

> **`zugliste` ungefähr alle 2 Minuten Simulationszeit aktualisieren.**

Bei jeder Aktualisierung:

1. aktuelle ZIDs mit bekannten ZIDs vergleichen,
2. neue ZIDs erkennen,
3. neue Züge initialisieren,
4. verschwundene ZIDs nicht aus der lokalen Historie löschen.

## 3.3 Wann Fahrpläne erscheinen

**BETRIEBLICHE/STS-BEOBACHTUNG**

Ein Fahrplan erscheint ungefähr **60 Minuten vor seiner planmäßigen Einfahrt** in das Stellwerk.

Dabei erscheinen gleichzeitig bereits zugehörige **Folgefahrpläne**, z. B. für:

- Zugwenden an einer Endstation,
- aus einem Zug entstehende Folgezüge,
- Flügelzüge,
- weitere fahrplanseitig verknüpfte Zugläufe.

Das ist für die Datenmodellierung entscheidend: Nicht jeder sichtbare Fahrplan ist fachlich völlig unabhängig von den anderen.

## 3.4 Wann Fahrpläne verschwinden

**BETRIEBLICHE/STS-BEOBACHTUNG**

Ein Fahrplan verschwindet aus dem aktiven Bestand insbesondere wenn:

- der Zug das Stellwerk verlässt,
- der Zug beim Kuppeln in einem anderen Zug aufgeht.

**Wichtig:** Ein verschwundener Fahrplan darf lokal nicht gelöscht werden. Er wird als abgeschlossen markiert und bleibt für Bildfahrplan/Statistik erhalten.

## 3.5 Kuppel-Lebenszyklus

Beim Kuppeln gilt typischerweise:

- Fahrplan A kuppelt an Fahrplan B.
- Fahrplan A endet.
- Zug A wird Bestandteil von Zug B.
- Fahrplan B bleibt der fortgeführte Zuglauf.

Daraus folgt intern eher eine Zusammenführung:

```text
A ─────┐
       ├── B ─────>
B ─────┘
```

## 3.6 Flügel-Lebenszyklus

Beim Flügeln entsteht eine Verzweigung:

```text
           ┌── Folgezug / Zugteil A
Stammzug ──┤
           └── Folgezug / Zugteil B
```

Folgefahrpläne können bereits vor dem tatsächlichen Flügelvorgang bekannt sein.

---

# 4. Besondere `Lok ...`-Einträge

## 4.1 Beobachtung

**IM LOG BESTÄTIGT**

Während Lokumläufen können zusätzliche temporäre Zuglisteneinträge erscheinen, z. B.:

```xml
<zug zid="-1" name="Lok IC 2085" />
<zug zid="-2" name="Lok CB 69484" />
```

Diese Lokbewegungen dienen typischerweise dem Umsetzen der Lok.

## 4.2 Fachliche Behandlung

**BETRIEBLICHE/STS-BEOBACHTUNG**

- Bei einem Lokumlauf bleibt das fachliche Bahnhofsgleis im Regelfall erhalten.
- Der Zug verlässt dabei normalerweise nicht die Betriebsstelle.
- Für einen klassischen Bildfahrplan soll die Lokbewegung **nicht als normale Zugtrasse** behandelt werden.

**DESIGNENTSCHEIDUNG**

- Einträge `name.startswith("Lok ")` und/oder negative ZIDs separat klassifizieren.
- Nicht als normalen Reise-/Güterzug in der Haupttrasse darstellen.
- Am Stammzug darf aber ein Hinweis wie **„Lok setzt um“** erscheinen.
- Originaldaten trotzdem vollständig speichern, damit spätere Rangier-/Lokumlauf-Tools sie verwenden können.

---

# 5. Zugdetails

Abfrage:

```xml
<zugdetails zid="72230" />
```

Beispiel:

```xml
<zugdetails
    zid="72230"
    verspaetung="5"
    gleis="MBLH 2"
    amgleis="false"
    von=""
    name="RE 32924"
    nach="Kempten Hbf"
    plangleis="MBLH 1"
    sichtbar="true"
/>
```

Relevante Felder:

- `zid`
- `name`
- `verspaetung`
- `gleis`
- `plangleis`
- `amgleis`
- `sichtbar`
- `von`
- `nach`
- ggf. `usertext`
- ggf. `usertextsender`

## 5.1 Plan- und Istgleis

**IM LOG BESTÄTIGT**

Vor einer Gleisänderung:

```text
gleis      = MBLH 1
plangleis  = MBLH 1
```

Nach manueller Gleisänderung:

```text
gleis      = MBLH 2
plangleis  = MBLH 1
```

Interpretation:

- `plangleis` = ursprünglich geplantes Gleis
- `gleis` = aktuell eingestelltes/tatsächliches Gleis

Das gleiche Prinzip findet sich im Fahrplan mit `plan` und `name` wieder.

---

# 6. Zugfahrplan

Abfrage:

```xml
<zugfahrplan zid="114452" />
```

Typische Antwort:

```xml
<zugfahrplan zid="114452">
    <gleis ab="15:18" name="EA Kempten" flags="D" plan="EA Kempten" an="15:18" />
    <gleis ab="15:21" name="Martinszell" flags="D" plan="Martinszell" an="15:21" />
    <gleis ab="15:46" name="MIMS 2" flags="LRP[r]" plan="MIMS 2" an="15:29" />
    ...
</zugfahrplan>
```

Jeder Fahrplanpunkt kann enthalten:

- `name` – aktuell verwendete/ggf. disponierte Bezeichnung
- `plan` – ursprüngliche Planbezeichnung
- `an` – planmäßige Ankunft
- `ab` – planmäßige Abfahrt
- `flags` – betriebliche/fahrplanseitige Zusatzinformationen

## 6.1 Der Fahrplan ist dynamisch

**IM LOG BESTÄTIGT**

Der über `zugfahrplan` gelieferte Fahrplan enthält nur den aktuell noch relevanten Rest des Fahrplans. Bereits abgearbeitete Punkte können verschwinden.

Deshalb:

> Einen Fahrplan beim ersten Auftauchen möglichst vollständig lokal sichern und später nicht durch kürzere Antworten überschreiben.

Empfehlung:

- `initial_plan_schedule` = unveränderliche erste bekannte Plantrasse
- `current_schedule` = aktuell von StellwerkSim gelieferte Rest-/Iststruktur

## 6.2 Gleisänderungen im Fahrplan

**IM LOG BESTÄTIGT**

Vor Änderung:

```xml
<gleis name="MBLH 1" plan="MBLH 1" ... />
```

Nach Änderung:

```xml
<gleis name="MBLH 2" plan="MBLH 1" ... />
```

Damit können Plan- und Istgleis auch für zukünftige Fahrplanpunkte sauber unterschieden werden.

---

# 7. Ereignisse

Ereignisse werden pro ZID und Ereignisart abonniert.

Beispiel:

```xml
<ereignis zid="72230" art="ankunft" />
```

Bisher verwendete/abonnierbare Arten:

- `einfahrt`
- `ankunft`
- `abfahrt`
- `ausfahrt`
- `rothalt`
- `wurdegruen`
- `kuppeln`
- `fluegeln`

## 7.1 Event-Nutzdaten

Beispiel Ankunft:

```xml
<ereignis
    art="ankunft"
    zid="72230"
    verspaetung="5"
    gleis="MBLH 2"
    amgleis="true"
    von=""
    name="RE 32924"
    nach="Kempten Hbf"
    plangleis="MBLH 1"
    sichtbar="true"
/>
```

Events enthalten damit bereits viel Zugzustand:

- Eventart
- ZID
- Zugname
- Verspätung
- Istgleis
- Plangleis
- `amgleis`
- `sichtbar`
- `von`
- `nach`

Nach einem Event ist daher nicht zwingend immer eine zusätzliche `zugdetails`-Abfrage erforderlich.

## 7.2 Kuppeln

**IM LOG BESTÄTIGT**

Beispiel:

```xml
<ereignis
    art="kuppeln"
    zid="37854"
    name="RE 32741"
    gleis="MIMS 3a"
    verspaetung="3"
    ...
/>
```

Das Event sagt, **welcher Zug kuppelt**, aber aus dem Event allein ist der Zielzug nicht zwingend ersichtlich. Beziehungen müssen daher zusätzlich aus Fahrplan/Folgefahrplan/Flags bzw. dem vorher bekannten Zuglaufmodell stammen.

## 7.3 Flügeln

**IM LOG BESTÄTIGT**

Beispiel:

```xml
<ereignis
    art="fluegeln"
    zid="103246"
    name="RE 3991"
    gleis="MIMS 1"
    ...
/>
```

Auch hier sollte die fachliche Beziehung zu den entstehenden Folgefahrplänen bereits vorher modelliert werden, soweit sie bekannt ist.

---

# 8. Event-Deduplizierung und Zustandsautomaten

## 8.1 Mehrfache identische Events

**IM LOG BESTÄTIGT**

Ein Ereignis kann mehrfach nacheinander mit praktisch identischen Attributen auftreten.

Beobachtet wurden insbesondere:

- wiederholte `rothalt`-Events am gleichen Ort,
- wiederholte `abfahrt`-Events desselben Zuges am selben Gleis.

Deshalb gilt:

> Events niemals blind 1:1 als einmalige betriebliche Vorgänge zählen.

## 8.2 Empfohlene Deduplizierung

### Ankunft/Abfahrt

Für Kombination `(zid, fahrplanpunkt/gleis, eventart)`:

- erstes fachlich neues Event speichern,
- unmittelbar folgende identische Wiederholungen ignorieren,
- Zustandswechsel berücksichtigen.

Beispiel:

```text
ANKUNFT MIMS 3a
→ actual_arrival setzen
→ status = am_gleis

ABFAHRT MIMS 3a
→ actual_departure setzen
→ status = abgefahren

weitere identische ABFAHRT-Events
→ ignorieren
```

### Rothalt / wurdegruen

Empfohlener Zustandsautomat:

```text
erstes rothalt
→ SignalStop.start

weitere rothalt am selben Signal-/Gleiszustand
→ Heartbeat / ignorieren

wurdegruen
→ SignalStop.end
```

Damit kann später eine Rot-Halt-Dauer berechnet werden.

---

# 9. Fahrplan-Flags

Flags dürfen **niemals verworfen** werden. Sie sind für Zugbeziehungen und betriebliche Sondervorgänge relevant.

Bisher beobachtete Beispiele:

```text
D
P[r]
P[l]
R
LRP[r]
RAP
RF(128001)
E(112398)
RA
```

Bekannt bzw. im bisherigen Projekt bereits verwendet:

- `L` – Lokumlauf / Lok umsetzen
- `R` – Richtungswechsel
- `P[...]` – betriebliche Park-/Aufstellinformation mit Orientierung
- weitere Flags können Verweise auf andere ZIDs enthalten

**DESIGNENTSCHEIDUNG**

Flags immer zweigleisig speichern:

```python
raw_flags: str
parsed_flags: list[Flag]
```

Der Raw-String bleibt unverändert erhalten. Parserlogik darf später erweitert werden.

Für den Bildfahrplan können Sondervorgänge als kleine Marker erscheinen; für andere Tools bleiben die vollständigen Flags verfügbar.

---

# 10. Bahnsteigliste

Abfrage:

```xml
<bahnsteigliste />
```

Typische Struktur:

```xml
<bahnsteigliste>
    <bahnsteig name="MIMS 1" haltepunkt="false">
        <n name="MIMS 2" />
        <n name="MIMS 3a" />
        ...
    </bahnsteig>
    ...
</bahnsteigliste>
```

`<n>`-Einträge beschreiben benachbarte/verwandte Bahnsteig-/Gleisbezeichnungen.

**Wichtig:** Die Bezeichnung `bahnsteig` in der Schnittstelle darf nicht vorschnell mit einem eindeutig physischen Bahnsteiggleis gleichgesetzt werden.

---

# 11. Gleis- und Bahnsteigbezeichnungen: zentrale Modellierungsregeln

Dieser Abschnitt ist besonders wichtig für universelle Tools.

## 11.1 Es gibt keine einheitliche Benennung

Ein Gleis kann z. B. so bezeichnet sein:

```text
TU 3
Ulm Hbf 3
Ulm Hbf Gleis 3
Ulm 3
Gleis 3
3
```

Das Präfix kann sein:

- RIL100-artige Abkürzung,
- ausgeschriebener Betriebsstellenname,
- verkürzter Name,
- gar kein Präfix.

Daraus folgt:

> Die Betriebsstelle darf **nicht ausschließlich aus einem vermeintlichen RIL100-Präfix** abgeleitet werden.

## 11.2 Große Betriebsstelle ohne Präfix

Wenn ein Stellwerk einen großen Hauptbahnhof und mehrere kleinere Betriebsstellen umfasst, können die Gleise der zentralen Betriebsstelle z. B. einfach heißen:

```text
1
2
3
4N
4S
```

während kleinere Betriebsstellen durchaus Präfixe oder ausgeschriebene Namen verwenden.

## 11.3 Nur eine Betriebsstelle im ganzen Stellwerk

Bei Rangierbahnhöfen oder großen Hauptbahnhöfen kann es vorkommen, dass **sämtliche** Bahnsteig-/Haltepositionen nur aus Gleisnummern bzw. örtlichen Bezeichnungen bestehen.

Beispiel:

```text
1
2
3
4N
4S
Ausziehgleis
Wende
```

Fehlendes Präfix ist dann normal.

---

# 12. Haltepunkte ohne Gleisnummer

**IM LOG BESTÄTIGT + BETRIEBLICHE/STS-BEOBACHTUNG**

Haltepunkte können als einzelner Fahrplan-/Bahnsteigpunkt erscheinen, obwohl real mehrere Strecken-/Bahnsteiggleise existieren.

Beispiel:

```xml
<bahnsteig name="Martinszell" haltepunkt="true">
</bahnsteig>
```

Obwohl Martinszell an einer zweigleisigen Strecke zwei Bahnsteige besitzt, liefert StellwerkSim hier keine unterscheidbaren Gleisnummern.

Daraus folgt:

```text
operating_point = Martinszell
physical_track = unbekannt
track_resolution = abstract
```

Für einen Bildfahrplan ist das unproblematisch. Für eine Gleisbelegungsanzeige darf jedoch **kein künstliches Gleis 1/2 erfunden werden**.

---

# 13. Haltepositionen wie `Flügel`, `Kuppel` und `G`

StellwerkSim kann mehrere unterschiedliche Fahrplanpunkte für dasselbe physische Gleis verwenden, z. B.:

```text
TU 3
TU 3 Flügel
TU 3 Kuppel
TU 1
TU 1G
```

Diese Bezeichnungen können lediglich unterschiedliche **Haltepositionen auf demselben physischen Gleis** darstellen.

Beispiele:

```text
raw_name        = "TU 3 Flügel"
operating_point = TU
physical_track  = 3
stop_position   = Flügel
```

```text
raw_name        = "TU 3 Kuppel"
operating_point = TU
physical_track  = 3
stop_position   = Kuppel
```

```text
raw_name        = "TU 1G"
operating_point = TU
physical_track  = 1
stop_position   = G
```

**BETRIEBLICHE/STS-BEOBACHTUNG**

Ein angehängtes `G` kann in StellwerkSim einen abweichenden Haltepunkt auf demselben physischen Gleis kennzeichnen, der insbesondere für lange Güterzüge verwendet wird, wenn der normale kurze Bahnsteighaltepunkt für die Zuglänge ungeeignet ist.

Für unsere Modellierung bedeutet dies typischerweise:

```text
TU 1  und  TU 1G
→ gleiches physisches Gleis
→ unterschiedliche Halteposition
```

Für eine spätere Gleisbelegungsanzeige wirken beide daher auf dieselbe physische Gleisressource.

**Wichtig:** Daraus darf **keine globale Parserregel** "`G` bedeutet immer Güterzughaltepunkt" abgeleitet werden. Die Bedeutung muss stellwerksspezifisch konfigurierbar bleiben.

**Wichtig:** Originalbezeichnung immer behalten.

---

# 14. Gleisabschnitte und mehrdeutige Suffixe

## 14.1 Nord-/Süd-/Ost-/West-Abschnitte

Ein **physisches Gleis** kann in mehrere betriebliche Abschnitte unterteilt sein, z. B.:

```text
3N
3S
3
```

oder mit uneinheitlicher Schreibweise:

```text
3 N
3 S
3N
3S
```

analog ggf. Ost/West.

Typischer Fall:

- `3N` = Nordteil von physischem Gleis `3`
- `3S` = Südteil von physischem Gleis `3`
- `3` = das gesamte physische Gleis `3`

Damit gilt ausdrücklich:

```text
3N und 3S
→ dasselbe physische Gleis 3
→ unterschiedliche Gleisabschnitte
```

Sie sind also **keine zwei verschiedenen physischen Gleise**.

Je nach örtlicher Infrastruktur können zwei kurze Züge gleichzeitig verschiedene Abschnitte desselben Gleises nutzen, während ein langer Zug mit Belegung des Vollgleises beide Abschnitte blockiert.

## 14.2 Konfliktmodell

Deshalb dürfen `3`, `3N`, `3S` nicht einfach als drei unabhängige Gleise behandelt werden.

Empfohlene Konfliktbeziehungen:

```text
3  kollidiert mit 3N
3  kollidiert mit 3S
3N kollidiert nicht zwingend mit 3S
```

Für eine spätere Gleisbelegungsanzeige ist dies wesentlich.

## 14.3 Normalisierung bekannter Abschnittsmuster

Wenn die Stellwerkskonfiguration bestätigt, dass `N/S/O/W` echte Abschnitte desselben physischen Gleises darstellen, kann intern normalisiert werden:

```text
"3N"  -> physical_track=3, track_section=N
"3 N" -> physical_track=3, track_section=N
"3S"  -> physical_track=3, track_section=S
"3 S" -> physical_track=3, track_section=S
```

Aber:

> Der unveränderte `raw_name` bleibt immer erhalten.

## 14.4 Mehrdeutige Suffixe wie `a` / `b`

Bezeichnungen wie:

```text
5a
5b
```

dürfen **nicht automatisch** als zwei Abschnitte desselben Gleises interpretiert werden.

Je nach Stellwerk können sie beispielsweise bedeuten:

```text
Fall A:
5a und 5b
→ dasselbe physische Gleis 5
→ unterschiedliche Abschnitte
```

oder:

```text
Fall B:
5a und 5b
→ zwei tatsächlich unterschiedliche physische Gleise
```

oder auch eine andere örtliche bzw. simulatorische Unterscheidung.

Daher gilt:

> Aus einem Buchstabensuffix allein darf keine allgemeine Aussage über physisches Gleis, Gleisabschnitt oder Halteposition abgeleitet werden.

Die Zuordnung muss stellwerksspezifisch konfiguriert bzw. bestätigt werden.

---

# 15. Empfohlenes Orts-/Gleisdatenmodell

Ein einzelner StellwerkSim-Name sollte nicht direkt als "Gleis" modelliert werden.

Empfohlene Struktur:

```python
Location:
    raw_name: str
    operating_point: str | None
    physical_track: str | None
    track_section: str | None
    stop_position: str | None
    location_type: str
    resolution: str
```

Mögliche `location_type`-Werte:

```text
platform_track
track_section
stop_position
haltpunkt
entry_exit
turnback
unknown
```

Mögliche `resolution`:

```text
exact       # konkretes physisches Gleis bekannt
section     # Teilabschnitt eines Gleises
abstract    # nur Betriebsstelle/Haltepunkt bekannt
unknown
```

Beispiele:

### Konkretes Gleis

```text
raw_name        = "TU 3"
operating_point = "TU"
physical_track  = "3"
resolution      = exact
```

### Ausgeschriebener Name

```text
raw_name        = "Ulm Hbf 3 Flügel"
operating_point = "Ulm Hbf"
physical_track  = "3"
stop_position   = "Flügel"
resolution      = exact
```

### Gleisabschnitt

```text
raw_name        = "3 N"
operating_point = "Ulm Hbf"
physical_track  = "3"
track_section   = "N"
resolution      = section
```

### Halteposition auf demselben physischen Gleis

```text
raw_name        = "TU 1G"
operating_point = "TU"
physical_track  = "1"
stop_position   = "G"
resolution      = exact
```

### Mehrdeutige Bezeichnung

```text
raw_name        = "5a"
operating_point = "..."
physical_track  = None
track_section   = None
stop_position   = None
resolution      = unknown
```

Bis eine stellwerksspezifische Zuordnung vorliegt, darf nicht geraten werden, ob `5a` ein eigenes Gleis, ein Abschnitt oder nur eine Halteposition ist.

### Haltepunkt ohne Gleisauflösung

```text
raw_name        = "Martinszell"
operating_point = "Martinszell"
physical_track  = None
resolution      = abstract
```

---

# 16. Keine vollautomatische Namensinterpretation erzwingen

**DESIGNENTSCHEIDUNG**

Automatische Normalisierung darf Vorschläge machen, aber nicht so tun, als seien alle Namen eindeutig parsebar.

Empfohlenes Verfahren je Stellwerk:

1. `bahnsteigliste` laden,
2. alle `raw_name`-Werte sammeln,
3. automatische Gruppierungs-/Normalisierungsvorschläge erstellen,
4. Nutzer kann Zuordnungen korrigieren,
5. Mapping stellwerksspezifisch speichern,
6. beim nächsten Start wiederverwenden.

Beispielkonfiguration:

```json
{
  "Ulm Hbf 3": {
    "operating_point": "Ulm Hbf",
    "physical_track": "3"
  },
  "Ulm Hbf 3 Flügel": {
    "operating_point": "Ulm Hbf",
    "physical_track": "3",
    "stop_position": "Flügel"
  },
  "3N": {
    "operating_point": "Ulm Hbf",
    "physical_track": "3",
    "track_section": "N"
  },
  "TU 1G": {
    "operating_point": "TU",
    "physical_track": "1",
    "stop_position": "G"
  },
  "5a": {
    "operating_point": "Beispielbahnhof",
    "physical_track": "5a"
  },
  "5b": {
    "operating_point": "Beispielbahnhof",
    "physical_track": "5b"
  }
}
```

Die letzten beiden Einträge sind nur ein **Beispiel für eine bestätigte stellwerksspezifische Zuordnung**. In einem anderen Stellwerk könnten `5a` und `5b` stattdessen Abschnitte desselben physischen Gleises sein.

---

# 17. Wege / Infrastrukturgraph

Abfrage:

```xml
<wege />
```

Die Antwort enthält u. a.:

- `shape`
- `type`
- `enr`
- `connector`
- `enr1`
- `enr2`
- `name1`
- `name2`

Beispiele:

```xml
<connector enr1="124" name2="MOF 2" />
<connector enr2="125" name1="MOF 2" />
<connector name2="MIMS 2" name1="MIMS 2 ALX" />
```

Damit lässt sich prinzipiell ein Infrastruktur-/Topologiegraph rekonstruieren.

**Aber:**

- `wege` ist kein fertiger Streckenverlauf für einen Bildfahrplan.
- Bahnhofsnamen, physische Gleise und betriebliche Bedeutung müssen weiterhin interpretiert werden.
- Topologieinferenz muss von direkt gelieferten Fakten getrennt bleiben.

Mögliche spätere Nutzung:

- Infrastrukturgraph
- automatische/unterstützte Gruppierung von Gleisen
- Fahrweg-/Konfliktanalyse
- Ableitung von Richtungen
- ggf. Ermittlung, welches reale Streckengleis bei abstrakten Haltepunkten genutzt wurde

---

# 18. Empfohlenes Zugdatenmodell

```python
TrainFamily:
    id
    services: list[Service]
    relations: list[Relation]

Service:
    zid: int
    name: str
    initial_plan_schedule: list[SchedulePoint]
    current_schedule: list[SchedulePoint]
    events: list[TrainEvent]
    status: str
    delay_minutes: int | None
    current_track_raw: str | None
    planned_track_raw: str | None

SchedulePoint:
    raw_name: str
    raw_plan_name: str
    location: Location
    planned_arrival: Time | None
    planned_departure: Time | None
    actual_arrival: Time | None
    actual_departure: Time | None
    raw_flags: str
    parsed_flags: list

Relation:
    type: str
    source_zid: int
    target_zid: int | None
    location: Location | None
```

Mögliche Relationstypen:

```text
continuation
turnback
coupling
splitting
rename
locomotive_runaround
locomotive_change
```

---

# 19. Empfohlener Live-Ablauf

## 19.1 Programmstart

```text
TCP verbinden
→ registrieren
→ anlageninfo
→ simzeit
→ bahnsteigliste / Konfiguration laden
→ zugliste
```

Für jede neue normale ZID:

```text
zugdetails
→ zugfahrplan
→ initialen Fahrplan sichern
→ Flags/Relationen auswerten
→ relevante Events abonnieren
```

## 19.2 Laufender Betrieb

- `zugliste` etwa alle **2 Minuten Simulationszeit** aktualisieren.
- neue ZIDs initialisieren.
- Events asynchron verarbeiten.
- Simulationszeit synchron halten.
- Fahrplan nicht destruktiv überschreiben.
- Event-Duplikate deduplizieren.
- verschwundene ZIDs als abgeschlossen markieren.

---

# 20. Bildfahrplan: Konsequenzen

Für einen Live-Bildfahrplan benötigen wir mindestens:

- Betriebsstellen-/Fahrplanpunktreihenfolge
- Plan-Ankunft/-Abfahrt
- aktuelle Verspätung
- Ist-Ankunft/-Abfahrt aus Events
- Simulationszeit
- Plan-/Istgleis
- Zugbeziehungen (Wende, Kuppeln, Flügeln)

Empfohlene Darstellung:

- Plantrasse aus `initial_plan_schedule`
- bereits gefahrene Isttrasse aus echten Events
- zukünftige Prognose zunächst aus Planzeit + aktueller Verspätung
- aktuelle Simulationszeit als vertikale Linie
- Zugnummer/Zugname an Trasse
- Gleisänderungen optional markieren
- Lokumlauf/Richtungswechsel/Sonderaktionen als kleine Symbole oder Annotationen

Bei Haltepunkten ohne Gleisauflösung wird nur die Betriebsstelle dargestellt.

---

# 21. Spätere Gleisbelegungsanzeige: Konsequenzen

Eine Gleisbelegung darf nicht nur Stringgleichheit vergleichen.

Benötigt werden:

- `physical_track`
- `track_section`
- `stop_position`
- Konfliktbeziehungen
- Vollgleis-/Teilgleislogik

Beispiel:

```text
15:20–15:27  RE 1   Gleis 3N
15:21–15:29  RE 2   Gleis 3S
```

kann zulässig sein.

Dagegen:

```text
15:18–15:31  ICE 1  Gleis 3
```

sollte sowohl `3N` als auch `3S` blockieren, sofern die Stellwerkskonfiguration dies so definiert.

`TU 3`, `TU 3 Flügel`, `TU 3 Kuppel` können auf dasselbe physische Gleis wirken, müssen aber als unterschiedliche Haltepositionen erhalten bleiben.

Dasselbe gilt für bestätigte Varianten wie:

```text
TU 1
TU 1G
```

wenn `1G` lediglich einen abweichenden Haltepunkt auf demselben physischen Gleis bezeichnet.

Dagegen dürfen Bezeichnungen wie:

```text
5a
5b
```

nicht ohne stellwerksspezifische Kenntnis zusammengelegt werden. Sie können Abschnitte desselben Gleises sein, aber ebenso zwei verschiedene physische Gleise.

---

# 22. Weitere mögliche Tools auf Basis derselben Daten

Neben dem Bildfahrplan sind möglich:

- Live-Zugmonitor
- Ankunfts-/Abfahrtstafel
- Verspätungsmonitor
- Verspätungshistorie je Zug
- Plan-/Ist-Vergleich
- Gleisänderungsmonitor
- Stellwerks-Ein-/Ausgangsliste
- Session-Statistik
- Rot-Halt-Statistik
- Verspätungsaufbau/-abbau je Abschnitt
- Kreuzungs-/Überholungsanalyse
- Kuppel-/Flügelvisualisierung
- Zuglauf-Familienansicht
- Gleisbelegungsanzeige
- Gleiskonfliktanalyse
- Infrastruktur-/Topologiegraph
- Dispositionsdashboard

---

# 23. Bereits praktisch bestätigte Kernpunkte

Die folgenden Punkte können für die weitere Implementierung als belastbar behandelt werden:

1. Verbindung und Registrierung über die lokale TCP/XML-Schnittstelle funktionieren.
2. Mehrzeilige XML-Container müssen gesammelt werden.
3. `zugliste` liefert ZID + Zugname und verändert sich während der Simulation.
4. Eine Aktualisierung der Zugliste etwa alle 2 Minuten Simulationszeit ist für unser Design ausreichend.
5. Fahrpläne werden ungefähr 60 Minuten vor planmäßiger Einfahrt verfügbar; verbundene Folgefahrpläne können gleichzeitig erscheinen.
6. Fahrpläne verschwinden u. a. bei Ausfahrt bzw. wenn ein Zug beim Kuppeln in einem anderen aufgeht.
7. `zugdetails` liefert Verspätung, Gleis, Plangleis und Sichtbarkeits-/Standstatus.
8. `zugfahrplan` liefert Fahrplanpunkte mit `name`, `plan`, `an`, `ab`, `flags`.
9. `zugfahrplan` kann nach Fortschritt nur noch den verbleibenden Fahrplanteil enthalten.
10. Gleisänderungen werden sauber als `name != plan` bzw. `gleis != plangleis` sichtbar.
11. Ankunfts-/Abfahrts-/Rothalt-/Grün-/Kuppel-/Flügel-Events funktionieren grundsätzlich.
12. Events können mehrfach identisch auftreten und müssen dedupliziert werden.
13. `Lok ...`-Einträge können als temporäre Lokumlaufbewegungen auftauchen und sollen separat behandelt werden.
14. Bahnsteig-/Gleisnamen sind nicht einheitlich und dürfen nicht nur anhand eines vermeintlichen RIL100-Präfixes interpretiert werden.
15. Ausgeschriebene Betriebsstellennamen, reine Gleisnummern und Mischformen sind möglich.
16. Haltepunkte können ohne Gleisnummer erscheinen, obwohl real mehrere Gleise/Bahnsteige existieren.
17. Zusätze wie `Flügel`/`Kuppel` können nur unterschiedliche Haltepositionen desselben physischen Gleises darstellen.
18. Ein Suffix wie `G` kann stellwerksspezifisch eine andere Halteposition auf demselben physischen Gleis kennzeichnen, z. B. für lange Güterzüge; daraus darf keine universelle Parserregel entstehen.
19. Teilgleise wie `3N`, `3S`, `3 N`, `3 S` können Abschnitte **desselben physischen Gleises** sein und müssen vom Vollgleis `3` getrennt modelliert werden.
20. Buchstabensuffixe wie `a`/`b` sind nicht eindeutig: `5a` und `5b` können Abschnitte desselben Gleises, aber auch verschiedene physische Gleise sein.
21. Originalbezeichnungen müssen immer unverändert erhalten bleiben.
22. Stellwerksspezifische Mapping-/Normalisierungskonfigurationen sind für universelle Tools sinnvoll.

---

# 24. Noch offene bzw. vorsichtig zu behandelnde Punkte

Nicht ohne zusätzliche Tests verallgemeinern:

- Exaktes Verhalten von `einfahrt`-Events in allen Stellwerken.
- Exaktes Verhalten von `ausfahrt`-Events in allen Stellwerken.
- Ob abstrakte Haltepunkte über `wege` zuverlässig einem konkreten Streckengleis zugeordnet werden können.
- Vollständige Semantik aller Fahrplan-Flags.
- Ob alle Stellwerke dieselben Namensmuster für `Flügel`, `Kuppel`, `G`, Nord/Süd/Ost/West verwenden.
- Welche konkrete Bedeutung Buchstabensuffixe wie `a`, `b`, `G` usw. in einem jeweiligen Stellwerk haben.
- Ob `a`/`b` in einer konkreten Betriebsstelle Abschnitte desselben physischen Gleises oder getrennte physische Gleise bezeichnen.
- Ob negative ZIDs ausschließlich für temporäre Lokbewegungen vorkommen.
- Welche Event-Duplikate generell auftreten und ob das Verhalten stellwerks-/situationsabhängig ist.

---

# 25. Implementierungs-Invarianten für Codex

Diese Regeln sollten im Code grundsätzlich gelten:

1. **Raw XML nie wegwerfen.**
2. **Originalnamen nie überschreiben.** Normalisierte Werte separat speichern.
3. **Planfahrplan nie destruktiv überschreiben.**
4. **Verschwundene Züge nicht löschen.** Nur Status ändern.
5. **ZID ist technische Identität, nicht automatisch der komplette fachliche Zuglauf.**
6. **Folgefahrpläne/Kuppeln/Flügeln als Relationen modellieren.**
7. **`name` und `plan` getrennt halten.**
8. **`gleis` und `plangleis` getrennt halten.**
9. **Events deduplizieren.**
10. **Simulationszeit statt PC-Zeit für betriebliche Historie verwenden.**
11. **Betriebsstelle nicht allein aus einem Präfix erraten.**
12. **Physisches Gleis, Gleisabschnitt und Halteposition getrennt modellieren.**
13. **`3N`/`3S` nicht als eigenständige physische Gleise behandeln, wenn sie laut Stellwerkskonfiguration Abschnitte desselben Gleises sind.**
14. **Suffixe wie `a`/`b`/`G` niemals allein anhand ihrer Schreibweise universell deuten.**
15. **Bei fehlender Gleisauflösung keine Gleisnummer oder Ressourcenbeziehung erfinden.**
16. **Automatische Normalisierung muss korrigierbar sein.**
17. **Stellwerksspezifische Konfiguration anhand Anlagen-ID/Name speichern.**
18. **`zugliste` etwa alle 2 Minuten Simulationszeit erneut abfragen.**
19. **Neue ZIDs sofort initialisieren und Events abonnieren.**
20. **Temporäre `Lok ...`-Züge nicht als normale Bildfahrplantrassen behandeln.**

---

# 26. Empfohlene Repository-Struktur

Beispiel:

```text
/docs
    STS_Schnittstellen_Handbuch.md
/config
    stellwerke/
        823_immenstadt.json
/src
    protocol/
    model/
    normalization/
    events/
    ui/
```

Codex sollte dieses Dokument vor Änderungen an Protokoll-, Zugmodell-, Fahrplan-, Gleis- oder Eventlogik als fachliche Grundlage heranziehen.
