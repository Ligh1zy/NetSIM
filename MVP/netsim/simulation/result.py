"""Structured input/output types for the simulation engine.

These types are the contract between the engine and everything else (GUI,
tests, SQLite history). They contain no behaviour beyond formatting.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from ..domain.models import Protocol
from ..domain.topology import Topology


class FailureReason(str, Enum):
    """Machine-readable cause of a failed simulation."""

    NONE = "none"
    NO_SOURCE = "no_source"
    NO_DESTINATION = "no_destination"
    SAME_DEVICE = "same_device"
    UNSUPPORTED_ENDPOINT = "unsupported_endpoint"
    CONFIGURATION_ERROR = "configuration_error"
    NOT_PHYSICALLY_CONNECTED = "not_physically_connected"
    NO_GATEWAY = "no_gateway"
    INVALID_GATEWAY = "invalid_gateway"
    GATEWAY_UNREACHABLE = "gateway_unreachable"
    LOCAL_DELIVERY_FAILED = "local_delivery_failed"
    NO_ROUTE = "no_route"
    NEXT_HOP_UNREACHABLE = "next_hop_unreachable"
    FIREWALL_BLOCKED = "firewall_blocked"
    ROUTING_LOOP = "routing_loop"
    HOP_LIMIT_EXCEEDED = "hop_limit_exceeded"
    DELIVERED_TO_WRONG_HOST = "delivered_to_wrong_host"


class LogLevel(str, Enum):
    INFO = "info"
    DECISION = "decision"
    WARNING = "warning"
    ERROR = "error"
    SUCCESS = "success"


@dataclass
class LogEntry:
    level: LogLevel
    message: str

    def __str__(self) -> str:
        prefix = {
            LogLevel.INFO: "",
            LogLevel.DECISION: "-> ",
            LogLevel.WARNING: "! ",
            LogLevel.ERROR: "X ",
            LogLevel.SUCCESS: "OK ",
        }[self.level]
        return prefix + self.message


@dataclass
class Hop:
    """One device the packet was processed by, in order."""

    device_id: str
    device_name: str
    device_type: str
    ingress_interface: Optional[str] = None   # interface name, not id
    egress_interface: Optional[str] = None
    decision: str = ""


@dataclass
class SimulationRequest:
    topology: Topology
    source_device_id: Optional[str]
    destination_device_id: Optional[str]
    protocol: Protocol = Protocol.ICMP
    destination_port: Optional[int] = None


@dataclass
class SimulationResult:
    success: bool = False
    source_device_id: Optional[str] = None
    source_name: str = ""
    source_ip: Optional[str] = None
    destination_device_id: Optional[str] = None
    destination_name: str = ""
    destination_ip: Optional[str] = None
    protocol: str = Protocol.ICMP.value
    destination_port: Optional[int] = None

    #: Devices touched, in traversal order (includes switches).
    path_devices: List[str] = field(default_factory=list)
    #: Cables traversed, in order.
    path_connections: List[str] = field(default_factory=list)
    #: Layer-3 processing steps.
    hops: List[Hop] = field(default_factory=list)

    failure_reason: FailureReason = FailureReason.NONE
    failure_message: str = ""
    #: Device where the simulation stopped (highlighted red by the GUI).
    failure_device_id: Optional[str] = None
    #: Set only when a firewall dropped the traffic.
    blocked_by_device_id: Optional[str] = None
    blocked_by_rule_index: Optional[int] = None
    blocked_by_rule_summary: Optional[str] = None

    log: List[LogEntry] = field(default_factory=list)
    #: Validation problems found before/while simulating (informational).
    validation_messages: List[str] = field(default_factory=list)

    # -- helpers -----------------------------------------------------------
    def log_lines(self) -> List[str]:
        return [str(entry) for entry in self.log]

    def log_text(self) -> str:
        return "\n".join(self.log_lines())

    def summary(self) -> str:
        if self.success:
            return "SUCCESS: %s -> %s (%d hops)" % (
                self.source_name,
                self.destination_name,
                len(self.hops),
            )
        return "FAILED: %s -> %s (%s)" % (
            self.source_name or "?",
            self.destination_name or "?",
            self.failure_message or self.failure_reason.value,
        )
