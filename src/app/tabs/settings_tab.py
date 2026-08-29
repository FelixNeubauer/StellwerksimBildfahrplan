"""Benutzereinstellungen für die Bildfahrplandarstellung."""

from PySide6 import QtCore, QtGui, QtWidgets

from app.settings import ApplicationSettings, normalize_hex_color


class SettingsTab(QtWidgets.QWidget):
    settingsChanged = QtCore.Signal(object)

    def __init__(self, settings: ApplicationSettings, save, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self._save = save
        root = QtWidgets.QVBoxLayout(self)
        group = QtWidgets.QGroupBox("Zugdarstellung")
        form = QtWidgets.QFormLayout(group)
        self.mode = QtWidgets.QComboBox()
        self.mode.addItem("Einfarbig", "single")
        self.mode.addItem("Bunt", "colorful")
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
        root.addStretch()
        self.mode.currentIndexChanged.connect(self._apply)
        self.color.editingFinished.connect(self._apply)
        self._update_enabled()

    def _choose_color(self) -> None:
        selected = QtWidgets.QColorDialog.getColor(QtGui.QColor(self.color.text()), self)
        if selected.isValid():
            self.color.setText(selected.name().upper())
            self._apply()

    def _apply(self) -> None:
        color = normalize_hex_color(self.color.text())
        if color is None:
            self.color.setText(self.settings.single_train_color)
            return
        self.settings = ApplicationSettings(
            train_color_mode=self.mode.currentData(), single_train_color=color,
            live_follow_position_percent=self.settings.live_follow_position_percent,
        ).validated()
        self.color.setText(self.settings.single_train_color)
        self._update_enabled()
        self._save(self.settings)
        self.settingsChanged.emit(self.settings)

    def _update_enabled(self) -> None:
        self.color.setEnabled(self.mode.currentData() == "single")

    def set_live_follow_position(self, percent: int) -> None:
        self.settings = ApplicationSettings(
            self.settings.train_color_mode, self.settings.single_train_color, percent,
        ).validated()
        self._save(self.settings)
        self.settingsChanged.emit(self.settings)
