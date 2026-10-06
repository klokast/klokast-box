# Tailscale automation

Each `<box>-ops` controller owns separate Tailscale OAuth material and
root-owned wrappers. Run Platform operations on the active controller as `smith`. Follow the authority
boundaries in [Architecture](../../doc/architecture.md) and
[Platform lifecycle](../../doc/platform-lifecycle.md).

## Policy

- Keep topology and grants in `policy.hujson.j2`; keep family identities and
  deployment selections in the private Instance.
- Render with `ansible/bin/render-tailscale-policy --instance
  ~/private/klokast/instance/klokast-instance.json --output
  ~/private/klokast/tailscale-policy.hujson` on the controller.
- The installed pull and validate wrappers accept only the controller-private
  policy path. Pull supplies comparison evidence; it does not change desired state.
- Apply development network changes with `ansible/bin/platform-apply network
  --box BOX`. See [controller operations](../../doc/platform-syscalls.md).
- `ts-policy-mutate-internal` is an internal root Apply helper. Do not invoke
  it directly or add it to an account's privilege rules.

## Wrapper installation and secrets

Source is under `klokast-ops/tailscale/bin/`. The controller Ansible role
installs root-owned copies under `/usr/local/sbin/` and manages narrow privilege
rules. Do not execute repository source with root privileges.

After a reviewed wrapper change, use the supported
[controller convergence](../../doc/platform-deploy.md) workflow. For OAuth
setup or rotation, read the [credential setup runbook](../runbooks/40-tailscale-wrapper-setup-policy.md).
Credentials stay root-only under `/etc/klokast/`. Do not copy them to cloud
runners, application workloads, or public inventory.

## Enrollment

Use the owning Ansible workflow to enroll a target. The enrollment command
runs on the target machine. Installed `ts-authkey-*` wrappers validate purpose,
hostname, and the complete tag set, then mint short-lived, single-use keys
through `ts-authkey-mint`. Use `--check-config` with the same hostname and tags
as the enrollment call to verify the credential's permitted scope.

Use `ts-authkey-vm` for router, shared-zone, and declared app VMs. Dedicated
Torrent and household VPN VMs use their declared app tag with `tag:vm`.
Application ingress identities use their purpose-specific wrappers. See the
[CLI index](../../doc/cli-tools.md#tailscale-root-wrappers) for available tools.

The controller uses `tag:ops`. Cloud AI runners use `tag:infra` and the declared
SSH path to the controller as `smith`. In-Platform AI runner containers use
`tag:airunner`. Apps cannot grant themselves these identities.

## Device lifecycle

- List through the installed `ts-devices-list` wrapper.
- Use the stale-device Ansible role to classify exact-name matches and prove
  offline state. Preview before an authorized deletion.
- `ts-device-delete-stale` re-fetches and verifies ID, hostname, tag, and offline
  state before deletion. Unknown state and online machines are preserved.
- For whole-box removal, use `ansible/bin/decommission-box --box BOX` from the
  controller instead of individual wrapper calls.

See [device lifecycle](../runbooks/41-tailscale-wrapper-devices.md) for the
supported operation path.
