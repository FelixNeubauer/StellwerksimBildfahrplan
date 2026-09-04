"""Visueller Editor fuer den autoritativen, manuellen Streckengraphen."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from infrastructure import (
    CorridorGraphBuilder, EditableTopologyGraph, EditableTopologyGraphStore,
    InfrastructureGraphBuilder, OperatingPointResolver, RawInfrastructureGraph,
    SavedStellwerkIdentity, SchedulePointGraph, archive_artifact,
    find_identity_candidate, parse_bahnsteigliste, parse_wege, save_generated_graph,
    TopologyTargetRegistry,
    estimate_kilometrage, validate_kilometrage,
    validate_saved_stellwerk_identity,
)
from app.widgets.topology_graphics import (
    EditorMode, TopologyEdgeItem, TopologyGraphicsScene, TopologyGraphicsView, TopologyNodeItem,
)

TYPE_LABELS = {
    "line": "Streckenbetriebsstelle", "entry": "Einfahrt", "junction": "Abzweigbetriebsstelle",
}
ID_ROLE = QtCore.Qt.ItemDataRole.UserRole
KILOMETRE_COLUMN = 3


class KilometrageTable(QtWidgets.QTableWidget):
    """Tabelle, deren Tab-Kette ausschließlich die Kilometerspalte umfasst."""

    def __init__(self, rows: int, columns: int, parent=None) -> None:
        super().__init__(rows, columns, parent)
        QtWidgets.QApplication.instance().installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:
        if (event.type() == QtCore.QEvent.Type.KeyPress
                and event.key() in (QtCore.Qt.Key.Key_Tab, QtCore.Qt.Key.Key_Backtab)
                and isinstance(watched, QtWidgets.QLineEdit) and self.isAncestorOf(watched)):
            backwards = (event.key() == QtCore.Qt.Key.Key_Backtab
                         or bool(event.modifiers() & QtCore.Qt.KeyboardModifier.ShiftModifier))
            row = self.currentRow()
            self.item(row, KILOMETRE_COLUMN).setText(watched.text())
            self.closeEditor(watched, QtWidgets.QAbstractItemDelegate.EndEditHint.NoHint)
            self.edit_kilometre((row + (-1 if backwards else 1)) % self.rowCount())
            return True
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event) -> None:
        if event.key() in (QtCore.Qt.Key.Key_Tab, QtCore.Qt.Key.Key_Backtab) and self.rowCount():
            backwards = (event.key() == QtCore.Qt.Key.Key_Backtab
                         or bool(event.modifiers() & QtCore.Qt.KeyboardModifier.ShiftModifier))
            row = self.currentRow() if self.currentRow() >= 0 else (0 if backwards else -1)
            self.edit_kilometre((row + (-1 if backwards else 1)) % self.rowCount())
            return
        super().keyPressEvent(event)

    def edit_kilometre(self, row: int) -> None:
        self.setCurrentCell(row, KILOMETRE_COLUMN)
        self.editItem(self.item(row, KILOMETRE_COLUMN))
        QtCore.QTimer.singleShot(0, self._select_editor_text)

    def _select_editor_text(self) -> None:
        editor = self.focusWidget()
        if isinstance(editor, QtWidgets.QLineEdit):
            editor.selectAll()


class KilometrageDialog(QtWidgets.QDialog):
    """Transaktionaler Editor für genau eine Bildfahrplan-Streckeninstanz."""

    def __init__(self, route, nodes, ordered_node_ids, initial_values, auto_values, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Streckenkilometer – {route.display_name}")
        self.resize(660, 480)
        self._ordered_node_ids = tuple(ordered_node_ids)
        self._auto_values = tuple(auto_values)
        layout = QtWidgets.QVBoxLayout(self)
        heading = QtWidgets.QLabel(f"<b>{route.display_name}</b><br>Links: {nodes[ordered_node_ids[0]].display_name}")
        layout.addWidget(heading)
        reset = QtWidgets.QPushButton("Automatisch ausfüllen")
        reset.setToolTip("Manuelle Eingaben durch die Schätzung aus planmäßigen Fahrzeiten ersetzen")
        reset.clicked.connect(self._fill_automatically); layout.addWidget(reset, alignment=QtCore.Qt.AlignmentFlag.AlignLeft)
        self.table = KilometrageTable(len(ordered_node_ids), 4)
        self.table.setHorizontalHeaderLabels(("Nr.", "Name", "Typ", "Kilometer"))
        self.table.verticalHeader().hide(); self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.Stretch)
        for row, node_id in enumerate(ordered_node_ids):
            node = nodes[node_id]
            for column, text in enumerate((str(row + 1), node.display_name,
                                           "Einfahrt" if node.node_type == "entry" else "Betriebsstelle")):
                item = QtWidgets.QTableWidgetItem(text)
                item.setFlags(item.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(row, column, item)
            value = initial_values.get(node_id)
            self.table.setItem(row, 3, QtWidgets.QTableWidgetItem("" if value is None else f"{value:.3f}"))
        self.table.itemChanged.connect(self._validate); layout.addWidget(self.table)
        self.validation_label = QtWidgets.QLabel(); self.validation_label.setWordWrap(True)
        layout.addWidget(self.validation_label)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Save |
                                             QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept_if_valid); buttons.rejected.connect(self.reject)
        self.save_button = buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Save)
        self.save_button.setText("Speichern"); buttons.button(
            QtWidgets.QDialogButtonBox.StandardButton.Cancel).setText("Abbrechen")
        layout.addWidget(buttons); self._validate()

    def _texts(self):
        return [self.table.item(row, 3).text() if self.table.item(row, 3) else ""
                for row in range(self.table.rowCount())]

    def _validate(self, *_args) -> None:
        result = validate_kilometrage(self._texts(), len(self._ordered_node_ids))
        self.save_button.setEnabled(result.valid)
        self.validation_label.setText("✓ Gültige monotone Kilometrierung" if result.valid else f"⚠ {result.message}")
        self.validation_label.setStyleSheet("color: #287a32" if result.valid else "color: #a03030")

    def _fill_automatically(self) -> None:
        self.table.blockSignals(True)
        for row, value in enumerate(self._auto_values):
            self.table.item(row, 3).setText(f"{value:.3f}")
        self.table.blockSignals(False); self._validate()

    def _accept_if_valid(self) -> None:
        result = validate_kilometrage(self._texts(), len(self._ordered_node_ids))
        if not result.valid:
            QtWidgets.QMessageBox.warning(self, "Ungültige Kilometrierung", result.message); return
        self.values = dict(zip(self._ordered_node_ids, result.values)); self.accept()


class MoveNodeCommand(QtGui.QUndoCommand):
    def __init__(self, tab, node_id, old, new) -> None:
        super().__init__("Betriebsstelle verschieben")
        self.tab, self.node_id, self.old, self.new = tab, node_id, old, new
        self._first = True

    def _set(self, value) -> None:
        item = self.tab.node_items.get(self.node_id)
        if item: item.setPos(value)
        self.tab._mark_dirty()

    def redo(self) -> None:
        if self._first: self._first = False; return
        self._set(self.new)

    def undo(self) -> None: self._set(self.old)


class InfrastructureTab(QtWidgets.QWidget):
    def __init__(self, config_directory, parent=None, *, target_registry_provider=None,
                 target_registry_ready_provider=None) -> None:
        super().__init__(parent)
        self.config_directory = Path(config_directory)
        self.store = EditableTopologyGraphStore(config_directory)
        self.graph = EditableTopologyGraph()
        self.aid = None; self.facility_name = None
        self._last_signature = None; self._automatic_graph = None; self._automatic_source = None
        self._target_registry = TopologyTargetRegistry()
        self._target_registry_provider = target_registry_provider
        self._target_registry_ready_provider = target_registry_ready_provider
        self._services = ()
        self._dirty = False; self._identity_ready = False; self._loading = False
        self._route_path: list[str] | None = None
        self._route_endpoints: list[str] | None = None; self._editing_route_id: str | None = None
        self.node_items: dict[str, TopologyNodeItem] = {}; self.edge_items: dict[str, TopologyEdgeItem] = {}
        self.undo_stack = QtGui.QUndoStack(self)
        self.autosave_timer = QtCore.QTimer(self); self.autosave_timer.setSingleShot(True)
        self.autosave_timer.setInterval(30_000); self.autosave_timer.timeout.connect(self.flush_pending_save)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QtWidgets.QVBoxLayout(self); toolbar = QtWidgets.QHBoxLayout()
        self.regenerate = QtWidgets.QPushButton("Graph automatisch neu erzeugen")
        self.regenerate.clicked.connect(self._regenerate_graph); toolbar.addWidget(self.regenerate)
        auto_layout = QtWidgets.QPushButton("Auto-Layout"); auto_layout.clicked.connect(self._auto_layout)
        toolbar.addWidget(auto_layout)
        self.mode_group = QtWidgets.QButtonGroup(self); self.mode_group.setExclusive(True)
        for mode, text in ((EditorMode.NAVIGATE, "✋ Umsehen"), (EditorMode.RECTANGLE, "▧ Auswahl"),
                           (EditorMode.CONNECT, "✎ Verbinden")):
            button = QtWidgets.QToolButton(); button.setText(text); button.setCheckable(True)
            if mode == EditorMode.RECTANGLE:
                button.setToolTip("Mehrere Elemente mit einem Auswahlrechteck markieren")
            button.setProperty("editor_mode", mode); self.mode_group.addButton(button); toolbar.addWidget(button)
            if mode == EditorMode.NAVIGATE: button.setChecked(True)
        self.mode_group.buttonClicked.connect(lambda button: self._set_editor_mode(button.property("editor_mode")))
        delete = QtWidgets.QPushButton("Löschen"); delete.clicked.connect(self._delete_selected); toolbar.addWidget(delete)
        toolbar.addWidget(self._tool_button("Undo", self.undo_stack.undo)); toolbar.addWidget(self._tool_button("Redo", self.undo_stack.redo))
        toolbar.addStretch(); self.search = QtWidgets.QLineEdit(); self.search.setPlaceholderText("Suche …")
        self.search.textChanged.connect(self._search); toolbar.addWidget(self.search); root.addLayout(toolbar)

        self.scene = TopologyGraphicsScene(self); self.scene.selectionChanged.connect(self._selection_changed)
        self.scene.nodeActivated.connect(self._node_activated)
        self.view = TopologyGraphicsView(self.scene); self.view.deletePressed.connect(self._delete_selected)
        self.view.connectionRequested.connect(self._connect_nodes)
        self.view.connection_validator = lambda first, second: self.graph.edge_between(first, second) is None
        inspector = self._build_inspector()
        split = QtWidgets.QSplitter(); split.addWidget(self.view); split.addWidget(inspector)
        split.setStretchFactor(0, 5); split.setStretchFactor(1, 1); root.addWidget(split, 5)

        lower = QtWidgets.QSplitter(); lower.addWidget(self._build_routes_panel()); lower.addWidget(self._build_bildfahrplan_panel())
        lower.setStretchFactor(0, 1); lower.setStretchFactor(1, 1); root.addWidget(lower, 2)
        self.status = QtWidgets.QLabel("Noch keine Graphdaten verfügbar."); root.addWidget(self.status)

    @staticmethod
    def _tool_button(text, callback):
        button = QtWidgets.QPushButton(text); button.clicked.connect(callback); return button

    def _build_inspector(self):
        box = QtWidgets.QGroupBox("Eigenschaften"); form = QtWidgets.QFormLayout(box)
        self.inspector_name = QtWidgets.QLineEdit(); self.inspector_name.editingFinished.connect(self._edit_node)
        self.inspector_type = QtWidgets.QComboBox()
        for value, label in TYPE_LABELS.items(): self.inspector_type.addItem(label, value)
        self.inspector_type.currentIndexChanged.connect(self._edit_node)
        self.inspector_connections = QtWidgets.QLabel("–"); self.inspector_id = QtWidgets.QLabel("–")
        self.inspector_id.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        self.inspector_source = QtWidgets.QLabel("–"); self.inspector_validation = QtWidgets.QLabel("Keine Auswahl")
        self.inspector_diagnostics = QtWidgets.QLabel("–")
        self.inspector_diagnostics.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        self.inspector_diagnostics.setWordWrap(True)
        self.inspector_validation.setWordWrap(True)
        form.addRow("Name:", self.inspector_name); form.addRow("Typ:", self.inspector_type)
        form.addRow("Verbindungen:", self.inspector_connections); form.addRow("ID:", self.inspector_id)
        form.addRow("Quelle:", self.inspector_source); form.addRow("Validierung:", self.inspector_validation)
        form.addRow("Diagnose:", self.inspector_diagnostics)
        return box

    def _build_routes_panel(self):
        box = QtWidgets.QGroupBox("Definierte Strecken"); layout = QtWidgets.QVBoxLayout(box)
        self.route_list = QtWidgets.QListWidget(); self.route_list.currentItemChanged.connect(self._route_selected)
        layout.addWidget(self.route_list)
        self.route_path_label = QtWidgets.QLabel("Keine Strecke ausgewählt"); self.route_path_label.setWordWrap(True)
        layout.addWidget(self.route_path_label)
        buttons = QtWidgets.QHBoxLayout(); add = QtWidgets.QPushButton("+ Strecke definieren")
        add.clicked.connect(self._start_route); buttons.addWidget(add)
        change = QtWidgets.QPushButton("Pfad ändern"); change.clicked.connect(self._change_route_path)
        buttons.addWidget(change)
        rename = QtWidgets.QPushButton("Umbenennen"); rename.clicked.connect(self._rename_route); buttons.addWidget(rename)
        remove = QtWidgets.QPushButton("Löschen"); remove.clicked.connect(self._delete_route); buttons.addWidget(remove)
        layout.addLayout(buttons); return box

    def _build_bildfahrplan_panel(self):
        box = QtWidgets.QGroupBox("Bildfahrplan"); layout = QtWidgets.QVBoxLayout(box)
        self.bf_list = QtWidgets.QListWidget(); self.bf_list.setDragDropMode(QtWidgets.QAbstractItemView.DragDropMode.InternalMove)
        self.bf_list.setDefaultDropAction(QtCore.Qt.DropAction.MoveAction)
        self.bf_list.model().rowsMoved.connect(self._instances_reordered); layout.addWidget(self.bf_list)
        add = QtWidgets.QPushButton("+ Strecke hinzufügen"); add.clicked.connect(self._add_instance); layout.addWidget(add)
        return box

    def refresh(self, snapshot) -> None:
        self._services = tuple(snapshot.services)
        signature = (snapshot.aid, snapshot.facility_name, snapshot.infrastructure_documents,
                     tuple((service.zid, len(service.original_schedule), getattr(service, "origin", None),
                            getattr(service, "destination", None)) for service in snapshot.services))
        identity_changed = (snapshot.aid, snapshot.facility_name) != (self.aid, self.facility_name)
        if identity_changed:
            self.flush_pending_save(); self.aid = snapshot.aid; self.facility_name = snapshot.facility_name
            self._identity_ready = False; self._last_signature = None
        if signature == self._last_signature: return
        self._last_signature = signature
        try:
            automatic, context = self._build_automatic(snapshot)
            self._automatic_graph = automatic
            self._automatic_source = context[2]
            if not self._identity_ready and self.aid is not None and self.facility_name:
                saved_exists = self.store.path_for(self.aid).exists()
                registry_ready = (self._target_registry_ready_provider() if
                                  self._target_registry_ready_provider else True)
                if saved_exists or registry_ready:
                    self._load_or_initialize(automatic)
            elif self._identity_ready:
                before = self.graph.to_dict(); self._apply_supplements(self.graph)
                if self.graph.to_dict() != before:
                    self._mark_dirty(); self._rebuild_scene()
            raw, builder, operational, platforms, schedule, operating, corridor = context
            if self.aid is not None:
                save_generated_graph(self.config_directory, self.aid, self.facility_name or "unbekannt",
                                     raw, builder.anchors, operational, platforms, schedule=schedule,
                                     operating=operating, corridor=corridor)
        except (ValueError, StopIteration, json.JSONDecodeError, OSError) as exc:
            self.status.setText(f"Graphdaten konnten nicht ausgewertet werden: {exc}")

    def _build_automatic(self, snapshot):
        wege_xml = next((raw for raw in reversed(snapshot.infrastructure_documents)
                         if raw.lstrip().startswith("<wege")), None)
        raw = parse_wege(wege_xml) if wege_xml else RawInfrastructureGraph()
        builder = InfrastructureGraphBuilder(raw); schedule = SchedulePointGraph.from_services(snapshot.services)
        builder.resolve_names(schedule.nodes)
        platform_xml = next((raw_xml for raw_xml in reversed(snapshot.infrastructure_documents)
                             if raw_xml.lstrip().startswith("<bahnsteigliste")), None)
        platforms = parse_bahnsteigliste(platform_xml) if platform_xml else ()
        operating = OperatingPointResolver(platforms, aid=snapshot.aid).resolve(schedule)
        corridor = CorridorGraphBuilder(schedule, operating, raw).build(); operational = corridor.to_operational_graph()
        self._target_registry = (self._target_registry_provider()
                                 if self._target_registry_provider else TopologyTargetRegistry())
        graph = EditableTopologyGraph.from_registry_projection(operational, self._target_registry)
        return graph, (raw, builder, operational, platforms, schedule, operating, corridor)

    def _apply_supplements(self, graph: EditableTopologyGraph) -> None:
        graph.add_registry_supplements(self._target_registry)

    def _raw_to_topology_node(self, node_ids) -> dict[str, str]:
        result = dict(self._target_registry.raw_to_target)
        for node_id in node_ids:
            node = self.graph.nodes.get(node_id)
            if node is None:
                continue
            for key in ("raw_names", "target_raw_members"):
                for raw_name in node.metadata.get(key, ()):
                    result.setdefault(raw_name, node_id)
        return result

    def _estimated_route_kilometrage(self, route) -> dict[str, float]:
        ordered = route.ordered_node_ids
        estimate = estimate_kilometrage(
            ordered, {node_id: self.graph.nodes[node_id].node_type for node_id in ordered
                      if node_id in self.graph.nodes}, self._services,
            self._raw_to_topology_node(ordered))
        return dict(zip(ordered, estimate.kilometres))

    def _ensure_kilometrages(self) -> bool:
        """Ergänzt Legacy-Defaults und leere Legacy-Instanzen deterministisch."""
        return bool(self.graph.initialize_missing_kilometrages(self._estimated_route_kilometrage))

    def _load_or_initialize(self, automatic) -> None:
        current = SavedStellwerkIdentity(self.aid, self.facility_name)
        path = self.store.path_for(self.aid)
        validation = (validate_saved_stellwerk_identity(self.store.load_path(path), current, path)
                      if path.exists() else find_identity_candidate(self.store.directory, current,
                                                                   self.store.ARTIFACT_TYPE))
        data = None
        if validation.status == "match": data = self.store.load_path(path)
        elif validation.status in {"name_changed", "aid_changed", "legacy_identity_confirmation"}:
            message = "Die gespeicherten Streckendaten besitzen eine abweichende Stellwerksidentität. Trotzdem laden?"
            if QtWidgets.QMessageBox.question(self, "Gespeicherte Streckendaten prüfen", message,
                    QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
                    QtWidgets.QMessageBox.StandardButton.No) == QtWidgets.QMessageBox.StandardButton.Yes:
                data = self.store.load_path(validation.path)
                old_path = validation.path
                graph = EditableTopologyGraph.from_dict(data); self.store.save(self.aid, self.facility_name, graph)
                if old_path != path and old_path.exists(): archive_artifact(old_path)
            elif validation.path and validation.path.exists(): archive_artifact(validation.path)
        elif validation.status == "ambiguous":
            QtWidgets.QMessageBox.warning(self, "Mehrdeutige Stellwerkdateien",
                                          "Mehrere Dateien besitzen denselben Stellwerknamen; keine wird geladen.")
        elif validation.status == "different_installation" and validation.path:
            QtWidgets.QMessageBox.warning(
                self, "Anderes Stellwerk", "Die gespeicherte Streckendatei gehört zu einem anderen Stellwerk. "
                "Sie wird archiviert und nicht geladen.")
            if validation.path.exists(): archive_artifact(validation.path)
        self.graph = (EditableTopologyGraph.from_dict(data) if data else
                      EditableTopologyGraph.from_dict(automatic.to_dict()))
        legacy_migrated = bool(data and int(data.get("schema_version", 1)) < self.store.SCHEMA_VERSION)
        if legacy_migrated:
            self.graph.migrate_to_registry(self._target_registry)
        before_supplements = self.graph.to_dict(); self._apply_supplements(self.graph)
        supplements_changed = self.graph.to_dict() != before_supplements
        # Aktuelle Artefakte werden exakt geladen. Fehlende Kilometerwerte
        # werden nur im einmaligen Legacy-Pfad migriert, nie im Normalbetrieb.
        kilometrages_changed = self._ensure_kilometrages() if legacy_migrated else False
        self._identity_ready = True; self._dirty = data is None or supplements_changed or legacy_migrated
        self._dirty = self._dirty or kilometrages_changed
        self._rebuild_scene(); self._refresh_routes(); self._refresh_instances()
        if data is None or legacy_migrated: self.flush_pending_save()
        unresolved = len(self.graph.metadata.get("unmapped_legacy_nodes", ()))
        message = ("Finaler gespeicherter Graph geladen." if data
                   else "Initialgraph automatisch erzeugt und gespeichert.")
        if unresolved:
            message += (f" {unresolved} alte manuelle/automatische Knoten ohne Ortszuordnungsziel "
                        "wurden nur als Diagnose übernommen.")
        self.status.setText(message)

    def _rebuild_scene(self) -> None:
        self._loading = True; self.scene.clear(); self.node_items = {}; self.edge_items = {}
        for node in self.graph.nodes.values():
            item = TopologyNodeItem(node, self.graph); item.moved.connect(self._node_moved)
            self.scene.addItem(item); self.node_items[node.id] = item
        for edge in self.graph.edges.values():
            if edge.node_a not in self.node_items or edge.node_b not in self.node_items: continue
            item = TopologyEdgeItem(edge.id, self.node_items[edge.node_a], self.node_items[edge.node_b])
            self.scene.addItem(item); self.edge_items[edge.id] = item
            self.node_items[edge.node_a].edges.add(item); self.node_items[edge.node_b].edges.add(item)
        self.scene.setSceneRect(self.scene.itemsBoundingRect().adjusted(-120, -120, 120, 120)); self._loading = False
        self.view.set_editor_mode(self.view.editor_mode)

    def _mark_dirty(self) -> None:
        if self._loading: return
        self._dirty = True; self.autosave_timer.start(); self.status.setText("Ungespeicherte Änderungen")

    def is_dirty(self) -> bool: return self._dirty

    def flush_pending_save(self) -> None:
        if not self._dirty or not self._identity_ready or self.aid is None or not self.facility_name: return
        self.store.save(self.aid, self.facility_name, self.graph)
        self._dirty = False; self.autosave_timer.stop(); self.status.setText("Gespeichert")

    def _node_moved(self, node_id, old, new) -> None:
        self.undo_stack.push(MoveNodeCommand(self, node_id, old, new)); self._mark_dirty()

    def _selection_changed(self) -> None:
        nodes = [item for item in self.scene.selectedItems() if isinstance(item, TopologyNodeItem)]
        self._loading = True
        if len(nodes) == 1:
            node = self.graph.nodes[nodes[0].node_id]; self.inspector_name.setText(node.display_name)
            self.inspector_type.setCurrentIndex(self.inspector_type.findData(node.node_type))
            self.inspector_connections.setText(str(self.graph.degree(node.id))); self.inspector_id.setText(node.id)
            self.inspector_source.setText(node.source)
            automatic_ids = ", ".join(node.metadata.get("automatic_node_ids", ())) or "–"
            supplement = node.metadata.get("supplement_source") or "–"
            target_evidence = ", ".join(node.metadata.get("target_evidence", ())) or "–"
            self.inspector_diagnostics.setText(
                f"Target: {node.target_id or '–'} ({node.target_kind or '–'})\n"
                f"OperatingPoint: {node.operating_point_id or '–'}\n"
                f"Automatic IDs: {automatic_ids}\nSupplement: {supplement}\n"
                f"Eligibility: {target_evidence}")
            warning = self.graph.node_validation(node.id)
            self.inspector_validation.setText("⚠ " + warning if warning else "✓ gültig")
        else:
            self.inspector_name.clear(); self.inspector_connections.setText("–"); self.inspector_id.setText("–")
            self.inspector_source.setText("–"); self.inspector_validation.setText(
                f"{len(nodes)} Betriebsstellen ausgewählt" if nodes else "Keine Auswahl")
            self.inspector_diagnostics.setText("–")
        self._loading = False

    def _edit_node(self) -> None:
        if self._loading: return
        nodes = [item for item in self.scene.selectedItems() if isinstance(item, TopologyNodeItem)]
        if len(nodes) != 1: return
        node = self.graph.nodes[nodes[0].node_id]; name = self.inspector_name.text().strip()
        if name: node.display_name = name
        node.node_type = self.inspector_type.currentData(); nodes[0].setToolTip(node.display_name); nodes[0].update()
        self._mark_dirty(); self._selection_changed(); self._refresh_routes(); self._refresh_instances()

    def _set_editor_mode(self, mode: EditorMode) -> None:
        self.view.set_editor_mode(mode)
        self.status.setText({EditorMode.NAVIGATE: "Umsehen: Hintergrund verschieben oder ein Objekt bearbeiten.",
                             EditorMode.RECTANGLE: "Auswahlrechteck: mehrere Graphobjekte auswählen.",
                             EditorMode.CONNECT: "Verbinden: Von einem Knoten zu einem anderen ziehen."}[mode])

    def _node_activated(self, node_id: str) -> None:
        if self._route_endpoints is None:
            return
        if self.graph.nodes[node_id].node_type not in {"entry", "junction"}:
            self.status.setText("Start und Ende müssen Einfahrt oder Abzweigbetriebsstelle sein."); return
        if self._route_endpoints and node_id == self._route_endpoints[0]:
            self.status.setText("Start und Ende müssen verschieden sein."); return
        self._route_endpoints.append(node_id)
        self._highlight_path(self._route_endpoints)
        if len(self._route_endpoints) == 1:
            self.status.setText("Endknoten auswählen."); return
        self._complete_route_from_endpoints()

    def _connect_nodes(self, node_a: str, node_b: str) -> None:
        if self.graph.edge_between(node_a, node_b):
            self.status.setText("Diese Verbindung existiert bereits."); return
        self.graph.add_edge(node_a, node_b); self._mark_dirty(); self._rebuild_scene()
        self.status.setText("Verbindung erstellt. Verbinden bleibt aktiv.")


    def _add_node(self) -> None:
        dialog = QtWidgets.QDialog(self); dialog.setWindowTitle("Betriebsstelle hinzufügen")
        form = QtWidgets.QFormLayout(dialog); name = QtWidgets.QLineEdit(); kind = QtWidgets.QComboBox()
        for value, label in TYPE_LABELS.items(): kind.addItem(label, value)
        form.addRow("Name:", name); form.addRow("Typ:", kind)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok |
                                             QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject); form.addRow(buttons)
        if dialog.exec() and name.text().strip():
            center = self.view.mapToScene(self.view.viewport().rect().center())
            self.graph.add_node(name.text().strip(), kind.currentData(), position=(center.x(), center.y()))
            self._mark_dirty(); self._rebuild_scene()

    def _delete_selected(self) -> None:
        edges = [item.edge_id for item in self.scene.selectedItems() if isinstance(item, TopologyEdgeItem)]
        nodes = [item.node_id for item in self.scene.selectedItems() if isinstance(item, TopologyNodeItem)]
        affected = {route.route_id for node_id in nodes for route in self.graph.routes_using_node(node_id)}
        if affected:
            answer = QtWidgets.QMessageBox.question(
                self, "Betriebsstelle wird verwendet",
                f"Diese Auswahl wird von {len(affected)} definierten Strecken verwendet. Trotzdem löschen und "
                "betroffene Strecken ungültig markieren?",
                QtWidgets.QMessageBox.StandardButton.Cancel | QtWidgets.QMessageBox.StandardButton.Yes,
                QtWidgets.QMessageBox.StandardButton.Cancel)
            if answer != QtWidgets.QMessageBox.StandardButton.Yes: return
        for edge_id in edges:
            if edge_id in self.graph.edges: self.graph.delete_edge(edge_id)
        for node_id in nodes:
            if node_id in self.graph.nodes: self.graph.delete_node(node_id)
        if edges or nodes:
            self._mark_dirty(); self._rebuild_scene(); self._refresh_routes()

    def _auto_layout(self) -> None:
        self.graph.auto_layout(); self._mark_dirty(); self._rebuild_scene(); self.view.fitInView(
            self.scene.itemsBoundingRect(), QtCore.Qt.AspectRatioMode.KeepAspectRatio)

    def _regenerate_graph(self) -> None:
        if self._automatic_graph is None: return
        box = QtWidgets.QMessageBox(self); box.setIcon(QtWidgets.QMessageBox.Icon.Warning)
        box.setWindowTitle("Graph automatisch neu erzeugen")
        box.setText("Der gespeicherte Streckengraph wird durch eine neue automatische Erkennung ersetzt. "
                    "Manuelle Änderungen gehen verloren.")
        regenerate = box.addButton("Graph neu erzeugen", QtWidgets.QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("Abbrechen", QtWidgets.QMessageBox.ButtonRole.RejectRole); box.exec()
        if box.clickedButton() != regenerate: return
        self.flush_pending_save(); path = self.store.path_for(self.aid)
        if path.exists(): archive_artifact(path)
        self.graph = EditableTopologyGraph.from_dict(self._automatic_graph.to_dict())
        self.undo_stack.clear(); self._dirty = True
        self._rebuild_scene(); self._refresh_routes(); self._refresh_instances(); self.flush_pending_save()

    def _start_route(self) -> None:
        self._editing_route_id = None; self._begin_endpoint_selection()

    def _change_route_path(self) -> None:
        item = self.route_list.currentItem()
        if not item: return
        self._editing_route_id = item.data(ID_ROLE); self._begin_endpoint_selection()

    def _begin_endpoint_selection(self) -> None:
        self._route_endpoints = []; self._route_path = None
        select_button = next(button for button in self.mode_group.buttons()
                             if button.property("editor_mode") == EditorMode.NAVIGATE)
        select_button.setChecked(True); self._set_editor_mode(EditorMode.NAVIGATE)
        self.route_path_label.setText("Start- und Endknoten im Graph auswählen.")
        self.status.setText("Startknoten auswählen.")

    def _complete_route_from_endpoints(self) -> None:
        start, end = self._route_endpoints
        result = self.graph.enumerate_simple_paths(start, end, limit=50)
        if not result.paths:
            QtWidgets.QMessageBox.warning(self, "Kein Weg", "Zwischen den Endpunkten existiert kein Graphpfad.")
            self._route_endpoints = None; self._highlight_path([]); return
        path = result.paths[0]
        if len(result.paths) > 1:
            dialog = QtWidgets.QDialog(self); dialog.setWindowTitle("Mehrere Wege gefunden")
            layout = QtWidgets.QVBoxLayout(dialog)
            if result.truncated:
                layout.addWidget(QtWidgets.QLabel("Es werden maximal 50 deterministische Kandidaten angezeigt."))
            choices = QtWidgets.QListWidget(); layout.addWidget(choices)
            for candidate in result.paths:
                item = QtWidgets.QListWidgetItem(" → ".join(self.graph.nodes[node].display_name for node in candidate))
                item.setData(ID_ROLE, candidate); choices.addItem(item)
            choices.setCurrentRow(0)
            choices.currentItemChanged.connect(
                lambda current, _previous: self._highlight_path(current.data(ID_ROLE) if current else ()))
            buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok |
                                                 QtWidgets.QDialogButtonBox.StandardButton.Cancel)
            buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject); layout.addWidget(buttons)
            if not dialog.exec(): self._route_endpoints = None; self._highlight_path([]); return
            path = tuple(choices.currentItem().data(ID_ROLE))
        self._highlight_path(path)
        default = " – ".join((self.graph.nodes[path[0]].display_name, self.graph.nodes[path[-1]].display_name))
        existing = self.graph.defined_routes.get(self._editing_route_id)
        name, accepted = QtWidgets.QInputDialog.getText(
            self, "Strecke speichern", "Name:", text=existing.display_name if existing else default)
        if accepted and name.strip():
            if existing:
                old_a, old_b = existing.endpoint_a, existing.endpoint_b
                old_path = tuple(existing.ordered_node_ids)
                existing.display_name = name.strip(); existing.ordered_node_ids = list(path)
                existing.endpoint_a, existing.endpoint_b = path[0], path[-1]
                existing.default_kilometrage = self._estimated_route_kilometrage(existing)
                for instance in self.graph.bildfahrplan_routes:
                    if instance.route_id == existing.route_id:
                        instance.left_endpoint = path[-1] if instance.left_endpoint == old_b else path[0]
                        instance.kilometrage_stale = (old_path != tuple(path)
                                                      or set(instance.kilometrage) != set(path))
            else:
                route = self.graph.add_route(name.strip(), path)
                route.default_kilometrage = self._estimated_route_kilometrage(route)
            self._mark_dirty()
        self._route_endpoints = None; self._editing_route_id = None; self._highlight_path([])
        self._refresh_routes(); self._refresh_instances()

    def _refresh_routes(self) -> None:
        current = self.route_list.currentItem().data(ID_ROLE) if self.route_list.currentItem() else None
        self.route_list.blockSignals(True); self.route_list.clear()
        for route in self.graph.defined_routes.values():
            errors = self.graph.route_validation(route)
            item = QtWidgets.QListWidgetItem(("⚠ " if errors else "") + route.display_name)
            item.setData(ID_ROLE, route.route_id); item.setToolTip("\n".join(errors)); self.route_list.addItem(item)
            if route.route_id == current: self.route_list.setCurrentItem(item)
        self.route_list.blockSignals(False)

    def _route_selected(self, current, previous=None) -> None:
        if not current: self._highlight_path([]); self.route_path_label.setText("Keine Strecke ausgewählt"); return
        route = self.graph.defined_routes[current.data(ID_ROLE)]
        self.route_path_label.setText(" → ".join(self.graph.nodes[node].display_name if node in self.graph.nodes else node
                                                for node in route.ordered_node_ids))
        self._highlight_path(route.ordered_node_ids)

    def _highlight_path(self, path) -> None:
        nodes = set(path); pairs = {frozenset(pair) for pair in zip(path, path[1:])}
        for node_id, item in self.node_items.items(): item.setData(1, node_id in nodes); item.update()
        for edge in self.edge_items.values():
            model = self.graph.edges[edge.edge_id]; edge.setData(1, frozenset((model.node_a, model.node_b)) in pairs); edge.update()

    def _rename_route(self) -> None:
        item = self.route_list.currentItem()
        if not item: return
        route = self.graph.defined_routes[item.data(ID_ROLE)]
        name, accepted = QtWidgets.QInputDialog.getText(self, "Strecke umbenennen", "Name:", text=route.display_name)
        if accepted and name.strip(): route.display_name = name.strip(); self._mark_dirty(); self._refresh_routes(); self._refresh_instances()

    def _delete_route(self) -> None:
        item = self.route_list.currentItem()
        if item: self.graph.delete_route(item.data(ID_ROLE)); self._mark_dirty(); self._refresh_routes(); self._refresh_instances()

    def _add_instance(self) -> None:
        if not self.graph.defined_routes:
            QtWidgets.QMessageBox.information(self, "Keine Strecken", "Zuerst muss eine Strecke definiert werden."); return
        route_id = next(iter(self.graph.defined_routes)); self.graph.add_bildfahrplan_instance(route_id)
        self._mark_dirty(); self._refresh_instances()

    def _refresh_instances(self) -> None:
        self.bf_list.blockSignals(True); self.bf_list.clear()
        for instance in self.graph.bildfahrplan_routes:
            item = QtWidgets.QListWidgetItem(); item.setData(ID_ROLE, instance.instance_id); item.setSizeHint(QtCore.QSize(300, 38))
            self.bf_list.addItem(item); row = QtWidgets.QWidget(); layout = QtWidgets.QHBoxLayout(row)
            layout.setContentsMargins(2, 1, 2, 1); layout.addWidget(QtWidgets.QLabel("≡"))
            routes = QtWidgets.QComboBox()
            for route in self.graph.defined_routes.values(): routes.addItem(route.display_name, route.route_id)
            routes.setCurrentIndex(routes.findData(instance.route_id)); routes.currentIndexChanged.connect(
                lambda _index, iid=instance.instance_id, combo=routes: self._instance_route_changed(iid, combo.currentData()))
            endpoints = QtWidgets.QComboBox(); self._fill_endpoint_combo(endpoints, instance)
            endpoints.currentIndexChanged.connect(lambda _index, iid=instance.instance_id, combo=endpoints:
                                                  self._instance_endpoint_changed(iid, combo.currentData()))
            remove = QtWidgets.QPushButton("×"); remove.setMaximumWidth(30)
            remove.clicked.connect(lambda _checked=False, iid=instance.instance_id: self._remove_instance(iid))
            kilometres = QtWidgets.QPushButton("km…"); kilometres.setMaximumWidth(48)
            kilometres.setToolTip("Streckenkilometer bearbeiten" +
                                   (" (Pfad wurde geändert; bitte prüfen oder neu berechnen)"
                                    if instance.kilometrage_stale else ""))
            kilometres.clicked.connect(lambda _checked=False, iid=instance.instance_id:
                                        self._edit_instance_kilometrage(iid))
            layout.addWidget(routes, 3); layout.addWidget(endpoints, 2); layout.addWidget(kilometres); layout.addWidget(remove)
            self.bf_list.setItemWidget(item, row)
        self.bf_list.blockSignals(False)

    def _fill_endpoint_combo(self, combo, instance) -> None:
        combo.clear(); route = self.graph.defined_routes.get(instance.route_id)
        if not route: return
        for node_id in (route.endpoint_a, route.endpoint_b):
            combo.addItem(self.graph.nodes[node_id].display_name if node_id in self.graph.nodes else node_id, node_id)
        combo.setCurrentIndex(combo.findData(instance.left_endpoint))

    def _instance(self, instance_id):
        return next(item for item in self.graph.bildfahrplan_routes if item.instance_id == instance_id)

    def _instance_route_changed(self, instance_id, route_id) -> None:
        if self._loading or route_id is None: return
        instance = self._instance(instance_id); instance.route_id = route_id
        route = self.graph.defined_routes[route_id]
        instance.left_endpoint = route.endpoint_a
        instance.kilometrage = dict(route.default_kilometrage)
        instance.kilometrage_stale = False
        self._mark_dirty(); self._refresh_instances()

    def _instance_endpoint_changed(self, instance_id, endpoint) -> None:
        if endpoint is not None: self._instance(instance_id).left_endpoint = endpoint; self._mark_dirty()

    def _edit_instance_kilometrage(self, instance_id) -> None:
        instance = self._instance(instance_id)
        route = self.graph.defined_routes.get(instance.route_id)
        if route is None or not route.ordered_node_ids:
            QtWidgets.QMessageBox.warning(self, "Ungültige Strecke", "Für diese Zeile ist kein Streckenpfad verfügbar.")
            return
        ordered = list(route.ordered_node_ids)
        if instance.left_endpoint == route.endpoint_b:
            ordered.reverse()
        estimate = estimate_kilometrage(
            ordered, {node_id: self.graph.nodes[node_id].node_type for node_id in ordered},
            self._services, self._raw_to_topology_node(ordered))
        initial = instance.kilometrage
        if set(initial) != set(ordered):
            initial = dict(zip(ordered, estimate.kilometres))
        dialog = KilometrageDialog(route, self.graph.nodes, ordered, initial,
                                   estimate.kilometres, self)
        if dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            instance.kilometrage = dialog.values
            instance.kilometrage_stale = False
            self._mark_dirty()

    def _remove_instance(self, instance_id) -> None:
        self.graph.bildfahrplan_routes = [item for item in self.graph.bildfahrplan_routes if item.instance_id != instance_id]
        self.graph.normalize_instance_order(); self._mark_dirty(); self._refresh_instances()

    def _instances_reordered(self, parent, start, end, destination, row) -> None:
        # rowsMoved liefert die Modellindizes zuverlässig; die QListWidget-
        # Reihenfolge wird nicht als paralleler fachlicher State zurückgelesen.
        self.graph.move_bildfahrplan_instances(start, end, row); self._mark_dirty()

    def _search(self, text: str) -> None:
        needle = text.casefold().strip()
        for node_id, item in self.node_items.items(): item.setVisible(not needle or needle in self.graph.nodes[node_id].display_name.casefold())
