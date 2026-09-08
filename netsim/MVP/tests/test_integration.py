"""Integration tests and the architectural rule that keeps the engine clean.

The whole point of the MVP is this chain:

    topology model -> simulation engine -> SimulationResult -> persistence

with no GUI anywhere near it.
"""

from __future__ import annotations

import os
import subprocess
import sys

from netsim.app.controller import AppController, NetSimError
from netsim.domain.models import Action, DeviceType, FirewallRule, Protocol, Route
from netsim.persistence import json_store
from netsim.simulation.result import FailureReason

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# -- the architectural rule ------------------------------------------------

def test_importing_the_engine_does_not_load_pyqt():
    """A subprocess check, so it cannot be fooled by an earlier import."""
    code = (
        "import sys;"
        "import netsim.simulation.engine, netsim.app.controller,"
        " netsim.persistence.json_store, netsim.persistence.database,"
        " netsim.samples.demo;"
        "loaded=[m for m in sys.modules if m.startswith('PyQt')];"
        "print('LOADED', loaded);"
        "sys.exit(1 if loaded else 0)"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code], cwd=PROJECT_ROOT,
        capture_output=True, text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_no_module_below_the_gui_package_mentions_qt():
    offenders = []
    for folder, _dirs, files in os.walk(os.path.join(PROJECT_ROOT, "netsim")):
        if os.path.basename(folder) == "gui":
            continue
        for filename in files:
            if not filename.endswith(".py"):
                continue
            path = os.path.join(folder, filename)
            with open(path, "r", encoding="utf-8") as handle:
                if "PyQt" in handle.read():
                    offenders.append(os.path.relpath(path, PROJECT_ROOT))
    assert offenders == []


# -- full stack without a GUI ---------------------------------------------

def test_build_simulate_save_load_and_record(tmp_path):
    controller = AppController(history_path=str(tmp_path / "history.sqlite3"))

    pc = controller.add_device(DeviceType.PC, 0, 0)
    switch = controller.add_device(DeviceType.SWITCH, 100, 0)
    router = controller.add_device(DeviceType.ROUTER, 200, 0)
    firewall = controller.add_device(DeviceType.FIREWALL, 300, 0)
    server = controller.add_device(DeviceType.SERVER, 400, 0)

    pc.interfaces[0].ip, pc.interfaces[0].prefix = "192.168.1.10", 24
    pc.gateway = "192.168.1.1"

    router.interfaces[0].ip, router.interfaces[0].prefix = "192.168.1.1", 24
    router.interfaces[1].ip, router.interfaces[1].prefix = "10.0.0.1", 30
    router.routes = [Route("10.0.3.0", 24, "10.0.0.2", router.interfaces[1].id)]

    firewall.interfaces[0].ip, firewall.interfaces[0].prefix = "10.0.0.2", 30
    firewall.interfaces[1].ip, firewall.interfaces[1].prefix = "10.0.3.1", 24
    firewall.default_policy = Action.DENY
    firewall.firewall_rules = [
        FirewallRule(id="r1", action=Action.ALLOW, protocol=Protocol.TCP, dst_port=443),
    ]

    server.interfaces[0].ip, server.interfaces[0].prefix = "10.0.3.10", 24
    server.gateway = "10.0.3.1"

    controller.connect_auto(pc.id, switch.id)
    controller.connect_auto(switch.id, router.id)
    controller.connect_auto(router.id, firewall.id)
    controller.connect_auto(firewall.id, server.id)

    assert controller.validate().ok, [str(i) for i in controller.validate().errors]

    allowed = controller.run_simulation(pc.id, server.id, Protocol.TCP, 443)
    assert allowed.success, allowed.failure_message

    blocked = controller.run_simulation(pc.id, server.id, Protocol.TCP, 80)
    assert not blocked.success
    assert blocked.failure_reason is FailureReason.FIREWALL_BLOCKED

    assert controller.history.count() == 2

    path = str(tmp_path / "topology.json")
    controller.save(path)
    reloaded = AppController(history_path=str(tmp_path / "history2.sqlite3"))
    reloaded.load(path)
    again = reloaded.run_simulation(pc.id, server.id, Protocol.TCP, 443)
    assert again.success
    assert again.path_devices == allowed.path_devices

    controller.close()
    reloaded.close()


def test_connect_auto_picks_free_interfaces_and_then_refuses(tmp_path):
    controller = AppController(history_path=str(tmp_path / "h.sqlite3"))
    router = controller.add_device(DeviceType.ROUTER, 0, 0)      # two interfaces
    a = controller.add_device(DeviceType.PC, 0, 0)
    b = controller.add_device(DeviceType.PC, 0, 0)
    c = controller.add_device(DeviceType.PC, 0, 0)

    controller.connect_auto(router.id, a.id)
    controller.connect_auto(router.id, b.id)
    used = {
        connection.endpoint_for(router.id).interface_id
        for connection in controller.topology.connections_of(router.id)
    }
    assert used == {router.interfaces[0].id, router.interfaces[1].id}

    try:
        controller.connect_auto(router.id, c.id)
    except NetSimError as exc:
        assert "already has a cable" in str(exc)
    else:
        raise AssertionError("expected the third connection to be refused")
    controller.close()


def test_deleting_a_device_removes_its_cables(tmp_path):
    controller = AppController(history_path=str(tmp_path / "h.sqlite3"))
    a = controller.add_device(DeviceType.PC, 0, 0)
    switch = controller.add_device(DeviceType.SWITCH, 0, 0)
    b = controller.add_device(DeviceType.PC, 0, 0)
    controller.connect_auto(a.id, switch.id)
    controller.connect_auto(switch.id, b.id)
    removed = controller.delete_device(switch.id)
    assert len(removed) == 2
    assert controller.topology.connections == {}
    controller.close()


def test_moving_a_device_updates_the_model_and_survives_a_save(tmp_path):
    controller = AppController(history_path=str(tmp_path / "h.sqlite3"))
    device = controller.add_device(DeviceType.PC, 0, 0)
    controller.move_device(device.id, 123.5, -45.25)
    path = str(tmp_path / "t.json")
    controller.save(path)
    restored = json_store.load_topology(path)
    assert (restored.device(device.id).x, restored.device(device.id).y) == (123.5, -45.25)
    controller.close()


def test_controller_reports_bad_files_as_netsim_errors(tmp_path):
    controller = AppController(history_path=str(tmp_path / "h.sqlite3"))
    broken = tmp_path / "broken.json"
    broken.write_text("{", encoding="utf-8")
    try:
        controller.load(str(broken))
    except NetSimError:
        pass
    else:
        raise AssertionError("expected a NetSimError")
    controller.close()
