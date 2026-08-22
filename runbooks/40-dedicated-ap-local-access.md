# Dedicated AP Local Access

Use this runbook when a box starts overlay-only and later receives a dedicated
Wi-Fi AP, such as a GL.iNet Flint 2, for physical-presence access to local apps.

Run Platform operations from the active controller, not from an infra-agent
host:

```sh
tailscale ssh smith@<box>-ops
cd ~/src/klokast/klokast-box
git pull --ff-only
REG=~/private/klokast/platform-resources.yml
```

## Before The AP Arrives

Keep the box overlay-only. The private registry should keep `local-lan` and
`vpn-egress` out of `enabled_capabilities`; stock deployments should explicitly
prohibit the paths that are never intended:

```yaml
boxes:
  boxb:
    access:
      prohibited_capabilities: [rg-lan, direct-egress, direct-ingress]
```

Validate the live baseline:

```sh
ansible/bin/platform-resources --registry "$REG" lint
ansible/bin/platform-resources --registry "$REG" diff
ansible/bin/platform-resources --registry "$REG" verify
ansible/bin/platform-check --box boxb --target router
```

Expected baseline:

- `boxb` compiles as `available=overlay enabled=overlay`.
- `local-presence-control`, `private-service-ingress`, and `file-upload` select
  `overlay`.
- The router has no DHCP range for `household` or `admin`.
- Router nftables has no direct household/admin-to-WAN rule and no old
  `lan-wan-web-egress-tcp`.
- The AP parent interface has no local-presence route; those routes belong on
  `eth1.10` and `eth1.20` only after `local-lan` is enabled.

## AP Physical Setup

Configure the AP as a bridge/dumb AP:

- Disable NAT, firewalling, DHCP, and WAN routing on the AP.
- Use the AP uplink as an 802.1Q trunk toward the box.
- Bridge the household SSID to VLAN 10.
- Bridge the admin/AP-management SSID or management interface to VLAN 20.
- Set the AP management address to `10.10.20.2/24` with gateway `10.10.20.1`,
  or reserve that address through the AP if static management is handled there.

On the box inventory, attach the physical NIC connected to the AP to `br-lan`
by setting the box-specific `dom0_bridge_physical_ports.lan`. The router VM
then sees that trunk as its `eth1`; Klokast creates `eth1.10` for household
clients and `eth1.20` for admin/AP management.

Do not attach the AP to the residential gateway LAN for this design.

## Enable Local Music Control

Edit the private registry on the controller. The minimal local-presence setup
enables only LAN music control; upload remains overlay-only:

```yaml
boxes:
  boxb:
    access:
      available_capabilities: [overlay, local-lan]
      enabled_capabilities: [overlay, local-lan]
      prohibited_capabilities: [rg-lan, direct-egress, direct-ingress]
      policy:
        local-presence-control: local-lan
        private-service-ingress: overlay
        file-upload: overlay
        household-wan-egress: none
        public-ingress: none
apps:
  local-ingress:
    enabled: true
    placement:
      active_master: boxb
    resources: {}
```

Apply and verify platform resources:

```sh
approved_commit="$(git rev-parse HEAD)"
ansible/bin/platform-resources --registry "$REG" lint
ansible/bin/platform-resources --registry "$REG" diff
ansible/bin/platform-resources --registry "$REG" --approved-commit "$approved_commit" apply
ansible/bin/platform-resources --registry "$REG" verify
ansible/bin/platform-check --box boxb --target router
```

Deploy the local ingress and refresh the music backend:

```sh
apps/local-ingress/bin/local-ingressctl deploy \
  --box boxb \
  --resources-registry "$REG" \
  --local-domain home.example.com \
  --tls-cert ~/private/klokast/certs/home.example.com/fullchain.pem \
  --tls-key ~/private/klokast/certs/home.example.com/privkey.pem \
  --approved-commit "$approved_commit"

apps/music/bin/musicctl backend-install \
  --box boxb \
  --resources-registry "$REG"

apps/music/bin/musicctl verify \
  --box boxb \
  --resources-registry "$REG"
```

Client checks from the AP:

- A household client receives `10.10.10.0/24`, gateway `10.10.10.1`.
- An admin/AP-management client receives or uses `10.10.20.0/24`, gateway
  `10.10.20.1`.
- `https://music.<local-domain>` controls playback on the physically local box.
- `https://boxb-music-upload.<tailnet>` remains the upload path.
- Internet egress from AP clients is unavailable unless `vpn-egress` is enabled.

Only remove the old `<box>-music` overlay ingress after local playback has been
verified from the AP. Do not remove `<box>-music-upload`.

## Optional Household VPN Egress

Enable this only after local music works. Extend the registry:

```yaml
boxes:
  boxb:
    access:
      available_capabilities: [overlay, local-lan, vpn-egress]
      enabled_capabilities: [overlay, local-lan, vpn-egress]
      prohibited_capabilities: [rg-lan, direct-egress, direct-ingress]
      policy:
        local-presence-control: local-lan
        private-service-ingress: overlay
        file-upload: overlay
        household-wan-egress: vpn-egress
        public-ingress: none
apps:
  household-vpn:
    enabled: true
    placement:
      active_master: boxb
    app_vms:
      gateway:
        boxb:
          vm_ipv4_address: 192.168.200.40
    resources: {}
```

Apply resources again, then deploy and verify the gateway:

```sh
ansible/bin/platform-resources --registry "$REG" --approved-commit "$approved_commit" apply
ansible/bin/platform-resources --registry "$REG" verify
apps/household-vpn/bin/household-vpnctl deploy \
  --box boxb \
  --resources-registry "$REG" \
  --vpn-config ~/private/klokast/household-vpn.yml \
  --local-domain home.example.com \
  --approved-commit "$approved_commit"
apps/household-vpn/bin/household-vpnctl verify --box boxb --resources-registry "$REG"
ansible/bin/platform-check --box boxb --target router
```

Expected VPN state: household/admin DHCP hands out the VPN VM as DNS, router
policy routes non-RFC1918 household/admin traffic through `192.168.200.40`, and
the router still has no direct household/admin-to-WAN path.

## Rollback

Disable the new capabilities in the registry:

```yaml
boxes:
  boxb:
    access:
      available_capabilities: [overlay]
      enabled_capabilities: [overlay]
      prohibited_capabilities: [rg-lan, direct-egress, direct-ingress]
      policy:
        local-presence-control: overlay
        private-service-ingress: overlay
        file-upload: overlay
        household-wan-egress: none
        public-ingress: none
```

Then run:

```sh
ansible/bin/platform-resources --registry "$REG" --approved-commit "$(git rev-parse HEAD)" apply
ansible/bin/platform-resources --registry "$REG" verify
ansible/bin/platform-check --box boxb --target router
```

After rollback, clients on the AP should not receive Klokast DHCP or reach
local app surfaces through the AP.
