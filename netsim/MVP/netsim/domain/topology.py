"""The Topology container: the single source of truth for a network diagram.

The GUI holds *no* networking state of its own. Graphics items only remember
the ``device_id`` / ``connection_id`` they represent and read/write through
this object.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

import networkx as nx

from .models import (
    Action,
    Connection,
    Device,
    DeviceType,
    Endpoint,
    Interface,
    new_id,
)


#: Default interface counts when a device is created from the palette.
DEFAULT_INTERFACE_COUNT = {
    DeviceType.PC: 1,
    DeviceType.SERVER: 1,
    DeviceType.SWITCH: 0,
    DeviceType.ROUTER: 2,
    DeviceType.FIREWALL: 2,
}


@dataclass
class Topology:
    name: str = "Untitled topology"
    devices: Dict[str, Device] = field(default_factory=dict)
    connections: Dict[str, Connection] = field(default_factory=dict)

    # -- devices -----------------------------------------------------------
    def add_device(self, device: Device) -> Device:
        if device.id in self.devices:
            raise ValueError("duplicate device id: %s" % device.id)
        self.devices[device.id] = device
        return device

    def create_device(
        self,
        device_type: DeviceType,
        name: Optional[str] = None,
        x: float = 0.0,
        y: float = 0.0,
    ) -> Device:
        """Create a device with sensible empty defaults and register it."""
        device_id = new_id(device_type.value)
        if name is None:
            name = self._unique_name(device_type)
        interfaces = [
            Interface(id=new_id("if"), name="eth%d" % index)
            for index in range(DEFAULT_INTERFACE_COUNT[device_type])
        ]
        device = Device(
            id=device_id,
            type=device_type,
            name=name,
            x=x,
            y=y,
            interfaces=interfaces,
            default_policy=Action.DENY,
        )
        return self.add_device(device)

    def _unique_name(self, device_type: DeviceType) -> str:
        base = device_type.label
        index = 1
        existing = {d.name for d in self.devices.values()}
        while "%s%d" % (base, index) in existing:
            index += 1
        return "%s%d" % (base, index)

    def remove_device(self, device_id: str) -> List[str]:
        """Remove a device and every connection attached to it.

        Returns the ids of the connections that were removed so the GUI can
        drop the matching graphics items.
        """
        self.devices.pop(device_id, None)
        removed = [c.id for c in self.connections.values() if c.touches(device_id)]
        for connection_id in removed:
            self.connections.pop(connection_id, None)
        return removed

    def device(self, device_id: Optional[str]) -> Optional[Device]:
        if device_id is None:
            return None
        return self.devices.get(device_id)

    def device_by_name(self, name: str) -> Optional[Device]:
        for device in self.devices.values():
            if device.name == name:
                return device
        return None

    def hosts(self) -> List[Device]:
        return [d for d in self.devices.values() if d.is_host]

    # -- connections -------------------------------------------------------
    def connect(
        self,
        device_a: str,
        device_b: str,
        interface_a: Optional[str] = None,
        interface_b: Optional[str] = None,
        connection_id: Optional[str] = None,
    ) -> Connection:
        if device_a not in self.devices or device_b not in self.devices:
            raise ValueError("cannot connect unknown devices")
        if device_a == device_b:
            raise ValueError("a device cannot be connected to itself")
        connection = Connection(
            id=connection_id or new_id("link"),
            a=Endpoint(device_a, interface_a),
            b=Endpoint(device_b, interface_b),
        )
        self.connections[connection.id] = connection
        return connection

    def add_connection(self, connection: Connection) -> Connection:
        self.connections[connection.id] = connection
        return connection

    def remove_connection(self, connection_id: str) -> None:
        self.connections.pop(connection_id, None)

    def connection(self, connection_id: Optional[str]) -> Optional[Connection]:
        if connection_id is None:
            return None
        return self.connections.get(connection_id)

    def connections_of(self, device_id: str) -> List[Connection]:
        return [c for c in self.connections.values() if c.touches(device_id)]

    def connections_of_interface(self, device_id: str, interface_id: Optional[str]):
        """Cables plugged into one specific interface.

        Switches have no interfaces, so for them every attached cable counts.
        """
        result = []
        for connection in self.connections.values():
            endpoint = connection.endpoint_for(device_id)
            if endpoint is None:
                continue
            if interface_id is None or endpoint.interface_id == interface_id:
                result.append(connection)
        return result

    def free_interfaces(self, device_id: str) -> List[Interface]:
        """Interfaces on a device that do not have a cable yet."""
        device = self.device(device_id)
        if device is None:
            return []
        used = set()
        for connection in self.connections_of(device_id):
            endpoint = connection.endpoint_for(device_id)
            if endpoint and endpoint.interface_id:
                used.add(endpoint.interface_id)
        return [iface for iface in device.interfaces if iface.id not in used]

    def all_ids(self) -> Iterable[str]:
        yield from self.devices.keys()
        yield from self.connections.keys()
        for device in self.devices.values():
            for iface in device.interfaces:
                yield iface.id
            for rule in device.firewall_rules:
                yield rule.id

    # -- graph helpers (NetworkX) -----------------------------------------
    def physical_graph(self) -> "nx.Graph":
        """Undirected graph of physical cabling.

        NOTE: this graph answers "are these boxes wired together?" only. It
        deliberately says nothing about whether IP forwarding is permitted;
        that is the job of the simulation engine.
        """
        graph = nx.Graph()
        for device_id in self.devices:
            graph.add_node(device_id)
        for connection in self.connections.values():
            graph.add_edge(
                connection.a.device_id,
                connection.b.device_id,
                connection_id=connection.id,
            )
        return graph

    def isolated_devices(self) -> List[str]:
        graph = self.physical_graph()
        return [node for node in graph.nodes if graph.degree(node) == 0]

    def physically_connected(self, device_a: str, device_b: str) -> bool:
        graph = self.physical_graph()
        if device_a not in graph or device_b not in graph:
            return False
        return nx.has_path(graph, device_a, device_b)
