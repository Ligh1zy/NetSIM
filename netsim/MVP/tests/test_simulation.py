"""End-to-end simulation scenarios.

These are the ten scenarios the MVP brief asks for, plus loop detection and a
few robustness cases. They run the real engine on real topologies and assert
on the structured result - never on hard-coded text.
"""

from __future__ import annotations

from netsim.domain.models import Action, DeviceType, Protocol, Route
from netsim.simulation.engine import run_simulation
from netsim.simulation.result import FailureReason

from conftest import allow, deny, host, router


def names(topology, result):
    return [topology.device(d).name for d in result.path_devices if topology.device(d)]


# -- Scenario 1: same subnet ------------------------------------------------

def test_scenario_1_same_subnet_succeeds_through_a_switch(same_subnet):
    result = run_simulation(same_subnet, "pc1", "pc2", Protocol.ICMP)
    assert result.success
    assert names(same_subnet, result) == ["PC1", "Switch1", "PC2"]
    assert result.path_connections == ["l1", "l2"]
    assert any("local subnet" in line for line in result.log_lines())


def test_same_subnet_needs_no_gateway(same_subnet):
    assert same_subnet.device("pc1").gateway is None
    assert run_simulation(same_subnet, "pc1", "pc2", Protocol.ICMP).success


def test_switch_does_not_route_between_subnets(same_subnet):
    """A switch must not magically bridge two different subnets."""
    same_subnet.device("pc2").interfaces[0].ip = "192.168.2.20"
    result = run_simulation(same_subnet, "pc1", "pc2", Protocol.ICMP)
    assert not result.success
    assert result.failure_reason is FailureReason.NO_GATEWAY


def test_switch_with_a_gateway_but_no_router_still_fails(same_subnet):
    same_subnet.device("pc2").interfaces[0].ip = "192.168.2.20"
    same_subnet.device("pc1").gateway = "192.168.1.1"
    result = run_simulation(same_subnet, "pc1", "pc2", Protocol.ICMP)
    assert not result.success
    assert result.failure_reason is FailureReason.GATEWAY_UNREACHABLE


# -- Scenario 2: missing gateway -------------------------------------------

def test_scenario_2_missing_gateway_fails_with_a_clear_reason(single_router):
    single_router.device("pc1").gateway = None
    result = run_simulation(single_router, "pc1", "srv1", Protocol.ICMP)
    assert not result.success
    assert result.failure_reason is FailureReason.NO_GATEWAY
    assert result.failure_device_id == "pc1"
    assert "default gateway" in result.failure_message


# -- Scenario 3: correct gateway -------------------------------------------

def test_scenario_3_correct_gateway_succeeds(single_router):
    single_router.device("pc1").gateway = "192.168.1.1"
    result = run_simulation(single_router, "pc1", "srv1", Protocol.ICMP)
    assert result.success
    assert names(single_router, result) == ["PC1", "Switch1", "Router1", "Server1"]
    # The router used its connected route, not a static one.
    assert any("directly connected" in line for line in result.log_lines())


# -- Scenario 4: multi-router ----------------------------------------------

def test_scenario_4_two_routers_deliver_the_packet(two_routers):
    result = run_simulation(two_routers, "pc1", "srv1", Protocol.ICMP)
    assert result.success
    assert names(two_routers, result) == \
        ["PC1", "Switch1", "Router1", "Router2", "Server1"]
    assert [hop.device_name for hop in result.hops] == \
        ["PC1", "Router1", "Router2", "Server1"]


def test_hops_record_ingress_and_egress_interfaces(two_routers):
    result = run_simulation(two_routers, "pc1", "srv1", Protocol.ICMP)
    router1 = [hop for hop in result.hops if hop.device_name == "Router1"][0]
    assert router1.ingress_interface == "eth0"
    assert router1.egress_interface == "eth1"


# -- Scenario 5: routing failure -------------------------------------------

def test_scenario_5_missing_route_fails_at_the_right_router(two_routers):
    two_routers.device("r1").routes = []
    result = run_simulation(two_routers, "pc1", "srv1", Protocol.ICMP)
    assert not result.success
    assert result.failure_reason is FailureReason.NO_ROUTE
    assert result.failure_device_id == "r1"
    assert "Router1 has no route" in result.failure_message


def test_missing_route_on_the_second_router_is_located_correctly(two_routers):
    two_routers.device("r2").interfaces[1].ip = "10.9.9.1"    # loses 10.0.2.0/24
    two_routers.device("srv1").interfaces[0].ip = "10.0.2.10"
    result = run_simulation(two_routers, "pc1", "srv1", Protocol.ICMP)
    assert not result.success
    assert result.failure_device_id == "r2"


def test_route_with_an_unreachable_next_hop_fails_gracefully(two_routers):
    two_routers.device("r1").routes = [Route("10.0.2.0", 24, "10.0.0.9", "r1-eth1")]
    result = run_simulation(two_routers, "pc1", "srv1", Protocol.ICMP)
    assert not result.success
    assert result.failure_reason is FailureReason.NEXT_HOP_UNREACHABLE
    assert result.failure_device_id == "r1"


# -- Scenarios 6-8: firewall -----------------------------------------------

def test_scenario_6_firewall_allows_configured_traffic(firewalled):
    firewalled.device("fw1").firewall_rules = [allow(Protocol.TCP, 443)]
    result = run_simulation(firewalled, "pc1", "srv1", Protocol.TCP, 443)
    assert result.success
    assert names(firewalled, result) == ["PC1", "Router1", "Firewall1", "Server1"]


def test_scenario_7_firewall_blocks_and_is_identified(firewalled):
    firewalled.device("fw1").firewall_rules = [
        allow(Protocol.TCP, 443, rule_id="a"),
        deny(Protocol.TCP, 80, rule_id="b"),
    ]
    result = run_simulation(firewalled, "pc1", "srv1", Protocol.TCP, 80)
    assert not result.success
    assert result.failure_reason is FailureReason.FIREWALL_BLOCKED
    assert result.blocked_by_device_id == "fw1"
    assert result.blocked_by_rule_index == 2
    assert "DENY TCP" in result.blocked_by_rule_summary
    # The attempted path stops at the firewall: the server is never reached.
    assert "srv1" not in result.path_devices


def test_scenario_8_first_matching_rule_wins(firewalled):
    firewall = firewalled.device("fw1")

    firewall.firewall_rules = [allow(Protocol.TCP, 80, rule_id="a"),
                               deny(Protocol.TCP, 80, rule_id="b")]
    assert run_simulation(firewalled, "pc1", "srv1", Protocol.TCP, 80).success

    firewall.firewall_rules = [deny(Protocol.TCP, 80, rule_id="b"),
                               allow(Protocol.TCP, 80, rule_id="a")]
    blocked = run_simulation(firewalled, "pc1", "srv1", Protocol.TCP, 80)
    assert not blocked.success
    assert blocked.blocked_by_rule_index == 1


def test_default_deny_policy_blocks_unmatched_traffic(firewalled):
    firewalled.device("fw1").firewall_rules = [allow(Protocol.TCP, 443)]
    result = run_simulation(firewalled, "pc1", "srv1", Protocol.ICMP)
    assert not result.success
    assert result.failure_reason is FailureReason.FIREWALL_BLOCKED
    assert result.blocked_by_rule_index is None
    assert "default policy" in result.failure_message


def test_default_allow_policy_lets_unmatched_traffic_through(firewalled):
    firewalled.device("fw1").default_policy = Action.ALLOW
    firewalled.device("fw1").firewall_rules = []
    assert run_simulation(firewalled, "pc1", "srv1", Protocol.ICMP).success


def test_firewall_also_routes(firewalled):
    """The firewall is a layer-3 device: it must forward, not just filter."""
    firewalled.device("fw1").default_policy = Action.ALLOW
    result = run_simulation(firewalled, "pc1", "srv1", Protocol.ICMP)
    firewall_hop = [hop for hop in result.hops if hop.device_name == "Firewall1"][0]
    assert firewall_hop.egress_interface == "eth1"


# -- Scenario 9: subnet misconfiguration -----------------------------------

def test_scenario_9_gateway_outside_the_local_subnet_is_rejected(single_router):
    single_router.device("pc1").gateway = "192.168.2.1"
    result = run_simulation(single_router, "pc1", "srv1", Protocol.ICMP)
    assert not result.success
    assert result.failure_reason is FailureReason.INVALID_GATEWAY
    assert "192.168.2.1" in result.failure_message
    assert "192.168.1.10/24" in result.failure_message


# -- Scenario 10: longest prefix match -------------------------------------

def test_scenario_10_longest_prefix_match_selects_the_right_router(longest_prefix):
    result = run_simulation(longest_prefix, "pc1", "srv1", Protocol.ICMP)
    assert result.success, result.failure_message
    assert names(longest_prefix, result) == ["PC1", "Router1", "RouterC", "Server1"]
    assert "RouterA" not in names(longest_prefix, result)
    assert any("longest prefix" in line for line in result.log_lines())


def test_longest_prefix_choice_is_explained_in_the_log(longest_prefix):
    result = run_simulation(longest_prefix, "pc1", "srv1", Protocol.ICMP)
    text = result.log_text()
    assert "10.10.20.0/24" in text
    assert "10.10.0.0/16" in text
    assert "10.0.0.0/8" in text


# -- Loop detection --------------------------------------------------------

def test_routing_loop_is_detected_and_named(two_routers):
    two_routers.device("r2").routes = [Route("10.0.2.0", 24, "10.0.0.1", "r2-eth0")]
    two_routers.device("r2").interfaces[1].ip = "10.9.9.1"   # remove the connected route
    result = run_simulation(two_routers, "pc1", "srv1", Protocol.ICMP)
    assert not result.success
    assert result.failure_reason is FailureReason.ROUTING_LOOP
    assert "Router1" in result.failure_message and "Router2" in result.failure_message


# -- Input handling and robustness ----------------------------------------

def test_missing_source_is_reported(two_routers):
    result = run_simulation(two_routers, None, "srv1")
    assert result.failure_reason is FailureReason.NO_SOURCE


def test_missing_destination_is_reported(two_routers):
    result = run_simulation(two_routers, "pc1", None)
    assert result.failure_reason is FailureReason.NO_DESTINATION


def test_switch_cannot_be_a_simulation_endpoint(two_routers):
    result = run_simulation(two_routers, "sw1", "srv1")
    assert result.failure_reason is FailureReason.UNSUPPORTED_ENDPOINT


def test_same_source_and_destination_is_rejected(two_routers):
    result = run_simulation(two_routers, "pc1", "pc1")
    assert result.failure_reason is FailureReason.SAME_DEVICE


def test_disconnected_devices_fail_before_any_routing(two_routers):
    two_routers.remove_connection("l3")
    result = run_simulation(two_routers, "pc1", "srv1")
    assert result.failure_reason is FailureReason.NOT_PHYSICALLY_CONNECTED


def test_invalid_source_address_is_a_configuration_error(two_routers):
    two_routers.device("pc1").interfaces[0].ip = "999.1.1.1"
    result = run_simulation(two_routers, "pc1", "srv1")
    assert result.failure_reason is FailureReason.CONFIGURATION_ERROR


def test_out_of_range_port_is_ignored_with_a_warning(firewalled):
    firewalled.device("fw1").default_policy = Action.ALLOW
    result = run_simulation(firewalled, "pc1", "srv1", Protocol.TCP, 99999)
    assert result.destination_port is None
    assert any("out of range" in line for line in result.log_lines())


def test_port_is_ignored_for_icmp(firewalled):
    firewalled.device("fw1").default_policy = Action.ALLOW
    result = run_simulation(firewalled, "pc1", "srv1", Protocol.ICMP, 80)
    assert result.destination_port is None
    assert any("has no port number" in line for line in result.log_lines())


def test_result_carries_everything_the_gui_needs(two_routers):
    result = run_simulation(two_routers, "pc1", "srv1", Protocol.ICMP)
    assert result.source_ip == "192.168.1.10/24"
    assert result.destination_ip == "10.0.2.10/24"
    assert result.path_devices and result.path_connections and result.hops
    assert result.log_text()


def test_log_is_generated_from_real_decisions(two_routers):
    """Changing the configuration must change the log."""
    first = run_simulation(two_routers, "pc1", "srv1", Protocol.ICMP).log_text()
    two_routers.device("r1").routes = [Route("10.0.0.0", 8, "10.0.0.2", "r1-eth1")]
    second = run_simulation(two_routers, "pc1", "srv1", Protocol.ICMP).log_text()
    assert "10.0.2.0/24 via 10.0.0.2" in first
    assert "10.0.0.0/8 via 10.0.0.2" in second
