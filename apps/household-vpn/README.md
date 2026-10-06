# Household VPN

Dedicated local-presence gateway for the dumb AP design.

## Model

- `<box>-household-vpn`: Alpine app VM in the DMZ, `tag:household-vpn`.
- Household/admin Wi-Fi clients receive this VM as DNS through DHCP.
- Router policy routes non-RFC1918 traffic from household/admin CIDRs through this VM.
- Mihomo runs in TUN mode and provides DNS on the gateway address.
- Split DNS maps local app names to the DMZ local ingress.

Declare the application present in the private Instance. This fragment shows
its `apps` entry; keep the other required Instance fields:

```json
{
  "apps": {
    "household-vpn": {
      "desired-state": "present",
      "placement": {
        "mode": "single-box",
        "box": "boxb"
      }
    }
  }
}
```

Validate and publish desired-state changes through the
[Instance workflow](../../doc/klokast-instance-specification.md). The controller
tools consume the validated resource view derived from Instance.
The app manifest requires the `vpn-egress` capability and a gateway address.
The current Instance schema cannot declare these required application inputs.
Installation must stop until Instance and its resource projection support them.
Do not add unsupported connectivity values or application fields.

After the required resource support is available, deploy from the controller
as `smith`:

```sh
apps/household-vpn/bin/household-vpnctl deploy \
  --box boxb \
  --resources-registry ~/private/klokast/platform-resources.yml \
  --vpn-config ~/private/klokast/household-vpn.yml \
  --local-domain home.example.com
```
