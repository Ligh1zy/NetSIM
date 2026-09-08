"""Layer-2 ("same LAN") resolution.

This module answers exactly one question:

    Starting from interface I on device D, which device on the same broadcast
    domain owns IP address X, and which cables do we cross to get there?

Rules that make this a *layer-2* search rather than a generic graph search:

* A switch is transparent: the search floods out of every other port.
* A router/firewall/host is opaque: the search stops there. It never walks
  *through* a layer-3 device, because crossing one requires a routing
  decision, and routing decisions belong to :mod:`netsim.simulation.routing`.

That single restriction is what makes "PC1 -- Switch -- PC2 on different
subnets" fail correctly instead of being magically bridged.
"""

from __future__ import annotations

import ipaddress
from collections import deque
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from ..domain.models import Device, Interface
from ..domain.topology import Topology


@dataclass
class L2Hit:
    """Result of a successful layer-2 lookup."""

    device_id: str
    interface_id: Optional[str]
    #: Devices crossed, excluding the starting device, including the target.
    path_devices: List[str] = field(default_factory=list)
    #: Cables crossed, in order.
    path_connections: List[str] = field(default_factory=list)


def _interfaces_at_endpoint(device: Device, interface_id: Optional[str]) -> List[Interface]:
    """Which interfaces can answer for a cable landing on this endpoint.

    If the cable recorded an explicit interface we use exactly that one. If it
    did not (older files, or hosts wired without picking a port) we fall back
    to every interface on the device, which is the forgiving behaviour a
    student expects from a teaching tool.
    """
    if interface_id is not None:
        iface = device.interface(interface_id)
        return [iface] if iface is not None else []
    return list(device.interfaces)


def _owns_address(
    device: Device,
    interface_id: Optional[str],
    target: ipaddress.IPv4Address,
) -> Optional[Interface]:
    for iface in _interfaces_at_endpoint(device, interface_id):
        parsed, _ = iface.parse()
        if parsed is not None and parsed.ip == target:
            return iface
    return None


def resolve_on_segment(
    topology: Topology,
    start_device_id: str,
    start_interface_id: Optional[str],
    target_ip: ipaddress.IPv4Address,
) -> Optional[L2Hit]:
    """Breadth-first flood across the local segment looking for ``target_ip``.

    Returns the shortest cable path to the device owning that address, or
    ``None`` if nothing on this broadcast domain answers for it.
    """
    start_device = topology.device(start_device_id)
    if start_device is None:
        return None

    # queue items: (device_id, arrival_interface_id, devices_so_far, links_so_far)
    queue: deque = deque()
    # Seed with every cable plugged into the starting interface.
    for connection in topology.connections_of_interface(start_device_id, start_interface_id):
        other = connection.other(start_device_id)
        if other is None:
            continue
        queue.append((other.device_id, other.interface_id, [other.device_id], [connection.id]))

    visited = {(start_device_id, start_interface_id)}

    while queue:
        device_id, arrival_iface_id, devices, links = queue.popleft()
        key = (device_id, arrival_iface_id)
        if key in visited:
            continue
        visited.add(key)

        device = topology.device(device_id)
        if device is None:
            continue

        if device.is_switch:
            # Transparent bridge: flood out of every other port.
            for connection in topology.connections_of(device_id):
                if connection.id in links:
                    continue
                other = connection.other(device_id)
                if other is None:
                    continue
                queue.append(
                    (
                        other.device_id,
                        other.interface_id,
                        devices + [other.device_id],
                        links + [connection.id],
                    )
                )
            continue

        # Opaque device: it either owns the address or the search stops here.
        iface = _owns_address(device, arrival_iface_id, target_ip)
        if iface is not None:
            return L2Hit(
                device_id=device_id,
                interface_id=iface.id,
                path_devices=devices,
                path_connections=links,
            )

    return None


def segment_members(
    topology: Topology,
    start_device_id: str,
    start_interface_id: Optional[str],
) -> List[Tuple[str, Optional[str]]]:
    """List every (device_id, interface_id) reachable at layer 2.

    Used by validation and by the "who else is on this LAN?" error messages.
    """
    members: List[Tuple[str, Optional[str]]] = []
    queue: deque = deque()
    for connection in topology.connections_of_interface(start_device_id, start_interface_id):
        other = connection.other(start_device_id)
        if other is not None:
            queue.append((other.device_id, other.interface_id, [connection.id]))

    visited = {(start_device_id, start_interface_id)}
    while queue:
        device_id, iface_id, links = queue.popleft()
        key = (device_id, iface_id)
        if key in visited:
            continue
        visited.add(key)
        device = topology.device(device_id)
        if device is None:
            continue
        if device.is_switch:
            for connection in topology.connections_of(device_id):
                if connection.id in links:
                    continue
                other = connection.other(device_id)
                if other is not None:
                    queue.append((other.device_id, other.interface_id, links + [connection.id]))
            continue
        members.append((device_id, iface_id))
    return members
