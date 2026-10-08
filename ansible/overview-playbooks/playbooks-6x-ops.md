# 6x Ops Controller

## `65-vm-ops.yml`

Purpose:

- create one optional `<box>-ops` Alpine VIRT VM on the selected master box;
- enroll it into Tailscale as `tag:ops`;
- converge the local controller split between `smith` and
  `minion`;
- install controller packages and root-owned wrapper executables;
- generate an independent Instance read key and prepare its GitHub checkout;
- clone or fast-forward the public infra repository over credentialless HTTPS
  under `smith`.

Roles:

- `network-bridges`
- `dom0-bridge-runtime`
- `xen-guest-instance`
- `podman-guest-clone`
- `xen-guest-artifacts`
- `xen-guest`
- `bootstrap-python-alpine`
- `vm-base`
- `tailscale-client`
- `vm-tailscale-enrollment`
- `podman-vm-firewall`
- `ops-controller`

Run through:

```sh
ansible/bin/provision-ops-vm --box boxa
```

The wrapper runs `31-vm-router.yml` first to verify that the accepted router
already has the approved `ops` control-network VIF, interface config, and
egress policy. If the accepted release lacks them, verification stops before
`65-vm-ops.yml`. Apply the topology through a qualified router release before
provisioning the ops VM.

Prerequisites:

- dom0, router, and the Alpine VM template already exist on the selected box;
- the current controller has root-only Tailscale OAuth material in
  `/etc/klokast/tailscale-policy.env`;
- Tailnet policy permits controller-to-controller `tag:ops` SSH as
  `smith`.

First-run Instance gate:

- setup generates `/home/smith/.ssh/github-klokast-instance` on the target;
- register the public key as read-only in the private Instance repository;
- set `KLOKAST_INSTANCE_ORIGIN` and rerun controller convergence to clone it;
- install separate OAuth credentials from the operator workstation.

See [independent controller setup](../../doc/platform-deploy.md#independent-controller-setup).

For ordinary Instance Git authoring on the active development controller, use
`68-ops-instance-git.yml`. It prepares a controller-held SSH key and configures
the existing checkout after GitHub access is verified. See
[development Instance Git access](../../doc/platform-syscalls.md#development-instance-git-access)
for registration, invocation and revocation.

Trust boundary:

- `<box>-ops` belongs to the Control TCB.
- app manifests cannot place workloads in the `ops` control zone.
- Cloud runners provide a remote terminal to the controller. They hold no
  Platform private state or controller credentials.
- If the infra agent stays on Hetzner, give that machine `tag:infra`.
  Keep `<box>-ops` as `tag:ops`; Tailnet policy grants only the SSH path from
  `tag:infra` to `tag:ops` as `smith`.

## `67-ops-controller-converge.yml`

Purpose:

- converge an existing `<box>-ops` controller without VM clone, stop,
  reinstall, router, or enrollment phases;
- refresh controller packages, root-owned wrapper executables, sudoers,
  approved-state directories, and the `smith` checkout;
- keep this as the safe baseline refresh before app installation from the
  controller.

Run through:

```sh
ansible/bin/converge-ops-controller --box boxb
```

The controller APK world is an exact allowlist. Unexpected world entries stop
normal convergence so their origin can be reviewed. Add justified packages to
the allowlist, or rerun with `--prune-package-drift` to remove the reviewed
drift and orphaned dependencies.

Use `65-vm-ops.yml` only for creating or rebuilding the ops VM. Use this
playbook when the controller already exists and should stay live.

## Controller root growth

Run `ansible/bin/converge-ops-controller --box BOX --grow-root` as `smith` on
the active controller. Qualify it on the standby before the active controller.
The wrapper holds the existing installation lock. Playbook
`67-ops-root-grow.yml` grows the existing `lv_ops` to at least 100 GiB, waits
for Xen to report that capacity inside the guest, and then grows partition 3
and ext4. It does not shrink, clone, stop or reboot a controller. The original
partition table stays in `/var/lib/klokast/ops-root-grow/partition-table.before`.
If disk or partition size discovery fails, stop and inspect that failure before
retrying. Temporary native partition tools are removed after the operation.
