"""Headless demonstration of the NetSim simulation engine.

    python demo_cli.py

Runs the six required demonstrations against the demo topology **without
importing PyQt at all**. If this script works, the engine is genuinely
GUI-independent - which is the central architectural claim of the MVP.
"""

from __future__ import annotations

import copy
import os
import sys
import tempfile

from netsim.app.controller import AppController
from netsim.domain.models import Action, FirewallRule, Protocol
from netsim.samples.demo import build_demo_topology, build_same_subnet_topology


LINE = "=" * 78


def show(title: str, controller: AppController, source: str, destination: str,
         protocol: Protocol, port=None) -> None:
    print(LINE)
    print(title)
    print(LINE)
    result = controller.run_simulation(source, destination, protocol, port)
    for line in result.log_lines():
        print("   " + line)
    print()
    print("   RESULT      : %s" % ("SUCCESS" if result.success else "FAILURE"))
    if not result.success:
        print("   REASON CODE : %s" % result.failure_reason.value)
        if result.blocked_by_device_id:
            device = controller.topology.device(result.blocked_by_device_id)
            print("   BLOCKED BY  : %s (rule #%s)" % (
                device.name if device else result.blocked_by_device_id,
                result.blocked_by_rule_index,
            ))
    names = [
        (controller.topology.device(d).name if controller.topology.device(d) else d)
        for d in result.path_devices
    ]
    print("   PATH        : %s" % (" -> ".join(names) if names else "(none)"))
    print("   LINKS       : %s" % (", ".join(result.path_connections) or "(none)"))
    print()


def main() -> int:
    db_path = os.path.join(tempfile.gettempdir(), "netsim_demo_history.sqlite3")
    controller = AppController(history_path=db_path)

    # -- Scenario 1: same subnet through a switch -------------------------
    controller.topology = build_same_subnet_topology()
    show("SCENARIO 1  Same subnet: PC1 -> PC2 through a switch (ICMP)",
         controller, "pc1", "pc2", Protocol.ICMP)

    # -- Demo topology ----------------------------------------------------
    base = build_demo_topology()

    controller.topology = copy.deepcopy(base)
    show("DEMO 1  Successful traffic: PC1 -> Server1 (ICMP, allowed by rule #3)",
         controller, "pc1", "srv1", Protocol.ICMP)

    controller.topology = copy.deepcopy(base)
    controller.topology.device("pc1").gateway = None
    show("DEMO 2  Missing gateway: PC1 has no default gateway",
         controller, "pc1", "srv1", Protocol.ICMP)

    controller.topology = copy.deepcopy(base)
    router2 = controller.topology.device("r2")
    router2.routes = [r for r in router2.routes if r.destination != "10.0.3.0"]
    show("DEMO 3  Missing route: Router2 no longer knows 10.0.3.0/24",
         controller, "pc1", "srv1", Protocol.ICMP)

    controller.topology = copy.deepcopy(base)
    show("DEMO 4  Firewall block: TCP port 80 (rule #2 DENY)",
         controller, "pc1", "srv1", Protocol.TCP, 80)

    controller.topology = copy.deepcopy(base)
    show("DEMO 5  Firewall allow: TCP port 443 (rule #1 ALLOW)",
         controller, "pc1", "srv1", Protocol.TCP, 443)

    # -- Firewall rule ordering ------------------------------------------
    allow_80 = FirewallRule(id="tmp-allow-80", action=Action.ALLOW,
                            protocol=Protocol.TCP, dst_port=80,
                            description="allow HTTP")
    deny_80 = FirewallRule(id="tmp-deny-80", action=Action.DENY,
                           protocol=Protocol.TCP, dst_port=80,
                           description="deny HTTP")

    controller.topology = copy.deepcopy(base)
    controller.topology.device("fw1").firewall_rules = [
        copy.deepcopy(allow_80), copy.deepcopy(deny_80)
    ]
    show("DEMO 6a  Rule order: ALLOW 80 before DENY 80 -> the ALLOW wins",
         controller, "pc1", "srv1", Protocol.TCP, 80)

    controller.topology = copy.deepcopy(base)
    controller.topology.device("fw1").firewall_rules = [
        copy.deepcopy(deny_80), copy.deepcopy(allow_80)
    ]
    show("DEMO 6b  Rule order reversed: DENY 80 before ALLOW 80 -> the DENY wins",
         controller, "pc1", "srv1", Protocol.TCP, 80)

    # -- Routing loop -----------------------------------------------------
    controller.topology = copy.deepcopy(base)
    r1 = controller.topology.device("r1")
    r2 = controller.topology.device("r2")
    for route in r2.routes:
        if route.destination == "10.0.3.0":
            route.next_hop = "10.0.0.1"          # point straight back at Router1
            route.interface_id = "r2-eth0"
    r1.routes = [r for r in r1.routes if r.destination != "10.0.0.0"]
    show("EXTRA  Routing loop: Router2 sends 10.0.3.0/24 back to Router1",
         controller, "pc1", "srv1", Protocol.ICMP)

    print(LINE)
    print("SQLite history (%s)" % controller.history.path)
    print(LINE)
    for row in reversed(controller.history_rows(limit=20)):
        print("   " + row.summary())
    print()
    print("%d simulations recorded." % controller.history.count())
    controller.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
