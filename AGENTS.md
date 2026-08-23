# AGENTS.md

## Zweck

Dieses Repository enthält Werkzeuge für die StellwerkSim-Plugin-Schnittstelle und insbesondere einen geplanten Live-Bildfahrplan.

Vor Änderungen an Schnittstellenlogik, Zugmodell, Fahrplanverarbeitung, Ereignisverarbeitung, Bahnsteig-/Gleisnormalisierung oder daraus abgeleiteten Tools muss zuerst das Handbuch gelesen werden:

`docs/STS_Schnittstellen_Handbuch.md`

Das Handbuch enthält sowohl offiziell dokumentierte Eigenschaften der StellwerkSim-Schnittstelle als auch experimentell bestätigte Beobachtungen aus Testlogs und projektspezifische Modellierungsregeln.

---

## Verbindliche Arbeitsregeln

1. **Handbuch zuerst lesen.**  
   Bei Änderungen an folgenden Bereichen ist `docs/STS_Schnittstellen_Handbuch.md` die primäre projektspezifische Referenz:
   - TCP/XML-Protokoll
   - Zugliste
   - Zugdetails
   - Zugfahrplan
   - Events
   - Fahrplanflags
   - Zuglebenszyklen
   - Kuppeln / Flügeln / Folgefahrpläne
   - Lokumläufe
   - Bahnsteige und Gleise
   - Gleisnormalisierung
   - Gleisbelegung
   - Infrastrukturgraph / `wege`

2. **Keine Annahmen als Protokollfakten behandeln.**  
   Zwischen folgenden Kategorien unterscheiden:
   - offiziell dokumentiert
   - durch Testlogs bestätigt
   - durch Nutzererfahrung bekannt
   - aus Daten abgeleitet / heuristisch
   - noch ungeklärt

3. **Rohdaten immer erhalten.**  
   Originalwerte aus StellwerkSim dürfen bei Normalisierung nicht verloren gehen.  
   Beispiel:
   - `raw_name = "TU 3 Flügel"`
   - normalisiert: Betriebsstelle `TU`, physisches Gleis `3`, Halteposition `Flügel`

4. **Fahrplanpunkt ist nicht automatisch physisches Gleis.**  
   Namen wie `Martinszell` können einen Fahrplan-/Haltepunkt darstellen, obwohl mehrere reale Bahnsteige oder Streckengleise existieren.

5. **Betriebsstellen nicht ausschließlich aus Präfixen ableiten.**  
   Gleise können z. B. heißen:
   - `TU 3`
   - `Ulm Hbf 3`
   - `Gleis 3`
   - `3`
   - `4N`
   - `4 S`

   RIL100-Kürzel, ausgeschriebene Namen und fehlende Präfixe sind möglich.

6. **Gleisabschnitte nicht mit Vollgleisen gleichsetzen.**  
   `3`, `3N`, `3 N`, `3S`, `3 S` können unterschiedliche Belegungslogik haben.  
   Ein Vollgleis `3` kann beide Teilabschnitte blockieren, während `3N` und `3S` unter Umständen gleichzeitig nutzbar sind.

7. **Simulatorbedingte Haltepositionen separat modellieren.**  
   Bezeichnungen wie:
   - `TU 3`
   - `TU 3 Flügel`
   - `TU 3 Kuppel`

   können dasselbe physische Gleis meinen, aber unterschiedliche Haltepositionen.

8. **Plan und Ist getrennt speichern.**  
   Bei Gleisänderungen:
   - `plan` / `plangleis` = ursprünglicher Sollzustand
   - `name` / `gleis` = aktuell disponierter bzw. tatsächlicher Zustand

   Planwerte dürfen nicht überschrieben werden.

9. **Zugfahrpläne frühzeitig cachen.**  
   `zugfahrplan` enthält nur den noch verbleibenden Teil. Bereits abgearbeitete Fahrplanpunkte verschwinden.

10. **Zugliste regelmäßig aktualisieren.**  
    Die Zugliste soll ungefähr alle **2 Minuten Simulationszeit** neu abgefragt werden.

11. **Neue ZIDs initialisieren.**  
    Für neu auftauchende relevante Züge:
    - Zugdetails abrufen
    - Zugfahrplan abrufen
    - Planfahrplan lokal speichern
    - relevante Events abonnieren
    - Beziehungen zu Folgefahrplänen / Kuppel- / Flügelvorgängen erfassen

12. **Verschwundene Züge nicht aus der Historie löschen.**  
    Ein Zug kann aus der aktuellen Zugliste verschwinden, weil er:
    - das Stellwerk verlassen hat
    - gekuppelt wurde
    - in einen anderen Zuglauf übergegangen ist

    Historische Plan- und Ist-Daten müssen erhalten bleiben.

13. **Zuglauf-Familien statt nur einzelner ZIDs modellieren.**  
    Folgefahrpläne, Wenden, Kuppeln und Flügeln können mehrere ZIDs logisch miteinander verbinden.

14. **`Lok ...`-Einträge nicht als normale Zugtrassen behandeln.**  
    Negative ZIDs bzw. temporäre `Lok ...`-Einträge entstehen bei Lokbewegungen wie Umsetzen.  
    Sie können für den normalen Bildfahrplan ausgeblendet, aber als Betriebsereignis am Stammzug gekennzeichnet werden.

15. **Events deduplizieren / zustandsbasiert verarbeiten.**  
    Ereignisse wie `rothalt` und `abfahrt` können mehrfach identisch eintreffen.  
    Nicht jedes empfangene Event ist automatisch ein neues reales Betriebsereignis.

16. **Netzwerkempfang und GUI trennen.**  
    Netzwerk-I/O darf die Oberfläche nicht blockieren.  
    GUI-Updates müssen thread-sicher erfolgen.

17. **Mehrzeilige XML-Container korrekt zusammensetzen.**  
    Eine einzelne LF-terminierte Protokollzeile ist nicht zwingend ein vollständiges XML-Dokument.  
    Container wie `zugliste`, `bahnsteigliste`, `wege` und `zugfahrplan` müssen vollständig gesammelt und erst dann als Ganzes verarbeitet werden.

18. **Heuristiken müssen korrigierbar sein.**  
    Automatische Zuordnungen von Betriebsstellen, Gleisen, Teilgleisen und Haltepositionen dürfen niemals unumkehrbar sein.  
    Eine manuelle Mapping-/Konfigurationsebene ist vorzusehen.

---

## Bevorzugte Datenmodellierung

Die genaue Implementierung kann variieren, sollte aber die folgenden Konzepte getrennt halten:

```text
TrainFamily
    services[]
    relations[]

Service
    zid
    name
    plan_schedule[]
    current_schedule[]
    events[]
    status

SchedulePoint
    raw_name
    operating_point
    plan_track
    actual_track
    track_section
    stop_position
    planned_arrival
    planned_departure
    actual_arrival
    actual_departure
    flags

Location
    raw_name
    operating_point
    physical_track
    track_section
    stop_position
    track_resolution

Relation
    type:
        continuation
        coupling
        splitting
        rename
        locomotive_runaround
        locomotive_change
```

`track_resolution` sollte ausdrücken können, wie präzise die Information tatsächlich ist, z. B.:

```text
exact
section
abstract
unknown
```

Keine höhere Genauigkeit vortäuschen als die Schnittstelle liefert.

---

## Verhalten bei Unklarheiten

Wenn eine Implementierung eine nicht dokumentierte Annahme benötigt:

1. zuerst im Handbuch nachsehen,
2. vorhandene Tests und Logs prüfen,
3. Annahme im Code deutlich kennzeichnen,
4. bevorzugt einen reproduzierbaren Testfall ergänzen,
5. keine stillen Heuristiken einbauen, die später wie gesicherte Protokolleigenschaften wirken.

---

## Tests

Neue Änderungen an der Schnittstellenlogik sollten nach Möglichkeit mindestens abdecken:

- fragmentierte TCP-Empfangsdaten
- mehrere XML-Zeilen in einem `recv`
- mehrzeilige XML-Container
- asynchrone Events
- doppelte Events
- neue und verschwundene ZIDs
- Plan-/Istgleis-Abweichungen
- Fahrplanpunkte ohne Gleisnummer
- uneinheitliche Gleisnamen
- Gleisabschnitte wie `3N` / `3 S`
- simulatorbedingte Haltepositionen wie `Flügel` / `Kuppel`
- negative ZIDs / `Lok ...`

---

## Priorität

Bei Konflikten zwischen einer bequemen Implementierung und einer fachlich sauberen Abbildung der StellwerkSim-Daten gilt:

**Daten korrekt und verlustfrei modellieren; Darstellung und Komfort darauf aufbauen.**
