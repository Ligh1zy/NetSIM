"""JSON persistence for topologies.

Schema version 1. The format is explicit and hand-editable; nothing is
pickled and no Python object is serialised implicitly.

    {
      "version": 1,
      "name": "Demo topology",
      "devices": [
        {
          "id": "pc-1",
          "type": "pc",                       # pc|server|switch|router|firewall
          "name": "PC1",
          "position": {"x": 40.0, "y": 120.0},
          "gateway": "192.168.1.1",           # hosts only, may be null
          "default_policy": "deny",           # firewalls only
          "interfaces": [
            {"id": "if-1", "name": "eth0", "ip": "192.168.1.10", "prefix": 24}
          ],
          "routes": [
            {"destination": "10.0.3.0", "prefix": 24, "next_hop": "10.0.0.2",
             "interface_id": "if-4", "metric": 10}
          ],
          "firewall_rules": [
            {"id": "rule-1", "action": "allow", "protocol": "tcp",
             "src": null, "dst": null, "dst_port": 443,
             "enabled": true, "description": "allow HTTPS"}
          ]
        }
      ],
      "connections": [
        {"id": "link-1",
         "a": {"device_id": "pc-1", "interface_id": "if-1"},
         "b": {"device_id": "switch-1", "interface_id": null}}
      ]
    }

Unknown fields are ignored on load and missing optional fields fall back to
defaults, so a file written by a slightly older build still opens.
"""

from __future__ import annotations

import json
from typing import Any, Dict

from ..domain.models import (
    Action,
    Connection,
    Device,
    DeviceType,
    Endpoint,
    FirewallRule,
    Interface,
    Protocol,
    Route,
    reseed_ids,
)
from ..domain.topology import Topology

SCHEMA_VERSION = 1


class TopologyFileError(Exception):
    """Raised when a file cannot be understood as a NetSim topology."""


# --------------------------------------------------------------------------
# Serialisation
# --------------------------------------------------------------------------

def topology_to_dict(topology: Topology) -> Dict[str, Any]:
    return {
        "version": SCHEMA_VERSION,
        "name": topology.name,
        "devices": [_device_to_dict(d) for d in topology.devices.values()],
        "connections": [_connection_to_dict(c) for c in topology.connections.values()],
    }


def _device_to_dict(device: Device) -> Dict[str, Any]:
    data: Dict[str, Any] = {
        "id": device.id,
        "type": device.type.value,
        "name": device.name,
        "position": {"x": float(device.x), "y": float(device.y)},
        "interfaces": [
            {"id": i.id, "name": i.name, "ip": i.ip, "prefix": i.prefix}
            for i in device.interfaces
        ],
    }
    if device.is_host:
        data["gateway"] = device.gateway
    if device.is_l3:
        data["routes"] = [
            {
                "destination": r.destination,
                "prefix": r.prefix,
                "next_hop": r.next_hop,
                "interface_id": r.interface_id,
                "metric": r.metric,
            }
            for r in device.routes
        ]
    if device.type is DeviceType.FIREWALL:
        data["default_policy"] = device.default_policy.value
        data["firewall_rules"] = [
            {
                "id": rule.id,
                "action": rule.action.value,
                "protocol": rule.protocol.value,
                "src": rule.src,
                "dst": rule.dst,
                "dst_port": rule.dst_port,
                "enabled": bool(rule.enabled),
                "description": rule.description,
            }
            for rule in device.firewall_rules
        ]
    return data


def _connection_to_dict(connection: Connection) -> Dict[str, Any]:
    return {
        "id": connection.id,
        "a": {"device_id": connection.a.device_id, "interface_id": connection.a.interface_id},
        "b": {"device_id": connection.b.device_id, "interface_id": connection.b.interface_id},
    }


# --------------------------------------------------------------------------
# Deserialisation
# --------------------------------------------------------------------------

def topology_from_dict(data: Dict[str, Any]) -> Topology:
    if not isinstance(data, dict):
        raise TopologyFileError("The file does not contain a JSON object.")
    version = data.get("version")
    if version is None:
        raise TopologyFileError("The file has no 'version' field; it is not a NetSim topology.")
    if int(version) != SCHEMA_VERSION:
        raise TopologyFileError(
            "Unsupported topology schema version %s (this build understands version %d)."
            % (version, SCHEMA_VERSION)
        )

    topology = Topology(name=str(data.get("name") or "Untitled topology"))

    for raw in data.get("devices", []) or []:
        topology.add_device(_device_from_dict(raw))

    for raw in data.get("connections", []) or []:
        connection = _connection_from_dict(raw)
        if connection.a.device_id not in topology.devices:
            raise TopologyFileError(
                "Connection %s refers to unknown device %s."
                % (connection.id, connection.a.device_id)
            )
        if connection.b.device_id not in topology.devices:
            raise TopologyFileError(
                "Connection %s refers to unknown device %s."
                % (connection.id, connection.b.device_id)
            )
        topology.add_connection(connection)

    reseed_ids(list(topology.all_ids()))
    return topology


def _enum(enum_cls, value, default):
    if value is None:
        return default
    try:
        return enum_cls(str(value).lower())
    except ValueError:
        raise TopologyFileError(
            "'%s' is not a valid %s (expected one of: %s)."
            % (value, enum_cls.__name__, ", ".join(e.value for e in enum_cls))
        )


def _device_from_dict(raw: Dict[str, Any]) -> Device:
    if not isinstance(raw, dict):
        raise TopologyFileError("A device entry is not a JSON object.")
    device_id = raw.get("id")
    if not device_id:
        raise TopologyFileError("A device entry has no 'id'.")
    device_type = _enum(DeviceType, raw.get("type"), None)
    if device_type is None:
        raise TopologyFileError("Device %s has no 'type'." % device_id)

    position = raw.get("position") or {}
    interfaces = []
    for raw_iface in raw.get("interfaces", []) or []:
        interfaces.append(
            Interface(
                id=str(raw_iface.get("id") or ""),
                name=str(raw_iface.get("name") or "eth"),
                ip=raw_iface.get("ip"),
                prefix=raw_iface.get("prefix"),
            )
        )

    routes = []
    for raw_route in raw.get("routes", []) or []:
        routes.append(
            Route(
                destination=str(raw_route.get("destination") or "0.0.0.0"),
                prefix=raw_route.get("prefix", 0),
                next_hop=raw_route.get("next_hop"),
                interface_id=raw_route.get("interface_id"),
                metric=raw_route.get("metric", 10),
            )
        )

    rules = []
    for index, raw_rule in enumerate(raw.get("firewall_rules", []) or [], start=1):
        rules.append(
            FirewallRule(
                id=str(raw_rule.get("id") or "rule-%d" % index),
                action=_enum(Action, raw_rule.get("action"), Action.DENY),
                protocol=_enum(Protocol, raw_rule.get("protocol"), Protocol.ANY),
                src=raw_rule.get("src"),
                dst=raw_rule.get("dst"),
                dst_port=raw_rule.get("dst_port"),
                enabled=bool(raw_rule.get("enabled", True)),
                description=str(raw_rule.get("description") or ""),
            )
        )

    return Device(
        id=str(device_id),
        type=device_type,
        name=str(raw.get("name") or device_id),
        x=float(position.get("x", 0.0)),
        y=float(position.get("y", 0.0)),
        interfaces=interfaces,
        gateway=raw.get("gateway"),
        routes=routes,
        firewall_rules=rules,
        default_policy=_enum(Action, raw.get("default_policy"), Action.DENY),
    )


def _connection_from_dict(raw: Dict[str, Any]) -> Connection:
    if not isinstance(raw, dict):
        raise TopologyFileError("A connection entry is not a JSON object.")
    for side in ("a", "b"):
        if not isinstance(raw.get(side), dict):
            raise TopologyFileError(
                "Connection %s is missing endpoint '%s'." % (raw.get("id"), side)
            )
    return Connection(
        id=str(raw.get("id") or ""),
        a=Endpoint(str(raw["a"].get("device_id")), raw["a"].get("interface_id")),
        b=Endpoint(str(raw["b"].get("device_id")), raw["b"].get("interface_id")),
    )


# --------------------------------------------------------------------------
# File helpers
# --------------------------------------------------------------------------

def save_topology(topology: Topology, path: str) -> None:
    payload = topology_to_dict(topology)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def load_topology(path: str) -> Topology:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        raise TopologyFileError("File not found: %s" % path)
    except json.JSONDecodeError as exc:
        raise TopologyFileError("%s is not valid JSON (%s)." % (path, exc))
    except OSError as exc:
        raise TopologyFileError("Could not read %s (%s)." % (path, exc))
    return topology_from_dict(data)
