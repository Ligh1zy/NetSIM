"""Routing-table construction and longest-prefix matching.

The routing table a device actually forwards with is built from two sources:

1. **Connected routes** - derived automatically from every validly configured
   interface. A router with ``eth0 = 192.168.1.1/24`` always knows how to
   reach ``192.168.1.0/24``; students should not have to type that in.
   Connected routes get metric 0 and win ties.
2. **Static routes** - the entries the user typed into the routing table.

Route selection is classic longest-prefix match, with metric and then table
order used only to break ties between routes of equal prefix length.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import List, Optional, Tuple

from ..domain.models import Device
from ..domain.topology import Topology


CONNECTED = "connected"
STATIC = "static"


@dataclass
class RouteEntry:
    """A normalised, ready-to-match routing table row."""

    network: ipaddress.IPv4Network
    next_hop: Optional[ipaddress.IPv4Address]
    interface_id: Optional[str]
    metric: int
    origin: str
    index: int            # position in the user's static table (-1 for connected)
    error: Optional[str] = None

    @property
    def is_connected(self) -> bool:
        return self.origin == CONNECTED

    def describe(self, device: Device) -> str:
        iface = device.interface(self.interface_id)
        iface_name = iface.name if iface else "?"
        if self.is_connected:
            return "%s via %s (directly connected, metric %d)" % (
                self.network, iface_name, self.metric
            )
        if self.next_hop is not None:
            return "%s via %s out of %s (static, metric %d)" % (
                self.network, self.next_hop, iface_name, self.metric
            )
        return "%s out of %s (static, on-link, metric %d)" % (
            self.network, iface_name, self.metric
        )


def build_routing_table(device: Device) -> Tuple[List[RouteEntry], List[str]]:
    """Return ``(usable_entries, problem_messages)`` for a layer-3 device."""
    entries: List[RouteEntry] = []
    problems: List[str] = []

    for iface, parsed in device.addresses():
        entries.append(
            RouteEntry(
                network=parsed.network,
                next_hop=None,
                interface_id=iface.id,
                metric=0,
                origin=CONNECTED,
                index=-1,
            )
        )

    for index, route in enumerate(device.routes):
        network, error = route.parse()
        if error is not None:
            problems.append("%s route #%d is unusable: %s" % (device.name, index + 1, error))
            continue
        next_hop, hop_error = route.parse_next_hop()
        if hop_error is not None:
            problems.append("%s route #%d is unusable: %s" % (device.name, index + 1, hop_error))
            continue
        try:
            metric = int(route.metric)
        except (TypeError, ValueError):
            metric = 10
        entries.append(
            RouteEntry(
                network=network,
                next_hop=next_hop,
                interface_id=route.interface_id,
                metric=metric,
                origin=STATIC,
                index=index,
            )
        )

    return entries, problems


def matching_routes(
    entries: List[RouteEntry], destination: ipaddress.IPv4Address
) -> List[RouteEntry]:
    """Every route whose network contains ``destination``, best first.

    Ordering: longest prefix, then lowest metric, then connected before
    static, then table order.
    """
    matches = [entry for entry in entries if destination in entry.network]
    matches.sort(
        key=lambda e: (
            -e.network.prefixlen,
            e.metric,
            0 if e.is_connected else 1,
            e.index if e.index >= 0 else -1,
        )
    )
    return matches


def select_route(
    entries: List[RouteEntry], destination: ipaddress.IPv4Address
) -> Optional[RouteEntry]:
    matches = matching_routes(entries, destination)
    return matches[0] if matches else None


def resolve_egress(
    topology: Topology,
    device: Device,
    entry: RouteEntry,
    destination: ipaddress.IPv4Address,
) -> Tuple[Optional[str], Optional[ipaddress.IPv4Address], Optional[str]]:
    """Work out which interface to send on and which IP to send *to*.

    Returns ``(interface_id, next_hop_ip, error_message)``.

    * Connected / on-link routes forward straight to the destination address.
    * Routes with a next hop must have that next hop sitting on one of this
      device's own subnets - otherwise the route is unusable and we say so.
    """
    if entry.next_hop is None:
        interface_id = entry.interface_id
        if interface_id is None:
            return None, None, (
                "route %s has neither a next hop nor an outgoing interface" % entry.network
            )
        iface = device.interface(interface_id)
        if iface is None:
            return None, None, (
                "route %s points at an interface that no longer exists" % entry.network
            )
        parsed, iface_error = iface.parse()
        if parsed is None:
            return None, None, (
                "route %s uses %s, but %s" % (entry.network, iface.name, iface_error)
            )
        return interface_id, destination, None

    # Route with an explicit next hop: find the interface that reaches it.
    candidates = []
    for iface, parsed in device.addresses():
        if entry.next_hop in parsed.network:
            candidates.append(iface)

    if entry.interface_id is not None:
        iface = device.interface(entry.interface_id)
        if iface is None:
            return None, None, (
                "route %s points at an interface that no longer exists" % entry.network
            )
        parsed, iface_error = iface.parse()
        if parsed is None:
            return None, None, (
                "route %s uses %s, but %s" % (entry.network, iface.name, iface_error)
            )
        if entry.next_hop not in parsed.network:
            return None, None, (
                "route %s sends traffic to next hop %s out of %s (%s), but %s is not on that "
                "subnet" % (entry.network, entry.next_hop, iface.name, parsed.with_prefixlen,
                            entry.next_hop)
            )
        return iface.id, entry.next_hop, None

    if not candidates:
        subnets = ", ".join(str(p.network) for _, p in device.addresses()) or "none"
        return None, None, (
            "next hop %s for route %s is not on any subnet configured on %s "
            "(configured subnets: %s)" % (entry.next_hop, entry.network, device.name, subnets)
        )
    return candidates[0].id, entry.next_hop, None
