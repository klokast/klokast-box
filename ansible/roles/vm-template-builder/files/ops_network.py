"""Fixed controller network recipe shared by personalization and live migration.

Platform root executes these files. Inputs are approved controller configuration;
no application can select an interface, firewall exception, or forwarding role.
"""
import hashlib
import ipaddress
import re


def uplink(box):
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,30}', box):
        raise ValueError('invalid controller box name')
    digest = hashlib.sha256((box + '-ops-uplink').encode()).hexdigest()
    return {'bridge': 'br-wan', 'interface': 'eth1',
            'mac': '02:' + ':'.join(digest[i:i+2] for i in range(0, 10, 2))}


def validate(value):
    if value.get('role') != 'ops' or value.get('uplink') != uplink(value['box']):
        raise ValueError('controller uplink must be the fixed same-link br-wan interface')
    if (value['bridge'] != 'br-ops' or value['address'] != '192.168.125.10/24' or
            value['bootstrap_source'] != '192.168.125.1'):
        raise ValueError('controller recovery link differs from the Platform allocation')
    resolvers = value['bootstrap_dns']
    if (not isinstance(resolvers, list) or len(resolvers) != 2 or len(set(resolvers)) != 2 or
            any(not ipaddress.IPv4Address(server).is_global for server in resolvers)):
        raise ValueError('controller DNS requires two distinct public IPv4 resolvers')
    return value


def network_value(value):
    return {key: value[key] for key in ('box', 'role', 'bridge', 'address', 'bootstrap_source', 'bootstrap_dns', 'uplink')}


def interfaces(value):
    validate(value)
    return '''auto lo
iface lo inet loopback

auto eth0
iface eth0 inet static
    address 192.168.125.10/24
    pre-up /usr/sbin/nft -f /etc/nftables.nft
    pre-up /sbin/sysctl -q -w net.ipv6.conf.eth0.disable_ipv6=1

auto eth1
iface eth1 inet dhcp
    dhcp-program udhcpc
    pre-up /usr/sbin/nft -f /etc/nftables.nft
    pre-up /sbin/sysctl -q -w net.ipv6.conf.eth1.accept_ra=1
    pre-up /sbin/sysctl -q -w net.ipv6.conf.eth1.autoconf=1
'''


def firewall(value):
    validate(value)
    dns = ', '.join(value['bootstrap_dns'])
    return '''#!/usr/sbin/nft -f
# Own only this table; keep the Tailscale rules intact.
destroy table inet klokast_vm_filter
table inet klokast_vm_filter {
 chain input {
  type filter hook input priority 100; policy drop;
  ct state invalid drop
  ct state { established, related } accept
  iifname "lo" accept
  iifname "tailscale0" accept comment "tailscale-management-input"
  ip protocol icmp accept
  meta l4proto ipv6-icmp accept
  iifname "eth1" udp sport 67 udp dport 68 accept comment "uplink-dhcp"
  iifname "eth1" udp dport 41641 accept comment "uplink-tailscale"
  iifname "eth0" ip saddr 192.168.125.1 tcp dport 22 accept comment "dom0-recovery-ssh"
  iifname "eth0" ip saddr 192.168.125.1 udp dport 41641 accept comment "dom0-tailscale"
 }
 chain forward { type filter hook forward priority 100; policy drop; }
 chain output {
  type filter hook output priority 100; policy drop;
  ct state invalid drop
  ct state { established, related } accept
  oifname "lo" accept
  oifname "tailscale0" accept
  ip protocol icmp accept
  meta l4proto ipv6-icmp accept
  oifname "eth0" ip daddr 192.168.125.1 tcp dport 22 accept
  oifname "eth0" ip daddr 192.168.125.1 udp dport 41641 accept
  oifname "eth1" udp sport 68 udp dport 67 accept
  oifname "eth1" ip daddr { ''' + dns + ''' } meta l4proto { tcp, udp } th dport 53 accept
  oifname "eth1" tcp dport { 80, 443, 5000 } accept comment "controller-public-inputs-and-provider-api"
  oifname "eth1" udp dport { 123, 3478 } accept
  oifname "eth1" udp sport 41641 accept comment "tailscale-peer-transport"
 }
}
'''


def files(value):
    validate(value)
    dns = ''.join('nameserver ' + server + '\n' for server in value['bootstrap_dns'])
    return {
        'etc/network/interfaces': (interfaces(value), 0o644),
        'etc/nftables.nft': (firewall(value), 0o644),
        'etc/udhcpc/udhcpc.conf': ('# Controller DNS is Platform-owned, independent of DHCP.\nNO_DNS="eth1"\n', 0o644),
        'etc/sysctl.d/92-klokast-ops-uplink.conf': (
            'net.ipv4.ip_forward=0\nnet.ipv6.conf.all.forwarding=0\n'
            'net.ipv6.conf.eth0.disable_ipv6=1\nnet.ipv6.conf.eth1.accept_ra=1\n'
            'net.ipv6.conf.eth1.autoconf=1\n', 0o644),
        'etc/klokast/dns-filter.nft': ('destroy table inet klokast_dns\n', 0o644),
        'etc/klokast/overlay-ipv6-input.nft': ('# Controller IPv6 comes from its own upstream interface.\n', 0o600),
        'etc/network/if-up.d/10-klokast-dns': (
            '#!/bin/sh\n# bootstrap-dns: ' + ' '.join(value['bootstrap_dns']) + '\nset -eu\n'
            '[ "${IFACE:-}" = eth1 ] || exit 0\n'
            '/usr/sbin/nft -f /etc/klokast/dns-filter.nft\n'
            '/usr/sbin/resolvconf -f -d eth0.klokast\n'
            '/usr/sbin/resolvconf -f -d udhcpc\n'
            '/usr/sbin/resolvconf -u\n'
            '/usr/sbin/resolvconf -a eth1.klokast <<\'DNS\'\n' + dns + 'DNS\n', 0o755),
    }
