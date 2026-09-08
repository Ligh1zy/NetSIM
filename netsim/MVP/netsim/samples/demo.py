"""The demo topology used by the GUI ("Load demo topology") and by the tests.

    PC1 --- Switch1 --- Router1 --- Router2 --- Firewall1 --- Server1

    LAN A            192.168.1.0/24   PC1 .10, Router1 eth0 .1
    Router1-Router2  10.0.0.0/30      R1 eth1 .1, R2 eth0 .2
    Router2-Firewall 10.0.2.0/30      R2 eth1 .1, FW1 eth0 .2
    LAN B            10.0.3.0/24      FW1 eth1 .1, Server1 .10

Router1 deliberately carries both a 10.0.0.0/8 route and a 10.0.3.0/24 route
so that the demo itself exercises longest-prefix matching.

This is *sample data only*. The engine has no knowledge of it.
"""

from __future__ import annotations

from ..domain.models import (
    Action,
    Device,
    DeviceType,
    FirewallRule,
    Interface,
    Protocol,
    Route,
    reseed_ids,
)
from ..domain.topology import Topology


def _interface(iface_id: str, name: str, ip=None, prefix=None) -> Interface:
    return Interface(id=iface_id, name=name, ip=ip, prefix=prefix)


def build_demo_topology() -> Topology:
    topology = Topology(name="NetSim demo topology")

    pc1 = Device(
        id="pc1",
        type=DeviceType.PC,
        name="PC1",
        x=-360.0,
        y=-40.0,
        interfaces=[_interface("pc1-eth0", "eth0", "192.168.1.10", 24)],
        gateway="192.168.1.1",
    )

    switch1 = Device(
        id="sw1",
        type=DeviceType.SWITCH,
        name="Switch1",
        x=-200.0,
        y=-40.0,
        interfaces=[],
    )

    router1 = Device(
        id="r1",
        type=DeviceType.ROUTER,
        name="Router1",
        x=-40.0,
        y=-40.0,
        interfaces=[
            _interface("r1-eth0", "eth0", "192.168.1.1", 24),
            _interface("r1-eth1", "eth1", "10.0.0.1", 30),
        ],
        routes=[
            # Both of these match 10.0.3.10; the /24 must win.
            Route(destination="10.0.0.0", prefix=8, next_hop="10.0.0.2",
                  interface_id="r1-eth1", metric=10),
            Route(destination="10.0.3.0", prefix=24, next_hop="10.0.0.2",
                  interface_id="r1-eth1", metric=10),
        ],
    )

    router2 = Device(
        id="r2",
        type=DeviceType.ROUTER,
        name="Router2",
        x=120.0,
        y=-40.0,
        interfaces=[
            _interface("r2-eth0", "eth0", "10.0.0.2", 30),
            _interface("r2-eth1", "eth1", "10.0.2.1", 30),
        ],
        routes=[
            Route(destination="10.0.3.0", prefix=24, next_hop="10.0.2.2",
                  interface_id="r2-eth1", metric=10),
            Route(destination="192.168.1.0", prefix=24, next_hop="10.0.0.1",
                  interface_id="r2-eth0", metric=10),
        ],
    )

    firewall1 = Device(
        id="fw1",
        type=DeviceType.FIREWALL,
        name="Firewall1",
        x=280.0,
        y=-40.0,
        interfaces=[
            _interface("fw1-eth0", "eth0", "10.0.2.2", 30),
            _interface("fw1-eth1", "eth1", "10.0.3.1", 24),
        ],
        routes=[
            Route(destination="192.168.1.0", prefix=24, next_hop="10.0.2.1",
                  interface_id="fw1-eth0", metric=10),
        ],
        default_policy=Action.DENY,
        firewall_rules=[
            FirewallRule(
                id="fw1-rule-1", action=Action.ALLOW, protocol=Protocol.TCP,
                dst_port=443, description="allow HTTPS to the server LAN",
            ),
            FirewallRule(
                id="fw1-rule-2", action=Action.DENY, protocol=Protocol.TCP,
                dst_port=80, description="plain HTTP is not permitted",
            ),
            FirewallRule(
                id="fw1-rule-3", action=Action.ALLOW, protocol=Protocol.ICMP,
                description="allow ping for troubleshooting",
            ),
        ],
    )

    server1 = Device(
        id="srv1",
        type=DeviceType.SERVER,
        name="Server1",
        x=440.0,
        y=-40.0,
        interfaces=[_interface("srv1-eth0", "eth0", "10.0.3.10", 24)],
        gateway="10.0.3.1",
    )

    for device in (pc1, switch1, router1, router2, firewall1, server1):
        topology.add_device(device)

    topology.connect("pc1", "sw1", "pc1-eth0", None, connection_id="link-pc1-sw1")
    topology.connect("sw1", "r1", None, "r1-eth0", connection_id="link-sw1-r1")
    topology.connect("r1", "r2", "r1-eth1", "r2-eth0", connection_id="link-r1-r2")
    topology.connect("r2", "fw1", "r2-eth1", "fw1-eth0", connection_id="link-r2-fw1")
    topology.connect("fw1", "srv1", "fw1-eth1", "srv1-eth0", connection_id="link-fw1-srv1")

    reseed_ids(list(topology.all_ids()))
    return topology


def build_same_subnet_topology() -> Topology:
    """PC1 -- Switch1 -- PC2, used for the layer-2 scenarios."""
    topology = Topology(name="Same subnet demo")
    topology.add_device(
        Device(
            id="pc1", type=DeviceType.PC, name="PC1", x=-200.0, y=0.0,
            interfaces=[_interface("pc1-eth0", "eth0", "192.168.1.10", 24)],
        )
    )
    topology.add_device(
        Device(id="sw1", type=DeviceType.SWITCH, name="Switch1", x=0.0, y=0.0, interfaces=[])
    )
    topology.add_device(
        Device(
            id="pc2", type=DeviceType.PC, name="PC2", x=200.0, y=0.0,
            interfaces=[_interface("pc2-eth0", "eth0", "192.168.1.20", 24)],
        )
    )
    topology.connect("pc1", "sw1", "pc1-eth0", None, connection_id="link-1")
    topology.connect("sw1", "pc2", None, "pc2-eth0", connection_id="link-2")
    reseed_ids(list(topology.all_ids()))
    return topology
