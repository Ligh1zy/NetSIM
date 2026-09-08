"""Core domain data model for NetSim.

This module is deliberately free of any GUI dependency. Everything here is a
plain dataclass or enum so that it is trivially serialisable to JSON and easy
to construct inside unit tests.

Relationships (the authoritative model):

    Topology 1 --- * Device
    Device   1 --- * Interface        (routers/firewalls have several)
    Device   1 --- * Route            (routers/firewalls only)
    Device   1 --- * FirewallRule     (firewalls only)
    Topology 1 --- * Connection       (a physical cable between two endpoints)
    Connection     --- 2 Endpoint     (device_id + optional interface_id)

A "Network" is not an explicit object. Networks are derived from interface
addresses via the ipaddress module; keeping them implicit removes a whole
class of "two sources of truth" bugs.
"""

from __future__ import annotations

import ipaddress
import itertools
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Tuple


# --------------------------------------------------------------------------
# Enums
# --------------------------------------------------------------------------

class DeviceType(str, Enum):
    PC = "pc"
    SERVER = "server"
    SWITCH = "switch"
    ROUTER = "router"
    FIREWALL = "firewall"

    @property
    def label(self) -> str:
        return {
            DeviceType.PC: "PC",
            DeviceType.SERVER: "Server",
            DeviceType.SWITCH: "Switch",
            DeviceType.ROUTER: "Router",
            DeviceType.FIREWALL: "Firewall",
        }[self]


class Protocol(str, Enum):
    ANY = "any"
    TCP = "tcp"
    UDP = "udp"
    ICMP = "icmp"


class Action(str, Enum):
    ALLOW = "allow"
    DENY = "deny"


#: Device types that terminate traffic (can be a simulation source/destination).
HOST_TYPES = (DeviceType.PC, DeviceType.SERVER)
#: Device types that make layer-3 forwarding decisions.
L3_TYPES = (DeviceType.ROUTER, DeviceType.FIREWALL)


# --------------------------------------------------------------------------
# Interface
# --------------------------------------------------------------------------

@dataclass
class Interface:
    """A single layer-3 interface on a device.

    ip and prefix are stored as raw user input (str / int) so that an invalid
    configuration can be persisted and reported rather than crashing the
    editor. Use parse() to obtain a validated object.
    """

    id: str
    name: str
    ip: Optional[str] = None
    prefix: Optional[int] = None

    def parse(self) -> Tuple[Optional[ipaddress.IPv4Interface], Optional[str]]:
        """Return (IPv4Interface, None) or (None, error_message)."""
        if self.ip is None or str(self.ip).strip() == "":
            return None, "interface '%s' has no IPv4 address configured" % self.name
        if self.prefix is None:
            return None, "interface '%s' has no subnet prefix configured" % self.name
        try:
            prefix = int(self.prefix)
        except (TypeError, ValueError):
            return None, "interface '%s' has a non-numeric prefix '%s'" % (self.name, self.prefix)
        if not 0 <= prefix <= 32:
            return None, (
                "interface '%s' has prefix /%s; an IPv4 prefix must be between 0 and 32"
                % (self.name, prefix)
            )
        try:
            parsed = ipaddress.IPv4Interface("%s/%d" % (str(self.ip).strip(), prefix))
        except ValueError as exc:
            return None, (
                "interface '%s' has an invalid IPv4 address '%s' (%s)"
                % (self.name, self.ip, exc)
            )
        return parsed, None

    @property
    def address(self) -> Optional[ipaddress.IPv4Address]:
        parsed, _ = self.parse()
        return parsed.ip if parsed else None

    @property
    def network(self) -> Optional[ipaddress.IPv4Network]:
        parsed, _ = self.parse()
        return parsed.network if parsed else None

    @property
    def cidr(self) -> str:
        if self.ip and self.prefix is not None:
            return "%s/%s" % (self.ip, self.prefix)
        return "(unconfigured)"


# --------------------------------------------------------------------------
# Route
# --------------------------------------------------------------------------

@dataclass
class Route:
    """A static routing-table entry.

    next_hop is None for a route that is treated as directly attached (the
    destination is assumed to live on interface_id's segment).
    """

    destination: str = "0.0.0.0"
    prefix: int = 0
    next_hop: Optional[str] = None
    interface_id: Optional[str] = None
    metric: int = 10

    def parse(self) -> Tuple[Optional[ipaddress.IPv4Network], Optional[str]]:
        try:
            prefix = int(self.prefix)
        except (TypeError, ValueError):
            return None, "route prefix '%s' is not a number" % self.prefix
        if not 0 <= prefix <= 32:
            return None, "route prefix /%d is out of range (0-32)" % prefix
        try:
            net = ipaddress.IPv4Network(
                "%s/%d" % (str(self.destination).strip(), prefix), strict=True
            )
        except ValueError as exc:
            return None, (
                "route destination '%s/%s' is not a valid IPv4 network (%s)"
                % (self.destination, prefix, exc)
            )
        return net, None

    def parse_next_hop(self) -> Tuple[Optional[ipaddress.IPv4Address], Optional[str]]:
        if self.next_hop is None or str(self.next_hop).strip() == "":
            return None, None
        try:
            return ipaddress.IPv4Address(str(self.next_hop).strip()), None
        except ValueError as exc:
            return None, (
                "route next hop '%s' is not a valid IPv4 address (%s)" % (self.next_hop, exc)
            )

    @property
    def cidr(self) -> str:
        return "%s/%s" % (self.destination, self.prefix)


# --------------------------------------------------------------------------
# Firewall rule
# --------------------------------------------------------------------------

@dataclass
class FirewallRule:
    """One ordered firewall rule.

    None in src / dst / dst_port means "match anything". src and dst accept
    either a bare address (10.0.3.10) or CIDR notation (10.0.3.0/24).
    """

    id: str
    action: Action = Action.DENY
    protocol: Protocol = Protocol.ANY
    src: Optional[str] = None
    dst: Optional[str] = None
    dst_port: Optional[int] = None
    enabled: bool = True
    description: str = ""

    def parse_src(self):
        return _parse_optional_network(self.src, "source")

    def parse_dst(self):
        return _parse_optional_network(self.dst, "destination")

    def parse_port(self) -> Tuple[Optional[int], Optional[str]]:
        if self.dst_port is None or str(self.dst_port).strip() == "":
            return None, None
        try:
            port = int(self.dst_port)
        except (TypeError, ValueError):
            return None, "destination port '%s' is not a number" % self.dst_port
        if not 1 <= port <= 65535:
            return None, "destination port %d is out of range (1-65535)" % port
        return port, None

    def summary(self) -> str:
        """Human readable one-liner used in simulation logs."""
        parts = [self.action.value.upper(), self.protocol.value.upper()]
        parts.append("src %s" % self.src if self.src else "src any")
        parts.append("dst %s" % self.dst if self.dst else "dst any")
        parts.append("dport %s" % self.dst_port if self.dst_port is not None else "dport any")
        text = " ".join(parts)
        if not self.enabled:
            text += " [disabled]"
        return text


def _parse_optional_network(value, what: str):
    """Parse an optional address-or-CIDR string into an IPv4Network."""
    if value is None or str(value).strip() == "":
        return None, None
    text = str(value).strip()
    try:
        if "/" in text:
            return ipaddress.IPv4Network(text, strict=False), None
        return ipaddress.IPv4Network(text + "/32", strict=False), None
    except ValueError as exc:
        return None, (
            "%s '%s' is not a valid IPv4 address or network (%s)" % (what, value, exc)
        )


# --------------------------------------------------------------------------
# Device
# --------------------------------------------------------------------------

@dataclass
class Device:
    id: str
    type: DeviceType
    name: str
    x: float = 0.0
    y: float = 0.0
    interfaces: List[Interface] = field(default_factory=list)
    gateway: Optional[str] = None                       # hosts only
    routes: List[Route] = field(default_factory=list)   # routers / firewalls
    firewall_rules: List[FirewallRule] = field(default_factory=list)
    default_policy: Action = Action.DENY                # firewalls only

    @property
    def is_host(self) -> bool:
        return self.type in HOST_TYPES

    @property
    def is_l3(self) -> bool:
        return self.type in L3_TYPES

    @property
    def is_switch(self) -> bool:
        return self.type is DeviceType.SWITCH

    def interface(self, interface_id: Optional[str]) -> Optional[Interface]:
        if interface_id is None:
            return None
        for iface in self.interfaces:
            if iface.id == interface_id:
                return iface
        return None

    def primary_interface(self) -> Optional[Interface]:
        """First interface; hosts are modelled with exactly one."""
        return self.interfaces[0] if self.interfaces else None

    def parse_gateway(self) -> Tuple[Optional[ipaddress.IPv4Address], Optional[str]]:
        if self.gateway is None or str(self.gateway).strip() == "":
            return None, None
        try:
            return ipaddress.IPv4Address(str(self.gateway).strip()), None
        except ValueError as exc:
            return None, (
                "default gateway '%s' is not a valid IPv4 address (%s)" % (self.gateway, exc)
            )

    def addresses(self):
        """Yield (interface, IPv4Interface) for every validly configured interface."""
        for iface in self.interfaces:
            parsed, _ = iface.parse()
            if parsed is not None:
                yield iface, parsed


# --------------------------------------------------------------------------
# Connection
# --------------------------------------------------------------------------

@dataclass
class Endpoint:
    device_id: str
    interface_id: Optional[str] = None   # None for switch ports


@dataclass
class Connection:
    id: str
    a: Endpoint
    b: Endpoint

    def endpoints(self) -> Tuple[Endpoint, Endpoint]:
        return self.a, self.b

    def other(self, device_id: str) -> Optional[Endpoint]:
        if self.a.device_id == device_id:
            return self.b
        if self.b.device_id == device_id:
            return self.a
        return None

    def endpoint_for(self, device_id: str) -> Optional[Endpoint]:
        if self.a.device_id == device_id:
            return self.a
        if self.b.device_id == device_id:
            return self.b
        return None

    def touches(self, device_id: str) -> bool:
        return device_id in (self.a.device_id, self.b.device_id)


# --------------------------------------------------------------------------
# Stable ID generation
# --------------------------------------------------------------------------

_counter = itertools.count(1)


def new_id(prefix: str) -> str:
    """Generate a short, stable, JSON-friendly identifier.

    IDs only have to be unique inside one topology document, so a monotonic
    counter combined with the object kind is enough and keeps saved files
    readable (router-3 rather than a UUID).
    """
    return "%s-%d" % (prefix, next(_counter))


def reseed_ids(existing_ids) -> None:
    """Make sure freshly generated IDs cannot collide with loaded ones."""
    global _counter
    highest = 0
    for ident in existing_ids:
        tail = str(ident).rpartition("-")[2]
        if tail.isdigit():
            highest = max(highest, int(tail))
    _counter = itertools.count(highest + 1)
