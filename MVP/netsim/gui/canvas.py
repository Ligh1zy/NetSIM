"""The topology canvas (QGraphicsScene + QGraphicsView).

The scene mirrors the domain topology. It never invents network state; it
emits signals and lets the main window ask the controller to change the
model, then re-syncs.

Two interaction modes:

* SELECT  - normal editing: click to select, drag to move.
* CONNECT - click device A, then device B, to request a cable. The scene only
  *asks*; the controller decides whether the connection is legal.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from PyQt6.QtCore import QPointF, Qt, pyqtSignal
from PyQt6.QtGui import QPainter
from PyQt6.QtWidgets import QGraphicsScene, QGraphicsView

from ..domain.topology import Topology
from ..simulation.result import SimulationResult
from .device_items import (
    HL_FAIL,
    HL_NONE,
    HL_PATH,
    HL_VISITED,
    DeviceItem,
    LinkItem,
)

MODE_SELECT = "select"
MODE_CONNECT = "connect"


class TopologyScene(QGraphicsScene):
    deviceSelected = pyqtSignal(str)
    connectionSelected = pyqtSignal(str)
    selectionCleared = pyqtSignal()
    deviceMoved = pyqtSignal(str, float, float)
    connectRequested = pyqtSignal(str, str)
    statusMessage = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSceneRect(-800, -450, 1600, 900)
        self.device_items: Dict[str, DeviceItem] = {}
        self.link_items: Dict[str, LinkItem] = {}
        self.mode = MODE_SELECT
        self._pending_device: Optional[str] = None
        self.selectionChanged.connect(self._on_selection_changed)

    # -- mode --------------------------------------------------------------
    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self._pending_device = None
        for item in self.device_items.values():
            item.setFlag(
                item.GraphicsItemFlag.ItemIsMovable, mode == MODE_SELECT
            )

    def cancel_pending(self) -> None:
        self._pending_device = None

    # -- rebuilding from the domain model ---------------------------------
    def rebuild(self, topology: Topology) -> None:
        self.clear()
        self.device_items.clear()
        self.link_items.clear()
        self._pending_device = None
        for device in topology.devices.values():
            item = DeviceItem(device)
            self.addItem(item)
            self.device_items[device.id] = item
        for connection in topology.connections.values():
            self._add_link_item(connection.id, connection.a.device_id, connection.b.device_id)
        self.set_mode(self.mode)

    def _add_link_item(self, connection_id: str, device_a: str, device_b: str) -> None:
        source = self.device_items.get(device_a)
        target = self.device_items.get(device_b)
        if source is None or target is None:
            return
        link = LinkItem(connection_id, source, target)
        self.addItem(link)
        self.link_items[connection_id] = link

    def add_device_item(self, device) -> DeviceItem:
        item = DeviceItem(device)
        self.addItem(item)
        self.device_items[device.id] = item
        item.setFlag(item.GraphicsItemFlag.ItemIsMovable, self.mode == MODE_SELECT)
        return item

    def add_connection_item(self, connection) -> None:
        self._add_link_item(connection.id, connection.a.device_id, connection.b.device_id)

    def remove_device_item(self, device_id: str, connection_ids: List[str]) -> None:
        for connection_id in connection_ids:
            self.remove_connection_item(connection_id)
        item = self.device_items.pop(device_id, None)
        if item is not None:
            self.removeItem(item)

    def remove_connection_item(self, connection_id: str) -> None:
        link = self.link_items.pop(connection_id, None)
        if link is not None:
            self.removeItem(link)

    def refresh_device(self, device) -> None:
        item = self.device_items.get(device.id)
        if item is not None:
            item.refresh(device)

    # -- callbacks from items ---------------------------------------------
    def on_device_moved(self, item: DeviceItem) -> None:
        for link in self.link_items.values():
            if link.source is item or link.target is item:
                link.update_position()
        self.deviceMoved.emit(item.device_id, item.pos().x(), item.pos().y())

    def _on_selection_changed(self) -> None:
        selected = self.selectedItems()
        if not selected:
            self.selectionCleared.emit()
            return
        item = selected[0]
        if isinstance(item, DeviceItem):
            self.deviceSelected.emit(item.device_id)
        elif isinstance(item, LinkItem):
            self.connectionSelected.emit(item.connection_id)

    # -- mouse -------------------------------------------------------------
    def mousePressEvent(self, event):
        if self.mode == MODE_CONNECT and event.button() == Qt.MouseButton.LeftButton:
            device_item = self._device_at(event.scenePos())
            if device_item is None:
                self.statusMessage.emit("Connect mode: click a device.")
                event.accept()
                return
            if self._pending_device is None:
                self._pending_device = device_item.device_id
                self.clearSelection()
                device_item.setSelected(True)
                self.statusMessage.emit(
                    "Connect mode: now click the second device (Esc to cancel)."
                )
            else:
                first = self._pending_device
                self._pending_device = None
                self.clearSelection()
                self.connectRequested.emit(first, device_item.device_id)
            event.accept()
            return
        super().mousePressEvent(event)

    def _device_at(self, position: QPointF) -> Optional[DeviceItem]:
        for item in self.items(position):
            if isinstance(item, DeviceItem):
                return item
            parent = item.parentItem()
            if isinstance(parent, DeviceItem):
                return parent
        return None

    # -- highlighting ------------------------------------------------------
    def clear_highlights(self) -> None:
        for item in self.device_items.values():
            item.set_highlight(HL_NONE)
        for link in self.link_items.values():
            link.set_highlight(HL_NONE)

    def apply_result(self, result: SimulationResult) -> None:
        """Colour the canvas from a SimulationResult.

        GREEN  path of a successful simulation
        YELLOW devices/links the packet actually reached before failing
        RED    the device where the simulation stopped
        """
        self.clear_highlights()
        path_state = HL_PATH if result.success else HL_VISITED
        for device_id in result.path_devices:
            item = self.device_items.get(device_id)
            if item is not None:
                item.set_highlight(path_state)
        for connection_id in result.path_connections:
            link = self.link_items.get(connection_id)
            if link is not None:
                link.set_highlight(path_state)
        if not result.success and result.failure_device_id:
            item = self.device_items.get(result.failure_device_id)
            if item is not None:
                item.set_highlight(HL_FAIL)


class TopologyView(QGraphicsView):
    def __init__(self, scene: TopologyScene, parent=None):
        super().__init__(scene, parent)
        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setMinimumSize(560, 380)

    def wheelEvent(self, event):
        """Ctrl + wheel zooms; a plain wheel scrolls as usual."""
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
            self.scale(factor, factor)
            event.accept()
            return
        super().wheelEvent(event)
