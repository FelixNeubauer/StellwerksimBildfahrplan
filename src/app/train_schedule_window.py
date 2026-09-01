from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from bildfahrplan.train_schedule import TrainScheduleViewModel


class TrainScheduleWindow(QtWidgets.QWidget):
    closed = QtCore.Signal(int)

    def __init__(self, view_model: TrainScheduleViewModel, parent=None) -> None:
        super().__init__(parent, QtCore.Qt.WindowType.Window)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose)
        self.resize(980, 560)
        self._signature = None
        layout = QtWidgets.QVBoxLayout(self)
        self.summary = QtWidgets.QLabel()
        self.summary.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        layout.addWidget(self.summary)
        self.notices = QtWidgets.QLabel()
        self.notices.setWordWrap(True)
        layout.addWidget(self.notices)
        self.table = QtWidgets.QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ("Betriebsstelle", "Gleis / Fahrplanpunkt", "Ankunft", "Abfahrt", "Flags", "Hinweis", "Optionen"))
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setWordWrap(True)
        header = self.table.horizontalHeader()
        for column in range(7):
            header.setSectionResizeMode(column, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QtWidgets.QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(5, QtWidgets.QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 1)
        self.footer = QtWidgets.QHBoxLayout()
        self.footer.addStretch()
        layout.addLayout(self.footer)
        self.update_view_model(view_model)

    def update_view_model(self, model: TrainScheduleViewModel) -> None:
        if model.signature == self._signature:
            return
        structure_changed = self.table.rowCount() != len(model.rows)
        self._signature = model.signature
        self.zid = model.zid
        self.setWindowTitle(f"Fahrplan – {model.train_name}")
        delay = "" if model.delay is None else f" · Verspätung: {model.delay:+d} min"
        status = "" if model.in_current_snapshot else "\nZug nicht mehr im aktuellen Stellwerk"
        self.summary.setText(f"{model.train_name} · {model.origin or '–'} → {model.destination or '–'}{delay}{status}")
        self.notices.setText("Allgemeine Hinweise: " + "\n".join(model.common_notices) if model.common_notices else "")
        self.notices.setVisible(bool(model.common_notices))
        if structure_changed:
            self.table.setRowCount(len(model.rows))
        palette = self.palette()
        base = palette.color(QtGui.QPalette.ColorRole.Base)
        alternate = palette.color(QtGui.QPalette.ColorRole.AlternateBase)
        muted = palette.color(QtGui.QPalette.ColorGroup.Disabled, QtGui.QPalette.ColorRole.Text)
        normal = palette.color(QtGui.QPalette.ColorRole.Text)
        for row_number, row in enumerate(model.rows):
            values = (row.operating_point, row.raw_schedule_name, row.arrival, row.departure,
                      " · ".join(row.flags), row.notice)
            background = base if row.group_index % 2 == 0 else alternate
            if row.completed:
                background = QtGui.QColor(background).darker(115)
            for column, value in enumerate(values):
                item = self.table.item(row_number, column)
                if item is None:
                    item = QtWidgets.QTableWidgetItem()
                    self.table.setItem(row_number, column, item)
                item.setText(value)
                item.setToolTip(row.raw_flags if column == 4 else value)
                item.setBackground(background)
                item.setForeground(muted if row.completed else normal)
            button = self.table.cellWidget(row_number, 6)
            if button is None:
                button = QtWidgets.QPushButton("Optionen")
                self.table.setCellWidget(row_number, 6, button)
            button.setEnabled(not row.completed)
        self.table.resizeRowsToContents()

    def closeEvent(self, event) -> None:  # noqa: N802
        self.closed.emit(self.zid)
        super().closeEvent(event)
