"""Firewall rule evaluation: matching, ordering and the default policy."""

from __future__ import annotations

import ipaddress

from netsim.domain.models import Action, DeviceType, Protocol
from netsim.simulation import firewall as fw

from conftest import allow, deny, router


def make_firewall(rules, default_policy=Action.DENY):
    device = router("fw1", "Firewall1", [("10.0.2.2", 30), ("10.0.3.1", 24)],
                    device_type=DeviceType.FIREWALL)
    device.firewall_rules = list(rules)
    device.default_policy = default_policy
    return device


def packet(protocol=Protocol.TCP, port=80, src="192.168.1.10", dst="10.0.3.10"):
    return fw.Packet(
        src_ip=ipaddress.IPv4Address(src),
        dst_ip=ipaddress.IPv4Address(dst),
        protocol=protocol,
        dst_port=port,
    )


def test_allow_rule_permits_matching_traffic():
    device = make_firewall([allow(Protocol.TCP, 443)])
    decision = fw.evaluate(device, packet(port=443))
    assert decision.allowed
    assert decision.matched_index == 1


def test_deny_rule_blocks_matching_traffic():
    device = make_firewall([deny(Protocol.TCP, 80)])
    decision = fw.evaluate(device, packet(port=80))
    assert not decision.allowed
    assert decision.matched_index == 1
    assert decision.matched_rule.action is Action.DENY


def test_first_matching_rule_wins_allow_before_deny():
    device = make_firewall([
        allow(Protocol.TCP, 80, rule_id="a"),
        deny(Protocol.TCP, 80, rule_id="b"),
    ])
    decision = fw.evaluate(device, packet(port=80))
    assert decision.allowed
    assert decision.matched_index == 1


def test_first_matching_rule_wins_deny_before_allow():
    device = make_firewall([
        deny(Protocol.TCP, 80, rule_id="b"),
        allow(Protocol.TCP, 80, rule_id="a"),
    ])
    decision = fw.evaluate(device, packet(port=80))
    assert not decision.allowed
    assert decision.matched_index == 1


def test_later_rules_are_not_evaluated_after_a_match():
    device = make_firewall([
        allow(Protocol.TCP, 80, rule_id="a"),
        deny(Protocol.TCP, 80, rule_id="b"),
    ])
    decision = fw.evaluate(device, packet(port=80))
    assert [e.index for e in decision.evaluations] == [1]


def test_default_policy_applies_when_nothing_matches():
    device = make_firewall([allow(Protocol.TCP, 443)], default_policy=Action.DENY)
    decision = fw.evaluate(device, packet(port=80))
    assert not decision.allowed
    assert decision.used_default_policy
    assert decision.matched_rule is None


def test_default_policy_allow_lets_unmatched_traffic_through():
    device = make_firewall([deny(Protocol.TCP, 22)], default_policy=Action.ALLOW)
    decision = fw.evaluate(device, packet(port=80))
    assert decision.allowed
    assert decision.used_default_policy


def test_disabled_rules_are_skipped():
    rule = deny(Protocol.TCP, 80)
    rule.enabled = False
    device = make_firewall([rule], default_policy=Action.ALLOW)
    decision = fw.evaluate(device, packet(port=80))
    assert decision.allowed
    assert decision.used_default_policy
    assert "disabled" in decision.evaluations[0].note


def test_protocol_must_match():
    device = make_firewall([deny(Protocol.UDP, 80)], default_policy=Action.ALLOW)
    assert fw.evaluate(device, packet(Protocol.TCP, 80)).allowed


def test_any_protocol_rule_matches_everything():
    device = make_firewall([deny(Protocol.ANY)], default_policy=Action.ALLOW)
    assert not fw.evaluate(device, packet(Protocol.ICMP, None)).allowed
    assert not fw.evaluate(device, packet(Protocol.TCP, 443)).allowed


def test_source_network_filter():
    device = make_firewall(
        [deny(Protocol.TCP, 80, src="192.168.1.0/24")], default_policy=Action.ALLOW
    )
    assert not fw.evaluate(device, packet(src="192.168.1.10")).allowed
    assert fw.evaluate(device, packet(src="192.168.9.10")).allowed


def test_destination_host_filter():
    device = make_firewall(
        [deny(Protocol.TCP, 80, dst="10.0.3.10")], default_policy=Action.ALLOW
    )
    assert not fw.evaluate(device, packet(dst="10.0.3.10")).allowed
    assert fw.evaluate(device, packet(dst="10.0.3.11")).allowed


def test_port_rule_does_not_match_a_portless_packet():
    device = make_firewall([deny(Protocol.ANY, 80)], default_policy=Action.ALLOW)
    decision = fw.evaluate(device, packet(Protocol.ICMP, None))
    assert decision.allowed
    assert "no port" in decision.evaluations[0].note


def test_malformed_rule_is_skipped_with_a_warning_and_does_not_crash():
    bad = deny(Protocol.TCP, 80, src="not-an-address")
    device = make_firewall([bad, deny(Protocol.TCP, 80)], default_policy=Action.ALLOW)
    decision = fw.evaluate(device, packet(port=80))
    assert decision.warnings and "malformed" in decision.warnings[0]
    assert not decision.allowed          # the second, valid rule still applies
    assert decision.matched_index == 2


def test_validate_rules_flags_a_port_on_icmp():
    device = make_firewall([deny(Protocol.ICMP, 80)])
    problems = fw.validate_rules(device)
    assert any("can never match" in problem for problem in problems)
