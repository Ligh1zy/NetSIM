"""Application / integration layer.

This is the only place that knows about *all* of the lower layers at once. It
owns the topology, talks to the simulation engine, and writes history to
SQLite. The GUI calls this object and never reaches past it.

Keeping this layer separate is what makes the GUI replaceable: the CLI demo
script uses exactly the same API.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from ..domain.models import (
    Connection,
    Device,
    DeviceType,
    Interface,
    Protocol,
    new_id,
)
from ..domain.topology import Topology
from ..persistence import json_store
from ..persistence.database import HistoryRow, SimulationHistory
from ..simulation.engine import simulate
from ..simulation.result import (
    LogEntry,
    LogLevel,
    SimulationRequest,
    SimulationResult,
)
from ..simulation.validation import ValidationReport, validate_topology
from ..samples.demo import build_demo_topology


class NetSimError(Exception):
    """A user-facing problem (bad input, illegal operation). Not a bug."""


class AppController:
    def __init__(self, history_path: Optional[str] = None):
        self.topology = Topology()
        self.history = SimulationHistory(history_path)
        self.current_path: Optional[str] = None
        self.last_result: Optional[SimulationResult] = None

    # ------------------------------------------------------------ topology
    def new_topology(self) -> Topology:
        self.topology = Topology()
        self.current_path = None
        self.last_result = None
        return self.topology

    def load_demo(self) -> Topology:
        self.topology = build_demo_topology()
        self.current_path = None
        self.last_result = None
        return self.topology

    def add_device(self, device_type: DeviceType, x: float, y: float) -> Device:
        return self.topology.create_device(device_type, x=x, y=y)

    def move_device(self, device_id: str, x: float, y: float) -> None:
        device = self.topology.device(device_id)
        if device is None:
            raise NetSimError("Unknown device: %s" % device_id)
        device.x = float(x)
        device.y = float(y)

    def delete_device(self, device_id: str) -> List[str]:
        if device_id not in self.topology.devices:
            raise NetSimError("Unknown device: %s" % device_id)
        return self.topology.remove_device(device_id)

    def delete_connection(self, connection_id: str) -> None:
        if connection_id not in self.topology.connections:
            raise NetSimError("Unknown connection: %s" % connection_id)
        self.topology.remove_connection(connection_id)

    def add_interface(self, device_id: str) -> Interface:
        device = self.topology.device(device_id)
        if device is None:
            raise NetSimError("Unknown device: %s" % device_id)
        if device.is_switch:
            raise NetSimError("A switch in this MVP has no IP interfaces.")
        if device.is_host and device.interfaces:
            raise NetSimError(
                "A PC or Server is modelled with exactly one interface in this MVP."
            )
        iface = Interface(id=new_id("if"), name="eth%d" % len(device.interfaces))
        device.interfaces.append(iface)
        return iface

    def remove_interface(self, device_id: str, interface_id: str) -> None:
        device = self.topology.device(device_id)
        if device is None:
            raise NetSimError("Unknown device: %s" % device_id)
        if self.topology.connections_of_interface(device_id, interface_id):
            raise NetSimError(
                "That interface still has a cable attached. Delete the link first."
            )
        device.interfaces = [i for i in device.interfaces if i.id != interface_id]
        for route in device.routes:
            if route.interface_id == interface_id:
                route.interface_id = None

    # --------------------------------------------------------- connections
    def pick_interface(self, device_id: str) -> Tuple[Optional[str], Optional[str]]:
        """Choose an interface automatically for a new cable.

        Returns ``(interface_id, error_message)``. Switches get ``(None, None)``
        because they have ports rather than addressed interfaces.
        """
        device = self.topology.device(device_id)
        if device is None:
            return None, "Unknown device: %s" % device_id
        if device.is_switch:
            return None, None
        free = self.topology.free_interfaces(device_id)
        if not free:
            if not device.interfaces:
                return None, "%s has no interfaces to connect." % device.name
            return None, (
                "Every interface on %s already has a cable. Add another interface first."
                % device.name
            )
        return free[0].id, None

    def connect(
        self,
        device_a: str,
        device_b: str,
        interface_a: Optional[str] = None,
        interface_b: Optional[str] = None,
    ) -> Connection:
        if device_a == device_b:
            raise NetSimError("A device cannot be connected to itself.")
        for device_id, interface_id in ((device_a, interface_a), (device_b, interface_b)):
            device = self.topology.device(device_id)
            if device is None:
                raise NetSimError("Unknown device: %s" % device_id)
            if interface_id is not None and device.interface(interface_id) is None:
                raise NetSimError("%s has no interface %s." % (device.name, interface_id))
            if interface_id is not None and self.topology.connections_of_interface(
                device_id, interface_id
            ):
                iface = device.interface(interface_id)
                raise NetSimError(
                    "%s %s already has a cable attached." % (device.name, iface.name)
                )
        return self.topology.connect(device_a, device_b, interface_a, interface_b)

    def connect_auto(self, device_a: str, device_b: str) -> Connection:
        """Connect two devices, choosing free interfaces automatically."""
        iface_a, error_a = self.pick_interface(device_a)
        if error_a:
            raise NetSimError(error_a)
        iface_b, error_b = self.pick_interface(device_b)
        if error_b:
            raise NetSimError(error_b)
        return self.connect(device_a, device_b, iface_a, iface_b)

    # ---------------------------------------------------------- validation
    def validate(self) -> ValidationReport:
        return validate_topology(self.topology)

    # ---------------------------------------------------------- simulation
    def run_simulation(
        self,
        source_device_id: Optional[str],
        destination_device_id: Optional[str],
        protocol: Protocol = Protocol.ICMP,
        destination_port: Optional[int] = None,
        record: bool = True,
    ) -> SimulationResult:
        request = SimulationRequest(
            topology=self.topology,
            source_device_id=source_device_id,
            destination_device_id=destination_device_id,
            protocol=protocol,
            destination_port=destination_port,
        )
        result = simulate(request)
        self.last_result = result
        if record:
            try:
                self.history.record(result)
            except Exception as exc:  # pragma: no cover - disk/permission issues
                # History is a convenience, not a requirement: never let a
                # database problem destroy a simulation the user just ran.
                result.log.append(
                    LogEntry(LogLevel.WARNING, "Could not write simulation history: %s" % exc)
                )
        return result

    def history_rows(self, limit: int = 20) -> List[HistoryRow]:
        return self.history.recent(limit)

    # --------------------------------------------------------- persistence
    def save(self, path: Optional[str] = None) -> str:
        target = path or self.current_path
        if not target:
            raise NetSimError("No file name was given.")
        try:
            json_store.save_topology(self.topology, target)
        except OSError as exc:
            raise NetSimError("Could not write %s (%s)." % (target, exc))
        self.current_path = target
        return target

    def load(self, path: str) -> Topology:
        try:
            topology = json_store.load_topology(path)
        except json_store.TopologyFileError as exc:
            raise NetSimError(str(exc))
        self.topology = topology
        self.current_path = path
        self.last_result = None
        return topology

    def close(self) -> None:
        self.history.close()
