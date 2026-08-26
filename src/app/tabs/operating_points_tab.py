"""Qt-Editor fuer Betriebsstelle-zu-Rawname-Zuordnungen."""

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from infrastructure import OperatingPointResolver, SchedulePointGraph, parse_bahnsteigliste
from infrastructure.operating_point_assignments import (
    OperatingPointAssignments, OperatingPointConfigStore, natural_sort_key, related_selection,
)

ID_ROLE = QtCore.Qt.ItemDataRole.UserRole


class OperatingPointsTab(QtWidgets.QWidget):
    def __init__(self, config_directory, parent=None) -> None:
        super().__init__(parent)
        self.store = OperatingPointConfigStore(config_directory)
        self.model = OperatingPointAssignments()
        self.aid: int | None = None
        self._last_signature = None
        self._automatic = None
        self._haltpunkte: set[str] = set()
        self._build_ui()

    def _build_ui(self) -> None:
        root = QtWidgets.QVBoxLayout(self)
        toolbar = QtWidgets.QHBoxLayout()
        self.auto_button = QtWidgets.QPushButton("Automatisch zuordnen")
        self.clear_button = QtWidgets.QPushButton("Alle Zuordnungen entfernen")
        self.group_button = QtWidgets.QPushButton("Gleiches Kürzel auswählen")
        self.add_button = QtWidgets.QPushButton("Betriebsstelle hinzufügen")
        for widget in (self.auto_button, self.clear_button, self.group_button, self.add_button):
            toolbar.addWidget(widget)
        toolbar.addStretch(1)
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("Bahnsteig oder Betriebsstelle suchen…")
        self.search.setClearButtonEnabled(True)
        toolbar.addWidget(self.search, 1)
        root.addLayout(toolbar)

        columns = QtWidgets.QHBoxLayout()
        self.points = self._column(columns, "Betriebsstellen")
        middle = QtWidgets.QVBoxLayout()
        middle.addWidget(QtWidgets.QLabel("Zugeordnet"))
        self.assigned = self._list(); middle.addWidget(self.assigned, 1)
        self.assign_button = QtWidgets.QPushButton("← Zuordnen")
        self.unassign_button = QtWidgets.QPushButton("→ Zuordnung entfernen")
        actions = QtWidgets.QHBoxLayout(); actions.addWidget(self.assign_button); actions.addWidget(self.unassign_button)
        middle.addLayout(actions); columns.addLayout(middle, 3)
        self.unassigned = self._column(columns, "Nicht zugeordnet")
        columns.setStretch(0, 2); columns.setStretch(1, 3); columns.setStretch(2, 3)
        root.addLayout(columns, 1)
        self.empty = QtWidgets.QLabel("Noch keine Bahnsteig-/Fahrplandaten verfügbar.")
        self.empty.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter); root.addWidget(self.empty)
        self.status = QtWidgets.QLabel(); root.addWidget(self.status)

        self.points.currentItemChanged.connect(lambda *_: self._refresh_lists())
        self.assign_button.clicked.connect(self._assign)
        self.unassign_button.clicked.connect(self._unassign)
        self.auto_button.clicked.connect(self._auto_assign)
        self.clear_button.clicked.connect(self._clear)
        self.group_button.clicked.connect(self._select_group)
        self.add_button.clicked.connect(self._add_point)
        self.points.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.points.customContextMenuRequested.connect(self._point_menu)
        self.search.returnPressed.connect(self._search_exact_or_first)

    def _list(self) -> QtWidgets.QListWidget:
        widget = QtWidgets.QListWidget()
        widget.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        return widget

    def _column(self, parent: QtWidgets.QHBoxLayout, title: str) -> QtWidgets.QListWidget:
        layout = QtWidgets.QVBoxLayout(); layout.addWidget(QtWidgets.QLabel(title))
        widget = self._list(); layout.addWidget(widget, 1); parent.addLayout(layout)
        return widget

    def refresh(self, snapshot) -> None:
        platform_xml = next((raw for raw in reversed(snapshot.infrastructure_documents)
                             if raw.lstrip().startswith("<bahnsteigliste")), None)
        signature = (snapshot.aid, platform_xml, tuple(
            (service.zid, tuple((point.planned_name or point.raw_name)
                                for point in service.original_schedule)) for service in snapshot.services))
        if signature == self._last_signature:
            return
        self._last_signature = signature; self.aid = snapshot.aid
        schedule = SchedulePointGraph.from_services(snapshot.services)
        platforms = parse_bahnsteigliste(platform_xml) if platform_xml else ()
        raw_names = set(schedule.nodes)
        for platform in platforms:
            raw_names.add(platform.raw_name); raw_names.update(platform.related_names)
        self._haltpunkte = {item.raw_name for item in platforms
                            if item.metadata.get("haltepunkt", "false").lower() == "true"
                            and item.raw_name in schedule.nodes}
        config = self.store.load(self.aid)
        self._automatic = OperatingPointResolver(platforms, config, self.aid).resolve(schedule)
        selected = self._selected_point_id()
        self.model.rebuild(self._automatic, raw_names, self._haltpunkte, config)
        self._refresh_points(selected)

    def _selected_point_id(self) -> str | None:
        item = self.points.currentItem()
        return item.data(ID_ROLE) if item else None

    def _refresh_points(self, preferred: str | None = None) -> None:
        preferred = preferred or self._selected_point_id()
        self.points.blockSignals(True); self.points.clear()
        counts = {point_id: sum(owner == point_id for owner in self.model.assignments.values())
                  for point_id in self.model.points}
        for point in sorted(self.model.points.values(), key=lambda value: natural_sort_key(value.display_name)):
            item = QtWidgets.QListWidgetItem(f"{point.display_name}    {counts[point.id]}")
            item.setData(ID_ROLE, point.id)
            item.setToolTip("Manuell angelegt" if point.removable else "Automatisch erkannt")
            self.points.addItem(item)
            if point.id == preferred:
                self.points.setCurrentItem(item)
        self.points.blockSignals(False)
        if self.points.currentItem() is None and self.points.count():
            self.points.setCurrentRow(0)
        self._refresh_lists(); self._refresh_completer()

    def _fill_raw_list(self, widget: QtWidgets.QListWidget, names, selected=()) -> None:
        selected = set(selected); widget.clear()
        for name in sorted(names, key=natural_sort_key):
            item = QtWidgets.QListWidgetItem(name); item.setData(ID_ROLE, name)
            source = self.model.sources.get(name)
            item.setToolTip({"manual": "Manuell bestätigt", "automatic": "Automatisch zugeordnet",
                             "self_haltpunkt": "Eigenständiger Haltepunkt"}.get(source, "Nicht zugeordnet"))
            widget.addItem(item); item.setSelected(name in selected)

    def _refresh_lists(self) -> None:
        point_id = self._selected_point_id()
        middle_selection = {item.data(ID_ROLE) for item in self.assigned.selectedItems()}
        right_selection = {item.data(ID_ROLE) for item in self.unassigned.selectedItems()}
        members = {name for name, owner in self.model.assignments.items() if owner == point_id}
        self._fill_raw_list(self.assigned, members, middle_selection)
        self._fill_raw_list(self.unassigned, self.model.unassigned, right_selection)
        total = len(self.model.all_raw_names); assigned = len(self.model.assignments)
        manual = sum(source == "manual" for source in self.model.sources.values())
        self.status.setText(f"Betriebsstellen: {len(self.model.points)}   Zugeordnet: {assigned}   "
                            f"Nicht zugeordnet: {total - assigned}   Manuell bestätigt: {manual}")
        self.empty.setVisible(not total)
        self.assigned.setToolTip("Dieser Betriebsstelle sind noch keine Bahnsteige zugeordnet."
                                 if point_id and not members else "")
        self.unassigned.setToolTip("Alle gefundenen Bahnsteige sind zugeordnet."
                                   if total and not self.model.unassigned else "")

    def _persist(self) -> None:
        if self.aid is not None:
            self.store.save(self.aid, self.model)

    def _assign(self) -> None:
        point_id = self._selected_point_id()
        if point_id:
            self.model.assign((item.data(ID_ROLE) for item in self.unassigned.selectedItems()), point_id)
            self._persist(); self._refresh_points(point_id)

    def _unassign(self) -> None:
        self.model.remove_assignments(item.data(ID_ROLE) for item in self.assigned.selectedItems())
        self._persist(); self._refresh_points(self._selected_point_id())

    def _auto_assign(self) -> None:
        if self._automatic is None:
            return
        config = self.model.to_config()
        self.model.rebuild(self._automatic, self.model.all_raw_names, self._haltpunkte, config)
        self._persist(); self._refresh_points(self._selected_point_id())

    def _clear(self) -> None:
        answer = QtWidgets.QMessageBox.question(
            self, "Zuordnungen entfernen", "Wirklich alle Bahnsteigzuordnungen entfernen?\n\n"
            "Eigenständige Haltepunkte bleiben ihrer gleichnamigen Betriebsstelle zugeordnet.",
            QtWidgets.QMessageBox.StandardButton.Cancel | QtWidgets.QMessageBox.StandardButton.Yes,
            QtWidgets.QMessageBox.StandardButton.Cancel)
        if answer == QtWidgets.QMessageBox.StandardButton.Yes:
            self.model.clear_editable_assignments(); self._persist(); self._refresh_points()

    def _select_group(self) -> None:
        selected = [item.data(ID_ROLE) for widget in (self.assigned, self.unassigned)
                    for item in widget.selectedItems()]
        group = related_selection(self.model.all_raw_names, selected)
        for widget in (self.assigned, self.unassigned):
            widget.clearSelection()
            for index in range(widget.count()):
                widget.item(index).setSelected(widget.item(index).data(ID_ROLE) in group)

    def _add_point(self) -> None:
        dialog = QtWidgets.QDialog(self); dialog.setWindowTitle("Betriebsstelle hinzufügen")
        form = QtWidgets.QFormLayout(dialog); name = QtWidgets.QLineEdit(); key = QtWidgets.QLineEdit()
        form.addRow("Name:", name); form.addRow("Kürzel (optional):", key)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok |
                                             QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject); form.addRow(buttons)
        if dialog.exec() and name.text().strip():
            point_id = self.model.add_point(name.text().strip(), key.text().strip() or None)
            self._persist(); self._refresh_points(point_id)

    def _point_menu(self, position) -> None:
        point_id = self._selected_point_id()
        if not point_id or point_id not in self.model.manual_point_ids:
            return
        menu = QtWidgets.QMenu(self); rename = menu.addAction("Umbenennen"); delete = menu.addAction("Löschen")
        action = menu.exec(self.points.mapToGlobal(position))
        if action == rename:
            value, ok = QtWidgets.QInputDialog.getText(
                self, "Betriebsstelle umbenennen", "Name:", text=self.model.points[point_id].display_name)
            if ok and value.strip():
                self.model.rename_point(point_id, value.strip()); self._persist(); self._refresh_points(point_id)
        elif action == delete:
            count = sum(owner == point_id for owner in self.model.assignments.values())
            if not count or QtWidgets.QMessageBox.question(
                    self, "Betriebsstelle löschen", f"Die Betriebsstelle besitzt noch {count} Zuordnungen.\n"
                    "Beim Löschen werden diese wieder nicht zugeordnet.") == QtWidgets.QMessageBox.StandardButton.Yes:
                self.model.delete_point(point_id); self._persist(); self._refresh_points()

    def _refresh_completer(self) -> None:
        entries = []
        for point in self.model.points.values():
            entries.append(f"{point.display_name}  [Betriebsstelle]")
        entries.extend(f"{name}  [Bahnsteig/Fahrplanpunkt]" for name in self.model.all_raw_names)
        completer = QtWidgets.QCompleter(sorted(entries, key=natural_sort_key), self.search)
        completer.setCaseSensitivity(QtCore.Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(QtCore.Qt.MatchFlag.MatchContains)
        completer.activated.connect(self._navigate_search_entry)
        self.search.setCompleter(completer)

    def _search_exact_or_first(self) -> None:
        query = self.search.text().strip().casefold()
        matches = [self.search.completer().model().data(self.search.completer().model().index(i, 0))
                   for i in range(self.search.completer().model().rowCount())
                   if query in self.search.completer().model().data(
                       self.search.completer().model().index(i, 0)).casefold()]
        if len(matches) == 1:
            self._navigate_search_entry(matches[0])
        elif matches:
            self.search.completer().complete()

    def _navigate_search_entry(self, entry: str) -> None:
        label, _, kind = entry.rpartition("  [")
        if kind.startswith("Betriebsstelle"):
            point_id = next((p.id for p in self.model.points.values() if p.display_name == label), None)
            self._select_item(self.points, point_id)
        else:
            owner = self.model.assignments.get(label)
            if owner:
                self._select_item(self.points, owner); self._refresh_lists()
                self._select_item(self.assigned, label); self.assigned.setFocus()
            else:
                self._select_item(self.unassigned, label); self.unassigned.setFocus()

    @staticmethod
    def _select_item(widget: QtWidgets.QListWidget, value: str | None) -> None:
        for index in range(widget.count()):
            item = widget.item(index)
            if item.data(ID_ROLE) == value:
                widget.setCurrentItem(item); widget.scrollToItem(item); return
