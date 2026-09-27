#!/bin/sh
# Native dnsmasq regression: only disposable namespaces and documentation IPv6.
set -eu
[ "$(id -u)" = 0 ] || { echo 'Run the native RA test as root.' >&2; exit 1; }
umask 077
test_dir=$(mktemp -d /tmp/klokast-ra.XXXXXX)
router_ns="klokast-ra-r-${test_dir##*.}"
client_ns="klokast-ra-c-${test_dir##*.}"
router_created=false
client_created=false
daemon_pid=
stop_dnsmasq() {
  if [ -n "$daemon_pid" ]; then
    kill "$daemon_pid" 2>/dev/null || true
    wait "$daemon_pid" 2>/dev/null || true
    daemon_pid=
  fi
}
cleanup() {
  status=$?
  trap - EXIT HUP INT TERM
  stop_dnsmasq
  if [ "$status" -ne 0 ] && [ -f "$test_dir/daemon.log" ]; then cat "$test_dir/daemon.log" >&2; fi
  if [ "$client_created" = true ]; then ip netns del "$client_ns" || status=1; fi
  if [ "$router_created" = true ]; then ip netns del "$router_ns" || status=1; fi
  rm -f "$test_dir/main" "$test_dir/ops.conf" "$test_dir/daemon.log" "$test_dir/leases" "$test_dir/pid"
  rmdir "$test_dir" || status=1
  [ "$status" -ne 0 ] || echo 'Native RA test cleanup complete.'
  exit "$status"
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM
ip netns add "$router_ns"
router_created=true
ip netns add "$client_ns"
client_created=true
ip -n "$router_ns" link add eth6 type veth peer name eth0 netns "$client_ns"
ip -n "$router_ns" link set lo up
ip -n "$client_ns" link set lo up
ip netns exec "$router_ns" sysctl -qw net.ipv6.conf.all.forwarding=1
ip netns exec "$client_ns" sysctl -qw net.ipv6.conf.all.forwarding=0 net.ipv6.conf.eth0.accept_ra=1 net.ipv6.conf.eth0.autoconf=1
ip -n "$router_ns" address add 2001:db8:1234:1::1/64 dev eth6
ip -n "$router_ns" link set eth6 up
ip -n "$client_ns" link set eth0 up
sleep 2
cat >"$test_dir/main" <<'CONF'
port=0
no-hosts
no-resolv
interface=eth6
bind-interfaces
CONF
cat >"$test_dir/ops.conf" <<'CONF'
enable-ra
dhcp-range=::,constructor:eth6,ra-only,64,12h
CONF
start_dnsmasq() {
  ip netns exec "$router_ns" dnsmasq --test --conf-file="$test_dir/main"
  ip netns exec "$router_ns" dnsmasq --keep-in-foreground --user=root --conf-file="$test_dir/main" --dhcp-leasefile="$test_dir/leases" --pid-file="$test_dir/pid" --log-facility=- >"$test_dir/daemon.log" 2>&1 &
  daemon_pid=$!
  sleep 1
  kill -0 "$daemon_pid"
}
require_slaac() {
  attempt=0
  while [ "$attempt" -lt 20 ]; do
    address=$(ip -n "$client_ns" -o -6 address show dev eth0 scope global)
    if printf '%s\n' "$address" | grep -F '2001:db8:1234:1:' >/dev/null && ! printf '%s\n' "$address" | grep -E 'tentative|dadfailed' >/dev/null; then
      ip -n "$client_ns" -6 route show default | grep -F 'via fe80:' >/dev/null
      echo 'Client received the documentation prefix and an IPv6 default route.'
      return
    fi
    sleep 1
    attempt=$((attempt + 1))
  done
  echo 'Client did not receive a usable SLAAC address.' >&2
  exit 1
}
start_dnsmasq
sleep 3
[ -z "$(ip -n "$client_ns" -o -6 address show dev eth0 scope global)" ]
echo 'Legacy configuration without an include does not advertise the fragment.'
stop_dnsmasq
printf '\nconf-file=%s/ops.conf\n' "$test_dir" >>"$test_dir/main"
start_dnsmasq
require_slaac
stop_dnsmasq
ip -n "$client_ns" -6 address flush dev eth0 scope global
ip -n "$client_ns" -6 route flush default
printf 'conf-dir=%s,*.conf\n' "$test_dir" >>"$test_dir/main"
start_dnsmasq
require_slaac
echo 'Exact include also works when conf-dir already loads the same file.'
