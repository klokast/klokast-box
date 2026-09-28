#!/usr/bin/env python3
"""Render the actual overlay tasks for isolated native network tests."""
import argparse
import re
import shlex
from pathlib import Path

import yaml
from jinja2 import Environment

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(mode=0o700, parents=True, exist_ok=False)
    environment = Environment()
    environment.filters.update(quote=shlex.quote, regex_escape=re.escape,
                               regex_replace=lambda value, pattern, replacement: re.sub(pattern, replacement, value))
    variables = {
        "router_wan_interface": "eth0", "router_ops_interface": "eth6",
        "overlay_ipv6_prefix": "2001:db8:1234:1::/64", "overlay_ipv6_next_hop": "fe80::1234",
        "platform_control_zones": {"ops": {"vm_interface": "eth0", "vm_ipv4_address": "198.51.100.10"}},
        "overlay_ipv6_router_preimage": {"runtime": "forwarding=0\naccept_ra=1"},
        "overlay_ipv6_ops_preimage": {"runtime": "accept_ra=1\nautoconf=1"},
    }
    selections = {
        "83-overlay-ipv6-router.yml": [
            ("Install the narrow ops-only IPv6 forwarding rules", "ansible.builtin.copy", "content", "router-rules.nft"),
            ("Keep ops Tailscale WAN UDP off the slower direct IPv4 path", "ansible.builtin.lineinfile", "line", "router-ipv4-suppression.nft"),
            ("Restore router runtime sysctl and managed addresses", "ansible.builtin.shell", None, "router-cleanup.sh"),
            ("Collect the enabled ops IPv6 router state", "ansible.builtin.shell", None, "router-verify.sh"),
        ],
        "84-overlay-ipv6-ops.yml": [
            ("Permit only native IPv6 Tailscale input on the ops VM", "ansible.builtin.copy", "content", "ops-rules.nft"),
            ("Restore ops runtime sysctl and delegated addresses", "ansible.builtin.shell", None, "ops-cleanup.sh"),
        ],
    }
    for play, selected in selections.items():
        tasks = yaml.safe_load((ROOT / "ansible/playbooks" / play).read_text())[0]["tasks"]
        for name, module, field, filename in selected:
            value = next(task for task in tasks if task["name"] == name)[module]
            if field:
                value = value[field]
            content = environment.from_string(value).render(**variables)
            (args.output / filename).write_text(content + "\n", encoding="utf-8")
    (args.output / "run.sh").write_bytes((ROOT / "ansible/tests/overlay_ipv6_forwarding_native.sh").read_bytes())
    (args.output / "routes.py").write_bytes((ROOT / "ansible/lib/overlay_ipv6_routes.py").read_bytes())


if __name__ == "__main__":
    main()
