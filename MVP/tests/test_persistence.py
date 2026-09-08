"""JSON topology persistence and the SQLite simulation history."""

from __future__ import annotations

import json

import pytest

from netsim.domain.models import Action, DeviceType, Protocol
from netsim.persistence import json_store
from netsim.persistence.database import SimulationHistory
from netsim.samples.demo import build_demo_topology
from netsim.simulation.engine import run_simulation


# -- JSON ------------------------------------------------------------------

def test_round_trip_preserves_the_whole_topology():
    original = build_demo_topology()
    restored = json_store.topology_from_dict(json_store.topology_to_dict(original))

    assert set(restored.devices) == set(original.devices)
    assert set(restored.connections) == set(original.connections)

    for device_id, device in original.devices.items():
        other = restored.devices[device_id]
        assert other.type is device.type
        assert other.name == device.name
        assert (other.x, other.y) == (device.x, device.y)
        assert [(i.id, i.name, i.ip, i.prefix) for i in other.interfaces] == \
               [(i.id, i.name, i.ip, i.prefix) for i in device.interfaces]
        assert other.gateway == device.gateway
        assert [(r.destination, r.prefix, r.next_hop, r.interface_id, r.metric)
                for r in other.routes] == \
               [(r.destination, r.prefix, r.next_hop, r.interface_id, r.metric)
                for r in device.routes]
        assert [(r.action, r.protocol, r.src, r.dst, r.dst_port, r.enabled)
                for r in other.firewall_rules] == \
               [(r.action, r.protocol, r.src, r.dst, r.dst_port, r.enabled)
                for r in device.firewall_rules]
        assert other.default_policy is device.default_policy


def test_round_trip_still_simulates_identically():
    original = build_demo_topology()
    restored = json_store.topology_from_dict(json_store.topology_to_dict(original))
    before = run_simulation(original, "pc1", "srv1", Protocol.TCP, 443)
    after = run_simulation(restored, "pc1", "srv1", Protocol.TCP, 443)
    assert before.success and after.success
    assert before.path_devices == after.path_devices
    assert before.log_lines() == after.log_lines()


def test_saved_file_is_readable_json(tmp_path):
    path = tmp_path / "topology.json"
    json_store.save_topology(build_demo_topology(), str(path))
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["version"] == json_store.SCHEMA_VERSION
    assert {d["id"] for d in data["devices"]} >= {"pc1", "r1", "fw1", "srv1"}
    assert data["devices"][0]["position"] == {"x": -360.0, "y": -40.0}


def test_load_from_disk(tmp_path):
    path = tmp_path / "topology.json"
    json_store.save_topology(build_demo_topology(), str(path))
    topology = json_store.load_topology(str(path))
    assert topology.device("fw1").firewall_rules[0].dst_port == 443


def test_switch_entry_has_no_routes_or_rules():
    data = json_store.topology_to_dict(build_demo_topology())
    switch = [d for d in data["devices"] if d["type"] == "switch"][0]
    assert "routes" not in switch and "firewall_rules" not in switch


def test_missing_version_is_rejected():
    with pytest.raises(json_store.TopologyFileError):
        json_store.topology_from_dict({"devices": [], "connections": []})


def test_wrong_version_is_rejected():
    with pytest.raises(json_store.TopologyFileError):
        json_store.topology_from_dict({"version": 99, "devices": [], "connections": []})


def test_bad_device_type_is_rejected():
    with pytest.raises(json_store.TopologyFileError):
        json_store.topology_from_dict(
            {"version": 1, "devices": [{"id": "x", "type": "toaster", "name": "X"}],
             "connections": []}
        )


def test_dangling_connection_is_rejected():
    with pytest.raises(json_store.TopologyFileError):
        json_store.topology_from_dict(
            {
                "version": 1,
                "devices": [],
                "connections": [{"id": "l1", "a": {"device_id": "nope"},
                                 "b": {"device_id": "nope2"}}],
            }
        )


def test_missing_file_raises_a_useful_error(tmp_path):
    with pytest.raises(json_store.TopologyFileError) as exc:
        json_store.load_topology(str(tmp_path / "nothing.json"))
    assert "File not found" in str(exc.value)


def test_invalid_json_raises_a_useful_error(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(json_store.TopologyFileError) as exc:
        json_store.load_topology(str(path))
    assert "not valid JSON" in str(exc.value)


def test_optional_fields_may_be_absent():
    topology = json_store.topology_from_dict(
        {
            "version": 1,
            "devices": [{"id": "pc-1", "type": "pc", "name": "PC1"}],
            "connections": [],
        }
    )
    device = topology.device("pc-1")
    assert device.interfaces == [] and device.gateway is None


# -- SQLite ----------------------------------------------------------------

def test_history_records_a_simulation(tmp_path):
    history = SimulationHistory(str(tmp_path / "history.sqlite3"))
    topology = build_demo_topology()
    result = run_simulation(topology, "pc1", "srv1", Protocol.TCP, 443)
    row_id = history.record(result)
    assert row_id > 0
    rows = history.recent()
    assert len(rows) == 1
    assert rows[0].success is True
    assert rows[0].source_name == "PC1"
    assert rows[0].destination_name == "Server1"
    assert rows[0].protocol == "tcp"
    assert rows[0].destination_port == 443
    assert rows[0].log
    history.close()


def test_history_records_a_failure_with_its_reason(tmp_path):
    history = SimulationHistory(str(tmp_path / "history.sqlite3"))
    topology = build_demo_topology()
    result = run_simulation(topology, "pc1", "srv1", Protocol.TCP, 80)
    history.record(result)
    row = history.recent()[0]
    assert row.success is False
    assert row.failure_reason == "firewall_blocked"
    assert row.blocked_by == "fw1"
    history.close()


def test_history_keeps_multiple_runs_in_order(tmp_path):
    history = SimulationHistory(str(tmp_path / "history.sqlite3"))
    topology = build_demo_topology()
    for port in (443, 80, 443):
        history.record(run_simulation(topology, "pc1", "srv1", Protocol.TCP, port))
    assert history.count() == 3
    rows = history.recent()
    assert [row.destination_port for row in rows] == [443, 80, 443]   # newest first
    history.close()


def test_history_survives_reopening_the_file(tmp_path):
    path = str(tmp_path / "history.sqlite3")
    first = SimulationHistory(path)
    first.record(run_simulation(build_demo_topology(), "pc1", "srv1", Protocol.ICMP))
    first.close()
    second = SimulationHistory(path)
    assert second.count() == 1
    second.close()
