"""Routing-table construction, longest-prefix matching and egress resolution."""

from __future__ import annotations

import ipaddress

from netsim.domain.models import DeviceType, Route
from netsim.simulation import routing

from conftest import router


def _table(device):
    entries, problems = routing.build_routing_table(device)
    assert problems == []
    return entries


def test_connected_routes_are_generated_from_interfaces():
    device = router("r1", "R1", [("192.168.1.1", 24), ("10.0.0.1", 30)])
    entries = _table(device)
    networks = {str(e.network) for e in entries if e.is_connected}
    assert networks == {"192.168.1.0/24", "10.0.0.0/30"}
    assert all(e.metric == 0 for e in entries if e.is_connected)


def test_longest_prefix_match_prefers_the_most_specific_route():
    device = router(
        "r1", "R1",
        [("172.16.0.1", 24)],
        [
            Route("10.0.0.0", 8, "172.16.0.10", "r1-eth0"),
            Route("10.10.0.0", 16, "172.16.0.11", "r1-eth0"),
            Route("10.10.20.0", 24, "172.16.0.12", "r1-eth0"),
        ],
    )
    entries = _table(device)
    best = routing.select_route(entries, ipaddress.IPv4Address("10.10.20.50"))
    assert str(best.network) == "10.10.20.0/24"
    assert str(best.next_hop) == "172.16.0.12"


def test_longest_prefix_match_falls_back_to_shorter_prefixes():
    device = router(
        "r1", "R1",
        [("172.16.0.1", 24)],
        [
            Route("10.0.0.0", 8, "172.16.0.10", "r1-eth0"),
            Route("10.10.0.0", 16, "172.16.0.11", "r1-eth0"),
            Route("10.10.20.0", 24, "172.16.0.12", "r1-eth0"),
        ],
    )
    entries = _table(device)
    assert str(routing.select_route(entries, ipaddress.IPv4Address("10.10.99.7")).network) \
        == "10.10.0.0/16"
    assert str(routing.select_route(entries, ipaddress.IPv4Address("10.99.99.7")).network) \
        == "10.0.0.0/8"


def test_matching_routes_are_ordered_best_first():
    device = router(
        "r1", "R1",
        [("172.16.0.1", 24)],
        [
            Route("10.0.0.0", 8, "172.16.0.10", "r1-eth0"),
            Route("10.10.20.0", 24, "172.16.0.12", "r1-eth0"),
            Route("10.10.0.0", 16, "172.16.0.11", "r1-eth0"),
        ],
    )
    matches = routing.matching_routes(_table(device), ipaddress.IPv4Address("10.10.20.50"))
    assert [m.network.prefixlen for m in matches] == [24, 16, 8]


def test_connected_route_beats_a_static_route_of_the_same_length():
    device = router(
        "r1", "R1",
        [("10.0.5.1", 24)],
        [Route("10.0.5.0", 24, None, "r1-eth0", metric=1)],
    )
    best = routing.select_route(_table(device), ipaddress.IPv4Address("10.0.5.77"))
    assert best.is_connected


def test_default_route_matches_everything():
    device = router(
        "r1", "R1",
        [("192.168.1.1", 24)],
        [Route("0.0.0.0", 0, "192.168.1.254", "r1-eth0")],
    )
    best = routing.select_route(_table(device), ipaddress.IPv4Address("8.8.8.8"))
    assert best.network.prefixlen == 0


def test_no_route_returns_none():
    device = router("r1", "R1", [("192.168.1.1", 24)])
    assert routing.select_route(_table(device), ipaddress.IPv4Address("8.8.8.8")) is None


def test_malformed_route_is_reported_not_raised():
    device = router("r1", "R1", [("192.168.1.1", 24)], [Route("nonsense", 24, None, "r1-eth0")])
    entries, problems = routing.build_routing_table(device)
    assert len(problems) == 1
    assert "not a valid IPv4 network" in problems[0]
    # The usable part of the table survives.
    assert any(e.is_connected for e in entries)


def test_route_with_host_bits_set_is_rejected():
    route = Route("10.10.20.5", 24, None, "r1-eth0")
    network, error = route.parse()
    assert network is None
    assert "not a valid IPv4 network" in error


def test_resolve_egress_uses_the_interface_that_reaches_the_next_hop():
    device = router(
        "r1", "R1",
        [("192.168.1.1", 24), ("10.0.0.1", 30)],
        [Route("10.0.2.0", 24, "10.0.0.2", None)],
    )
    entry = routing.select_route(_table(device), ipaddress.IPv4Address("10.0.2.10"))
    interface_id, next_hop, error = routing.resolve_egress(
        None, device, entry, ipaddress.IPv4Address("10.0.2.10")
    )
    assert error is None
    assert interface_id == "r1-eth1"
    assert str(next_hop) == "10.0.0.2"


def test_resolve_egress_rejects_an_unreachable_next_hop():
    device = router(
        "r1", "R1",
        [("192.168.1.1", 24)],
        [Route("10.0.2.0", 24, "172.31.9.9", None)],
    )
    entry = routing.select_route(_table(device), ipaddress.IPv4Address("10.0.2.10"))
    interface_id, next_hop, error = routing.resolve_egress(
        None, device, entry, ipaddress.IPv4Address("10.0.2.10")
    )
    assert interface_id is None
    assert "not on any subnet" in error


def test_connected_route_forwards_straight_to_the_destination():
    device = router("r1", "R1", [("10.0.3.1", 24)])
    entry = routing.select_route(_table(device), ipaddress.IPv4Address("10.0.3.10"))
    interface_id, next_hop, error = routing.resolve_egress(
        None, device, entry, ipaddress.IPv4Address("10.0.3.10")
    )
    assert error is None
    assert interface_id == "r1-eth0"
    assert str(next_hop) == "10.0.3.10"
