# Household VPN

Dedicated local-presence gateway for the dumb AP design.

## Model

- `<box>-household-vpn`: Alpine app VM in the DMZ, `tag:household-vpn`.
- Household/admin Wi-Fi clients receive this VM as DNS through DHCP.
- Router policy routes non-RFC1918 traffic from household/admin CIDRs through this VM.
- Mihomo runs in TUN mode and provides DNS on the gateway address.
- Split DNS maps local app names to the DMZ local ingress.

Enable in the private platform-resource registry:

```yaml
boxes:
  boxb:
    access:
      available_capabilities: [overlay, local-lan, vpn-egress]
      enabled_capabilities: [overlay, local-lan, vpn-egress]
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

Deploy from the controller as `smith`:

```sh
apps/household-vpn/bin/household-vpnctl deploy \
  --box boxb \
  --resources-registry ~/private/klokast/platform-resources.yml \
  --vpn-config ~/private/klokast/household-vpn.yml \
  --local-domain home.example.com
```
