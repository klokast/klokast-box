# Platform Map

`ansible/bin/platform-map` discovers the current Platform state from the
deployment server. It writes a concise runtime summary to
`.run/platform-map/current.json`. That file is local operational state and must
not be committed.

Use it before answering questions about current boxes, Tailscale enrollment,
NanoKVM status, storage capacity, RAM pressure, Xen guests, or Podman workloads.
When inspecting dom0/router/VM runtime state, run the checked-in controller
workflow or Ansible path from the active controller; do not probe Platform hosts
directly from the infra-agent as `root`, because Tailnet policy is expected to
reject that access.

## CLI

Typical use:

```sh
ansible/bin/platform-map refresh --boxes boxa,boxb
ansible/bin/platform-map refresh \
  --boxes boxa \
  --resources-registry /home/codex/private/klokast/platform-resources.yml \
  --remote-scope dom0
ansible/bin/platform-map show
ansible/bin/platform-map validate
ansible/bin/platform-map export-observation \
  --file .run/platform-map/current.json >~/private/klokast/observation.json
ansible-inventory -i ansible/bin/platform-map --list
```

Subcommands:

- `refresh`: gather local, Tailscale, provider, remote Platform facts, and
  private resource expectations, then write `.run/platform-map/current.json`.
- `show`: print a short human summary from the current JSON file.
- `validate`: print warnings and critical findings from the current JSON file.
  Use `--strict` when warnings should return a nonzero exit code.
- `inventory`: emit Ansible dynamic inventory JSON. The script also accepts
  Ansible's direct `--list` and `--host <name>` calls.
- `export-observation`: read an existing mapper summary and write the narrow,
  redacted Observation v1 JSON document to standard output. It does not refresh
  facts or change state.

Create observation files with an owner-only umask because hostnames and
runtime status are private operational data:

```sh
umask 077
observation_file="$(mktemp ~/private/klokast/observation.XXXXXX)"
ansible/bin/platform-map export-observation \
  --file .run/platform-map/current.json >"$observation_file"
chmod 0600 "$observation_file"
```

Remove the temporary file after its read-only use. All mapper and observation
timestamps are UTC. The Platform timezone stays `Etc/UTC`; do not add a
timezone option to this interface.

Important options:

- `--boxes boxa,boxb`: select boxes explicitly. If omitted, the tool tries to
  infer box names from Tailscale peers. It loads the checked-in
  `cloud-providers.json` catalog and excludes supported `<cloud>-ops` hosts
  from box discovery.
- `--skip-ansible`: refresh only local, Tailscale, and provider facts.
- `--remote-scope full|dom0|podman|none`: choose which remote facts to collect.
  Default `full` collects dom0/Xen/storage and Podman VM facts. Use `dom0` for
  fast Xen and app-VM readiness checks.
  Use `podman` for container-only checks. `none` is equivalent to
  `--skip-ansible`.
- `--overrides .run/platform-map/overrides.yml`: load optional local facts,
  such as the NanoKVM physical connection when the operator wants to record
  where it is currently plugged in.
- `--probe-oob-ssh`: try a noninteractive SSH probe against online OOB devices.
- `--resources-registry path/to/platform-resources.yml`: compile private
  Platform resources during refresh so dynamic app VMs are expected even before
  collected host metadata is available.

Scope examples:

```sh
# Full inventory, capacity, Xen, and Podman check.
ansible/bin/platform-map refresh --boxes boxa,boxb --remote-scope full

# Fast app-VM readiness check: registry + Tailscale + dom0 Xen/LV.
ansible/bin/platform-map refresh \
  --boxes boxa \
  --resources-registry ~/private/klokast/platform-resources.yml \
  --remote-scope dom0

# Fast local/Tailscale/registry-only check.
ansible/bin/platform-map refresh \
  --boxes boxa \
  --resources-registry ~/private/klokast/platform-resources.yml \
  --remote-scope none
```

When `--remote-scope dom0` is used, Podman VM facts are intentionally not
refreshed. Podman container findings may be stale or absent until the next
`--remote-scope full` or `--remote-scope podman` refresh.

## Runtime Files

- `.run/platform-map/current.json`: concise summary used by `show`,
  `validate`, and dynamic inventory.
- `.run/platform-map/hosts/*.json`: per-host Ansible artifacts produced by
  `ansible/playbooks/70-platform-map.yml`.
- `.run/platform-map/inventory/*.yml`: generated temporary inventory for the
  selected boxes.
- `.run/platform-map/overrides.yml`: optional local override file.

Example override:

```yaml
oob_devices:
  oob:
    connected_box: boxa
boxes:
  boxa:
    site: site-a
    expect_ops: true
  boxb:
    site: site-b
```

## Current JSON Template

Top-level fields:

- `schema_version`: integer version for the summary shape.
- `generated_at`: UTC timestamp for the refresh.
- `source_host`: hostname of the machine that ran the CLI.
- `artifact_dir`: runtime artifact directory used for host facts and generated
  inventory.
- `selected_boxes`: box names included in the refresh.
- `deployment_server`: local controller facts.
- `tailnet`: summarized Tailscale state.
- `oob_devices`: NanoKVM or other out-of-band devices found in Tailscale.
- `boxes`: per-box Platform facts keyed by box name.
- `findings`: global warnings and critical findings.
- `warnings`: reserved list for non-finding warnings.

`deployment_server` contains:

- `hostname`, `user`, `uid`.
- `os`: local operating system ID, name, and version.
- `tailscale`: local Tailscale identity if available.
- `provider`: cloud or hardware metadata when detectable, including provider,
  availability zone, region, location label, city, country, public IPv4, DMI
  vendor/product, and confidence.

`tailnet` contains:

- `available`: whether `tailscale status --json` succeeded.
- `magicdns_suffix`: detected tailnet DNS suffix.
- `peer_count` and `online_count`.
- `self`: deployment server Tailscale peer summary.
- `peers`: summarized peers with names, DNS name, hostname, Tailscale IPs,
  tags, relay, online state, and last-seen timestamp.

`oob_devices[]` contains:

- `name`, `hostname`, `dns_name`, `tags`, `online`.
- `connected_box`: physical box connection if known from overrides; empty is
  normal because the NanoKVM can be moved between boxes.
- `connection_source` and `connection_confidence`.
- `ssh_probe`: whether SSH probing was attempted and whether it succeeded.

Each `boxes.<box>` object contains:

- `name`: box name.
- `overrides`: local override facts for that box.
- `expected_hosts`: expected Tailscale machine names for dom0 and VMs.
- `machines`: Tailscale presence and online state by role: `dom0`, `router`,
  `bak`, `dmz`, `iot`, plus optional `agent` and `ops` when present, running,
  or explicitly expected.
- `dom0`: host OS, reachability, SSD/storage, and Xen facts.
- `podman`: Podman VM facts by role: `bak`, `dmz`, `iot`, and `agent`.
- `app_vms`: compiler-managed dynamic app VMs keyed by Tailnet hostname, for
  example `boxa-usr-alice`.
- Compiler-managed box-scoped Tailnet resources and managed IoT devices, such
  as `boxb-music` or `boxb-audio`, are included in `expected_hosts` when
  compiled private Platform resources or collected
  `/etc/klokast/platform-resources/desired.json` declare them.
- `capacity`: box-level capacity summaries.
- `findings`: warnings and critical findings scoped to the box.

`boxes.<box>.dom0.storage` contains:

- `managed_layout`: detected target SSD, EFI partition, LVM partition, VG name,
  VG size/free bytes, confidence, source, and warnings.
- `volume_groups`: VG names, sizes, free bytes, PV count, and LV count.
- `logical_volumes`: LV name, path, size bytes, and VG name.
- `partitions`: partition path, parent disk, size, type, filesystem, labels,
  UUIDs, PARTUUIDs, and mountpoints.
- `disks`: disk facts when available from the target host.

`boxes.<box>.dom0.xen` contains:

- `available`: whether Xen tooling was usable.
- `info`: parsed `xl info` facts, including CPU and memory data.
- `domains`: parsed `xl list` domains with ID, memory MiB, vCPUs, state, and
  runtime.
- `expected_guests`: expected VM memory by role.
- `config_files`: Xen guest config files found on dom0.
- `autostart_files`: Xen guest autostart files or links found separately on
  dom0.

`boxes.<box>.app_vms.<hostname>` contains:

- `app`, `resource`, `user_slug`, `site_role`, `zone`, and `vm_ipv4_address`.
- `guest_name`: Xen domain name, for example `usr-alice`.
- `expected_tags`: compiled Tailnet tag set.
- `tailscale`: presence, online state, DNS name, actual tags, missing tags, and
  whether tags match compiled expectations.
- `xen`: whether the guest is running and its live memory/vCPU state.

`boxes.<box>.podman.<role>` contains:

- `reachable`, `host`, `runner_user`, `os`.
- `runtime`: Podman availability, rootless state, cgroup version, and network
  backend.
- `containers`, `pods`, `volumes`.
- `filesystems`: mounted filesystem source, target, type, size, used, and free
  bytes.
- `memory`: parsed `free -b` memory and swap data.

`boxes.<box>.capacity.memory` contains:

- `host_total_mib`, `xen_free_mib`, `dom0_live_memory_mib`.
- `live_domain_memory_mib` and `live_guest_memory_mib`.
- `expected_guest_memory_mib`.
- `pressure`: `ok`, `warning`, or `critical`.

## Findings

Findings use:

- `severity`: `warning` or `critical`.
- `scope`: affected box, host, OOB device, or global scope.
- `code`: stable machine-readable finding code.
- `message`: short human-readable explanation.

The tool reports issues such as missing or offline expected Tailscale machines,
unexpected box-prefixed peers, bootstrap identities still online, unmanaged Xen
domains, app-VM Tailnet tag mismatches, running VMs missing from Tailscale,
unmanaged Podman containers, and RAM pressure. Unknown NanoKVM physical
connection is informational, not a finding, because the device is movable.
Offline NanoKVM is an operator-access warning only when physical access is
available and the device can be plugged into the target box on demand.
Compiler-managed app VMs such as `boxa-usr-alice`, box-scoped Tailnet
resources such as `boxb-music`, and managed IoT devices such as `boxb-audio`
are expected only when compiled private Platform resources or collected
`/etc/klokast/platform-resources/desired.json` declare them.
The fixed `<box>-usr` and `<box>-ops` VMs are known optional VMs. They are
not reported as unexpected box-prefixed peers, but they are expected and
validated only when present, running, or explicitly marked in overrides with
`expect_usr: true` or `expect_ops: true`.

The `<box>-iot` VM is a standard future workload substrate, but it currently
hosts no app workloads in this deployment. App-scoped diagnostics should limit
checks to roles that carry declared resources; an idle or unreachable `iot`
role is not evidence for unrelated app failures unless that app targets `iot`.
When private or applied resource state declares a shared guest `stopped`, the
map records that intent and does not report its absent Xen domain or offline
Tailnet identity as drift. Controller-private registry intent takes precedence
over older desired-state metadata collected from a target.

Dom0 storage inspection is read-only. `lsblk` and `findmnt` are optional
diagnostic inputs; the role falls back to `/proc/mounts`, `blkid`, and LVM
reports. Missing `lsblk` or `findmnt` alone is not a reboot blocker when the
managed layout confidence remains `exact`.

## Security

The summary includes internal hostnames, Tailscale DNS names and IPs, provider
metadata, storage layout, capacity data, and container names. Keep everything
under `.run/platform-map/` out of git and avoid sharing it outside the
operator context.
