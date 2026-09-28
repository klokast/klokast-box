#!/bin/sh
# Run with a prepared fixture directory on an Alpine router. No host network changes.
set -eu
[ "$(id -u)" = 0 ] || { echo 'Run the native overlay test as root.' >&2; exit 1; }
fixture=$1
umask 077
test_dir=$(mktemp -d /tmp/klokast-overlay-native.XXXXXX)
router_ns="ko-r-${test_dir##*.}"
client_ns="ko-c-${test_dir##*.}"
wan_ns="ko-w-${test_dir##*.}"
created=
dns_pid=
udp_pid=
udp4_pid=
stop_dns() {
  if [ -n "$dns_pid" ]; then kill "$dns_pid" 2>/dev/null || true; wait "$dns_pid" 2>/dev/null || true; dns_pid=; fi
}
cleanup() {
  status=$?
  trap - EXIT HUP INT TERM
  stop_dns
  if [ -n "$udp_pid" ]; then kill "$udp_pid" 2>/dev/null || true; wait "$udp_pid" 2>/dev/null || true; fi
  if [ -n "$udp4_pid" ]; then kill "$udp4_pid" 2>/dev/null || true; wait "$udp4_pid" 2>/dev/null || true; fi
  if [ "$status" -ne 0 ] && [ -f "$test_dir/dns.log" ]; then cat "$test_dir/dns.log" >&2; fi
  for ns in $created; do ip netns del "$ns" || status=1; done
  rm -rf "$test_dir"
  [ "$status" -ne 0 ] || echo 'Native overlay test and namespace cleanup passed.'
  exit "$status"
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM
for ns in "$router_ns" "$client_ns" "$wan_ns"; do ip netns add "$ns"; created="$ns $created"; ip -n "$ns" link set lo up; done
ip -n "$router_ns" link add eth6 type veth peer name eth0 netns "$client_ns"
ip -n "$router_ns" link add eth0 type veth peer name eth0 netns "$wan_ns"
ip -n "$router_ns" link set eth0 up
ip -n "$router_ns" link set eth6 up
ip -n "$client_ns" link set eth0 up
ip -n "$wan_ns" link set eth0 up
ip -n "$router_ns" address add 192.0.2.2/24 dev eth0
ip -n "$wan_ns" address add 192.0.2.1/24 dev eth0
ip -n "$router_ns" route add default via 192.0.2.1
ip -n "$client_ns" address add 198.51.100.10/24 dev eth0
ip -n "$router_ns" address add 198.51.100.254/24 dev eth6
ip -n "$client_ns" route add default via 198.51.100.254
ip -n "$wan_ns" route add 198.51.100.0/24 via 192.0.2.2
ip netns exec "$router_ns" python3 "$fixture/routes.py" snapshot eth6 >"$test_dir/router-routes.json"
ip netns exec "$client_ns" python3 "$fixture/routes.py" snapshot eth0 >"$test_dir/client-routes.json"
ip -n "$router_ns" address add 2001:db8:1234::2/64 dev eth0
ip -n "$wan_ns" address add 2001:db8:1234::1/64 dev eth0
ip -n "$wan_ns" -6 route add 2001:db8:1234:1::/64 via 2001:db8:1234::2
ip netns exec "$router_ns" sysctl -qw net.ipv6.conf.all.forwarding=1 net.ipv6.conf.eth0.accept_ra=2
ip netns exec "$client_ns" sysctl -qw net.ipv6.conf.eth0.accept_ra=1 net.ipv6.conf.eth0.autoconf=1
ip -n "$router_ns" address add fe80::1234/64 dev eth0
ip -n "$router_ns" address add 2001:db8:1234:1::1/64 dev eth6
mkdir -p "$test_dir/etc/klokast" "$test_dir/etc/dnsmasq.d"
cp "$fixture/router-rules.nft" "$test_dir/etc/klokast/overlay-ipv6.nft"
cp "$fixture/router-ipv4-suppression.nft" "$test_dir/router-ipv4-suppression.nft"
cp "$fixture/ops-rules.nft" "$test_dir/ops-rules.nft"
cat >"$test_dir/router-base.nft" <<'NFT'
flush ruleset
table inet filter {
    chain forward {
        type filter hook forward priority 0; policy drop;
        ct state { established, related } accept
        meta nfproto ipv4 accept
    }
    chain output {
        type filter hook output priority 0; policy accept;
    }
}
NFT
cp "$test_dir/router-base.nft" "$test_dir/router.nft"
ip netns exec "$router_ns" nft -f "$test_dir/router.nft"
cat >"$test_dir/udp4.py" <<'PY'
import socket,sys
s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
if sys.argv[1]=='server':
    s.bind(('192.0.2.1',41641)); s.settimeout(60)
    while True:
        data,peer=s.recvfrom(1024); s.sendto(data,peer)
else:
    s.bind(('198.51.100.10',int(sys.argv[1]))); s.settimeout(2)
    s.sendto(b'klokast-native-ipv4',('192.0.2.1',41641))
    try:
        data,_=s.recvfrom(1024)
    except socket.timeout:
        sys.exit(1)
    assert data==b'klokast-native-ipv4'
PY
ip netns exec "$wan_ns" python3 "$test_dir/udp4.py" server &
udp4_pid=$!
sleep 1
ip netns exec "$client_ns" python3 "$test_dir/udp4.py" 41641
cat >"$test_dir/ops.nft" <<NFT
table inet filter {
 chain input {
  type filter hook input priority 100; policy drop;
  meta l4proto ipv6-icmp accept
  meta nfproto ipv4 accept
  include "$test_dir/ops-rules.nft"
 }
}
NFT
ip netns exec "$client_ns" nft -f "$test_dir/ops.nft"
cat >"$test_dir/etc/dnsmasq.d/91-klokast-ops-ipv6.conf" <<'CONF'
enable-ra
dhcp-range=::,constructor:eth6,ra-only,64,12h
CONF
cat >"$test_dir/etc/dnsmasq.conf" <<CONF
port=0
no-hosts
no-resolv
interface=eth6
bind-interfaces
conf-file=$test_dir/etc/dnsmasq.d/91-klokast-ops-ipv6.conf
CONF
ip netns exec "$router_ns" dnsmasq --keep-in-foreground --user=root --conf-file="$test_dir/etc/dnsmasq.conf" --dhcp-leasefile="$test_dir/leases" --pid-file="$test_dir/pid" --log-facility=- >"$test_dir/dns.log" 2>&1 &
dns_pid=$!
attempt=0
while [ "$attempt" -lt 30 ]; do
  address=$(ip -n "$client_ns" -o -6 address show dev eth0 to 2001:db8:1234:1::/64 scope global)
  if [ -n "$address" ] && ! printf '%s\n' "$address" | grep -E 'tentative|dadfailed|deprecated' >/dev/null; then break; fi
  sleep 1
  attempt=$((attempt + 1))
done
[ "$attempt" -lt 30 ] || { echo 'SLAAC failed in the isolated client.' >&2; exit 1; }
cat >"$test_dir/udp.py" <<'PY'
import socket,sys
s=socket.socket(socket.AF_INET6,socket.SOCK_DGRAM)
if sys.argv[1]=='server':
    s.bind(('2001:db8:1234::1',41641)); s.settimeout(60)
    while True:
        data,peer=s.recvfrom(1024); s.sendto(data,peer)
else:
    s.bind(('::',int(sys.argv[1]))); s.settimeout(2)
    s.sendto(b'klokast-native-overlay',('2001:db8:1234::1',41641))
    try:
        data,_=s.recvfrom(1024)
    except socket.timeout:
        sys.exit(1)
    assert data==b'klokast-native-overlay'
PY
ip netns exec "$wan_ns" python3 "$test_dir/udp.py" server &
udp_pid=$!
sleep 1
if ip netns exec "$client_ns" python3 "$test_dir/udp.py" 41641; then echo 'Missing router include unexpectedly forwarded IPv6.' >&2; exit 1; fi
echo 'Legacy firewall without the include blocks the native IPv6 probe.'
awk -v rule="$test_dir/router-ipv4-suppression.nft" -v include="$test_dir/etc/klokast/overlay-ipv6.nft" '
  { print }
  /type filter hook forward priority 0; policy drop;/ {
    while ((getline line < rule) > 0) print line
    close(rule)
    print "        include \"" include "\""
  }
' "$test_dir/router-base.nft" >"$test_dir/router.nft"
ip netns exec "$router_ns" nft -c -f "$test_dir/router.nft"
ip netns exec "$router_ns" nft -f "$test_dir/router.nft"
cp "$test_dir/router.nft" "$test_dir/etc/nftables.nft"
if ip netns exec "$client_ns" python3 "$test_dir/udp4.py" 41641; then echo 'Ops Tailscale direct IPv4 UDP bypassed suppression.' >&2; exit 1; fi
ip netns exec "$client_ns" python3 "$test_dir/udp4.py" 41642
ip netns exec "$client_ns" python3 "$test_dir/udp.py" 41641
if ip netns exec "$client_ns" python3 "$test_dir/udp.py" 41642; then echo 'Unexpected UDP source port passed the narrow rule.' >&2; exit 1; fi
sed "s|/etc/|$test_dir/etc/|g" "$fixture/router-verify.sh" >"$test_dir/router-verify.sh"
ip netns exec "$router_ns" sh "$test_dir/router-verify.sh"
printf '%s\n' 'enable-ra' >"$test_dir/etc/dnsmasq.d/unexpected.conf"
if ip netns exec "$router_ns" sh "$test_dir/router-verify.sh"; then
  echo 'Unexpected router advertisement configuration passed verification.' >&2; exit 1
fi
rm "$test_dir/etc/dnsmasq.d/unexpected.conf"
echo 'Actual forwarding rules prefer direct IPv6, suppress ops direct IPv4 UDP, and reject another IPv6 source port.'
stop_dns
# Reproduce both fully expanded residual SLAAC addresses from the live failure.
ip -n "$router_ns" address add 2001:db8:1234:1:216:3eff:fe71:1107/64 dev eth6 preferred_lft 0
ip netns exec "$router_ns" sh "$fixture/router-cleanup.sh"
ip netns exec "$client_ns" sh "$fixture/ops-cleanup.sh"
OVERLAY_IPV6_ROUTES_PREIMAGE=$(cat "$test_dir/router-routes.json") ip netns exec "$router_ns" python3 "$fixture/routes.py" restore eth6 --prefix 2001:db8:1234:1::/64
OVERLAY_IPV6_ROUTES_PREIMAGE=$(cat "$test_dir/client-routes.json") ip netns exec "$client_ns" python3 "$fixture/routes.py" restore eth0 --prefix 2001:db8:1234:1::/64
test -z "$(ip -n "$router_ns" -o -6 address show dev eth6 to 2001:db8:1234:1::/64)"
test -z "$(ip -n "$client_ns" -o -6 address show dev eth0 to 2001:db8:1234:1::/64)"
test -z "$(ip -n "$client_ns" -6 route show to 2001:db8:1234:1::/64)"
test -z "$(ip -n "$router_ns" -6 route show to 2001:db8:1234:1::/64)"
test -z "$(ip -n "$client_ns" -6 route show default proto ra)"
ip -n "$router_ns" -4 route show default | grep -F 'via 192.0.2.1'
ip -n "$client_ns" -4 route show default | grep -F 'via 198.51.100.254'
ip -n "$router_ns" -o -6 address show dev eth0 to 2001:db8:1234::2/128 | grep -F '2001:db8:1234::2/64'
echo 'Actual recovery scripts remove expanded SLAAC additions and preserve IPv4 and unrelated IPv6.'
