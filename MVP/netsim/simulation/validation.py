"""Configuration validation.

Validation is separate from simulation on purpose. There are three distinct
kinds of problem in this project and mixing them up makes the tool useless as
a teaching aid:

1. **Configuration errors** - the topology itself is wrong (bad IP, gateway in
   the wrong subnet, route pointing nowhere). Reported here.
2. **Simulation failures** - the configuration is legal but this particular
   packet cannot get through (no route, firewall DENY). Reported by the
   engine, in the simulation log.
3. **Programming errors** - bugs. Those raise exceptions and are caught at the
   application boundary so the GUI shows a dialog instead of dying.

Error messages here are written for students: they state the observed value,
the expected value, and why the two disagree.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

from ..domain.models import Device, DeviceType
from ..domain.topology import Topology
from . import firewall as fw


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


@dataclass
class ValidationIssue:
    severity: Severity
    message: str
    device_id: Optional[str] = None
    code: str = ""

    def __str__(self) -> str:
        tag = "ERROR" if self.severity is Severity.ERROR else "WARNING"
        return "[%s] %s" % (tag, self.message)


@dataclass
class ValidationReport:
    issues: List[ValidationIssue] = field(default_factory=list)

    def add(self, severity, message, device_id=None, code=""):
        self.issues.append(ValidationIssue(severity, message, device_id, code))

    def error(self, message, device_id=None, code=""):
        self.add(Severity.ERROR, message, device_id, code)

    def warning(self, message, device_id=None, code=""):
        self.add(Severity.WARNING, message, device_id, code)

    @property
    def errors(self) -> List[ValidationIssue]:
        return [i for i in self.issues if i.severity is Severity.ERROR]

    @property
    def warnings(self) -> List[ValidationIssue]:
        return [i for i in self.issues if i.severity is Severity.WARNING]

    @property
    def ok(self) -> bool:
        return not self.errors

    def for_device(self, device_id: str) -> List[ValidationIssue]:
        return [i for i in self.issues if i.device_id == device_id]

    def errors_for_device(self, device_id: str) -> List[ValidationIssue]:
        return [i for i in self.for_device(device_id) if i.severity is Severity.ERROR]

    def lines(self) -> List[str]:
        return [str(issue) for issue in self.issues]


def validate_topology(topology: Topology) -> ValidationReport:
    report = ValidationReport()
    _check_duplicate_addresses(topology, report)
    for device in topology.devices.values():
        _check_device(topology, device, report)
    _check_wiring(topology, report)
    return report


# --------------------------------------------------------------------------
# Individual checks
# --------------------------------------------------------------------------

def _check_duplicate_addresses(topology: Topology, report: ValidationReport) -> None:
    seen: Dict[ipaddress.IPv4Address, List[str]] = {}
    for device in topology.devices.values():
        for iface, parsed in device.addresses():
            seen.setdefault(parsed.ip, []).append("%s/%s" % (device.name, iface.name))
    for address, owners in seen.items():
        if len(owners) > 1:
            report.error(
                "IPv4 address %s is configured on more than one interface (%s). "
                "Each address must be unique inside a topology."
                % (address, ", ".join(owners)),
                code="duplicate_address",
            )


def _check_device(topology: Topology, device: Device, report: ValidationReport) -> None:
    if not device.name.strip():
        report.error("A %s has an empty name." % device.type.label, device.id, "empty_name")

    if device.type is DeviceType.SWITCH:
        if device.interfaces:
            report.warning(
                "%s is a switch but has IP interfaces configured. Switches in this MVP are "
                "layer-2 only and their addresses are ignored." % device.name,
                device.id,
                "switch_has_interfaces",
            )
        return

    # Interface addressing
    for iface in device.interfaces:
        parsed, error = iface.parse()
        if error is not None and iface.ip:
            # Only complain about *wrong* values here; "not filled in yet" is
            # handled per device type below.
            report.error("%s: %s" % (device.name, error), device.id, "bad_interface")
        elif parsed is not None:
            if parsed.network.prefixlen < 31:
                if parsed.ip == parsed.network.network_address:
                    report.error(
                        "%s %s is configured as %s, which is the network address of %s. "
                        "Use a usable host address instead."
                        % (device.name, iface.name, parsed.with_prefixlen, parsed.network),
                        device.id,
                        "network_address_used",
                    )
                elif parsed.ip == parsed.network.broadcast_address:
                    report.error(
                        "%s %s is configured as %s, which is the broadcast address of %s. "
                        "Use a usable host address instead."
                        % (device.name, iface.name, parsed.with_prefixlen, parsed.network),
                        device.id,
                        "broadcast_address_used",
                    )

    if device.is_host:
        _check_host(topology, device, report)
    if device.is_l3:
        _check_l3(topology, device, report)
    if device.type is DeviceType.FIREWALL:
        for problem in fw.validate_rules(device):
            report.error(problem, device.id, "bad_firewall_rule")


def _check_host(topology: Topology, device: Device, report: ValidationReport) -> None:
    if len(device.interfaces) != 1:
        report.warning(
            "%s has %d interfaces. In this MVP a PC/Server is modelled with exactly one."
            % (device.name, len(device.interfaces)),
            device.id,
            "host_interface_count",
        )
    iface = device.primary_interface()
    if iface is None:
        report.error(
            "%s has no interface, so it cannot be given an IPv4 address." % device.name,
            device.id,
            "no_interface",
        )
        return
    parsed, error = iface.parse()
    if parsed is None:
        report.error(
            "%s cannot take part in a simulation: %s" % (device.name, error),
            device.id,
            "host_unaddressed",
        )
        return

    gateway, gw_error = device.parse_gateway()
    if gw_error is not None:
        report.error("%s: %s" % (device.name, gw_error), device.id, "bad_gateway")
        return
    if gateway is None:
        report.warning(
            "%s has no default gateway. It will only be able to reach hosts on its own "
            "%s network." % (device.name, parsed.network),
            device.id,
            "no_gateway",
        )
        return
    if gateway not in parsed.network:
        report.error(
            "%s has gateway %s, but %s is configured as %s. The gateway is outside the local "
            "%s subnet, so %s can never send anything to it."
            % (device.name, gateway, device.name, parsed.with_prefixlen,
               parsed.network, device.name),
            device.id,
            "gateway_outside_subnet",
        )
        return
    if gateway == parsed.ip:
        report.error(
            "%s uses its own address %s as its default gateway." % (device.name, gateway),
            device.id,
            "gateway_is_self",
        )


def _check_l3(topology: Topology, device: Device, report: ValidationReport) -> None:
    addressed = list(device.addresses())
    if not addressed:
        report.error(
            "%s has no usable IPv4 interface, so it cannot forward anything." % device.name,
            device.id,
            "l3_unaddressed",
        )
    elif len(addressed) < 2:
        report.warning(
            "%s only has one addressed interface (%s). A %s normally joins at least two "
            "networks." % (device.name, addressed[0][1].with_prefixlen, device.type.label),
            device.id,
            "l3_single_interface",
        )

    own_networks = [parsed.network for _, parsed in addressed]

    for position, route in enumerate(device.routes, start=1):
        network, error = route.parse()
        if error is not None:
            report.error(
                "%s route #%d: %s" % (device.name, position, error), device.id, "bad_route"
            )
            continue
        next_hop, hop_error = route.parse_next_hop()
        if hop_error is not None:
            report.error(
                "%s route #%d: %s" % (device.name, position, hop_error), device.id, "bad_route"
            )
            continue
        if route.interface_id is not None and device.interface(route.interface_id) is None:
            report.error(
                "%s route #%d (%s) names an outgoing interface that does not exist on %s."
                % (device.name, position, network, device.name),
                device.id,
                "route_bad_interface",
            )
            continue
        if next_hop is None and route.interface_id is None:
            report.error(
                "%s route #%d (%s) has neither a next hop nor an outgoing interface, so the "
                "router would not know where to send the packet."
                % (device.name, position, network),
                device.id,
                "route_no_target",
            )
            continue
        if next_hop is not None and own_networks:
            if not any(next_hop in net for net in own_networks):
                report.error(
                    "%s route #%d sends %s to next hop %s, but %s is not on any network that "
                    "%s is connected to (%s). A next hop must be a neighbour on a directly "
                    "connected subnet."
                    % (device.name, position, network, next_hop, next_hop, device.name,
                       ", ".join(str(n) for n in own_networks)),
                    device.id,
                    "route_unreachable_next_hop",
                )


def _check_wiring(topology: Topology, report: ValidationReport) -> None:
    for device_id in topology.isolated_devices():
        device = topology.device(device_id)
        if device is None:
            continue
        report.warning(
            "%s is not connected to anything." % device.name, device_id, "isolated"
        )

    # An addressed interface with no cable cannot forward traffic.
    for device in topology.devices.values():
        if device.is_switch:
            continue
        for iface, parsed in device.addresses():
            if not topology.connections_of_interface(device.id, iface.id):
                report.warning(
                    "%s %s is configured as %s but has no cable attached."
                    % (device.name, iface.name, parsed.with_prefixlen),
                    device.id,
                    "interface_unplugged",
                )

    # Two devices on the same cable should agree on the subnet.
    for connection in topology.connections.values():
        a_device = topology.device(connection.a.device_id)
        b_device = topology.device(connection.b.device_id)
        if a_device is None or b_device is None:
            report.error(
                "Connection %s references a device that no longer exists." % connection.id,
                code="dangling_connection",
            )
            continue
        if a_device.is_switch or b_device.is_switch:
            continue
        a_iface = a_device.interface(connection.a.interface_id) or a_device.primary_interface()
        b_iface = b_device.interface(connection.b.interface_id) or b_device.primary_interface()
        if a_iface is None or b_iface is None:
            continue
        a_parsed, _ = a_iface.parse()
        b_parsed, _ = b_iface.parse()
        if a_parsed is None or b_parsed is None:
            continue
        if a_parsed.network != b_parsed.network:
            report.warning(
                "%s %s (%s) is cabled directly to %s %s (%s), but the two interfaces are on "
                "different subnets. Traffic cannot cross this link."
                % (a_device.name, a_iface.name, a_parsed.with_prefixlen,
                   b_device.name, b_iface.name, b_parsed.with_prefixlen),
                a_device.id,
                "subnet_mismatch_on_link",
            )
