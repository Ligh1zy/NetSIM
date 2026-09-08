"""Right-hand properties panel.

The panel is rebuilt whenever the selection changes. Edits are written back to
the domain model when the user presses Apply, after which the panel re-runs
validation and shows any problems that concern the selected device.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..app.controller import AppController, NetSimError
from ..domain.models import Device, DeviceType
from .dialogs import FirewallRulesDialog, InterfacesDialog, RoutesDialog


class PropertiesPanel(QWidget):
    deviceChanged = pyqtSignal(str)
    connectionDeleted = pyqtSignal(str)
    statusMessage = pyqtSignal(str)

    def __init__(self, controller: AppController, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.device_id: Optional[str] = None
        self.connection_id: Optional[str] = None

        self.setMinimumWidth(300)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(8, 8, 8, 8)

        self.header = QLabel("Nothing selected")
        self.header.setStyleSheet("font-weight: bold; font-size: 13px;")
        self._layout.addWidget(self.header)

        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self._layout.addWidget(self.body)

        self.issues = QTextEdit()
        self.issues.setReadOnly(True)
        self.issues.setMaximumHeight(140)
        self.issues.setPlaceholderText("Validation messages for the selected device appear here.")
        self._layout.addWidget(QLabel("Validation"))
        self._layout.addWidget(self.issues)
        self._layout.addStretch(1)

        self.show_nothing()

    # -- panel state -------------------------------------------------------
    def _clear_body(self) -> None:
        while self.body_layout.count():
            item = self.body_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)

    def show_nothing(self) -> None:
        self.device_id = None
        self.connection_id = None
        self._clear_body()
        self.header.setText("Nothing selected")
        hint = QLabel(
            "Click a device to configure it, or a link to inspect it.\n\n"
            "Add devices from the palette on the left, then use Connect mode to cable them "
            "together."
        )
        hint.setWordWrap(True)
        self.body_layout.addWidget(hint)
        self.issues.setPlainText("")

    def show_connection(self, connection_id: str) -> None:
        self.device_id = None
        self.connection_id = connection_id
        self._clear_body()
        connection = self.controller.topology.connection(connection_id)
        if connection is None:
            self.show_nothing()
            return
        topology = self.controller.topology
        self.header.setText("Link %s" % connection_id)

        def side(endpoint):
            device = topology.device(endpoint.device_id)
            if device is None:
                return endpoint.device_id
            iface = device.interface(endpoint.interface_id)
            if iface is None:
                return "%s (switch port)" % device.name
            return "%s %s (%s)" % (device.name, iface.name, iface.cidr)

        form = QGroupBox("Endpoints")
        form_layout = QFormLayout(form)
        form_layout.addRow("A:", QLabel(side(connection.a)))
        form_layout.addRow("B:", QLabel(side(connection.b)))
        self.body_layout.addWidget(form)

        delete = QPushButton("Delete this link")
        delete.clicked.connect(lambda: self._delete_connection(connection_id))
        self.body_layout.addWidget(delete)
        self.issues.setPlainText("")

    def _delete_connection(self, connection_id: str) -> None:
        try:
            self.controller.delete_connection(connection_id)
        except NetSimError as exc:
            QMessageBox.warning(self, "Cannot delete", str(exc))
            return
        self.connectionDeleted.emit(connection_id)
        self.show_nothing()

    def show_device(self, device_id: str) -> None:
        device = self.controller.topology.device(device_id)
        if device is None:
            self.show_nothing()
            return
        self.device_id = device_id
        self.connection_id = None
        self._clear_body()
        self.header.setText("%s  (%s)" % (device.name, device.type.label))

        general = QGroupBox("General")
        general_form = QFormLayout(general)
        self.name_edit = QLineEdit(device.name)
        general_form.addRow("Name:", self.name_edit)
        general_form.addRow("Device id:", _readonly(device.id))
        self.body_layout.addWidget(general)

        if device.is_host:
            self._build_host(device)
        elif device.type is DeviceType.SWITCH:
            self._build_switch(device)
        else:
            self._build_l3(device)

        apply_button = QPushButton("Apply")
        apply_button.clicked.connect(self._apply)
        self.body_layout.addWidget(apply_button)

        self.refresh_issues()

    # -- per-type bodies ---------------------------------------------------
    def _build_host(self, device: Device) -> None:
        iface = device.primary_interface()
        box = QGroupBox("IPv4 configuration")
        form = QFormLayout(box)
        self.ip_edit = QLineEdit(iface.ip if iface and iface.ip else "")
        self.ip_edit.setPlaceholderText("192.168.1.10")
        self.prefix_spin = QSpinBox()
        self.prefix_spin.setRange(0, 32)
        self.prefix_spin.setValue(int(iface.prefix) if iface and iface.prefix else 24)
        self.gateway_edit = QLineEdit(device.gateway or "")
        self.gateway_edit.setPlaceholderText("192.168.1.1 (leave empty for none)")
        form.addRow("IPv4 address:", self.ip_edit)
        form.addRow("Prefix (/n):", self.prefix_spin)
        form.addRow("Default gateway:", self.gateway_edit)
        self.body_layout.addWidget(box)

    def _build_switch(self, device: Device) -> None:
        label = QLabel(
            "A switch is a layer-2 bridge in this MVP. It has no IP address and never routes "
            "between subnets: it only forwards frames inside one LAN."
        )
        label.setWordWrap(True)
        self.body_layout.addWidget(label)

    def _build_l3(self, device: Device) -> None:
        box = QGroupBox("Interfaces")
        layout = QVBoxLayout(box)
        for iface in device.interfaces:
            layout.addWidget(QLabel("%s: %s" % (iface.name, iface.cidr)))
        if not device.interfaces:
            layout.addWidget(QLabel("(none)"))
        interfaces_button = QPushButton("Edit interfaces...")
        interfaces_button.clicked.connect(self._edit_interfaces)
        layout.addWidget(interfaces_button)
        self.body_layout.addWidget(box)

        routes_box = QGroupBox("Static routes (%d)" % len(device.routes))
        routes_layout = QVBoxLayout(routes_box)
        for route in device.routes[:6]:
            routes_layout.addWidget(
                QLabel("%s via %s" % (route.cidr, route.next_hop or "on-link"))
            )
        if len(device.routes) > 6:
            routes_layout.addWidget(QLabel("... and %d more" % (len(device.routes) - 6)))
        if not device.routes:
            routes_layout.addWidget(QLabel("(only directly connected networks)"))
        routes_button = QPushButton("Edit routing table...")
        routes_button.clicked.connect(self._edit_routes)
        routes_layout.addWidget(routes_button)
        self.body_layout.addWidget(routes_box)

        if device.type is DeviceType.FIREWALL:
            rules_box = QGroupBox("Firewall rules (%d)" % len(device.firewall_rules))
            rules_layout = QVBoxLayout(rules_box)
            for index, rule in enumerate(device.firewall_rules[:6], start=1):
                rules_layout.addWidget(QLabel("#%d %s" % (index, rule.summary())))
            if len(device.firewall_rules) > 6:
                rules_layout.addWidget(
                    QLabel("... and %d more" % (len(device.firewall_rules) - 6))
                )
            rules_layout.addWidget(
                QLabel("Default policy: %s" % device.default_policy.value.upper())
            )
            rules_button = QPushButton("Edit firewall rules...")
            rules_button.clicked.connect(self._edit_rules)
            rules_layout.addWidget(rules_button)
            self.body_layout.addWidget(rules_box)

    # -- actions -----------------------------------------------------------
    def _current_device(self) -> Optional[Device]:
        return self.controller.topology.device(self.device_id)

    def _apply(self) -> None:
        device = self._current_device()
        if device is None:
            return
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Name required", "A device needs a name.")
            return
        other = self.controller.topology.device_by_name(name)
        if other is not None and other.id != device.id:
            QMessageBox.warning(
                self, "Duplicate name", "Another device is already called '%s'." % name
            )
            return
        device.name = name

        if device.is_host:
            iface = device.primary_interface()
            if iface is None:
                iface = self.controller.add_interface(device.id)
            iface.ip = self.ip_edit.text().strip() or None
            iface.prefix = self.prefix_spin.value()
            device.gateway = self.gateway_edit.text().strip() or None

        self.deviceChanged.emit(device.id)
        self.show_device(device.id)
        self.statusMessage.emit("Applied changes to %s." % device.name)

    def _edit_interfaces(self) -> None:
        device = self._current_device()
        if device is None:
            return
        dialog = InterfacesDialog(device, self.controller.topology, self)
        if dialog.exec():
            device.interfaces = dialog.collect()
            valid_ids = {i.id for i in device.interfaces}
            for route in device.routes:
                if route.interface_id not in valid_ids:
                    route.interface_id = None
            self.deviceChanged.emit(device.id)
            self.show_device(device.id)

    def _edit_routes(self) -> None:
        device = self._current_device()
        if device is None:
            return
        dialog = RoutesDialog(device, self)
        if dialog.exec():
            device.routes = dialog.collect()
            self.deviceChanged.emit(device.id)
            self.show_device(device.id)

    def _edit_rules(self) -> None:
        device = self._current_device()
        if device is None:
            return
        dialog = FirewallRulesDialog(device, self)
        if dialog.exec():
            device.firewall_rules = dialog.collect()
            device.default_policy = dialog.default_policy()
            self.deviceChanged.emit(device.id)
            self.show_device(device.id)

    # -- validation feedback ----------------------------------------------
    def refresh_issues(self) -> None:
        if self.device_id is None:
            self.issues.setPlainText("")
            return
        report = self.controller.validate()
        issues = report.for_device(self.device_id)
        if not issues:
            self.issues.setPlainText("No problems found for this device.")
            return
        self.issues.setPlainText("\n\n".join(str(issue) for issue in issues))


def _readonly(text: str) -> QLabel:
    label = QLabel(text)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    label.setFrameStyle(QFrame.Shape.NoFrame)
    return label
