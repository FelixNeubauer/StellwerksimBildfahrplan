"""Benutzereinstellungen für die Bildfahrplandarstellung."""

from PySide6 import QtCore, QtGui, QtWidgets

from app.settings import (
    CATEGORY_LABELS, TRAIN_TYPE_CATEGORIES, ApplicationSettings, normalize_hex_color,
)


class SettingsTab(QtWidgets.QWidget):
    settingsChanged = QtCore.Signal(object)

    def __init__(self, settings: ApplicationSettings, save, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self._save = save
        self._loading = True
        root = QtWidgets.QVBoxLayout(self)
        group = QtWidgets.QGroupBox("Zugdarstellung")
        form = QtWidgets.QFormLayout(group)
        self.mode = QtWidgets.QComboBox()
        self.mode.addItem("Einfarbig", "single")
        self.mode.addItem("Bunt", "colorful")
        self.mode.addItem("Zuggattung", "train_type")
        self.mode.setCurrentIndex(self.mode.findData(settings.train_color_mode))
        form.addRow("Zugfarben:", self.mode)
        color_row = QtWidgets.QHBoxLayout()
        self.color = QtWidgets.QLineEdit(settings.single_train_color)
        self.color.setMaxLength(7)
        self.color.setPlaceholderText("#D0D0D0")
        self.color.setValidator(QtGui.QRegularExpressionValidator(
            QtCore.QRegularExpression("#[0-9A-Fa-f]{6}"), self.color))
        color_row.addWidget(self.color)
        choose = QtWidgets.QPushButton("Farbe wählen…")
        choose.clicked.connect(self._choose_color)
        color_row.addWidget(choose)
        form.addRow("Einzelfarbe:", color_row)
        root.addWidget(group)
        self.type_box = QtWidgets.QGroupBox("Farben nach Zuggattung")
        type_layout = QtWidgets.QVBoxLayout(self.type_box)
        type_layout.addWidget(QtWidgets.QLabel(
            "Eine Kategoriefarbe wird auf alle zugehörigen Gattungen angewendet; einzelne Farben können danach abweichen."))
        self.category_table = QtWidgets.QTableWidget(len(TRAIN_TYPE_CATEGORIES), 2)
        self.category_table.setHorizontalHeaderLabels(("Kategorie", "Farbe / auf alle anwenden"))
        self.category_table.horizontalHeader().setStretchLastSection(True)
        self.category_edits = {}
        for row, category in enumerate(TRAIN_TYPE_CATEGORIES):
            self.category_table.setItem(row, 0, QtWidgets.QTableWidgetItem(CATEGORY_LABELS[category]))
            edit = self._color_edit(settings.category_train_colors[category])
            edit.editingFinished.connect(lambda category=category, edit=edit: self._apply_category(category, edit))
            self.category_table.setCellWidget(row, 1, edit)
            self.category_edits[category] = edit
        type_layout.addWidget(self.category_table)
        train_types = [(train_type, category) for category, values in TRAIN_TYPE_CATEGORIES.items()
                       for train_type in values]
        self.train_type_table = QtWidgets.QTableWidget(len(train_types), 3)
        self.train_type_table.setHorizontalHeaderLabels(("Gattung", "Kategorie", "Individuelle Farbe"))
        self.train_type_table.horizontalHeader().setStretchLastSection(True)
        self.train_type_edits = {}
        for row, (train_type, category) in enumerate(train_types):
            self.train_type_table.setItem(row, 0, QtWidgets.QTableWidgetItem(train_type))
            self.train_type_table.setItem(row, 1, QtWidgets.QTableWidgetItem(CATEGORY_LABELS[category]))
            edit = self._color_edit(settings.train_type_colors.get(train_type, ""))
            edit.setPlaceholderText(settings.category_train_colors[category])
            edit.editingFinished.connect(
                lambda train_type=train_type, edit=edit: self._apply_train_type(train_type, edit))
            self.train_type_table.setCellWidget(row, 2, edit)
            self.train_type_edits[train_type] = edit
        type_layout.addWidget(self.train_type_table)
        root.addWidget(self.type_box)
        root.addStretch()
        self.mode.currentIndexChanged.connect(self._apply)
        self.color.editingFinished.connect(self._apply)
        self._update_enabled()
        self._loading = False

    @staticmethod
    def _color_edit(value: str) -> QtWidgets.QLineEdit:
        edit = QtWidgets.QLineEdit(value)
        edit.setMaxLength(7)
        edit.setValidator(QtGui.QRegularExpressionValidator(
            QtCore.QRegularExpression("#[0-9A-Fa-f]{6}"), edit))
        return edit

    def _choose_color(self) -> None:
        selected = QtWidgets.QColorDialog.getColor(QtGui.QColor(self.color.text()), self)
        if selected.isValid():
            self.color.setText(selected.name().upper())
            self._apply()

    def _apply(self) -> None:
        if self._loading:
            return
        color = normalize_hex_color(self.color.text())
        if color is None:
            self.color.setText(self.settings.single_train_color)
            return
        self.settings = ApplicationSettings(
            train_color_mode=self.mode.currentData(), single_train_color=color,
            live_follow_position_percent=self.settings.live_follow_position_percent,
            category_train_colors=self.settings.category_train_colors,
            train_type_colors=self.settings.train_type_colors,
        ).validated()
        self.color.setText(self.settings.single_train_color)
        self._update_enabled()
        self._save(self.settings)
        self.settingsChanged.emit(self.settings)

    def _update_enabled(self) -> None:
        self.color.setEnabled(self.mode.currentData() == "single")
        self.type_box.setVisible(self.mode.currentData() == "train_type")

    def _commit(self) -> None:
        self._save(self.settings)
        self.settingsChanged.emit(self.settings)

    def _apply_category(self, category: str, edit: QtWidgets.QLineEdit) -> None:
        color = normalize_hex_color(edit.text())
        if color is None:
            edit.setText(self.settings.category_train_colors[category]); return
        self.settings = self.settings.apply_category_color(category, color)
        edit.setText(color)
        for train_type in TRAIN_TYPE_CATEGORIES[category]:
            self.train_type_edits[train_type].setText(color)
        self._commit()

    def _apply_train_type(self, train_type: str, edit: QtWidgets.QLineEdit) -> None:
        text = edit.text().strip()
        if text and normalize_hex_color(text) is None:
            edit.setText(self.settings.train_type_colors.get(train_type, "")); return
        self.settings = self.settings.with_train_type_color(train_type, text or None)
        edit.setText(self.settings.train_type_colors.get(train_type, ""))
        self._commit()

    def set_live_follow_position(self, percent: int) -> None:
        self.settings = ApplicationSettings(
            self.settings.train_color_mode, self.settings.single_train_color, percent,
            self.settings.category_train_colors, self.settings.train_type_colors,
        ).validated()
        self._save(self.settings)
        self.settingsChanged.emit(self.settings)
