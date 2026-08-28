"""QGraphicsScene-basierter Editor fuer Betriebsstellen und Verbindungen."""

from __future__ import annotations

from enum import Enum

from PySide6 import QtCore, QtGui, QtWidgets

from infrastructure import EditableTopologyGraph


class EditorMode(Enum):
    PAN = "pan"
    SELECT = "select"
    CONNECT = "connect"


class TopologyEdgeItem(QtWidgets.QGraphicsLineItem):
    def __init__(self, edge_id: str, source, target) -> None:
        super().__init__(); self.edge_id = edge_id; self.source = source; self.target = target
        self.setFlag(self.GraphicsItemFlag.ItemIsSelectable)
        self.setZValue(-1); self.update_geometry()

    def update_geometry(self) -> None:
        self.setLine(QtCore.QLineF(self.source.pos(), self.target.pos()))

    def shape(self):
        stroker = QtGui.QPainterPathStroker(); stroker.setWidth(12)
        return stroker.createStroke(super().shape())

    def paint(self, painter, option, widget=None) -> None:
        highlighted = self.data(1) is True
        color = QtGui.QColor("#ffb300" if highlighted else "#64b5f6" if self.isSelected() else "#78909c")
        painter.setPen(QtGui.QPen(color, 4 if highlighted else 2, QtCore.Qt.PenStyle.SolidLine,
                                  QtCore.Qt.PenCapStyle.RoundCap))
        painter.drawLine(self.line())


class TopologyNodeItem(QtWidgets.QGraphicsObject):
    moved = QtCore.Signal(str, QtCore.QPointF, QtCore.QPointF)

    def __init__(self, node, graph: EditableTopologyGraph) -> None:
        super().__init__(); self.node_id = node.id; self.graph = graph; self.edges: set[TopologyEdgeItem] = set()
        self._drag_start = QtCore.QPointF(node.layout_x, node.layout_y)
        self.setPos(node.layout_x, node.layout_y); self.set_editable(False)
        self.setZValue(1); self.setToolTip(node.display_name)

    def set_editable(self, editable: bool) -> None:
        self.setFlag(self.GraphicsItemFlag.ItemIsMovable, editable)
        self.setFlag(self.GraphicsItemFlag.ItemIsSelectable, editable)
        self.setFlag(self.GraphicsItemFlag.ItemSendsGeometryChanges, True)

    def boundingRect(self): return QtCore.QRectF(-42, -28, 84, 56)

    def shape(self):
        path = QtGui.QPainterPath(); path.addEllipse(QtCore.QRectF(-18, -18, 36, 36)); return path

    def paint(self, painter, option, widget=None) -> None:
        node = self.graph.nodes[self.node_id]; warning = self.graph.node_validation(self.node_id)
        highlighted, connect_target = self.data(1) is True, self.data(2) is True
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        fill = QtGui.QColor("#66bb6a" if connect_target else "#ffb300" if highlighted else
                           "#42a5f5" if self.isSelected() else "#455a64")
        painter.setBrush(fill)
        painter.setPen(QtGui.QPen(QtGui.QColor("#ef5350" if warning else "#eceff1"), 3 if warning else 2))
        if node.node_type == "junction":
            painter.drawPolygon(QtGui.QPolygonF([QtCore.QPointF(0, -19), QtCore.QPointF(19, 0),
                                                  QtCore.QPointF(0, 19), QtCore.QPointF(-19, 0)]))
        elif node.node_type == "entry":
            painter.drawPolygon(QtGui.QPolygonF([QtCore.QPointF(-17, -18), QtCore.QPointF(20, 0),
                                                  QtCore.QPointF(-17, 18)]))
        else: painter.drawEllipse(QtCore.QRectF(-17, -17, 34, 34))
        painter.setPen(QtGui.QColor("#eceff1"))
        painter.drawText(QtCore.QRectF(-75, 20, 150, 22), QtCore.Qt.AlignmentFlag.AlignHCenter,
                         node.display_name)

    def mousePressEvent(self, event) -> None:
        self._drag_start = self.pos(); super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        if self.pos() != self._drag_start: self.moved.emit(self.node_id, self._drag_start, self.pos())

    def itemChange(self, change, value):
        if change == self.GraphicsItemChange.ItemPositionHasChanged and self.node_id in self.graph.nodes:
            self.graph.nodes[self.node_id].layout_x = value.x(); self.graph.nodes[self.node_id].layout_y = value.y()
            for edge in self.edges: edge.update_geometry()
        return super().itemChange(change, value)


class TopologyGraphicsScene(QtWidgets.QGraphicsScene):
    nodeActivated = QtCore.Signal(str)

    def mousePressEvent(self, event) -> None:
        item = self.itemAt(event.scenePos(), QtGui.QTransform())
        if isinstance(item, TopologyNodeItem): self.nodeActivated.emit(item.node_id)
        super().mousePressEvent(event)


class TopologyGraphicsView(QtWidgets.QGraphicsView):
    deletePressed = QtCore.Signal()
    connectionRequested = QtCore.Signal(str, str)

    def __init__(self, scene, parent=None) -> None:
        super().__init__(scene, parent); self.editor_mode = EditorMode.PAN
        self._connect_source: TopologyNodeItem | None = None
        self._connect_target: TopologyNodeItem | None = None
        self._preview: QtWidgets.QGraphicsLineItem | None = None
        self.connection_validator = lambda _source, _target: True
        self.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        self.setTransformationAnchor(self.ViewportAnchor.AnchorUnderMouse)
        self.setBackgroundBrush(QtGui.QColor("#263238")); self.set_editor_mode(EditorMode.PAN)

    def node_at(self, viewport_position) -> TopologyNodeItem | None:
        item = self.itemAt(viewport_position)
        return item if isinstance(item, TopologyNodeItem) else None

    def set_editor_mode(self, mode: EditorMode) -> None:
        self.cancel_connection(); self.editor_mode = mode; self.scene().clearSelection()
        editable = mode == EditorMode.SELECT
        for item in self.scene().items():
            if isinstance(item, TopologyNodeItem): item.set_editable(editable)
            elif isinstance(item, TopologyEdgeItem): item.setFlag(item.GraphicsItemFlag.ItemIsSelectable, editable)
        if mode == EditorMode.PAN:
            self.setDragMode(self.DragMode.ScrollHandDrag); self.setCursor(QtCore.Qt.CursorShape.OpenHandCursor)
        else:
            self.setDragMode(self.DragMode.NoDrag)
            self.setCursor(QtCore.Qt.CursorShape.CrossCursor if mode == EditorMode.CONNECT else
                           QtCore.Qt.CursorShape.ArrowCursor)

    def wheelEvent(self, event) -> None:
        self.scale(1.18 if event.angleDelta().y() > 0 else 1 / 1.18,
                   1.18 if event.angleDelta().y() > 0 else 1 / 1.18)

    def mousePressEvent(self, event) -> None:
        if self.editor_mode == EditorMode.CONNECT and event.button() == QtCore.Qt.MouseButton.LeftButton:
            source = self.node_at(event.position().toPoint())
            if source:
                self._connect_source = source
                self._preview = self.scene().addLine(QtCore.QLineF(source.pos(), source.pos()),
                    QtGui.QPen(QtGui.QColor("#90caf9"), 2, QtCore.Qt.PenStyle.DashLine))
            event.accept(); return
        if self.editor_mode == EditorMode.SELECT and event.button() == QtCore.Qt.MouseButton.LeftButton:
            self.scene().clearSelection()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._connect_source and self._preview:
            scene_pos = self.mapToScene(event.position().toPoint())
            self._preview.setLine(QtCore.QLineF(self._connect_source.pos(), scene_pos))
            target = self.node_at(event.position().toPoint())
            if (target is self._connect_source or
                    (target and not self.connection_validator(self._connect_source.node_id, target.node_id))):
                target = None
            if target is not self._connect_target:
                if self._connect_target: self._connect_target.setData(2, False); self._connect_target.update()
                self._connect_target = target
                if target: target.setData(2, True); target.update()
            event.accept(); return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._connect_source and event.button() == QtCore.Qt.MouseButton.LeftButton:
            source, target = self._connect_source, self.node_at(event.position().toPoint())
            if target and not self.connection_validator(source.node_id, target.node_id): target = None
            self.cancel_connection()
            if target and target is not source: self.connectionRequested.emit(source.node_id, target.node_id)
            event.accept(); return
        super().mouseReleaseEvent(event)

    def cancel_connection(self) -> None:
        if self._connect_target: self._connect_target.setData(2, False); self._connect_target.update()
        if self._preview and self._preview.scene(): self.scene().removeItem(self._preview)
        self._connect_source = self._connect_target = None; self._preview = None

    def keyPressEvent(self, event) -> None:
        if event.key() == QtCore.Qt.Key.Key_Escape: self.cancel_connection(); return
        if event.key() == QtCore.Qt.Key.Key_Delete and self.editor_mode == EditorMode.SELECT:
            self.deletePressed.emit(); return
        super().keyPressEvent(event)
