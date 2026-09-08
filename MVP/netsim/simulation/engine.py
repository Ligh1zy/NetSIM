"""The NetSim simulation engine.

This module contains **no GUI code and imports no GUI library**. It takes a
:class:`SimulationRequest` (data) and returns a :class:`SimulationResult`
(data). Everything it decides is recorded in a human-readable log as it goes,
so the log is a by-product of the real decisions rather than a script.

The algorithm, in words:

1. Validate the topology; refuse to start if the source or destination itself
   is misconfigured.
2. Resolve the source and destination IPv4 configuration.
3. Ask the classic end-host question: *is the destination on my own subnet?*
   - Yes -> deliver at layer 2 across switches only.
   - No  -> the host needs a default gateway, and that gateway must live on
     the local subnet.
4. Hand the packet to the first layer-3 device and loop:
   firewall check (if any) -> routing-table lookup (longest prefix) ->
   pick egress interface and next hop -> resolve that next hop at layer 2 ->
   move to the next device.
5. Stop on delivery, on a failure, or when a loop / hop limit is hit.

Crucially, physical connectivity is never sufficient on its own: every step
above must succeed for the packet to advance.
"""

from __future__ import annotations

import ipaddress
from typing import List, Optional, Tuple

from ..domain.models import Device, DeviceType, Protocol
from ..domain.topology import Topology
from . import firewall as fw
from . import l2, routing
from .result import (
    FailureReason,
    Hop,
    LogEntry,
    LogLevel,
    SimulationRequest,
    SimulationResult,
)
from .validation import Severity, validate_topology

#: Safety net in addition to explicit loop detection.
MAX_HOPS = 32

#: Validation codes that specifically describe a broken default gateway.
_GATEWAY_CODES = {"bad_gateway", "gateway_outside_subnet", "gateway_is_self"}


class _Run:
    """Mutable state for a single simulation run."""

    def __init__(self, request: SimulationRequest):
        self.request = request
        self.topology: Topology = request.topology
        self.result = SimulationResult(
            source_device_id=request.source_device_id,
            destination_device_id=request.destination_device_id,
            protocol=request.protocol.value,
            destination_port=request.destination_port,
        )

    # -- logging helpers ---------------------------------------------------
    def log(self, level: LogLevel, message: str) -> None:
        self.result.log.append(LogEntry(level, message))

    def info(self, message: str) -> None:
        self.log(LogLevel.INFO, message)

    def decision(self, message: str) -> None:
        self.log(LogLevel.DECISION, message)

    def warn(self, message: str) -> None:
        self.log(LogLevel.WARNING, message)

    # -- termination helpers ----------------------------------------------
    def fail(
        self,
        reason: FailureReason,
        message: str,
        device_id: Optional[str] = None,
    ) -> SimulationResult:
        self.result.success = False
        self.result.failure_reason = reason
        self.result.failure_message = message
        self.result.failure_device_id = device_id
        self.log(LogLevel.ERROR, message)
        self.log(LogLevel.ERROR, "Simulation failed.")
        return self.result

    def succeed(self, message: str) -> SimulationResult:
        self.result.success = True
        self.result.failure_reason = FailureReason.NONE
        self.log(LogLevel.SUCCESS, message)
        self.log(LogLevel.SUCCESS, "Simulation succeeded.")
        return self.result


def simulate(request: SimulationRequest) -> SimulationResult:
    """Run one simulation and return a structured result."""
    run = _Run(request)
    topology = run.topology
    result = run.result

    # ---------------------------------------------------------------- setup
    source = topology.device(request.source_device_id)
    destination = topology.device(request.destination_device_id)

    if source is None:
        return run.fail(FailureReason.NO_SOURCE, "No source device was selected.")
    if destination is None:
        return run.fail(FailureReason.NO_DESTINATION, "No destination device was selected.")

    result.source_name = source.name
    result.destination_name = destination.name

    if source.id == destination.id:
        return run.fail(
            FailureReason.SAME_DEVICE,
            "Source and destination are the same device (%s)." % source.name,
            source.id,
        )
    for role, device in (("Source", source), ("Destination", destination)):
        if not device.is_host:
            return run.fail(
                FailureReason.UNSUPPORTED_ENDPOINT,
                "%s %s is a %s. Only a PC or a Server can be the endpoint of a simulation."
                % (role, device.name, device.type.label),
                device.id,
            )

    protocol, port, port_note = _normalise_protocol(request)
    result.protocol = protocol.value
    result.destination_port = port
    if port_note:
        run.warn(port_note)

    # ----------------------------------------------------------- validation
    report = validate_topology(topology)
    result.validation_messages = report.lines()
    for issue in report.issues:
        if issue.severity is Severity.ERROR:
            run.warn("Configuration problem: %s" % issue.message)

    for device in (source, destination):
        errors = report.errors_for_device(device.id)
        if errors:
            reason = (
                FailureReason.INVALID_GATEWAY
                if any(e.code in _GATEWAY_CODES for e in errors)
                else FailureReason.CONFIGURATION_ERROR
            )
            return run.fail(reason, errors[0].message, device.id)

    # --------------------------------------------------- address resolution
    src_iface = source.primary_interface()
    dst_iface = destination.primary_interface()
    src_parsed, src_error = (src_iface.parse() if src_iface else (None, "no interface"))
    dst_parsed, dst_error = (dst_iface.parse() if dst_iface else (None, "no interface"))
    if src_parsed is None:
        return run.fail(
            FailureReason.CONFIGURATION_ERROR,
            "%s cannot send traffic: %s" % (source.name, src_error),
            source.id,
        )
    if dst_parsed is None:
        return run.fail(
            FailureReason.CONFIGURATION_ERROR,
            "%s cannot receive traffic: %s" % (destination.name, dst_error),
            destination.id,
        )

    result.source_ip = src_parsed.with_prefixlen
    result.destination_ip = dst_parsed.with_prefixlen
    src_ip = src_parsed.ip
    dst_ip = dst_parsed.ip

    packet = fw.Packet(src_ip=src_ip, dst_ip=dst_ip, protocol=protocol, dst_port=port)

    run.info("Simulation started: %s -> %s" % (source.name, destination.name))
    run.info("Source IP: %s" % src_parsed.with_prefixlen)
    run.info("Destination IP: %s" % dst_parsed.with_prefixlen)
    run.info("Traffic: %s" % packet.describe())

    result.path_devices.append(source.id)

    if not topology.physically_connected(source.id, destination.id):
        return run.fail(
            FailureReason.NOT_PHYSICALLY_CONNECTED,
            "%s and %s are not connected by any cable path, so no amount of IP configuration "
            "can help." % (source.name, destination.name),
            source.id,
        )

    # ------------------------------------------------ end-host local check
    local = dst_ip in src_parsed.network
    if local:
        run.decision(
            "%s compares %s with its own network %s: the destination is on the local subnet, "
            "so no router is required."
            % (source.name, dst_ip, src_parsed.network)
        )
        result.hops.append(
            Hop(
                source.id, source.name, source.type.label,
                egress_interface=src_iface.name if src_iface else None,
                decision="destination is on the local subnet; delivering directly",
            )
        )
        return _deliver_locally(run, source, src_iface, destination, dst_ip)

    run.decision(
        "%s compares %s with its own network %s: the networks are different, so %s must send "
        "the packet to its default gateway."
        % (source.name, dst_ip, src_parsed.network, source.name)
    )

    gateway, gw_error = source.parse_gateway()
    if gw_error is not None:
        return run.fail(FailureReason.INVALID_GATEWAY, "%s: %s" % (source.name, gw_error), source.id)
    if gateway is None:
        return run.fail(
            FailureReason.NO_GATEWAY,
            "%s has no default gateway configured, so it cannot reach anything outside its own "
            "%s network." % (source.name, src_parsed.network),
            source.id,
        )
    if gateway not in src_parsed.network:
        return run.fail(
            FailureReason.INVALID_GATEWAY,
            "%s has gateway %s, but %s is configured as %s. The gateway is outside the local "
            "%s subnet and can never be reached."
            % (source.name, gateway, source.name, src_parsed.with_prefixlen, src_parsed.network),
            source.id,
        )

    run.info("Default gateway: %s" % gateway)
    run.decision(
        "Gateway %s is inside the local %s network, so it is a valid next hop."
        % (gateway, src_parsed.network)
    )

    result.hops.append(
        Hop(
            source.id, source.name, source.type.label,
            egress_interface=src_iface.name if src_iface else None,
            decision="destination is remote; forwarding to default gateway %s" % gateway,
        )
    )

    hit = l2.resolve_on_segment(topology, source.id, src_iface.id if src_iface else None, gateway)
    if hit is None:
        return run.fail(
            FailureReason.GATEWAY_UNREACHABLE,
            "%s cannot reach its gateway %s. No device carrying that address is on the same "
            "layer-2 segment (remember that a router or firewall stops a segment)."
            % (source.name, gateway),
            source.id,
        )

    _append_l2_path(run, hit)
    gateway_device = topology.device(hit.device_id)
    if gateway_device is None:  # pragma: no cover - defensive
        return run.fail(FailureReason.GATEWAY_UNREACHABLE, "Gateway device disappeared.", source.id)
    if not gateway_device.is_l3:
        return run.fail(
            FailureReason.INVALID_GATEWAY,
            "Gateway %s belongs to %s, which is a %s. A default gateway must be a router or a "
            "firewall." % (gateway, gateway_device.name, gateway_device.type.label),
            gateway_device.id,
        )

    _log_l2_walk(run, source.name, hit, gateway)
    return _forward(run, gateway_device, hit.interface_id, packet, destination)


# --------------------------------------------------------------------------
# Steps
# --------------------------------------------------------------------------

def _normalise_protocol(request: SimulationRequest):
    """Return (protocol, port, note). Ports only exist for TCP and UDP."""
    protocol = request.protocol if isinstance(request.protocol, Protocol) else Protocol(
        str(request.protocol)
    )
    raw_port = request.destination_port
    note = None
    port: Optional[int] = None
    if raw_port not in (None, ""):
        try:
            port = int(raw_port)
        except (TypeError, ValueError):
            note = "Destination port '%s' is not a number and was ignored." % raw_port
            port = None
        else:
            if not 1 <= port <= 65535:
                note = "Destination port %d is out of range (1-65535) and was ignored." % port
                port = None
    if port is not None and protocol not in (Protocol.TCP, Protocol.UDP):
        note = (
            "Destination port %d was ignored because %s traffic has no port number."
            % (port, protocol.value.upper())
        )
        port = None
    return protocol, port, note


def _append_l2_path(run: _Run, hit: "l2.L2Hit") -> None:
    run.result.path_devices.extend(hit.path_devices)
    run.result.path_connections.extend(hit.path_connections)


def _log_l2_walk(run: _Run, from_name: str, hit: "l2.L2Hit", target_ip) -> None:
    """Explain the layer-2 walk, naming any switches that were crossed."""
    switches = []
    for device_id in hit.path_devices[:-1]:
        device = run.topology.device(device_id)
        if device is not None and device.is_switch:
            switches.append(device.name)
    target_device = run.topology.device(hit.device_id)
    target_name = target_device.name if target_device else hit.device_id
    if switches:
        run.info(
            "Layer 2: %s -> %s -> %s. Switches forward inside the segment without touching IP."
            % (from_name, " -> ".join(switches), target_name)
        )
    else:
        run.info("Layer 2: %s is cabled directly to %s." % (from_name, target_name))
    run.info("%s owns %s and receives the packet." % (target_name, target_ip))


def _deliver_locally(
    run: _Run,
    source: Device,
    src_iface,
    destination: Device,
    dst_ip: ipaddress.IPv4Address,
) -> SimulationResult:
    topology = run.topology
    hit = l2.resolve_on_segment(topology, source.id, src_iface.id if src_iface else None, dst_ip)
    if hit is None:
        return run.fail(
            FailureReason.LOCAL_DELIVERY_FAILED,
            "%s believes %s is on its local subnet, but nothing answering for that address is "
            "reachable at layer 2. The two devices are either not on the same segment, or a "
            "router/firewall sits between them (a router does not bridge two halves of the same "
            "subnet)." % (source.name, dst_ip),
            source.id,
        )

    _append_l2_path(run, hit)
    _log_l2_walk(run, source.name, hit, dst_ip)

    if hit.device_id != destination.id:
        wrong = topology.device(hit.device_id)
        return run.fail(
            FailureReason.DELIVERED_TO_WRONG_HOST,
            "Address %s is answered by %s, not by %s. Check for duplicate addresses."
            % (dst_ip, wrong.name if wrong else hit.device_id, destination.name),
            hit.device_id,
        )

    run.result.hops.append(
        Hop(
            destination.id, destination.name, destination.type.label,
            ingress_interface=_iface_name(destination, hit.interface_id),
            decision="packet delivered on the local subnet",
        )
    )
    return run.succeed("%s received the packet from %s." % (destination.name, source.name))


def _iface_name(device: Device, interface_id: Optional[str]) -> Optional[str]:
    iface = device.interface(interface_id)
    return iface.name if iface else None


def _forward(
    run: _Run,
    device: Device,
    ingress_interface_id: Optional[str],
    packet: "fw.Packet",
    destination: Device,
) -> SimulationResult:
    """Walk the packet through layer-3 devices until it is delivered or dropped."""
    topology = run.topology
    result = run.result
    visited = set()
    chain: List[str] = []
    hop_count = 0

    current: Optional[Tuple[Device, Optional[str]]] = (device, ingress_interface_id)

    while current is not None:
        device, ingress_interface_id = current
        hop_count += 1
        if hop_count > MAX_HOPS:
            return run.fail(
                FailureReason.HOP_LIMIT_EXCEEDED,
                "The packet was still being forwarded after %d hops; giving up. This usually "
                "means the routing configuration sends traffic in circles." % MAX_HOPS,
                device.id,
            )

        state = (device.id, ingress_interface_id)
        if state in visited:
            loop = " -> ".join(chain + [device.name])
            return run.fail(
                FailureReason.ROUTING_LOOP,
                "Routing loop detected: %s. %s has already forwarded this packet on the same "
                "interface, so the routing tables point at each other." % (loop, device.name),
                device.id,
            )
        visited.add(state)
        chain.append(device.name)

        ingress_name = _iface_name(device, ingress_interface_id)
        run.info(
            "%s received the packet on %s." % (device.name, ingress_name or "an interface")
        )

        hop = Hop(
            device.id, device.name, device.type.label,
            ingress_interface=ingress_name,
        )
        result.hops.append(hop)

        # -- firewall ------------------------------------------------------
        if device.type is DeviceType.FIREWALL:
            decision = fw.evaluate(device, packet)
            for warning in decision.warnings:
                run.warn(warning)
            for evaluation in decision.evaluations:
                run.info(
                    "%s evaluated rule #%d (%s): %s"
                    % (device.name, evaluation.index, evaluation.rule.summary(), evaluation.note)
                )
            if decision.used_default_policy:
                run.decision(
                    "%s found no matching rule and applied its default policy: %s."
                    % (device.name, device.default_policy.value.upper())
                )
            else:
                run.decision(
                    "%s: rule #%d is the first match, so its action %s is applied "
                    "(later rules are not evaluated)."
                    % (device.name, decision.matched_index, decision.action.value.upper())
                )
            if not decision.allowed:
                result.blocked_by_device_id = device.id
                result.blocked_by_rule_index = decision.matched_index
                result.blocked_by_rule_summary = (
                    decision.matched_rule.summary()
                    if decision.matched_rule
                    else "default policy %s" % device.default_policy.value.upper()
                )
                hop.decision = "blocked by firewall policy"
                if decision.matched_rule is not None:
                    message = (
                        "%s blocked the packet. Rule #%d (%s) matched %s."
                        % (device.name, decision.matched_index,
                           decision.matched_rule.summary(), packet.describe())
                    )
                else:
                    message = (
                        "%s blocked the packet: no rule matched %s and the default policy is "
                        "DENY." % (device.name, packet.describe())
                    )
                return run.fail(FailureReason.FIREWALL_BLOCKED, message, device.id)
            hop.decision = "allowed by firewall policy; "
        else:
            hop.decision = ""

        # -- routing -------------------------------------------------------
        entries, problems = routing.build_routing_table(device)
        for problem in problems:
            run.warn(problem)

        run.info(
            "%s performs a routing-table lookup for %s." % (device.name, packet.dst_ip)
        )
        matches = routing.matching_routes(entries, packet.dst_ip)
        if not matches:
            known = ", ".join(str(e.network) for e in entries) or "none"
            hop.decision += "no matching route"
            return run.fail(
                FailureReason.NO_ROUTE,
                "%s has no route to %s. Its routing table only covers: %s."
                % (device.name, packet.dst_ip, known),
                device.id,
            )

        for entry in matches:
            run.info("  candidate route: %s" % entry.describe(device))
        best = matches[0]
        if len(matches) > 1:
            run.decision(
                "%s selected %s because /%d is the longest prefix that matches %s."
                % (device.name, best.network, best.network.prefixlen, packet.dst_ip)
            )
        else:
            run.decision(
                "%s selected the only matching route %s." % (device.name, best.network)
            )

        egress_id, next_hop_ip, error = routing.resolve_egress(
            topology, device, best, packet.dst_ip
        )
        if error is not None:
            # The route matched, but it cannot actually be used: its next hop
            # or outgoing interface does not make sense on this device.
            hop.decision += "route unusable"
            return run.fail(
                FailureReason.NEXT_HOP_UNREACHABLE,
                "%s: %s" % (device.name, error),
                device.id,
            )

        egress_name = _iface_name(device, egress_id)
        hop.egress_interface = egress_name
        if best.is_connected or best.next_hop is None:
            hop.decision += "destination network is directly attached to %s" % egress_name
            run.info(
                "%s: %s is directly connected to %s, so the packet is delivered on that segment."
                % (device.name, best.network, egress_name)
            )
        else:
            hop.decision += "forwarding to next hop %s via %s" % (next_hop_ip, egress_name)
            run.info(
                "%s forwards the packet to next hop %s out of %s."
                % (device.name, next_hop_ip, egress_name)
            )

        # -- layer 2 to the next device -----------------------------------
        hit = l2.resolve_on_segment(topology, device.id, egress_id, next_hop_ip)
        if hit is None:
            return run.fail(
                FailureReason.NEXT_HOP_UNREACHABLE,
                "%s cannot reach %s on %s. No device on that segment is configured with that "
                "address (check the cabling and the addresses of the neighbouring device)."
                % (device.name, next_hop_ip, egress_name),
                device.id,
            )

        _append_l2_path(run, hit)
        _log_l2_walk(run, device.name, hit, next_hop_ip)

        next_device = topology.device(hit.device_id)
        if next_device is None:  # pragma: no cover - defensive
            return run.fail(
                FailureReason.NEXT_HOP_UNREACHABLE, "Next hop device disappeared.", device.id
            )

        if next_device.is_host:
            if next_device.id == destination.id and next_hop_ip == packet.dst_ip:
                result.hops.append(
                    Hop(
                        next_device.id, next_device.name, next_device.type.label,
                        ingress_interface=_iface_name(next_device, hit.interface_id),
                        decision="packet delivered",
                    )
                )
                return run.succeed(
                    "%s received the packet." % next_device.name
                )
            return run.fail(
                FailureReason.DELIVERED_TO_WRONG_HOST,
                "The packet arrived at %s (%s) instead of %s. Check for duplicate or wrong "
                "addresses." % (next_device.name, next_hop_ip, destination.name),
                next_device.id,
            )

        current = (next_device, hit.interface_id)

    return run.fail(  # pragma: no cover - the loop only exits via return
        FailureReason.CONFIGURATION_ERROR, "Simulation ended unexpectedly."
    )


# --------------------------------------------------------------------------
# Convenience wrapper used by tests and the CLI demo
# --------------------------------------------------------------------------

def run_simulation(
    topology: Topology,
    source_device_id: Optional[str],
    destination_device_id: Optional[str],
    protocol: Protocol = Protocol.ICMP,
    destination_port: Optional[int] = None,
) -> SimulationResult:
    return simulate(
        SimulationRequest(
            topology=topology,
            source_device_id=source_device_id,
            destination_device_id=destination_device_id,
            protocol=protocol,
            destination_port=destination_port,
        )
    )
