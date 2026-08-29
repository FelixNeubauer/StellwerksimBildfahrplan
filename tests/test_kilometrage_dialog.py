import os
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

QtCore = pytest.importorskip("PySide6.QtCore", exc_type=ImportError)
QtTest = pytest.importorskip("PySide6.QtTest", exc_type=ImportError)
QtWidgets = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from app.tabs.infrastructure_tab import KILOMETRE_COLUMN, KilometrageDialog
from infrastructure import DefinedRoute, TopologyNode


def _dialog():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    nodes = {
        name: TopologyNode(name, name, kind, "manual")
        for name, kind in (("A", "entry"), ("B", "line"), ("C", "entry"))
    }
    route = DefinedRoute("route", "Test", ["A", "B", "C"], "A", "C")
    dialog = KilometrageDialog(route, nodes, route.ordered_node_ids,
                               {"A": 0.0, "B": 4.0, "C": 9.0}, (0.0, 4.0, 9.0))
    dialog.show(); app.processEvents()
    return app, dialog


def _editor(dialog):
    QtWidgets.QApplication.processEvents()
    editor = dialog.table.focusWidget()
    assert isinstance(editor, QtWidgets.QLineEdit)
    return editor


def test_tab_and_backtab_only_visit_kilometres_and_wrap_with_selected_text():
    app, dialog = _dialog()
    dialog.table.edit_kilometre(0); app.processEvents()
    editor = _editor(dialog)
    assert editor.selectedText() == editor.text()

    editor.setText("1.5")
    QtTest.QTest.keyClick(editor, QtCore.Qt.Key.Key_Tab)
    app.processEvents()
    assert dialog.table.currentRow() == 1
    assert dialog.table.currentColumn() == KILOMETRE_COLUMN
    assert dialog.table.item(0, KILOMETRE_COLUMN).text() == "1.5"
    assert _editor(dialog).selectedText() == _editor(dialog).text()

    QtTest.QTest.keyClick(_editor(dialog), QtCore.Qt.Key.Key_Backtab)
    app.processEvents()
    assert (dialog.table.currentRow(), dialog.table.currentColumn()) == (0, KILOMETRE_COLUMN)

    dialog.table.edit_kilometre(2); app.processEvents()
    QtTest.QTest.keyClick(_editor(dialog), QtCore.Qt.Key.Key_Tab)
    app.processEvents()
    assert (dialog.table.currentRow(), dialog.table.currentColumn()) == (0, KILOMETRE_COLUMN)

    QtTest.QTest.keyClick(_editor(dialog), QtCore.Qt.Key.Key_Backtab)
    app.processEvents()
    assert (dialog.table.currentRow(), dialog.table.currentColumn()) == (2, KILOMETRE_COLUMN)
    for row in range(dialog.table.rowCount()):
        for column in range(KILOMETRE_COLUMN):
            assert not dialog.table.item(row, column).flags() & QtCore.Qt.ItemFlag.ItemIsFocusable
    dialog.close()
