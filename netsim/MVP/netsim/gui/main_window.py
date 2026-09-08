"""The NetSim main window.

Layout:

    +-----------+-------------------------------+------------------+
    | palette   |  topology canvas              |  properties      |
    |           |                               |                  |
    +-----------+-------------------------------+------------------+
    |  simulation controls + console                               |
    +--------------------------------------------------------------+

This module wires Qt widgets to :class:`AppController`. It contains no
networking logic whatsoever: every question about the network is answered by
the simulation layer.
"""

from __future__ import annotations

import traceback
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..app.controller import AppController, NetSimError
from ..domain.models import DeviceType, Protocol
from ..simulation.validation import Severity
from .canvas import MODE_CONNECT, MODE_SELECT, TopologyScene, TopologyView
from .device_items import DeviceItem, LinkItem
from .properties_panel import PropertiesPanel

PALETTE_ORDER = (
    DeviceType.PC,
    DeviceType.SERVER,
    DeviceType.SWITCH,
    DeviceType.ROUTER,
    DeviceType.FIREWALL,
)


class MainWindow(QMainWindow):
    def __init__(self, controller: Optional[AppController] = None):
        super().__init__()
        self.controller = controller or AppController()
        self.setWindowTitle("NetSim MVP - Network Topology Designer & Simulator")
        self.resize(1280, 820)

        self.scene = TopologyScene(self)
        self.view = TopologyView(self.scene, self)
        self.properties = PropertiesPanel(self.controller, self)

        self._build_ui()
        self._connect_signals()
        self._new_topology()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        palette = QWidget()
        palette_layout = QVBoxLayout(palette)
        palette_layout.addWidget(QLabel("<b>Add device</b>"))
        for device_type in PALETTE_ORDER:
            button = QPushButton(device_type.label)
            button.clicked.connect(
                lambda _checked=False, t=device_type: self._add_device(t)
            )
            palette_layout.addWidget(button)

        palette_layout.addSpacing(12)
        palette_layout.addWidget(QLabel("<b>Mode</b>"))
        self.select_button = QPushButton("Select / move")
        self.select_button.setCheckable(True)
        self.select_button.setChecked(True)
        self.connect_button = QPushButton("Connect devices")
        self.connect_button.setCheckable(True)
        self.select_button.clicked.connect(lambda: self._set_mode(MODE_SELECT))
        self.connect_button.clicked.connect(lambda: self._set_mode(MODE_CONNECT))
        palette_layout.addWidget(self.select_button)
        palette_layout.addWidget(self.connect_button)

        palette_layout.addSpacing(12)
        delete_button = QPushButton("Delete selected")
        delete_button.clicked.connect(self._delete_selected)
        palette_layout.addWidget(delete_button)
        palette_layout.addStretch(1)
        palette.setMaximumWidth(190)

        bottom = QWidget()
        bottom_layout = QVBoxLayout(bottom)

        controls = QGroupBox("Simulation")
        controls_layout = QHBoxLayout(controls)
        self.source_combo = QComboBox()
        self.destination_combo = QComboBox()
        self.protocol_combo = QComboBox()
        for protocol in (Protocol.ICMP, Protocol.TCP, Protocol.UDP, Protocol.ANY):
            self.protocol_combo.addItem(protocol.value.upper(), protocol)
        self.port_spin = QSpinBox()
        self.port_spin.setRange(0, 65535)
        self.port_spin.setSpecialValueText("(none)")
        self.port_spin.setValue(0)
        self.simulate_button = QPushButton("Simulate")
        self.simulate_button.setDefault(True)
        self.validate_button = QPushButton("Validate topology")
        self.clear_button = QPushButton("Clear highlights")

        controls_layout.addWidget(QLabel("Source:"))
        controls_layout.addWidget(self.source_combo, 1)
        controls_layout.addWidget(QLabel("Destination:"))
        controls_layout.addWidget(self.destination_combo, 1)
        controls_layout.addWidget(QLabel("Protocol:"))
        controls_layout.addWidget(self.protocol_combo)
        controls_layout.addWidget(QLabel("Dst port:"))
        controls_layout.addWidget(self.port_spin)
        controls_layout.addWidget(self.simulate_button)
        controls_layout.addWidget(self.validate_button)
        controls_layout.addWidget(self.clear_button)

        self.console = QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setMinimumHeight(180)
        self.console.setPlaceholderText(
            "Simulation output appears here. Every line is produced by a real decision in the "
            "simulation engine."
        )

        bottom_layout.addWidget(controls)
        bottom_layout.addWidget(self.console)

        centre_splitter = QSplitter(Qt.Orientation.Vertical)
        centre_splitter.addWidget(self.view)
        centre_splitter.addWidget(bottom)
        centre_splitter.setStretchFactor(0, 3)
        centre_splitter.setStretchFactor(1, 1)

        main_splitter = QSplitter(Qt.Orientation.Horizontal)
        main_splitter.addWidget(palette)
        main_splitter.addWidget(centre_splitter)
        main_splitter.addWidget(self.properties)
        main_splitter.setStretchFactor(1, 4)

        self.setCentralWidget(main_splitter)
        self.statusBar().showMessage("Ready.")
        self._build_menu()

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        file_menu.addAction(self._action("&New topology", self._new_topology, "Ctrl+N"))
        file_menu.addAction(self._action("Load &demo topology", self._load_demo))
        file_menu.addSeparator()
        file_menu.addAction(self._action("&Open...", self._open, QKeySequence.StandardKey.Open))
        file_menu.addAction(self._action("&Save", self._save, QKeySequence.StandardKey.Save))
        file_menu.addAction(self._action("Save &as...", self._save_as))
        file_menu.addSeparator()
        file_menu.addAction(self._action("E&xit", self.close))

        edit_menu = self.menuBar().addMenu("&Edit")
        edit_menu.addAction(self._action("&Delete selected", self._delete_selected, "Del"))

        sim_menu = self.menuBar().addMenu("&Simulation")
        sim_menu.addAction(self._action("&Run simulation", self._simulate, "F5"))
        sim_menu.addAction(self._action("&Validate topology", self._validate, "F6"))
        sim_menu.addAction(self._action("Simulation &history...", self._show_history))

        help_menu = self.menuBar().addMenu("&Help")
        help_menu.addAction(self._action("&About NetSim MVP", self._about))

        QShortcut(QKeySequence("Esc"), self, activated=self._cancel_connect)

    def _action(self, text, slot, shortcut=None) -> QAction:
        action = QAction(text, self)
        action.triggered.connect(slot)
        if shortcut is not None:
            action.setShortcut(shortcut)
        return action

    def _connect_signals(self) -> None:
        self.scene.deviceSelected.connect(self.properties.show_device)
        self.scene.connectionSelected.connect(self.properties.show_connection)
        self.scene.selectionCleared.connect(self.properties.show_nothing)
        self.scene.deviceMoved.connect(self._on_device_moved)
        self.scene.connectRequested.connect(self._on_connect_requested)
        self.scene.statusMessage.connect(self.statusBar().showMessage)

        self.properties.deviceChanged.connect(self._on_device_changed)
        self.properties.connectionDeleted.connect(self._on_connection_deleted)
        self.properties.statusMessage.connect(self.statusBar().showMessage)

        self.simulate_button.clicked.connect(self._simulate)
        self.validate_button.clicked.connect(self._validate)
        self.clear_button.clicked.connect(self._clear_highlights)

    # ------------------------------------------------------------ topology
    def _reload_scene(self) -> None:
        self.scene.rebuild(self.controller.topology)
        self._refresh_endpoint_combos()
        self.properties.show_nothing()

    def _new_topology(self) -> None:
        self.controller.new_topology()
        self.console.clear()
        self._reload_scene()
        self._set_title()
        self.statusBar().showMessage("New empty topology.")

    def _fit_view(self) -> None:
        """Zoom so the whole topology is visible, without magnifying it."""
        bounds = self.scene.itemsBoundingRect()
        if bounds.isEmpty():
            self.view.centerOn(0, 0)
            return
        bounds = bounds.adjusted(-60, -60, 60, 60)
        self.view.resetTransform()
        viewport = self.view.viewport().rect()
        if bounds.width() > viewport.width() or bounds.height() > viewport.height():
            self.view.fitInView(bounds, Qt.AspectRatioMode.KeepAspectRatio)
        self.view.centerOn(bounds.center())

    def _load_demo(self) -> None:
        self.controller.load_demo()
        self.console.clear()
        self._reload_scene()
        self._fit_view()
        self._set_title()
        self._log(
            "Loaded the demo topology: PC1 - Switch1 - Router1 - Router2 - Firewall1 - Server1.\n"
            "Try ICMP PC1 -> Server1 (allowed), TCP/80 (blocked by rule #2) and TCP/443 "
            "(allowed by rule #1)."
        )

    def _set_title(self) -> None:
        name = self.controller.current_path or "(unsaved)"
        self.setWindowTitle("NetSim MVP - %s" % name)

    def _add_device(self, device_type: DeviceType) -> None:
        centre = self.view.mapToScene(self.view.viewport().rect().center())
        offset = 26 * (len(self.controller.topology.devices) % 6)
        device = self.controller.add_device(
            device_type, centre.x() - 200 + offset, centre.y() - 120 + offset
        )
        self.scene.add_device_item(device)
        self._refresh_endpoint_combos()
        self.statusBar().showMessage("Added %s." % device.name)

    def _on_device_moved(self, device_id: str, x: float, y: float) -> None:
        try:
            self.controller.move_device(device_id, x, y)
        except NetSimError as exc:  # pragma: no cover - defensive
            self.statusBar().showMessage(str(exc))

    def _on_device_changed(self, device_id: str) -> None:
        device = self.controller.topology.device(device_id)
        if device is not None:
            self.scene.refresh_device(device)
        self._refresh_endpoint_combos()

    def _on_connection_deleted(self, connection_id: str) -> None:
        self.scene.remove_connection_item(connection_id)
        self.statusBar().showMessage("Link deleted.")

    def _on_connect_requested(self, device_a: str, device_b: str) -> None:
        try:
            connection = self.controller.connect_auto(device_a, device_b)
        except NetSimError as exc:
            QMessageBox.warning(self, "Cannot connect", str(exc))
            self.statusBar().showMessage(str(exc))
            return
        self.scene.add_connection_item(connection)
        names = []
        for device_id in (device_a, device_b):
            device = self.controller.topology.device(device_id)
            names.append(device.name if device else device_id)
        self.statusBar().showMessage("Connected %s to %s." % (names[0], names[1]))
        for device_id in (device_a, device_b):
            self._on_device_changed(device_id)

    def _set_mode(self, mode: str) -> None:
        self.scene.set_mode(mode)
        self.select_button.setChecked(mode == MODE_SELECT)
        self.connect_button.setChecked(mode == MODE_CONNECT)
        if mode == MODE_CONNECT:
            self.statusBar().showMessage(
                "Connect mode: click the first device, then the second (Esc to cancel)."
            )
        else:
            self.statusBar().showMessage("Select mode: click to select, drag to move.")

    def _cancel_connect(self) -> None:
        self.scene.cancel_pending()
        self._set_mode(MODE_SELECT)

    def _delete_selected(self) -> None:
        items = list(self.scene.selectedItems())
        if not items:
            self.statusBar().showMessage("Nothing selected.")
            return
        for item in items:
            try:
                if isinstance(item, DeviceItem):
                    removed = self.controller.delete_device(item.device_id)
                    self.scene.remove_device_item(item.device_id, removed)
                elif isinstance(item, LinkItem):
                    self.controller.delete_connection(item.connection_id)
                    self.scene.remove_connection_item(item.connection_id)
            except NetSimError as exc:
                QMessageBox.warning(self, "Cannot delete", str(exc))
        self._refresh_endpoint_combos()
        self.properties.show_nothing()

    # ---------------------------------------------------------- simulation
    def _refresh_endpoint_combos(self) -> None:
        for combo in (self.source_combo, self.destination_combo):
            previous = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("(none)", None)
            for device in sorted(self.controller.topology.hosts(), key=lambda d: d.name):
                combo.addItem(device.name, device.id)
            index = combo.findData(previous)
            combo.setCurrentIndex(index if index >= 0 else 0)
            combo.blockSignals(False)

    def _simulate(self) -> None:
        source = self.source_combo.currentData()
        destination = self.destination_combo.currentData()
        port = self.port_spin.value() or None
        protocol = self.protocol_combo.currentData() or Protocol.ICMP
        try:
            result = self.controller.run_simulation(source, destination, protocol, port)
        except Exception as exc:  # pragma: no cover - guards against engine bugs
            self._report_crash(exc)
            return

        self.console.clear()
        self._log(result.log_text())
        self._log("")
        self._log(result.summary())
        self.scene.apply_result(result)
        self.statusBar().showMessage(result.summary())
        self.properties.refresh_issues()

    def _validate(self) -> None:
        report = self.controller.validate()
        self.console.clear()
        if not report.issues:
            self._log("Validation: no problems found.")
            self.statusBar().showMessage("Topology is valid.")
            return
        for issue in report.issues:
            self._log(str(issue))
        self._log("")
        self._log(
            "%d error(s), %d warning(s)." % (len(report.errors), len(report.warnings))
        )
        self.statusBar().showMessage(
            "%d error(s), %d warning(s)." % (len(report.errors), len(report.warnings))
        )
        self.properties.refresh_issues()

    def _clear_highlights(self) -> None:
        self.scene.clear_highlights()
        self.statusBar().showMessage("Highlights cleared.")

    def _show_history(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Simulation history (SQLite)")
        dialog.resize(820, 420)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Stored in %s" % self.controller.history.path))
        text = QTextEdit()
        text.setReadOnly(True)
        rows = self.controller.history_rows(limit=100)
        if rows:
            text.setPlainText("\n".join(row.summary() for row in rows))
        else:
            text.setPlainText("No simulations recorded yet.")
        layout.addWidget(text)
        dialog.exec()

    # --------------------------------------------------------- persistence
    def _open(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open topology", "", "NetSim topology (*.json);;All files (*)"
        )
        if not path:
            return
        try:
            self.controller.load(path)
        except NetSimError as exc:
            QMessageBox.critical(self, "Could not open file", str(exc))
            return
        self._reload_scene()
        self._fit_view()
        self._set_title()
        self.statusBar().showMessage("Loaded %s." % path)

    def _save(self) -> None:
        if not self.controller.current_path:
            self._save_as()
            return
        self._write(self.controller.current_path)

    def _save_as(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save topology", "topology.json", "NetSim topology (*.json)"
        )
        if not path:
            return
        self._write(path)

    def _write(self, path: str) -> None:
        try:
            self.controller.save(path)
        except NetSimError as exc:
            QMessageBox.critical(self, "Could not save file", str(exc))
            return
        self._set_title()
        self.statusBar().showMessage("Saved %s." % path)

    # ---------------------------------------------------------------- misc
    def _about(self) -> None:
        QMessageBox.information(
            self,
            "About NetSim MVP",
            "NetSim MVP - an educational network topology designer and schematic simulator.\n\n"
            "The simulation engine is independent of this GUI: see demo_cli.py and the pytest "
            "suite, both of which run the same engine with no Qt involved.",
        )

    def _log(self, text: str) -> None:
        self.console.appendPlainText(text)

    def _report_crash(self, exc: Exception) -> None:
        """Programming errors must not take the application down."""
        details = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        self._log("INTERNAL ERROR: %s" % exc)
        self._log(details)
        QMessageBox.critical(
            self,
            "Internal error",
            "The simulation engine raised an unexpected error:\n\n%s\n\n"
            "This is a bug, not a configuration problem. The details are in the console." % exc,
        )

    def closeEvent(self, event):
        self.controller.close()
        super().closeEvent(event)


def run() -> int:
    """Entry point used by main.py."""
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.show()
    return app.exec()
