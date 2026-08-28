"""QGraphicsScene-basierter Editor fuer Betriebsstellen und Verbindungen."""

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from infrastructure import EditableTopologyGraph


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
        self.setPos(node.layout_x, node.layout_y)
        self.setFlags(self.GraphicsItemFlag.ItemIsMovable | self.GraphicsItemFlag.ItemIsSelectable |
                      self.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setZValue(1); self.setToolTip(node.display_name)

    def boundingRect(self):
        return QtCore.QRectF(-42, -28, 84, 56)

    def shape(self):
        path = QtGui.QPainterPath(); path.addEllipse(QtCore.QRectF(-18, -18, 36, 36)); return path

    def paint(self, painter, option, widget=None) -> None:
        node = self.graph.nodes[self.node_id]
        warning = self.graph.node_validation(self.node_id)
        highlighted = self.data(1) is True
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        fill = QtGui.QColor("#ffb300" if highlighted else "#42a5f5" if self.isSelected() else "#455a64")
        painter.setBrush(fill)
        painter.setPen(QtGui.QPen(QtGui.QColor("#ef5350" if warning else "#eceff1"), 3 if warning else 2))
        if node.node_type == "junction":
            polygon = QtGui.QPolygonF([QtCore.QPointF(0, -19), QtCore.QPointF(19, 0),
                                       QtCore.QPointF(0, 19), QtCore.QPointF(-19, 0)])
            painter.drawPolygon(polygon)
        elif node.node_type == "entry":
            polygon = QtGui.QPolygonF([QtCore.QPointF(-17, -18), QtCore.QPointF(20, 0),
                                       QtCore.QPointF(-17, 18)])
            painter.drawPolygon(polygon)
        else:
            painter.drawEllipse(QtCore.QRectF(-17, -17, 34, 34))
        painter.setPen(QtGui.QColor("#eceff1"))
        label = QtCore.QRectF(-75, 20, 150, 22)
        painter.drawText(label, QtCore.Qt.AlignmentFlag.AlignHCenter, node.display_name)

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
        while item and not isinstance(item, TopologyNodeItem): item = item.parentItem()
        if isinstance(item, TopologyNodeItem): self.nodeActivated.emit(item.node_id)
        super().mousePressEvent(event)


class TopologyGraphicsView(QtWidgets.QGraphicsView):
    deletePressed = QtCore.Signal()

    def __init__(self, scene, parent=None) -> None:
        super().__init__(scene, parent)
        self.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        self.setDragMode(self.DragMode.RubberBandDrag)
        self.setTransformationAnchor(self.ViewportAnchor.AnchorUnderMouse)
        self.setBackgroundBrush(QtGui.QColor("#263238"))

    def wheelEvent(self, event) -> None:
        factor = 1.18 if event.angleDelta().y() > 0 else 1 / 1.18
        self.scale(factor, factor)

    def keyPressEvent(self, event) -> None:
        if event.key() == QtCore.Qt.Key.Key_Delete: self.deletePressed.emit(); return
        super().keyPressEvent(event)
