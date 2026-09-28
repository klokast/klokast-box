#!/usr/bin/env python3
"""Snapshot IPv6 routes or remove only new routes from the closed ops repair."""
import argparse
import ipaddress
import json
import os
import re
import subprocess


def snapshot(interface):
    routes = json.loads(subprocess.check_output(
        ["ip", "-j", "-6", "route", "show", "table", "main", "dev", interface], text=True))
    # Lifetimes change between signed preflight and execution; route identity does not.
    return [{key: value for key, value in route.items() if key != "expires"} for route in routes]


def deletions(before, current, interface, prefix):
    if not isinstance(before, list) or not all(isinstance(route, dict) for route in before):
        raise ValueError("The signed IPv6 route preimage must be a list of routes.")
    commands = []
    identity = lambda route: tuple(route.get(key) for key in ("dst", "gateway", "protocol", "metric", "table"))
    previous = {identity(route) for route in before}
    for route in current:
        destination, protocol = route.get("dst"), route.get("protocol")
        selected = (destination == prefix and protocol in {"kernel", "ra"}) or (destination == "default" and protocol == "ra")
        if not selected or identity(route) in previous:
            continue
        allowed = {"dst", "gateway", "dev", "protocol", "metric", "flags", "pref", "table"}
        if set(route) - allowed or route.get("flags") or route.get("dev", interface) != interface or route.get("table", "main") != "main":
            raise ValueError("Refuse to remove an unknown IPv6 repair route.")
        command = ["ip", "-6", "route", "del", destination]
        if "gateway" in route:
            command += ["via", str(ipaddress.IPv6Address(route["gateway"]))]
        command += ["dev", interface, "proto", protocol]
        if "metric" in route:
            if type(route["metric"]) is not int or not 0 <= route["metric"] <= 4294967295:
                raise ValueError("The IPv6 repair route metric is invalid.")
            command += ["metric", str(route["metric"])]
        commands.append(command)
    return commands


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("snapshot", "restore"))
    parser.add_argument("interface")
    parser.add_argument("--prefix")
    args = parser.parse_args()
    if not re.fullmatch(r"eth[0-9]+", args.interface):
        parser.error("The interface must be one compiled Ethernet interface.")
    current = snapshot(args.interface)
    if args.operation == "snapshot":
        print(json.dumps(current, sort_keys=True))
        return
    prefix = ipaddress.IPv6Network(args.prefix, strict=True)
    if prefix.prefixlen != 64:
        parser.error("The repair prefix must be one /64.")
    before = json.loads(os.environ["OVERLAY_IPV6_ROUTES_PREIMAGE"])
    for command in deletions(before, current, args.interface, str(prefix)):
        subprocess.run(command, check=True)
    if deletions(before, snapshot(args.interface), args.interface, str(prefix)):
        raise RuntimeError("New IPv6 repair routes remain after restoration.")


if __name__ == "__main__":
    main()
