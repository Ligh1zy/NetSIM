"""Headless smoke test for the GUI layer.

    python gui_smoke_test.py [output.png]

Drives the real MainWindow without a human: loads the demo topology, adds and
connects devices, moves one, runs simulations, checks that the canvas
highlighting matches the SimulationResult, and saves the topology.

It is kept out of the pytest suite on purpose - the pytest suite must run
without PyQt. Run this separately when you touch the GUI.

On a machine with no display, set QT_QPA_PLATFORM=offscreen first.
"""

from __future__ import annotations

import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from netsim.app.controller import AppController  # noqa: E402
from netsim.domain.models import DeviceType, Protocol  # noqa: E402
from netsim.gui.canvas import MODE_CONNECT, MODE_SELECT  # noqa: E402
from netsim.gui.device_items import HL_FAIL, HL_NONE, HL_PATH, HL_VISITED  # noqa: E402
from netsim.gui.main_window import MainWindow  # noqa: E402

checks = 0


def check(condition, message):
    global checks
    if not condition:
        raise AssertionError(message)
    checks += 1
    print("  ok  %s" % message)


def select_endpoints(window, source_name, destination_name):
    window.source_combo.setCurrentIndex(window.source_combo.findText(source_name))
    window.destination_combo.setCurrentIndex(
        window.destination_combo.findText(destination_name)
    )


def main() -> int:
    output = sys.argv[1] if len(sys.argv) > 1 else "netsim_screenshot.png"
    app = QApplication.instance() or QApplication([])

    workdir = tempfile.mkdtemp(prefix="netsim-gui-")
    controller = AppController(history_path=os.path.join(workdir, "history.sqlite3"))
    window = MainWindow(controller)
    window.show()

    print("1. demo topology")
    window._load_demo()
    check(len(window.scene.device_items) == 6, "six device items on the canvas")
    check(len(window.scene.link_items) == 5, "five link items on the canvas")
    check(window.source_combo.count() == 3, "source combo lists (none) + 2 hosts")

    print("2. selection and properties panel")
    window.scene.device_items["r1"].setSelected(True)
    app.processEvents()
    check(window.properties.device_id == "r1", "selecting Router1 shows it in the panel")
    window.scene.clearSelection()
    app.processEvents()

    print("3. dragging a device updates the domain model")
    window.scene.device_items["pc1"].setPos(-380, 60)
    app.processEvents()
    check(
        (controller.topology.device("pc1").x, controller.topology.device("pc1").y)
        == (-380.0, 60.0),
        "the moved position reached the topology model",
    )

    print("4. successful simulation highlights the path in green")
    select_endpoints(window, "PC1", "Server1")
    window.protocol_combo.setCurrentIndex(window.protocol_combo.findText("ICMP"))
    window.port_spin.setValue(0)
    window._simulate()
    result = controller.last_result
    check(result.success, "ICMP PC1 -> Server1 succeeds")
    check(
        all(window.scene.device_items[d].highlight == HL_PATH for d in result.path_devices),
        "every device on the path is green",
    )
    check(
        all(window.scene.link_items[c].highlight == HL_PATH
            for c in result.path_connections),
        "every link on the path is green",
    )
    check("Simulation succeeded" in window.console.toPlainText(), "console shows the log")

    print("5. blocked simulation marks the firewall in red")
    window.protocol_combo.setCurrentIndex(window.protocol_combo.findText("TCP"))
    window.port_spin.setValue(80)
    window._simulate()
    blocked = controller.last_result
    check(not blocked.success, "TCP/80 is blocked")
    check(window.scene.device_items["fw1"].highlight == HL_FAIL, "Firewall1 is red")
    check(window.scene.device_items["pc1"].highlight == HL_VISITED,
          "the attempted path is yellow")
    check(window.scene.device_items["srv1"].highlight == HL_NONE,
          "Server1 was never reached, so it stays uncoloured")

    print("6. allowed port")
    window.port_spin.setValue(443)
    window._simulate()
    check(controller.last_result.success, "TCP/443 is allowed")

    print("7. adding and connecting devices from the GUI")
    before = len(controller.topology.devices)
    window._add_device(DeviceType.PC)
    check(len(controller.topology.devices) == before + 1, "palette added a PC")
    new_pc = [d for d in controller.topology.devices.values()
              if d.type is DeviceType.PC and d.id != "pc1"][0]
    window._set_mode(MODE_CONNECT)
    window.scene.connectRequested.emit(new_pc.id, "sw1")
    app.processEvents()
    check(len(controller.topology.connections) == 6, "a sixth cable exists in the model")
    check(len(window.scene.link_items) == 6, "and a sixth link item on the canvas")
    window._set_mode(MODE_SELECT)

    print("8. the new PC can talk to PC1's LAN once addressed")
    new_pc.interfaces[0].ip = "192.168.1.50"
    new_pc.interfaces[0].prefix = 24
    new_pc.name = "PC2"
    window._on_device_changed(new_pc.id)
    select_endpoints(window, "PC2", "PC1")
    window.protocol_combo.setCurrentIndex(window.protocol_combo.findText("ICMP"))
    window.port_spin.setValue(0)
    window._simulate()
    check(controller.last_result.success, "PC2 -> PC1 succeeds over the switch")

    print("9. deleting")
    window.scene.clearSelection()
    window.scene.device_items[new_pc.id].setSelected(True)
    window._delete_selected()
    check(new_pc.id not in controller.topology.devices, "device removed from the model")
    check(new_pc.id not in window.scene.device_items, "device removed from the canvas")
    check(len(controller.topology.connections) == 5, "its cable went with it")

    print("10. validation view")
    window._validate()
    check("warning" in window.console.toPlainText().lower()
          or "no problems" in window.console.toPlainText().lower(),
          "validation writes a report to the console")

    print("11. save / load round trip")
    path = os.path.join(workdir, "topology.json")
    window._write(path)
    check(os.path.exists(path), "topology written to disk")
    window.controller.new_topology()
    window._reload_scene()
    check(len(window.scene.device_items) == 0, "canvas cleared for a new topology")
    window.controller.load(path)
    window._reload_scene()
    check(len(window.scene.device_items) == 6, "reloaded topology is back on the canvas")

    print("12. simulation history")
    check(controller.history.count() >= 4, "simulations were recorded in SQLite")

    print("13. screenshot")
    window._load_demo()
    select_endpoints(window, "PC1", "Server1")
    window.protocol_combo.setCurrentIndex(window.protocol_combo.findText("TCP"))
    window.port_spin.setValue(80)
    window._simulate()
    window.scene.device_items["fw1"].setSelected(True)
    app.processEvents()
    window.grab().save(output)
    check(os.path.exists(output), "screenshot saved to %s" % output)

    controller.close()
    print()
    print("%d checks passed." % checks)
    return 0


if __name__ == "__main__":
    sys.exit(main())
