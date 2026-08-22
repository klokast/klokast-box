# Platform Map

## 70. `70-platform-map.yml`

Purpose:
- Collect read-only facts for the current Platform state.
- Feed `ansible/bin/platform-map refresh`, which writes the ignored summary
  file `.run/platform-map/current.json`.

The playbook targets:
- `dom0` hosts for storage, LVM, Xen domain, and RAM capacity facts.
- `podman_vms` hosts for rootless Podman runtime, container, pod, volume,
  filesystem, and RAM facts.

The CLI also collects local deployment-server facts, Tailscale status, Hetzner
metadata when available, and NanoKVM/OOB status from the controller.

Typical usage:

```sh
ansible/bin/platform-map refresh --boxes boxa,boxb
ansible/bin/platform-map show
ansible/bin/platform-map validate
ansible-inventory -i ansible/bin/platform-map --list
```

Local operator facts that cannot be inferred automatically, such as which box a
NanoKVM is physically connected to, can be kept in the ignored file
`.run/platform-map/overrides.yml`:

```yaml
oob_devices:
  oob:
    connected_box: boxa
boxes:
  boxa:
    site: site-a
  boxb:
    site: site-b
```

The summary file and host artifacts may contain internal hostnames, Tailscale
addresses, provider metadata, and capacity data. They are runtime state and must
not be committed.

## 72. Platform check playbooks

Purpose:
- Run read-only steady-state health checks for infrastructure targets.
- Feed `ansible/bin/platform-check --box <box> [--target <target>]`.

Targets:
- `all`: run map validation, dom0, router, Podman VM checks, registry-backed
  resource verification when a private registry is available, and ops checks
  when the box has an expected or present `<box>-ops` VM.
- `dom0`: `72-platform-check-dom0.yml`, using `dom0-health-verification`.
  The dom0 check includes the NanoKVM recovery invariant: `neo` must have a
  usable local console password hash, `root` must be locked, and `wheel` must
  retain passwordless `doas`.
- `router`: `72-platform-check-router.yml`, using `router-verification`.
- `podman`: `72-platform-check-podman.yml`, using
  `podman-vm-health-verification` for `bak`, `dmz`, and `iot`.
- `ops`: `72-platform-check-ops.yml`, using `ops-controller-verification`.
- `map`: run `platform-map refresh/show/validate` under the
  `.run/platform-check-<box>/platform-map/` artifact directory.
- `resources`: run `platform-resources verify` against the private registry.

The dom0 target verifies:
- dom0 hostname, Alpine OS identity, diskless persistence, EFI/apkovl state,
  GRUB loader handoff, and Tailscale `tag:dom0` readiness.
- Xen runtime, dom0 data mount, required core guest LVs, boot artifacts,
  autostart configs, and live guest memory/vCPU declarations.
- The PVH-only invariant: no QEMU packages, executables, processes, HVM config,
  or Xen `device_model` config are allowed on dom0.
- Bridge runtime: expected bridges exist, the default route uses `br-wan`, and
  the WAN bridge has an attached physical port.

The router target verifies hostname, service state, route tables, dnsmasq,
nftables config/live rules, app-resource includes, and Tailscale readiness.

The Podman target verifies hostname, Alpine identity, Tailscale `tag:vm`,
rootless Podman/cgroup/network backend, VM firewall baseline, routes, memory,
filesystems, and current Podman container/pod/volume inventory. It does not
start probe containers.

The ops target verifies hostname, Alpine identity, Tailscale `tag:ops`, local
automation users, controller tools, checked-out repo presence, wrapper
executability, and root-secret file ownership/modes without reading secret
contents.

Steady-state Platform VMs, including `<box>-ops`, use Tailscale SSH for
operator and Ansible access. Raw OpenSSH on TCP port 22 is intentionally stopped
after Tailscale SSH is active. When `platform-check --target ops` runs on the
active `<box>-ops` controller itself, the wrapper checks that host with a local
Ansible connection instead of probing port 22.

Typical usage on the Ansible controller:

```sh
ansible/bin/platform-check --box boxa
ansible/bin/platform-check --box boxa --target all
ansible/bin/platform-check --box boxa --target dom0
ansible/bin/platform-check --box boxa --target router
ansible/bin/platform-check --box boxa --target podman
ansible/bin/platform-check --box boxa --target map --remote-scope dom0
ansible/bin/platform-check --box boxa --target resources \
  --resources-registry ~/private/klokast/platform-resources.yml
ansible/bin/platform-check --box boxa --target dom0 -- -vvv
```

Typical usage from `vultr-ops` as `agent`:

```sh
ansible/bin/platform-check-remote --box boxa
ansible/bin/platform-check-remote --box boxa --target all
ansible/bin/platform-check-remote --box boxa --target dom0
ansible/bin/platform-check-remote --no-pull --box boxa --target dom0 -- --syntax-check
```

`--resources-registry auto` is the default. On the active controller it uses
`~/private/klokast/platform-resources.yml` when present; use
`--resources-registry none` to disable registry-backed expectations.

`platform-map` storage inspection is read-only. `lsblk` and `findmnt` improve
diagnostics when present, but dom0 checks also use `/proc/mounts`, `blkid`, and
LVM output. Missing `lsblk` or `findmnt` is not by itself a failed health check
when the managed layout confidence remains `exact`.

Future `platform-check` targets should keep the same split:
- `platform-map` discovers and summarizes state.
- `platform-check` runs live read-only assertions and exits nonzero on broken
  infrastructure invariants.
