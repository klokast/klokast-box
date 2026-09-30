"""Reconstruct the signed, ops-only IPv6 router repair on a new OS disk.

The root Secret Authority broker selects the signed source. This module checks
its closed shape and renders files; it cannot select a source or grant cutover.
"""

import hashlib
import ipaddress
import json
import re


HASH = re.compile(r"[0-9a-f]{64}\Z")
BOX = re.compile(r"[a-z0-9][a-z0-9-]{0,30}\Z")
INTERFACE = re.compile(r"[a-zA-Z][a-zA-Z0-9_.-]{0,14}\Z")
FIELDS = {"kind", "box", "peer_box", "delegated_prefix", "router_next_hop",
          "freebox_gateway_id_sha256", "delegation_slot", "authority_state_sha256",
          "repair_receipt_sha256", "intent_sha256", "source_sha256"}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def stable_next_hop(box):
    value = int.from_bytes(hashlib.sha256(("klokast-overlay-ipv6:" + box).encode("ascii")).digest()[:8], "big")
    value &= ~(1 << 57)
    value |= 1 << 56
    return str(ipaddress.IPv6Address(int(ipaddress.IPv6Address("fe80::")) | value))


def validate_source(source, box):
    if (not isinstance(source, dict) or set(source) != FIELDS or
            source.get("kind") != "klokast.router-overlay-ipv6-source.v1" or
            not isinstance(box, str) or not BOX.fullmatch(box) or source.get("box") != box or
            not isinstance(source.get("peer_box"), str) or not BOX.fullmatch(source["peer_box"]) or
            source["peer_box"] == box or
            not isinstance(source.get("delegated_prefix"), str) or
            not isinstance(source.get("router_next_hop"), str) or
            type(source.get("delegation_slot")) is not int or
            not 1 <= source["delegation_slot"] <= 7 or
            any(not isinstance(source.get(name), str) or not HASH.fullmatch(source[name]) for name in (
                "freebox_gateway_id_sha256", "authority_state_sha256", "repair_receipt_sha256",
                "intent_sha256", "source_sha256")) or
            source["source_sha256"] != digest({key: value for key, value in source.items()
                                               if key != "source_sha256"})):
        raise ValueError("signed router IPv6 source has an invalid box, field, or digest")
    try:
        prefix = ipaddress.IPv6Network(source["delegated_prefix"], strict=True)
        next_hop = ipaddress.IPv6Address(source["router_next_hop"])
    except (ValueError, TypeError, KeyError) as error:
        raise ValueError("signed router IPv6 source has an invalid address") from error
    if (prefix.prefixlen != 64 or not prefix.is_global or str(prefix) != source["delegated_prefix"] or
            not next_hop.is_link_local or
            str(next_hop) != stable_next_hop(box)):
        raise ValueError("signed router IPv6 source is not the stable ops-only selection")
    return prefix


def files(source, box, *, wan, ops, ops_ipv4):
    prefix = validate_source(source, box)
    if (not isinstance(wan, str) or not INTERFACE.fullmatch(wan) or
            not isinstance(ops, str) or not INTERFACE.fullmatch(ops) or wan == ops):
        raise ValueError("router IPv6 recipe has invalid network interfaces")
    try:
        if not isinstance(ops_ipv4, str):
            raise ValueError("ops IPv4 address is not text")
        address = ipaddress.IPv4Address(ops_ipv4)
    except (ipaddress.AddressValueError, TypeError) as error:
        raise ValueError("router IPv6 recipe has an invalid ops IPv4 address") from error
    if str(address) != ops_ipv4:
        raise ValueError("router IPv6 recipe has a non-canonical ops IPv4 address")
    gateway = str(prefix.network_address + 1)
    selected = str(prefix)
    next_hop = source["router_next_hop"]
    rules = (
        f'iifname "{ops}" oifname "{wan}" ip saddr {address} udp sport 41641 drop comment "ops-tailscale-wan-ipv4-suppression"\n'
        f'iifname "{ops}" oifname "{wan}" ip6 saddr {selected} udp sport 41641 accept comment "ops-ipv6-tailscale-source-egress"\n'
        f'iifname "{ops}" oifname "{wan}" ip6 saddr {selected} udp dport 3478 accept comment "ops-ipv6-tailscale-stun-egress"\n'
        f'iifname "{wan}" oifname "{ops}" ip6 daddr {selected} udp dport 41641 accept comment "ops-ipv6-tailscale-input"\n'
        f'iifname "{ops}" oifname "{wan}" ip6 saddr {selected} meta l4proto ipv6-icmp accept comment "ops-ipv6-icmp-egress"\n'
        f'iifname "{wan}" oifname "{ops}" ip6 daddr {selected} meta l4proto ipv6-icmp accept comment "ops-ipv6-icmp-input"\n'
    )
    return {
        "etc/klokast/overlay-ipv6.nft": rules,
        "etc/network/if-up.d/91-klokast-ops-ipv6": (
            "#!/bin/sh\nset -eu\ncase \"${IFACE:-}\" in\n"
            f"  {wan})\n    ip -6 address replace {next_hop}/64 dev {wan}\n    ;;\n"
            f"  {ops})\n    ip -6 address replace {gateway}/64 dev {ops}\n    ;;\n"
            "esac\n"
        ),
        "etc/sysctl.d/91-klokast-ops-ipv6.conf": (
            f"net.ipv6.conf.all.forwarding = 1\nnet.ipv6.conf.{wan}.accept_ra = 2\n"
        ),
        "etc/dnsmasq.d/91-klokast-ops-ipv6.conf": (
            f"enable-ra\ndhcp-range=::,constructor:{ops},ra-only,64,12h\n"
        ),
    }
