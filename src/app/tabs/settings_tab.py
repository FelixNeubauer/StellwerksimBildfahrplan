"""Benutzereinstellungen für die Bildfahrplandarstellung."""

from dataclasses import replace

from PySide6 import QtCore, QtGui, QtWidgets

from app.settings import CATEGORY_LABELS, TRAIN_TYPE_CATEGORIES, ApplicationSettings, normalize_hex_color


class AddTrainTypeDialog(QtWidgets.QDialog):
    def __init__(self, settings: ApplicationSettings, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Zuggattung hinzufügen")
        form = QtWidgets.QFormLayout(self)
        self.name = QtWidgets.QLineEdit()
        self.category = QtWidgets.QComboBox()
        for category, label in CATEGORY_LABELS.items():
            self.category.addItem(label, category)
        self.color = QtWidgets.QLineEdit(settings.category_train_colors["local"])
        self.color.setValidator(QtGui.QRegularExpressionValidator(
            QtCore.QRegularExpression("#[0-9A-Fa-f]{6}"), self.color))
        choose = QtWidgets.QPushButton("Farbe wählen…")
        choose.clicked.connect(self._choose_color)
        color_row = QtWidgets.QHBoxLayout(); color_row.addWidget(self.color); color_row.addWidget(choose)
        form.addRow("Zuggattung:", self.name)
        form.addRow("Kategorie:", self.category)
        form.addRow("Startfarbe:", color_row)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Save | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _choose_color(self) -> None:
        selected = QtWidgets.QColorDialog.getColor(QtGui.QColor(self.color.text()), self)
        if selected.isValid():
            self.color.setText(selected.name().upper())


class SettingsTab(QtWidgets.QWidget):
    settingsChanged = QtCore.Signal(object)

    def __init__(self, settings: ApplicationSettings, save, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings.validated()
        self._save = save
        self._loading = True
        root = QtWidgets.QVBoxLayout(self)
        group = QtWidgets.QGroupBox("Zugdarstellung")
        form = QtWidgets.QFormLayout(group)
        self.mode = QtWidgets.QComboBox()
        for label, value in (("Einfarbig", "single"), ("Bunt", "colorful"), ("Zuggattung", "train_type")):
            self.mode.addItem(label, value)
        self.mode.setCurrentIndex(self.mode.findData(self.settings.train_color_mode))
        form.addRow("Zugfarben:", self.mode)
        color_row = QtWidgets.QHBoxLayout()
        self.color = self._color_edit(self.settings.single_train_color)
        color_row.addWidget(self.color)
        choose = QtWidgets.QPushButton("Farbe wählen…"); choose.clicked.connect(self._choose_color)
        color_row.addWidget(choose); form.addRow("Einzelfarbe:", color_row)
        root.addWidget(group)

        self.type_box = QtWidgets.QGroupBox("Farben nach Zuggattung")
        type_layout = QtWidgets.QVBoxLayout(self.type_box)
        type_layout.addWidget(QtWidgets.QLabel(
            "Eine Kategoriefarbe überschreibt einmalig alle zugehörigen Gattungen."))
        self.category_table = QtWidgets.QTableWidget(len(TRAIN_TYPE_CATEGORIES), 2)
        self.category_table.setHorizontalHeaderLabels(("Kategorie", "Farbe / auf alle anwenden"))
        self.category_table.horizontalHeader().setStretchLastSection(True)
        self.category_edits = {}
        for row, category in enumerate(TRAIN_TYPE_CATEGORIES):
            self.category_table.setItem(row, 0, QtWidgets.QTableWidgetItem(CATEGORY_LABELS[category]))
            edit = self._color_edit(self.settings.category_train_colors[category])
            edit.editingFinished.connect(lambda category=category, edit=edit: self._apply_category(category, edit))
            self.category_table.setCellWidget(row, 1, edit); self.category_edits[category] = edit
        type_layout.addWidget(self.category_table)

        filters = QtWidgets.QHBoxLayout()
        self.type_search = QtWidgets.QLineEdit(); self.type_search.setPlaceholderText("Zuggattung suchen…")
        self.type_filter = QtWidgets.QComboBox(); self.type_filter.addItem("Alle", None)
        for category, label in CATEGORY_LABELS.items():
            self.type_filter.addItem(label, category)
        filters.addWidget(self.type_search); filters.addWidget(self.type_filter)
        type_layout.addLayout(filters)
        self.train_type_table = QtWidgets.QTableWidget(0, 4)
        self.train_type_table.setHorizontalHeaderLabels(("Gattung", "Kategorie", "Farbe", "Aktion"))
        self.train_type_table.horizontalHeader().setStretchLastSection(True)
        type_layout.addWidget(self.train_type_table)
        add = QtWidgets.QPushButton("+ Zuggattung hinzufügen"); add.clicked.connect(self._add_train_type)
        type_layout.addWidget(add); root.addWidget(self.type_box); root.addStretch()

        self.mode.currentIndexChanged.connect(self._apply)
        self.color.editingFinished.connect(self._apply)
        self.type_search.textChanged.connect(self._filter_types)
        self.type_filter.currentIndexChanged.connect(self._filter_types)
        self._rebuild_train_types(); self._update_enabled(); self._loading = False

    @staticmethod
    def _color_edit(value: str) -> QtWidgets.QLineEdit:
        edit = QtWidgets.QLineEdit(value); edit.setMaxLength(7); edit.setPlaceholderText("#D0D0D0")
        edit.setValidator(QtGui.QRegularExpressionValidator(QtCore.QRegularExpression("#[0-9A-Fa-f]{6}"), edit))
        return edit

    def _choose_color(self) -> None:
        selected = QtWidgets.QColorDialog.getColor(QtGui.QColor(self.color.text()), self)
        if selected.isValid():
            self.color.setText(selected.name().upper()); self._apply()

    def _apply(self) -> None:
        if self._loading:
            return
        color = normalize_hex_color(self.color.text())
        if color is None:
            self.color.setText(self.settings.single_train_color); return
        self.settings = replace(self.settings, train_color_mode=self.mode.currentData(),
                                single_train_color=color).validated()
        self.color.setText(self.settings.single_train_color); self._update_enabled(); self._commit()

    def _update_enabled(self) -> None:
        self.color.setEnabled(self.mode.currentData() == "single")
        self.type_box.setVisible(self.mode.currentData() == "train_type")

    def _commit(self) -> None:
        self._save(self.settings); self.settingsChanged.emit(self.settings)

    def _apply_category(self, category: str, edit: QtWidgets.QLineEdit) -> None:
        color = normalize_hex_color(edit.text())
        if color is None:
            edit.setText(self.settings.category_train_colors[category]); return
        self.settings = self.settings.apply_category_color(category, color)
        edit.setText(color); self._rebuild_train_types(); self._commit()

    def _rebuild_train_types(self) -> None:
        self.train_type_table.setRowCount(0)
        for name, category, custom in self.settings.all_train_types():
            row = self.train_type_table.rowCount(); self.train_type_table.insertRow(row)
            name_item = QtWidgets.QTableWidgetItem(name); name_item.setData(QtCore.Qt.ItemDataRole.UserRole, custom)
            self.train_type_table.setItem(row, 0, name_item)
            if custom:
                category_widget = QtWidgets.QComboBox()
                for value, label in CATEGORY_LABELS.items(): category_widget.addItem(label, value)
                category_widget.setCurrentIndex(category_widget.findData(category))
                category_widget.currentIndexChanged.connect(
                    lambda _i, name=name, widget=category_widget: self._change_custom_category(name, widget.currentData()))
                self.train_type_table.setCellWidget(row, 1, category_widget)
            else:
                self.train_type_table.setItem(row, 1, QtWidgets.QTableWidgetItem(CATEGORY_LABELS[category]))
            key = name.casefold(); edit = self._color_edit(self.settings.train_type_colors.get(key, ""))
            edit.setPlaceholderText(self.settings.category_train_colors[category])
            edit.editingFinished.connect(lambda name=name, edit=edit: self._apply_train_type(name, edit))
            self.train_type_table.setCellWidget(row, 2, edit)
            if custom:
                remove = QtWidgets.QPushButton("Löschen")
                remove.clicked.connect(lambda _checked=False, name=name: self._remove_train_type(name))
                self.train_type_table.setCellWidget(row, 3, remove)
            else:
                self.train_type_table.setItem(row, 3, QtWidgets.QTableWidgetItem("Standard"))
        self._filter_types()

    def _apply_train_type(self, name: str, edit: QtWidgets.QLineEdit) -> None:
        text = edit.text().strip()
        if text and normalize_hex_color(text) is None:
            edit.setText(self.settings.train_type_colors.get(name.casefold(), "")); return
        self.settings = self.settings.with_train_type_color(name, text or None)
        edit.setText(self.settings.train_type_colors.get(name.casefold(), "")); self._commit()

    def _filter_types(self) -> None:
        query = self.type_search.text().casefold() if hasattr(self, "type_search") else ""
        category = self.type_filter.currentData() if hasattr(self, "type_filter") else None
        for row in range(self.train_type_table.rowCount()):
            name = self.train_type_table.item(row, 0).text()
            cell = self.train_type_table.cellWidget(row, 1)
            row_category = cell.currentData() if cell else next(
                key for key, label in CATEGORY_LABELS.items()
                if label == self.train_type_table.item(row, 1).text())
            self.train_type_table.setRowHidden(row, query not in name.casefold()
                                               or (category is not None and category != row_category))

    def _add_train_type(self) -> None:
        dialog = AddTrainTypeDialog(self.settings, self)
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        try:
            self.settings = self.settings.add_custom_train_type(
                dialog.name.text(), dialog.category.currentData(), dialog.color.text())
        except ValueError as error:
            QtWidgets.QMessageBox.information(self, "Zuggattung", str(error)); return
        self._rebuild_train_types(); self._commit()

    def _remove_train_type(self, name: str) -> None:
        self.settings = self.settings.remove_custom_train_type(name)
        self._rebuild_train_types(); self._commit()

    def _change_custom_category(self, name: str, category: str) -> None:
        self.settings = self.settings.change_custom_train_type_category(name, category); self._commit()

    def set_live_follow_position(self, percent: int) -> None:
        self.settings = replace(self.settings, live_follow_position_percent=percent).validated()
        self._commit()
