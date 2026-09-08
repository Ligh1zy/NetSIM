"""Ordered, first-match-wins firewall evaluation.

Design decisions (documented deliberately, because they are exam questions):

* Rules are evaluated **top to bottom** and the **first matching rule wins**.
  A later DENY cannot override an earlier ALLOW. This is what makes rule
  ordering observable, which is the whole point of scenario 8.
* If no rule matches, the device's **default policy** applies. The MVP default
  is **DENY** (deny-by-default / whitelist model), matching how real perimeter
  firewalls are usually configured.
* Disabled rules are skipped but still reported in the log, so students can
  see that the rule exists and why it did nothing.
* A malformed rule (bad address, bad port) is **skipped with a warning**
  rather than crashing or silently matching. Editing one broken rule must not
  take the whole simulator down.
* The MVP evaluates traffic **on ingress**, once, in the direction of travel.
  There is no stateful return-traffic tracking.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from typing import List, Optional

from ..domain.models import Action, Device, FirewallRule, Protocol


@dataclass
class Packet:
    """The minimal 5-tuple-ish description the MVP firewall inspects."""

    src_ip: ipaddress.IPv4Address
    dst_ip: ipaddress.IPv4Address
    protocol: Protocol
    dst_port: Optional[int] = None

    def describe(self) -> str:
        port = "" if self.dst_port is None else ":%d" % self.dst_port
        return "%s -> %s%s %s" % (
            self.src_ip, self.dst_ip, port, self.protocol.value.upper()
        )


@dataclass
class RuleEvaluation:
    index: int              # 1-based, as shown to the user
    rule: FirewallRule
    matched: bool
    note: str = ""


@dataclass
class FirewallDecision:
    action: Action
    matched_index: Optional[int] = None       # 1-based
    matched_rule: Optional[FirewallRule] = None
    used_default_policy: bool = False
    evaluations: List[RuleEvaluation] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def allowed(self) -> bool:
        return self.action is Action.ALLOW


def _protocol_matches(rule_proto: Protocol, packet_proto: Protocol) -> bool:
    if rule_proto is Protocol.ANY:
        return True
    return rule_proto == packet_proto


def evaluate(device: Device, packet: Packet) -> FirewallDecision:
    """Run ``packet`` through ``device``'s ordered rule list."""
    decision = FirewallDecision(action=device.default_policy)

    for position, rule in enumerate(device.firewall_rules, start=1):
        if not rule.enabled:
            decision.evaluations.append(
                RuleEvaluation(position, rule, False, "rule is disabled, skipped")
            )
            continue

        src_net, src_error = rule.parse_src()
        dst_net, dst_error = rule.parse_dst()
        port, port_error = rule.parse_port()
        problem = src_error or dst_error or port_error
        if problem is not None:
            message = "%s rule #%d is malformed and was skipped: %s" % (
                device.name, position, problem
            )
            decision.warnings.append(message)
            decision.evaluations.append(
                RuleEvaluation(position, rule, False, "malformed rule skipped: %s" % problem)
            )
            continue

        if not _protocol_matches(rule.protocol, packet.protocol):
            decision.evaluations.append(
                RuleEvaluation(
                    position, rule, False,
                    "protocol %s does not match packet protocol %s"
                    % (rule.protocol.value.upper(), packet.protocol.value.upper()),
                )
            )
            continue

        if src_net is not None and packet.src_ip not in src_net:
            decision.evaluations.append(
                RuleEvaluation(
                    position, rule, False,
                    "source %s is not in %s" % (packet.src_ip, src_net),
                )
            )
            continue

        if dst_net is not None and packet.dst_ip not in dst_net:
            decision.evaluations.append(
                RuleEvaluation(
                    position, rule, False,
                    "destination %s is not in %s" % (packet.dst_ip, dst_net),
                )
            )
            continue

        if port is not None:
            if packet.dst_port is None:
                decision.evaluations.append(
                    RuleEvaluation(
                        position, rule, False,
                        "rule filters destination port %d but the packet has no port" % port,
                    )
                )
                continue
            if packet.dst_port != port:
                decision.evaluations.append(
                    RuleEvaluation(
                        position, rule, False,
                        "destination port %d does not match %d" % (packet.dst_port, port),
                    )
                )
                continue

        # First match wins - stop here.
        decision.evaluations.append(RuleEvaluation(position, rule, True, "MATCH"))
        decision.action = rule.action
        decision.matched_index = position
        decision.matched_rule = rule
        decision.used_default_policy = False
        return decision

    decision.used_default_policy = True
    return decision


def validate_rules(device: Device) -> List[str]:
    """Static checks used by the validation layer (not by evaluation)."""
    problems: List[str] = []
    for position, rule in enumerate(device.firewall_rules, start=1):
        for _, error in (rule.parse_src(), rule.parse_dst()):
            if error:
                problems.append("%s firewall rule #%d: %s" % (device.name, position, error))
        _, port_error = rule.parse_port()
        if port_error:
            problems.append("%s firewall rule #%d: %s" % (device.name, position, port_error))
        if rule.dst_port not in (None, "") and rule.protocol in (Protocol.ICMP, Protocol.ANY):
            problems.append(
                "%s firewall rule #%d filters destination port %s but its protocol is %s; "
                "destination ports only exist for TCP and UDP, so this rule can never match"
                % (device.name, position, rule.dst_port, rule.protocol.value.upper())
            )
    return problems
