# 6x Ops Controller

## `65-vm-ops.yml`

Purpose:

- create one optional `<box>-ops` Alpine VIRT VM on the selected master box;
- enroll it into Tailscale as `tag:ops`;
- converge the local controller split between `smith` and
  `minion`;
- install controller packages and root-owned wrapper executables;
- migrate private controller state and root-only Tailscale OAuth files from
  the current controller, without copying legacy reusable auth-key files;
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
- `ops-private-state-transfer`

Run through:

```sh
ansible/bin/provision-ops-vm --box boxa
```

The wrapper runs `31-vm-router.yml` first so the existing router VM receives
the `ops` control-network VIF, interface config, and egress policy before
`65-vm-ops.yml` enrolls the controller.

Prerequisites:

- dom0, router, and the Alpine VM template already exist on the selected box;
- the current controller has root-only Tailscale OAuth material in
  `/etc/klokast/tailscale-policy.env`;
- `/etc/klokast/tailscale-devices.env` exists root-only on the current
  controller so device lifecycle wrappers can be migrated;
- Tailnet policy permits controller-to-controller `tag:ops` SSH as
  `smith`.

First-run GitHub gate:

- the playbook creates
  `/home/smith/.ssh/github-klokast-box` on `<box>-ops`;
- when the key is freshly generated, the playbook stops immediately and prints
  the public key without probing GitHub first;
- the operator registers only the public key from the laptop in
  `klokast/klokast-box` deploy keys, then reruns the wrapper.

Trust boundary:

- `<box>-ops` belongs to the Control TCB.
- app manifests cannot place workloads in the `ops` control zone.
- Hetzner `codex` may remain an approved Codex/OpenAI runner and break-glass
  bootstrap controller.
- If the infra agent stays on Hetzner, give that machine `tag:infra`.
  Keep `<box>-ops` as `tag:ops`; Tailnet policy grants only the SSH path from
  `tag:infra` to `tag:ops` as `smith`.

## `66-ops-controller-secrets.yml`

Purpose:

- refresh root-only Tailscale OAuth files on an existing `<box>-ops`
  controller without rerunning VM clone, router, enrollment, or repository
  convergence phases;
- copy only the files listed in `ops_controller_root_secret_files` from the
  current controller to the target controller as `root:root` mode `0600`;
- verify the target auth-key and device-lifecycle Tailscale wrappers can use
  the refreshed credentials.

Run through:

```sh
ansible/bin/refresh-ops-secrets --box boxa
```

## `67-ops-controller-converge.yml`

Purpose:

- converge an existing `<box>-ops` controller without VM clone, stop,
  reinstall, router, enrollment, or private-state-transfer phases;
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
