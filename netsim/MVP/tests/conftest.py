"""Shared topology builders for the test suite.

Everything here is plain domain data. No Qt, no files, no network.
"""

from __future__ import annotations

import os
import sys
from typing import Optional

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from netsim.domain.models import (  # noqa: E402
    Action,
    Device,
    DeviceType,
    FirewallRule,
    Interface,
    Protocol,
    Route,
)
from netsim.domain.topology import Topology  # noqa: E402


def iface(iface_id: str, name: str, ip: Optional[str] = None, prefix: Optional[int] = None):
    return Interface(id=iface_id, name=name, ip=ip, prefix=prefix)


def host(device_id: str, name: str, ip: str, prefix: int, gateway: Optional[str] = None,
         device_type: DeviceType = DeviceType.PC) -> Device:
    return Device(
        id=device_id,
        type=device_type,
        name=name,
        interfaces=[iface("%s-eth0" % device_id, "eth0", ip, prefix)],
        gateway=gateway,
    )


def switch(device_id: str, name: str) -> Device:
    return Device(id=device_id, type=DeviceType.SWITCH, name=name, interfaces=[])


def router(device_id: str, name: str, addresses, routes=None,
           device_type: DeviceType = DeviceType.ROUTER) -> Device:
    interfaces = [
        iface("%s-eth%d" % (device_id, index), "eth%d" % index, ip, prefix)
        for index, (ip, prefix) in enumerate(addresses)
    ]
    return Device(
        id=device_id,
        type=device_type,
        name=name,
        interfaces=interfaces,
        routes=list(routes or []),
    )


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------

@pytest.fixture
def same_subnet() -> Topology:
    """PC1 -- Switch1 -- PC2, both in 192.168.1.0/24."""
    topology = Topology(name="same subnet")
    topology.add_device(host("pc1", "PC1", "192.168.1.10", 24))
    topology.add_device(switch("sw1", "Switch1"))
    topology.add_device(host("pc2", "PC2", "192.168.1.20", 24))
    topology.connect("pc1", "sw1", "pc1-eth0", None, connection_id="l1")
    topology.connect("sw1", "pc2", None, "pc2-eth0", connection_id="l2")
    return topology


@pytest.fixture
def single_router() -> Topology:
    """PC1 -- Switch1 -- Router1 -- Server1 (two networks, one router).

    PC1 deliberately starts *without* a default gateway so tests can add one.
    """
    topology = Topology(name="single router")
    topology.add_device(host("pc1", "PC1", "192.168.1.10", 24))
    topology.add_device(switch("sw1", "Switch1"))
    topology.add_device(
        router(
            "r1", "Router1",
            [("192.168.1.1", 24), ("10.0.0.1", 24)],
        )
    )
    topology.add_device(
        host("srv1", "Server1", "10.0.0.10", 24, gateway="10.0.0.1",
             device_type=DeviceType.SERVER)
    )
    topology.connect("pc1", "sw1", "pc1-eth0", None, connection_id="l1")
    topology.connect("sw1", "r1", None, "r1-eth0", connection_id="l2")
    topology.connect("r1", "srv1", "r1-eth1", "srv1-eth0", connection_id="l3")
    return topology


@pytest.fixture
def two_routers() -> Topology:
    """PC1 -- Switch1 -- Router1 -- Router2 -- Server1."""
    topology = Topology(name="two routers")
    topology.add_device(host("pc1", "PC1", "192.168.1.10", 24, gateway="192.168.1.1"))
    topology.add_device(switch("sw1", "Switch1"))
    topology.add_device(
        router(
            "r1", "Router1",
            [("192.168.1.1", 24), ("10.0.0.1", 30)],
            [Route("10.0.2.0", 24, "10.0.0.2", "r1-eth1")],
        )
    )
    topology.add_device(
        router(
            "r2", "Router2",
            [("10.0.0.2", 30), ("10.0.2.1", 24)],
            [Route("192.168.1.0", 24, "10.0.0.1", "r2-eth0")],
        )
    )
    topology.add_device(
        host("srv1", "Server1", "10.0.2.10", 24, gateway="10.0.2.1",
             device_type=DeviceType.SERVER)
    )
    topology.connect("pc1", "sw1", "pc1-eth0", None, connection_id="l1")
    topology.connect("sw1", "r1", None, "r1-eth0", connection_id="l2")
    topology.connect("r1", "r2", "r1-eth1", "r2-eth0", connection_id="l3")
    topology.connect("r2", "srv1", "r2-eth1", "srv1-eth0", connection_id="l4")
    return topology


@pytest.fixture
def firewalled() -> Topology:
    """PC1 -- Router1 -- Firewall1 -- Server1.

    The firewall starts with an empty rule list and a DENY default policy;
    each test installs the rules it wants to prove.
    """
    topology = Topology(name="firewalled")
    topology.add_device(host("pc1", "PC1", "192.168.1.10", 24, gateway="192.168.1.1"))
    topology.add_device(
        router(
            "r1", "Router1",
            [("192.168.1.1", 24), ("10.0.2.1", 30)],
            [Route("10.0.3.0", 24, "10.0.2.2", "r1-eth1")],
        )
    )
    firewall = router(
        "fw1", "Firewall1",
        [("10.0.2.2", 30), ("10.0.3.1", 24)],
        [Route("192.168.1.0", 24, "10.0.2.1", "fw1-eth0")],
        device_type=DeviceType.FIREWALL,
    )
    firewall.default_policy = Action.DENY
    topology.add_device(firewall)
    topology.add_device(
        host("srv1", "Server1", "10.0.3.10", 24, gateway="10.0.3.1",
             device_type=DeviceType.SERVER)
    )
    topology.connect("pc1", "r1", "pc1-eth0", "r1-eth0", connection_id="l1")
    topology.connect("r1", "fw1", "r1-eth1", "fw1-eth0", connection_id="l2")
    topology.connect("fw1", "srv1", "fw1-eth1", "srv1-eth0", connection_id="l3")
    return topology


@pytest.fixture
def longest_prefix() -> Topology:
    """A topology whose *only* correct answer requires longest-prefix matching.

        PC1 -- R1 -+-- RA (10.0.0.0/8 route points here)
                   +-- RB (10.10.0.0/16 route points here)
                   +-- RC (10.10.20.0/24 route points here) -- Server1

    Only RC can actually deliver 10.10.20.50, so a successful simulation
    proves that R1 chose the /24 route.
    """
    topology = Topology(name="longest prefix")
    topology.add_device(host("pc1", "PC1", "192.168.1.10", 24, gateway="192.168.1.1"))
    topology.add_device(
        router(
            "r1", "Router1",
            [("192.168.1.1", 24), ("172.16.0.1", 30), ("172.16.1.1", 30), ("172.16.2.1", 30)],
            [
                Route("10.0.0.0", 8, "172.16.0.2", "r1-eth1", metric=10),
                Route("10.10.0.0", 16, "172.16.1.2", "r1-eth2", metric=10),
                Route("10.10.20.0", 24, "172.16.2.2", "r1-eth3", metric=10),
            ],
        )
    )
    # Dead ends: they have no route towards 10.10.20.0/24.
    topology.add_device(router("ra", "RouterA", [("172.16.0.2", 30)]))
    topology.add_device(router("rb", "RouterB", [("172.16.1.2", 30)]))
    topology.add_device(
        router("rc", "RouterC", [("172.16.2.2", 30), ("10.10.20.1", 24)])
    )
    topology.add_device(
        host("srv1", "Server1", "10.10.20.50", 24, gateway="10.10.20.1",
             device_type=DeviceType.SERVER)
    )
    topology.connect("pc1", "r1", "pc1-eth0", "r1-eth0", connection_id="l0")
    topology.connect("r1", "ra", "r1-eth1", "ra-eth0", connection_id="l1")
    topology.connect("r1", "rb", "r1-eth2", "rb-eth0", connection_id="l2")
    topology.connect("r1", "rc", "r1-eth3", "rc-eth0", connection_id="l3")
    topology.connect("rc", "srv1", "rc-eth1", "srv1-eth0", connection_id="l4")
    return topology


def allow(protocol=Protocol.ANY, port=None, src=None, dst=None, rule_id="r-allow"):
    return FirewallRule(id=rule_id, action=Action.ALLOW, protocol=protocol,
                        src=src, dst=dst, dst_port=port)


def deny(protocol=Protocol.ANY, port=None, src=None, dst=None, rule_id="r-deny"):
    return FirewallRule(id=rule_id, action=Action.DENY, protocol=protocol,
                        src=src, dst=dst, dst_port=port)
