# Dedicated AP local access

Use this procedure to add a dedicated Wi-Fi AP to a box for local application
access. The AP bridges traffic to the box; the Platform router owns routing,
DHCP, and firewall policy. Run Platform operations as `smith` on the active
controller from `~/src/klokast/klokast-box`.

## Overlay baseline

Before the AP is available, keep `boxes.<box>.connectivity` set to `overlay`
only in Instance. Validate and synchronize desired state through the
[Instance workflow](../doc/klokast-instance-specification.md).

From the controller, inspect the baseline:

```sh
ansible/bin/platform-resources lint
ansible/bin/platform-resources diff
ansible/bin/platform-resources verify
ansible/bin/platform-check --box boxb --target router
```

The overlay-only baseline has no household/admin DHCP ranges, AP local-presence
routes, or household/admin internet egress. Application manifests select their
own declared flows.

## Physical setup

- Configure the AP as a bridge. Disable its NAT, DHCP, and WAN routing.
- Use an 802.1Q trunk toward the box.
- Bridge the household SSID to VLAN 10.
- Bridge the admin/AP-management SSID or management interface to VLAN 20.
- Use `10.10.20.2/24` for AP management, with gateway `10.10.20.1`, under the
  deployment's approved address plan.

Declare the physical AP NIC under `boxes.<box>.substrate.bridge-ports.lan` in
Instance. The router sees the trunk as `eth1` and uses `eth1.10` for household
clients and `eth1.20` for admin/AP management. The AP connects to the box's
approved LAN bridge.

## Application support

The current Instance schema accepts `local-ap-uplink` for a physical AP uplink.
That declaration does not supply the `local-lan` capability required by Local
Ingress or the `vpn-egress` capability required by Household VPN. The current
resource projection keeps those application capabilities unavailable.

Do not use this physical setup as an application installation procedure.
Installation must stop until Instance and its resource projection support the
required application capabilities and inputs. Do not add unsupported
connectivity values or application fields to Instance.

See [Local Ingress](../apps/local-ingress/README.md),
[Household VPN](../apps/household-vpn/README.md), and
[Instance desired state](../doc/klokast-instance-specification.md).
