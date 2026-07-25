#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  build-bootstrap-iso.sh \
    [--debian-release trixie] \
    [--arch amd64] \
    [--output-dir /path/to/output] \
    [--work-dir /path/to/work]

Build a generic Debian live ISO for the Platform bootstrap.

The ISO does not embed a box name or Tailscale auth key. At boot, the operator
opens http://kk.local/ or http://klokast.local/ from the DHCP LAN and enrolls
the live host through the onboarding portal.
EOF
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    printf 'Missing required command: %s\n' "$1" >&2
    exit 1
  }
}

goarch_for_live_arch() {
  case "$1" in
    amd64|x86_64)
      printf 'amd64\n'
      ;;
    arm64|aarch64)
      printf 'arm64\n'
      ;;
    *)
      printf 'Unsupported architecture for portal build: %s\n' "$1" >&2
      exit 1
      ;;
  esac
}

live_hostname='klokast-bootstrap'
debian_release='trixie'
architecture='amd64'
output_dir=''
work_dir=''

while [ "$#" -gt 0 ]; do
  case "$1" in
    --hostname|--node|--auth-key-file|--tailscale-hostname)
      printf '%s is not accepted by the portal-only generic ISO builder.\n' "$1" >&2
      exit 1
      ;;
    --live-hostname)
      live_hostname="${2:?missing value for --live-hostname}"
      shift 2
      ;;
    --debian-release)
      debian_release="${2:?missing value for --debian-release}"
      shift 2
      ;;
    --arch)
      architecture="${2:?missing value for --arch}"
      shift 2
      ;;
    --output-dir)
      output_dir="${2:?missing value for --output-dir}"
      shift 2
      ;;
    --work-dir)
      work_dir="${2:?missing value for --work-dir}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'Unknown argument: %s\n' "$1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
artifact_root="${script_dir}/../artifacts/bootstrap-iso-debian"
output_dir="${output_dir:-$artifact_root}"

if [ -z "$work_dir" ]; then
  work_dir="$(mktemp -d "${TMPDIR:-/tmp}/klokast-bootstrap.XXXXXX")"
  cleanup_work_dir='true'
else
  mkdir -p "$work_dir"
  cleanup_work_dir='false'
fi

trap 'if [ "${cleanup_work_dir}" = "true" ]; then rm -rf "$work_dir"; fi' EXIT

iso_basename="klokast-bootstrap-debian-${debian_release}-${architecture}"
portal_binary="${work_dir}/klokast-onboarding-portal"

require_cmd go
require_cmd lb
require_cmd curl
require_cmd sha512sum
require_cmd xorriso

portal_goarch="$(goarch_for_live_arch "$architecture")"
(
  cd "${script_dir}/portal"
  CGO_ENABLED=0 GOOS=linux GOARCH="${portal_goarch}" \
    go build -trimpath -ldflags="-s -w" \
      -o "$portal_binary" ./cmd/klokast-onboarding-portal
)

build_dir="${work_dir}/build"
mkdir -p "$build_dir" "$output_dir"
cd "$build_dir"

lb clean --purge || true

# Disable live-build's automatic firmware expansion and keep Stage 1 limited to
# the explicitly chosen packages below.
lb config \
  --mode debian \
  --distribution "$debian_release" \
  --architectures "$architecture" \
  --binary-images iso-hybrid \
  --debian-installer none \
  --archive-areas "main contrib non-free-firmware" \
  --firmware-binary false \
  --firmware-chroot false \
  --bootappend-live "boot=live components toram hostname=${live_hostname} username=root noeject"

mkdir -p \
  config/package-lists \
  config/hooks/live \
  config/includes.chroot/etc/systemd/system \
  config/includes.chroot/etc/systemd/system/multi-user.target.wants \
  config/includes.chroot/usr/local/sbin

cat >config/package-lists/klokast-bootstrap.list.chroot <<'EOF'
avahi-daemon
avahi-utils
ca-certificates
curl
dosfstools
efibootmgr
firmware-linux-free
# Keep the stage-1 carrier small and deterministic.
# firmware-misc-nonfree pulls in the broad firmware-linux meta path, including
# installer packages such as firmware-b43-installer that try to fetch blobs
# from GitHub during live-build chroot creation.
firmware-realtek
gdisk
grub-efi-amd64-bin
iproute2
iputils-ping
isc-dhcp-client
kmod
less
live-boot
lvm2
parted
python3
rsync
systemd-sysv
util-linux
vim-tiny
EOF

cat >config/hooks/live/0100-install-tailscale.chroot <<EOF
#!/bin/sh
set -eu
mkdir -p /usr/share/keyrings
curl -fsSL https://pkgs.tailscale.com/stable/debian/${debian_release}.noarmor.gpg >/usr/share/keyrings/tailscale-archive-keyring.gpg
curl -fsSL https://pkgs.tailscale.com/stable/debian/${debian_release}.tailscale-keyring.list >/etc/apt/sources.list.d/tailscale.list
apt-get update
apt-get install -y --no-install-recommends tailscale
apt-get clean
EOF
chmod 0755 config/hooks/live/0100-install-tailscale.chroot

install -m 0755 "$portal_binary" \
  config/includes.chroot/usr/local/sbin/klokast-onboarding-portal

cat >config/includes.chroot/usr/local/sbin/klokast-network-bootstrap.sh <<'EOF'
#!/bin/sh
set -eu

exec >>/var/log/klokast-network-bootstrap.log 2>&1

log() {
  printf '%s %s\n' "$(date -Is)" "$*"
}

pick_iface() {
  for sysfs in /sys/class/net/*; do
    iface="${sysfs##*/}"
    if is_candidate_iface "$iface"; then
      ip link set "$iface" up || true
    fi
  done

  attempts=0
  while [ "$attempts" -lt 30 ]; do
    current_default="$(ip route show default 2>/dev/null | awk 'NR==1 { print $5 }')"
    if [ -n "${current_default}" ] && is_candidate_iface "$current_default" && [ -n "$(interface_ipv4 "$current_default")" ]; then
      printf '%s\n' "${current_default}"
      return 0
    fi
    attempts=$((attempts + 1))
    sleep 1
  done

  if pick_carrier_iface false; then
    return 0
  fi
  if pick_any_iface false; then
    return 0
  fi

  if pick_carrier_iface true; then
    return 0
  fi
  pick_any_iface true
}

pick_carrier_iface() {
  allow_usb_gadget="$1"
  for sysfs in /sys/class/net/*; do
    iface="${sysfs##*/}"
    is_candidate_iface "$iface" || continue
    if [ "$allow_usb_gadget" != "true" ] && is_usb_gadget_iface "$iface"; then
      continue
    fi
    carrier="$(cat "${sysfs}/carrier" 2>/dev/null || printf '0')"
    if [ "${carrier}" = "1" ]; then
      printf '%s\n' "${iface}"
      return 0
    fi
  done

  return 1
}

pick_any_iface() {
  allow_usb_gadget="$1"
  for sysfs in /sys/class/net/*; do
    iface="${sysfs##*/}"
    is_candidate_iface "$iface" || continue
    if [ "$allow_usb_gadget" != "true" ] && is_usb_gadget_iface "$iface"; then
      continue
    fi
    printf '%s\n' "${iface}"
    return 0
  done

  return 1
}

is_candidate_iface() {
  iface="$1"
  [ "${iface}" = "lo" ] && return 1
  [ "${iface}" = "tailscale0" ] && return 1
  [ -d "/sys/class/net/${iface}/device" ] || return 1
  return 0
}

is_usb_gadget_iface() {
  iface="$1"
  driver_path="$(readlink -f "/sys/class/net/${iface}/device/driver" 2>/dev/null || true)"
  driver="${driver_path##*/}"
  case "$driver" in
    rndis_host|cdc_ether|cdc_subset|usbnet)
      return 0
      ;;
  esac
  return 1
}

interface_ipv4() {
  ip -4 -o addr show dev "$1" scope global 2>/dev/null |
    awk 'NR==1 { split($4, addr, "/"); print addr[1] }'
}

iface="$(pick_iface)"
log "chosen interface: ${iface}"
printf '%s\n' "$iface" >/run/klokast-bootstrap-interface

ip link set "$iface" up || true

if [ -z "$(interface_ipv4 "$iface")" ] || ! ip route show default dev "$iface" | grep -q .; then
  log "requesting DHCP on ${iface}"
  dhclient -1 -v "$iface" || dhclient -v "$iface"
fi

attempts=0
while [ "$attempts" -lt 60 ]; do
  ipv4="$(interface_ipv4 "$iface")"
  if [ -n "$ipv4" ]; then
    printf '%s\n' "$ipv4" >/run/klokast-bootstrap-ipv4
    log "dhcp address: ${ipv4}"
    exit 0
  fi
  attempts=$((attempts + 1))
  sleep 1
done

log "fatal: no DHCP IPv4 address on ${iface}"
exit 1
EOF
chmod 0755 config/includes.chroot/usr/local/sbin/klokast-network-bootstrap.sh

cat >config/includes.chroot/usr/local/sbin/klokast-avahi-publish.sh <<'EOF'
#!/bin/sh
set -eu

exec >>/var/log/klokast-avahi-publish.log 2>&1

log() {
  printf '%s %s\n' "$(date -Is)" "$*"
}

address=''
attempts=0
while [ "$attempts" -lt 60 ]; do
  if [ -s /run/klokast-bootstrap-ipv4 ]; then
    address="$(cat /run/klokast-bootstrap-ipv4)"
  fi
  if [ -n "$address" ]; then
    break
  fi
  attempts=$((attempts + 1))
  sleep 1
done

if [ -z "$address" ]; then
  log "fatal: no DHCP IPv4 address available for mDNS publishing"
  exit 1
fi

log "publishing kk.local and klokast.local at ${address}"
avahi-publish-address -R kk.local "$address" &
kk_pid="$!"
avahi-publish-address -R klokast.local "$address" &
klokast_pid="$!"

trap 'kill "$kk_pid" "$klokast_pid" >/dev/null 2>&1 || true' INT TERM EXIT
while :; do
  if ! kill -0 "$kk_pid" 2>/dev/null; then
    wait "$kk_pid"
    exit 1
  fi
  if ! kill -0 "$klokast_pid" 2>/dev/null; then
    wait "$klokast_pid"
    exit 1
  fi
  sleep 5
done
EOF
chmod 0755 config/includes.chroot/usr/local/sbin/klokast-avahi-publish.sh

cat >config/includes.chroot/etc/systemd/system/klokast-network-bootstrap.service <<'EOF'
[Unit]
Description=Klokast DHCP network bootstrap
Wants=systemd-udev-settle.service
After=systemd-udev-settle.service
Before=network-online.target tailscaled.service klokast-avahi-publish.service klokast-onboarding-portal.service

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/klokast-network-bootstrap.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

cat >config/includes.chroot/etc/systemd/system/klokast-avahi-publish.service <<'EOF'
[Unit]
Description=Klokast bootstrap mDNS aliases
Wants=avahi-daemon.service klokast-network-bootstrap.service
After=avahi-daemon.service klokast-network-bootstrap.service

[Service]
Type=simple
ExecStart=/usr/local/sbin/klokast-avahi-publish.sh
Restart=on-failure
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

cat >config/includes.chroot/etc/systemd/system/klokast-onboarding-portal.service <<'EOF'
[Unit]
Description=Klokast onboarding portal
Wants=klokast-network-bootstrap.service tailscaled.service klokast-avahi-publish.service
After=klokast-network-bootstrap.service tailscaled.service klokast-avahi-publish.service

[Service]
Type=simple
ExecStart=/usr/local/sbin/klokast-onboarding-portal --listen :80
Restart=on-failure
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

ln -s ../klokast-network-bootstrap.service \
  config/includes.chroot/etc/systemd/system/multi-user.target.wants/klokast-network-bootstrap.service
ln -s ../klokast-avahi-publish.service \
  config/includes.chroot/etc/systemd/system/multi-user.target.wants/klokast-avahi-publish.service
ln -s ../klokast-onboarding-portal.service \
  config/includes.chroot/etc/systemd/system/multi-user.target.wants/klokast-onboarding-portal.service
ln -s /lib/systemd/system/avahi-daemon.service \
  config/includes.chroot/etc/systemd/system/multi-user.target.wants/avahi-daemon.service
ln -s /lib/systemd/system/tailscaled.service \
  config/includes.chroot/etc/systemd/system/multi-user.target.wants/tailscaled.service

lb build

iso_source="$(find . -maxdepth 1 -type f -name '*.iso' | head -n 1)"
[ -n "$iso_source" ] || {
  printf 'live-build did not produce an ISO\n' >&2
  exit 1
}

iso_target="${output_dir}/${iso_basename}.iso"
cp "$iso_source" "$iso_target"
(
  cd "$output_dir"
  rm -f "${iso_basename}.iso.sha256"
  sha512sum "${iso_basename}.iso" >"${iso_basename}.iso.sha512"
)

xorriso -indev "$iso_target" -find / -maxdepth 0 >/dev/null 2>&1 || true

printf 'Built %s\n' "$iso_target"
