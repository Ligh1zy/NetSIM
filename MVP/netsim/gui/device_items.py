"""QGraphicsItems for devices and links.

These items are *views*. They store only the id of the domain object they
represent and read everything else from the topology. The domain model stays
the single source of truth; the only thing that flows back from the canvas is
the x/y position after a drag.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QPen
from PyQt6.QtWidgets import (
    QGraphicsItem,
    QGraphicsLineItem,
    QGraphicsRectItem,
    QGraphicsSimpleTextItem,
)

from ..domain.models import Device, DeviceType

DEVICE_WIDTH = 118
DEVICE_HEIGHT = 58

BASE_COLORS = {
    DeviceType.PC: "#cfe4ff",
    DeviceType.SERVER: "#d6ccff",
    DeviceType.SWITCH: "#d8f0d0",
    DeviceType.ROUTER: "#ffe3b0",
    DeviceType.FIREWALL: "#ffd0cc",
}

# Highlight states produced by a simulation result.
HL_NONE = "none"
HL_PATH = "path"        # green: part of a successful path
HL_FAIL = "fail"        # red: the device that stopped the packet
HL_VISITED = "visited"  # yellow: inspected on the way to a failure

HIGHLIGHT_PENS = {
    HL_NONE: ("#555555", 1.5),
    HL_PATH: ("#1e8b3a", 4.0),
    HL_FAIL: ("#cc2222", 4.0),
    HL_VISITED: ("#d0a000", 3.0),
}


class DeviceItem(QGraphicsRectItem):
    """A box on the canvas standing for one domain Device."""

    def __init__(self, device: Device):
        super().__init__(-DEVICE_WIDTH / 2, -DEVICE_HEIGHT / 2, DEVICE_WIDTH, DEVICE_HEIGHT)
        self.device_id = device.id
        self.device_type = device.type
        self.highlight = HL_NONE

        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setZValue(10)
        self.setPos(QPointF(device.x, device.y))
        self.setBrush(QBrush(QColor(BASE_COLORS[device.type])))

        self._title = QGraphicsSimpleTextItem(self)
        title_font = QFont()
        title_font.setBold(True)
        self._title.setFont(title_font)

        self._subtitle = QGraphicsSimpleTextItem(self)
        subtitle_font = QFont()
        subtitle_font.setPointSize(max(7, subtitle_font.pointSize() - 2))
        self._subtitle.setFont(subtitle_font)
        self._subtitle.setBrush(QBrush(QColor("#333333")))

        self.refresh(device)
        self._apply_pen()

    # -- appearance --------------------------------------------------------
    def refresh(self, device: Device) -> None:
        """Re-read the domain object (name / addressing may have changed)."""
        self.device_type = device.type
        self._title.setText(device.name)
        self._subtitle.setText(_subtitle_for(device))
        self.setToolTip(_tooltip_for(device))
        self._centre_labels()

    def _centre_labels(self) -> None:
        title_rect = self._title.boundingRect()
        self._title.setPos(-title_rect.width() / 2, -DEVICE_HEIGHT / 2 + 6)
        sub_rect = self._subtitle.boundingRect()
        self._subtitle.setPos(-sub_rect.width() / 2, -DEVICE_HEIGHT / 2 + 8 + title_rect.height())

    def set_highlight(self, state: str) -> None:
        self.highlight = state
        self._apply_pen()

    def _apply_pen(self) -> None:
        colour, width = HIGHLIGHT_PENS.get(self.highlight, HIGHLIGHT_PENS[HL_NONE])
        pen = QPen(QColor(colour))
        pen.setWidthF(width)
        if self.isSelected():
            pen.setStyle(Qt.PenStyle.DashLine)
            if self.highlight == HL_NONE:
                pen.setColor(QColor("#1058c8"))
                pen.setWidthF(2.5)
        self.setPen(pen)

    # -- Qt callbacks ------------------------------------------------------
    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            scene = self.scene()
            if scene is not None and hasattr(scene, "on_device_moved"):
                scene.on_device_moved(self)
        elif change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            self._apply_pen()
        return super().itemChange(change, value)


class LinkItem(QGraphicsLineItem):
    """A cable between two DeviceItems."""

    def __init__(self, connection_id: str, source: DeviceItem, target: DeviceItem):
        super().__init__()
        self.connection_id = connection_id
        self.source = source
        self.target = target
        self.highlight = HL_NONE
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setZValue(1)
        self._apply_pen()
        self.update_position()

    def update_position(self) -> None:
        self.setLine(
            self.source.pos().x(), self.source.pos().y(),
            self.target.pos().x(), self.target.pos().y(),
        )

    def set_highlight(self, state: str) -> None:
        self.highlight = state
        self._apply_pen()

    def _apply_pen(self) -> None:
        colour, width = HIGHLIGHT_PENS.get(self.highlight, HIGHLIGHT_PENS[HL_NONE])
        if self.highlight == HL_NONE:
            colour, width = "#777777", 2.0
        pen = QPen(QColor(colour))
        pen.setWidthF(width)
        if self.isSelected():
            pen.setStyle(Qt.PenStyle.DashLine)
            pen.setColor(QColor("#1058c8"))
        self.setPen(pen)

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            self._apply_pen()
        return super().itemChange(change, value)


# --------------------------------------------------------------------------
# Label helpers
# --------------------------------------------------------------------------

def _subtitle_for(device: Device) -> str:
    if device.type is DeviceType.SWITCH:
        return "switch (layer 2)"
    addresses = [parsed.with_prefixlen for _, parsed in device.addresses()]
    if not addresses:
        return "%s - no IP" % device.type.label.lower()
    if len(addresses) == 1:
        return addresses[0]
    return "%s  (+%d more)" % (addresses[0], len(addresses) - 1)


def _tooltip_for(device: Device) -> str:
    lines = ["%s  (%s)" % (device.name, device.type.label)]
    if device.type is DeviceType.SWITCH:
        lines.append("Layer-2 bridge: forwards inside one LAN, does not route.")
        return "\n".join(lines)
    for iface in device.interfaces:
        lines.append("  %s: %s" % (iface.name, iface.cidr))
    if device.is_host:
        lines.append("  gateway: %s" % (device.gateway or "(none)"))
    if device.is_l3 and device.routes:
        lines.append("  %d static route(s)" % len(device.routes))
    if device.type is DeviceType.FIREWALL:
        lines.append("  %d rule(s), default policy %s"
                     % (len(device.firewall_rules), device.default_policy.value.upper()))
    return "\n".join(lines)
