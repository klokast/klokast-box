# Exit appliance

`<box>-exit` is a disposable Alpine PVH VM in the DMZ. It is a Tailscale exit
node: a granted Tailnet member can send web traffic through the box, so that
web sites see the box's public IPv4 address. It has no SSH, interactive login,
proxy, DNS service, stored data, or runtime package management. See the
[exit appliance authority model](../../doc/architecture.md#exit-appliance).

Declare `exit-node` present in the private Instance, select its boxes, and
provide a free DMZ address for each box:

```json
{
  "desired-state": "present",
  "placement": {"mode": "multi-box", "boxes": ["boxb"]},
  "app-vms": {"exit": {"boxb": {"vm-ipv4-address": "192.168.200.50"}}}
}
```

Run on the selected box's own controller as `smith`, from the public
repository. The box's controller must also be the active controller:

```sh
ansible/bin/platform-resources --app exit-node --approved-commit "$(git rev-parse HEAD)" apply
ansible/bin/platform-exit-appliance preflight --box boxb
ansible/bin/platform-exit-appliance build --box boxb
ansible/bin/platform-exit-appliance verify --box boxb
```

`build` constructs and qualifies an image through the common verified APK
inputs and isolated Xen build transport, then replaces the appliance and its
Tailscale identity. Run it again to update the appliance. There is no rollback.
`health` is read-only and is what `kk status BOX-exit` shows. It checks that the
box dom0 runs the guest and starts it at boot, and checks the Tailnet identity,
control connection and key expiry. The appliance has no login, so it does not
send traffic through the exit. `verify` reads the Tailscale API: exactly one `<box>-exit` identity, with only
`tag:exit`, connected to Tailscale. `retire` stops the VM, removes its disk and
revokes its identity. Then set `desired-state` to `absent` in Instance and
apply the resources again.

## Use

On the MacBook, select **Exit Node → `<box>-exit`** in the Tailscale menu, or:

```sh
tailscale set --exit-node=boxb-exit
curl -4 https://ifconfig.me
tailscale set --exit-node=
```

Keep **Use Tailscale DNS settings** enabled. Name queries then go to the exit
appliance, which resolves them through the box router. Only HTTP, HTTPS and
QUIC (UDP 443) pass, and only to public addresses. Other protocols, for example
SSH or mail, fail while the exit node is selected. Clients have no IPv6 exit.
