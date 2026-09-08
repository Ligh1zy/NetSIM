"""Editing dialogs: interfaces, routing table, firewall rules.

Each dialog edits a *copy* of the data and only writes back to the domain
model when the user accepts. If the entered data cannot be parsed the dialog
says exactly what is wrong and lets the user decide whether to keep it
anyway - deliberately broken configurations are how students explore failure
scenarios.
"""

from __future__ import annotations

from typing import List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..domain.models import (
    Action,
    Device,
    FirewallRule,
    Interface,
    Protocol,
    Route,
    new_id,
)
from ..domain.topology import Topology


def _text(value) -> str:
    return "" if value is None else str(value)


def _or_none(text: str) -> Optional[str]:
    text = text.strip()
    return text or None


class _TableDialog(QDialog):
    """Shared plumbing: a table, add/remove buttons and OK/Cancel."""

    def __init__(self, title: str, headers: List[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(720, 360)

        self.table = QTableWidget(0, len(headers), self)
        self.table.setHorizontalHeaderLabels(headers)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive
        )
        self.table.horizontalHeader().setStretchLastSection(True)

        self.add_button = QPushButton("Add")
        self.remove_button = QPushButton("Remove selected")
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self.on_accept)
        self.buttons.rejected.connect(self.reject)

        self.button_row = QHBoxLayout()
        self.button_row.addWidget(self.add_button)
        self.button_row.addWidget(self.remove_button)
        self.button_row.addStretch(1)

        self.layout_ = QVBoxLayout(self)
        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        self.layout_.addWidget(self.hint)
        self.layout_.addWidget(self.table)
        self.layout_.addLayout(self.button_row)
        self.layout_.addWidget(self.buttons)

        self.add_button.clicked.connect(self.add_row)
        self.remove_button.clicked.connect(self.remove_selected)

    # -- to be provided by subclasses -------------------------------------
    def add_row(self) -> None:  # pragma: no cover - GUI
        raise NotImplementedError

    def collect(self):  # pragma: no cover - GUI
        raise NotImplementedError

    def problems(self) -> List[str]:  # pragma: no cover - GUI
        return []

    # -- helpers -----------------------------------------------------------
    def remove_selected(self) -> None:
        rows = sorted({index.row() for index in self.table.selectedIndexes()}, reverse=True)
        for row in rows:
            self.table.removeRow(row)

    def selected_row(self) -> int:
        rows = {index.row() for index in self.table.selectedIndexes()}
        return min(rows) if rows else -1

    def cell(self, row: int, column: int) -> str:
        item = self.table.item(row, column)
        return item.text().strip() if item is not None else ""

    def set_cell(self, row: int, column: int, value) -> None:
        self.table.setItem(row, column, QTableWidgetItem(_text(value)))

    def on_accept(self) -> None:
        issues = self.problems()
        if issues:
            box = QMessageBox(self)
            box.setWindowTitle("Check this configuration")
            box.setIcon(QMessageBox.Icon.Warning)
            box.setText("Some entries could not be parsed:")
            box.setInformativeText("\n".join("- " + issue for issue in issues))
            keep = box.addButton("Save anyway", QMessageBox.ButtonRole.AcceptRole)
            box.addButton("Go back", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() is not keep:
                return
        self.accept()


# --------------------------------------------------------------------------
# Interfaces
# --------------------------------------------------------------------------

class InterfacesDialog(_TableDialog):
    COL_NAME, COL_IP, COL_PREFIX = 0, 1, 2

    def __init__(self, device: Device, topology: Topology, parent=None):
        super().__init__(
            "Interfaces - %s" % device.name, ["Name", "IPv4 address", "Prefix"], parent
        )
        self.device = device
        self.topology = topology
        self.hint.setText(
            "Each interface belongs to one network, e.g. 192.168.1.1 with prefix 24. "
            "An interface that already has a cable cannot be removed here."
        )
        for iface in device.interfaces:
            self._append(iface.id, iface.name, iface.ip, iface.prefix)
        self.table.resizeColumnsToContents()

    def _append(self, iface_id: str, name: str, ip, prefix) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.set_cell(row, self.COL_NAME, name)
        self.set_cell(row, self.COL_IP, ip)
        self.set_cell(row, self.COL_PREFIX, prefix)
        self.table.item(row, self.COL_NAME).setData(Qt.ItemDataRole.UserRole, iface_id)

    def add_row(self) -> None:
        if self.device.is_host and self.table.rowCount() >= 1:
            QMessageBox.information(
                self, "One interface only",
                "A PC or Server is modelled with exactly one interface in this MVP.",
            )
            return
        self._append(new_id("if"), "eth%d" % self.table.rowCount(), "", 24)

    def remove_selected(self) -> None:
        row = self.selected_row()
        if row < 0:
            return
        item = self.table.item(row, self.COL_NAME)
        iface_id = item.data(Qt.ItemDataRole.UserRole) if item else None
        if iface_id and self.topology.connections_of_interface(self.device.id, iface_id):
            QMessageBox.warning(
                self, "Interface in use",
                "That interface still has a cable attached. Delete the link on the canvas "
                "first.",
            )
            return
        self.table.removeRow(row)

    def problems(self) -> List[str]:
        issues = []
        for interface in self.collect():
            _, error = interface.parse()
            if error and interface.ip:
                issues.append(error)
        return issues

    def collect(self) -> List[Interface]:
        interfaces = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, self.COL_NAME)
            iface_id = (item.data(Qt.ItemDataRole.UserRole) if item else None) or new_id("if")
            prefix_text = self.cell(row, self.COL_PREFIX)
            try:
                prefix = int(prefix_text) if prefix_text else None
            except ValueError:
                prefix = prefix_text  # keep the bad value so validation can report it
            interfaces.append(
                Interface(
                    id=iface_id,
                    name=self.cell(row, self.COL_NAME) or "eth%d" % row,
                    ip=_or_none(self.cell(row, self.COL_IP)),
                    prefix=prefix,
                )
            )
        return interfaces


# --------------------------------------------------------------------------
# Routing table
# --------------------------------------------------------------------------

class RoutesDialog(_TableDialog):
    COL_DEST, COL_PREFIX, COL_NEXT_HOP, COL_IFACE, COL_METRIC = 0, 1, 2, 3, 4

    def __init__(self, device: Device, parent=None):
        super().__init__(
            "Routing table - %s" % device.name,
            ["Destination network", "Prefix", "Next hop", "Out interface", "Metric"],
            parent,
        )
        self.device = device
        self.hint.setText(
            "Directly connected networks are added automatically and are not listed here. "
            "A next hop must be a neighbour address on one of this device's own subnets. "
            "When several routes match, the longest prefix wins."
        )
        for route in device.routes:
            self._append(route)
        self.table.resizeColumnsToContents()

    def _interface_combo(self, selected_id: Optional[str]) -> QComboBox:
        combo = QComboBox()
        combo.addItem("(auto)", None)
        for iface in self.device.interfaces:
            combo.addItem("%s  %s" % (iface.name, iface.cidr), iface.id)
        index = combo.findData(selected_id)
        combo.setCurrentIndex(index if index >= 0 else 0)
        return combo

    def _append(self, route: Route) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.set_cell(row, self.COL_DEST, route.destination)
        self.set_cell(row, self.COL_PREFIX, route.prefix)
        self.set_cell(row, self.COL_NEXT_HOP, route.next_hop)
        self.table.setCellWidget(row, self.COL_IFACE, self._interface_combo(route.interface_id))
        self.set_cell(row, self.COL_METRIC, route.metric)

    def add_row(self) -> None:
        self._append(Route(destination="0.0.0.0", prefix=0, next_hop=None, metric=10))

    def problems(self) -> List[str]:
        issues = []
        for index, route in enumerate(self.collect(), start=1):
            _, error = route.parse()
            if error:
                issues.append("route #%d: %s" % (index, error))
            _, hop_error = route.parse_next_hop()
            if hop_error:
                issues.append("route #%d: %s" % (index, hop_error))
        return issues

    def collect(self) -> List[Route]:
        routes = []
        for row in range(self.table.rowCount()):
            combo = self.table.cellWidget(row, self.COL_IFACE)
            interface_id = combo.currentData() if isinstance(combo, QComboBox) else None
            prefix_text = self.cell(row, self.COL_PREFIX)
            try:
                prefix = int(prefix_text)
            except ValueError:
                prefix = prefix_text
            metric_text = self.cell(row, self.COL_METRIC)
            try:
                metric = int(metric_text)
            except ValueError:
                metric = 10
            routes.append(
                Route(
                    destination=self.cell(row, self.COL_DEST) or "0.0.0.0",
                    prefix=prefix,
                    next_hop=_or_none(self.cell(row, self.COL_NEXT_HOP)),
                    interface_id=interface_id,
                    metric=metric,
                )
            )
        return routes


# --------------------------------------------------------------------------
# Firewall rules
# --------------------------------------------------------------------------

class FirewallRulesDialog(_TableDialog):
    COL_ENABLED, COL_ACTION, COL_PROTO = 0, 1, 2
    COL_SRC, COL_DST, COL_PORT, COL_DESC = 3, 4, 5, 6

    def __init__(self, device: Device, parent=None):
        super().__init__(
            "Firewall rules - %s" % device.name,
            ["On", "Action", "Protocol", "Source", "Destination", "Dst port", "Comment"],
            parent,
        )
        self.device = device
        self.resize(860, 420)
        self.hint.setText(
            "Rules are evaluated top to bottom and THE FIRST MATCH WINS. Empty source, "
            "destination or port means 'any'. If no rule matches, the default policy below "
            "is used."
        )

        self.policy_combo = QComboBox()
        for action in (Action.DENY, Action.ALLOW):
            self.policy_combo.addItem(action.value.upper(), action)
        index = self.policy_combo.findData(device.default_policy)
        self.policy_combo.setCurrentIndex(index if index >= 0 else 0)

        policy_row = QHBoxLayout()
        policy_row.addWidget(QLabel("Default policy (no rule matched):"))
        policy_row.addWidget(self.policy_combo)
        policy_row.addStretch(1)
        self.layout_.insertLayout(self.layout_.count() - 1, policy_row)

        self.up_button = QPushButton("Move up")
        self.down_button = QPushButton("Move down")
        self.button_row.insertWidget(2, self.up_button)
        self.button_row.insertWidget(3, self.down_button)
        self.up_button.clicked.connect(lambda: self._move(-1))
        self.down_button.clicked.connect(lambda: self._move(1))

        for rule in device.firewall_rules:
            self._append(rule)
        self.table.resizeColumnsToContents()

    # -- row helpers -------------------------------------------------------
    def _append(self, rule: FirewallRule) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self._fill(row, rule)

    def _fill(self, row: int, rule: FirewallRule) -> None:
        checkbox = QCheckBox()
        checkbox.setChecked(bool(rule.enabled))
        holder = QWidget()
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(checkbox, alignment=Qt.AlignmentFlag.AlignCenter)
        self.table.setCellWidget(row, self.COL_ENABLED, holder)

        action_combo = QComboBox()
        for action in (Action.ALLOW, Action.DENY):
            action_combo.addItem(action.value.upper(), action)
        action_combo.setCurrentIndex(max(0, action_combo.findData(rule.action)))
        self.table.setCellWidget(row, self.COL_ACTION, action_combo)

        proto_combo = QComboBox()
        for protocol in (Protocol.ANY, Protocol.TCP, Protocol.UDP, Protocol.ICMP):
            proto_combo.addItem(protocol.value.upper(), protocol)
        proto_combo.setCurrentIndex(max(0, proto_combo.findData(rule.protocol)))
        self.table.setCellWidget(row, self.COL_PROTO, proto_combo)

        self.set_cell(row, self.COL_SRC, rule.src)
        self.set_cell(row, self.COL_DST, rule.dst)
        self.set_cell(row, self.COL_PORT, rule.dst_port)
        self.set_cell(row, self.COL_DESC, rule.description)
        self.table.item(row, self.COL_DESC).setData(Qt.ItemDataRole.UserRole, rule.id)

    def _row_rule(self, row: int) -> FirewallRule:
        holder = self.table.cellWidget(row, self.COL_ENABLED)
        checkbox = holder.findChild(QCheckBox) if holder is not None else None
        action_combo = self.table.cellWidget(row, self.COL_ACTION)
        proto_combo = self.table.cellWidget(row, self.COL_PROTO)
        desc_item = self.table.item(row, self.COL_DESC)
        rule_id = (desc_item.data(Qt.ItemDataRole.UserRole) if desc_item else None)
        port_text = self.cell(row, self.COL_PORT)
        try:
            port = int(port_text) if port_text else None
        except ValueError:
            port = port_text
        return FirewallRule(
            id=rule_id or new_id("rule"),
            action=action_combo.currentData() if action_combo else Action.DENY,
            protocol=proto_combo.currentData() if proto_combo else Protocol.ANY,
            src=_or_none(self.cell(row, self.COL_SRC)),
            dst=_or_none(self.cell(row, self.COL_DST)),
            dst_port=port,
            enabled=bool(checkbox.isChecked()) if checkbox else True,
            description=self.cell(row, self.COL_DESC),
        )

    def _move(self, delta: int) -> None:
        row = self.selected_row()
        if row < 0:
            return
        target = row + delta
        if not 0 <= target < self.table.rowCount():
            return
        rules = self.collect()
        rules[row], rules[target] = rules[target], rules[row]
        self.table.setRowCount(0)
        for rule in rules:
            self._append(rule)
        self.table.selectRow(target)

    def add_row(self) -> None:
        self._append(
            FirewallRule(
                id=new_id("rule"), action=Action.ALLOW, protocol=Protocol.ANY,
                description="new rule",
            )
        )

    def problems(self) -> List[str]:
        issues = []
        for index, rule in enumerate(self.collect(), start=1):
            for _, error in (rule.parse_src(), rule.parse_dst()):
                if error:
                    issues.append("rule #%d: %s" % (index, error))
            _, port_error = rule.parse_port()
            if port_error:
                issues.append("rule #%d: %s" % (index, port_error))
        return issues

    def collect(self) -> List[FirewallRule]:
        return [self._row_rule(row) for row in range(self.table.rowCount())]

    def default_policy(self) -> Action:
        return self.policy_combo.currentData() or Action.DENY
