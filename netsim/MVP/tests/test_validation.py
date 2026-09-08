"""Configuration validation and IPv4 subnet reasoning."""

from __future__ import annotations

import ipaddress

from netsim.domain.models import Interface, Route
from netsim.simulation.validation import Severity, validate_topology

from conftest import host, router


def codes(report):
    return {issue.code for issue in report.issues}


# -- subnet membership -----------------------------------------------------

def test_addresses_in_the_same_subnet():
    a = ipaddress.IPv4Interface("192.168.1.10/24")
    b = ipaddress.IPv4Interface("192.168.1.20/24")
    assert b.ip in a.network


def test_addresses_in_different_subnets():
    a = ipaddress.IPv4Interface("192.168.1.10/24")
    b = ipaddress.IPv4Interface("10.0.2.10/24")
    assert b.ip not in a.network


def test_prefix_changes_subnet_membership():
    a = ipaddress.IPv4Interface("192.168.1.10/16")
    assert ipaddress.IPv4Address("192.168.99.1") in a.network


def test_interface_parse_rejects_a_bad_address():
    parsed, error = Interface("i", "eth0", "192.168.1.999", 24).parse()
    assert parsed is None
    assert "invalid IPv4 address" in error


def test_interface_parse_rejects_a_bad_prefix():
    parsed, error = Interface("i", "eth0", "192.168.1.1", 33).parse()
    assert parsed is None
    assert "between 0 and 32" in error


# -- gateway ---------------------------------------------------------------

def test_valid_gateway_produces_no_error(single_router):
    single_router.device("pc1").gateway = "192.168.1.1"
    report = validate_topology(single_router)
    assert not report.errors_for_device("pc1")


def test_gateway_outside_the_local_subnet_is_an_error(single_router):
    single_router.device("pc1").gateway = "192.168.2.1"
    report = validate_topology(single_router)
    issues = report.errors_for_device("pc1")
    assert issues
    assert issues[0].code == "gateway_outside_subnet"
    assert "192.168.2.1" in issues[0].message
    assert "192.168.1.10/24" in issues[0].message


def test_invalid_gateway_address_is_an_error(single_router):
    single_router.device("pc1").gateway = "banana"
    report = validate_topology(single_router)
    assert "bad_gateway" in {i.code for i in report.errors_for_device("pc1")}


def test_missing_gateway_is_only_a_warning(single_router):
    single_router.device("pc1").gateway = None
    report = validate_topology(single_router)
    assert not report.errors_for_device("pc1")
    assert "no_gateway" in codes(report)


def test_gateway_pointing_at_itself_is_an_error(single_router):
    single_router.device("pc1").gateway = "192.168.1.10"
    report = validate_topology(single_router)
    assert "gateway_is_self" in {i.code for i in report.errors_for_device("pc1")}


# -- addressing ------------------------------------------------------------

def test_duplicate_addresses_are_rejected(same_subnet):
    same_subnet.device("pc2").interfaces[0].ip = "192.168.1.10"
    report = validate_topology(same_subnet)
    assert "duplicate_address" in codes(report)


def test_network_address_cannot_be_used_as_a_host_address(same_subnet):
    same_subnet.device("pc2").interfaces[0].ip = "192.168.1.0"
    report = validate_topology(same_subnet)
    assert "network_address_used" in codes(report)


def test_broadcast_address_cannot_be_used_as_a_host_address(same_subnet):
    same_subnet.device("pc2").interfaces[0].ip = "192.168.1.255"
    report = validate_topology(same_subnet)
    assert "broadcast_address_used" in codes(report)


# -- routes ----------------------------------------------------------------

def test_route_to_an_unreachable_next_hop_is_an_error(two_routers):
    two_routers.device("r1").routes = [Route("10.0.2.0", 24, "203.0.113.9", "r1-eth1")]
    report = validate_topology(two_routers)
    assert "route_unreachable_next_hop" in {i.code for i in report.errors_for_device("r1")}


def test_route_without_a_target_is_an_error(two_routers):
    two_routers.device("r1").routes = [Route("10.0.2.0", 24, None, None)]
    report = validate_topology(two_routers)
    assert "route_no_target" in {i.code for i in report.errors_for_device("r1")}


def test_route_with_a_bad_network_is_an_error(two_routers):
    two_routers.device("r1").routes = [Route("10.0.2.55", 24, "10.0.0.2", "r1-eth1")]
    report = validate_topology(two_routers)
    assert "bad_route" in {i.code for i in report.errors_for_device("r1")}


def test_route_naming_a_missing_interface_is_an_error(two_routers):
    two_routers.device("r1").routes = [Route("10.0.2.0", 24, "10.0.0.2", "does-not-exist")]
    report = validate_topology(two_routers)
    assert "route_bad_interface" in {i.code for i in report.errors_for_device("r1")}


# -- wiring ----------------------------------------------------------------

def test_isolated_device_is_reported(same_subnet):
    same_subnet.add_device(host("pc9", "PC9", "192.168.1.99", 24))
    report = validate_topology(same_subnet)
    assert "isolated" in codes(report)


def test_subnet_mismatch_on_a_direct_link_is_reported(two_routers):
    two_routers.device("r2").interfaces[0].ip = "10.9.9.2"
    report = validate_topology(two_routers)
    assert "subnet_mismatch_on_link" in codes(report)


def test_unplugged_interface_is_reported(two_routers):
    two_routers.remove_connection("l4")
    report = validate_topology(two_routers)
    assert "interface_unplugged" in codes(report)


def test_a_clean_topology_has_no_errors(two_routers):
    report = validate_topology(two_routers)
    assert report.ok, [str(i) for i in report.errors]


def test_report_separates_errors_from_warnings(single_router):
    single_router.device("pc1").gateway = "192.168.2.1"   # error
    report = validate_topology(single_router)
    assert all(i.severity is Severity.ERROR for i in report.errors)
    assert all(i.severity is Severity.WARNING for i in report.warnings)
