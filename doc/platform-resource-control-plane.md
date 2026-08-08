# Platform Resource Control Plane

The Platform Resource Control Plane is the Git-approved boundary between
application automation and infrastructure security controls.

It is intentionally small: no daemon, no scheduler, and no self-service API.
Apps declare intent in YAML. Operators bind that intent to a deployment in a
private registry. The deployment server compiles the result and only
infra-owned Ansible applies router, VM firewall, Tailnet, and privileged
workload policy.

The trust classification for this boundary is in `doc/architecture.md`.
App manifests are service-plane requests; the private registry, compiler apply
path, Tailnet policy tooling, and builder placement decisions are TCB-owned.
On the target in-platform controller, those TCB-owned actions run as
`smith`; app installation and verification run as `minion`.

## Responsibility Boundary

Infrastructure owns:

- Xen dom0 and VM lifecycle.
- Router and Podman VM firewall policy.
- Tailnet tags, grants, and auth-key wrappers.
- Privileged or rootful workload exceptions.
- Resource apply provenance on managed machines.

Applications own:

- App-local runtime roles, containers, config, and app data.
- App manifests that describe required resources and threat rationale.
- Installers that verify platform resources before changing app services.

Apps must not directly edit router nftables, Podman VM firewall baselines,
Tailnet policy, dom0 Xen definitions, or privileged builder exceptions.

## Files

App-owned manifest:

```text
apps/<app>/platform-resources.yml
```

Deployment-owned private registry:

```text
ops/platform-resources.example.yml
```

Real enabled deployment registries should live in private operator state unless
they contain no sensitive placement, users, or policy data.

The old network-only manifest format and compiler have been removed. App
resource policy is compiled only from `platform-resources.yml`.

## Topology Boundary

App manifests use symbolic Platform zones. They must not contain router
interface names, VM interface names, or IP addresses.
They are schema-allowlisted: unsupported top-level fields, resource sections,
resource fields, and Tailnet grant fields are rejected instead of ignored.

Current app-facing workload zones:

- `bak`: backend service zone for private stateful workloads.
- `dmz`: ingress and internet-facing connector zone.
- `iot`: untrusted local device and sensor integration zone.
- `usr`: user workload zone. `per_user_app_vm` resources are allocated inside
  this zone.

The control-only `ops` zone exists for `<box>-ops`, but it is not an app-facing
zone. App manifests cannot request workloads, firewall resources, or Tailnet
placement there.

Aliases accepted by the compiler:

- `backend` -> `bak`

Current network realms:

- `wan`: upstream internet egress realm.
- `household`: local household/guest Wi-Fi client realm.
- `admin`: local AP-management/client realm.
- `ap-uplink`: untagged WAN-side uplink for router-mode access points that
  NAT or bridge local Wi-Fi clients behind one Ethernet handoff.
- `lan`: transitional alias for the household client realm. New app manifests
  should use `household` or `admin` explicitly.

Access capabilities describe the approved transport class for app-facing
surfaces. They are provider-neutral:

- `ap-uplink`: direct Ethernet handoff to a router-mode local access point.
  This only enables the local router interface, DHCP, and DNS listener; WAN
  egress still requires an explicit `household-wan-egress` policy.
- `local-lan`: direct access on the dedicated AP-local network.
- `rg-lan`: access through the residential-gateway LAN; reserved for
  deployment-specific experiments.
- `overlay`: access through an overlay network such as Tailscale or Nebula.
- `vpn-egress`: outbound WAN access through the shared VPN client.
- `edge-ingress`: inbound access through an edge reverse proxy or tunnel, such
  as Cloudflare.
- `direct-egress`: outbound WAN access directly through the residential gateway
  or router. Stock deployments should prohibit this for local clients.
- `direct-ingress`: inbound WAN access directly through gateway port forwarding
  or a public address. Stock deployments should prohibit this.

`host-local` is not an access capability. Keep same-host binding and loopback
scope in app/runtime configuration unless a future policy need makes it part of
the resource control plane.

App resources may add:

```yaml
access:
  intent: local-presence-control
  capability: local-lan
```

The private registry records observed and approved box capabilities. Discovery
may update `available_capabilities`, but only `enabled_capabilities` and
`policy` are authority for compilation:

```yaml
boxes:
  k002:
    access:
      available_capabilities: [overlay, local-lan]
      enabled_capabilities: [overlay]
      prohibited_capabilities: [rg-lan, direct-egress, direct-ingress]
      policy:
        local-presence-control: overlay
        private-service-ingress: overlay
        file-upload: overlay
        household-wan-egress: none
        public-ingress: none
```

For an AP such as a Flint whose uplink port is cabled to a box-local LAN
bridge, approve the physical port separately from the flow policy:

```yaml
boxes:
  k001:
    access:
      available_capabilities: [overlay, ap-uplink, direct-egress]
      enabled_capabilities: [overlay, ap-uplink, direct-egress]
      prohibited_capabilities: [rg-lan, direct-ingress]
      policy:
        household-wan-egress: direct-egress
    dom0_bridge_ports:
      lan:
        - eth2
    dhcp_reservations:
      - hostname: flint2
        mac: "02:00:00:00:00:01"
        ipv4_address: "10.10.30.2"
```

This gives the AP and its bridged clients DHCP leases from `ap-uplink`, permits
DNS/DHCP to `k001-router`, and opens only source-scoped web/DNS/NTP egress
from that AP uplink to `wan`. It does not expose Platform service zones unless
app-owned `realm_to_zone_tcp` resources also compile.

The default for boxes without `access` is overlay-only with no explicit
prohibited list. AP-only resources such as LAN music control, local HTTPS
ingress, and household VPN egress compile only after the controller-approved
registry enables the matching capability and policy. A capability listed in
`prohibited_capabilities` must not appear in `available_capabilities`,
`enabled_capabilities`, or any selected `policy` value.

The trusted topology source is `ansible/inventory/group_vars/all.yml` under
`platform_zones` and `platform_network_realms`. The compiler resolves app
zones through that data into router interfaces, VM roles, and IP addresses.

## Manifest Shape

```yaml
schema_version: 1
app: nextcloud
risk_class: private_service
default_isolation: zone_vm_rootless_podman
placement_mode: active_passive
resources:
  compute:
    - id: backend
      type: podman_workload
      zone: bak
      privilege: rootless
      lifecycle: steady
    - id: per-user-runtime
      type: per_user_app_vm
      zone: usr
      vm_name_prefix: usr
      tailnet_tag_prefix: user-shell
      memory_mb: 4096
      vcpus: 4
      lv_size: 50G
    - id: torrent
      type: app_vm
      zone: dmz
      hostname_suffix: torrent
      tailnet_tag_default: tag:torrent
      guest_os: alpine
      container_runtime: none
    - id: local-audio-endpoint
      type: managed_iot_device
      zone: iot
      hostname_suffix: streamer
      management_tag_default: tag:streamer
  network:
    - id: backend-http-upstream
      type: interzone_tcp
      required: true
      rationale: "Why the narrow flow is required."
      from_zone: dmz
      to_zone: bak
      ports: [8080]
    - id: per-user-storage
      type: interzone_tcp
      required: true
      rationale: "Why each per-user VM needs one matching backend port."
      from_zone: usr
      to_zone: bak
      ports_from_user: storage_port
    - id: cloudflare-tunnel-egress
      type: wan_egress
      required: false
      enabled_by_default: false
      rationale: "Why this outbound connector is required."
      from_zone: dmz
      tcp_ports: [7844]
      udp_ports: [7844]
    - id: household-https
      type: realm_to_zone_tcp
      required: true
      rationale: "Why LAN-presence clients need this narrow ingress."
      from_realm: household
      to_zone: dmz
      ports: [443]
    - id: snapcast-stream
      type: device_to_zone_tcp
      required: true
      device: local-audio-endpoint
      to_zone: bak
      ports: [1704]
    - id: audio-endpoint-updates
      type: device_wan_egress
      required: true
      device: local-audio-endpoint
      tcp_ports: [80, 443]
      udp_ports: [123, 41641]
  tailnet:
    - id: private-ingress
      required: true
      access:
        intent: private-service-ingress
        capability: overlay
      hostname_default: next
      tag_default: tag:nextcloud
      grants:
        - src: group:family
          tcp_ports: [443]
```

Current network resource types:

- `interzone_tcp`: renders router forward policy and destination VM input
  policy from `from_zone`, `to_zone`, and explicit TCP ports. For per-user
  app VMs, `ports_from_user` can select a validated integer port field from
  each rendered registry user entry.
- `realm_to_zone_tcp`: renders router forward policy and destination VM input
  policy from a non-workload network realm, such as `household` or `admin`, to
  one Platform zone VM. Use this for local-presence ingress, not for internet
  exposure.
- `wan_egress`: renders source-scoped router WAN egress policy.
- `zone_to_device_tcp`: renders router forward policy from one Platform zone
  VM to one TCB-registered managed IoT device.
- `device_to_zone_tcp`: renders router forward policy from one managed IoT
  device to one Platform zone VM and the destination VM input rule.
- `device_wan_egress`: renders source-scoped router WAN egress policy for one
  managed IoT device.

Current compute resource types:

- `managed_iot_device`: declares that an app needs a low-trust external device
  in the `iot` segment. Concrete MAC address, IPv4 address, and optional
  hostname live in the private platform-resource registry under
  `apps.<app>.devices.<resource>.<box>`. The compiler renders router DHCP
  reservations from that registry state.
- `per_user_app_vm`: expands private registry users into Xen PVH app VM specs,
  dynamic Ansible inventory, active/passive Tailscale identities, and
  source-scoped firewall resources. The app manifest declares the need and VM
  naming prefix; the private registry owns concrete user identities, IP
  addresses, and per-user backend ports.
- `app_vm`: expands private `apps.<app>.app_vms.<resource>.<box>` registry
  entries into one Alpine Xen PVH app VM per selected box. Use it for a
  dedicated appliance VM such as `<box>-torrent`; it is not a shared zone VM or
  a container host.

Tailnet resources are selected by `required`, `enabled_by_default`, explicit
registry resource flags, and optional access-capability policy. Grants may use
`ports` or `tcp_ports` for TCP access, plus `udp_ports` for UDP ports or port
ranges such as `60000-61000`. Applying
Tailnet policy logic is public in
`klokast-ops/tailscale/policy.hujson.j2`; family identities are rendered into
`~/private/klokast/tailscale-policy.hujson` and applied through the
`ts-policy-*` wrappers. The compiler also emits
`tailnet_policy_resources` for per-user app VMs so the TCB operator can mirror
the tag owner, exact-login grants, and exact-login SSH rules into the Tailnet
policy. App-VM enrollment uses TCB-owned one-off key minting: the wrapper
validates the requested hostname and full tag set, such as
`tag:vm,tag:user-shell-alice`, before issuing a single-use auth key. `tag:vm`
self-ownership is not part of the target policy.

### Direct UDP underlay for container identities

Most containers inherit their Podman VM's Tailscale identity and need no
separate underlay declaration. A container that runs its own `tailscaled` must
not be enabled until its Tailnet resource can declare both its hosting compute
resource and one stable UDP listen port, conceptually:

```yaml
underlay:
  compute: private-ingress-runtime
  udp_listen_port: 41644
```

The runtime must start `tailscaled` with that exact nonzero `--port`. The
Platform firewall must then permit, from the exact hosting VM address and
interface, that UDP **source** port to WAN peers and UDP **destination** port
`3478` for STUN. Destination port `41641` is not a substitute for the source
port rule. The host identity reserves `41641`; the canonical and candidate
airrunners reserve `41642` and `41643`. App-owned identities behind managed
hosts use unique ports from `41644` through `41999` so NAT can preserve each
identity's source port.

This is a required design contract for new or re-enabled app-owned Tailnet
identities, but the current manifest compiler does not yet accept `underlay`.
Keep such an application disabled until compiler validation, router rendering,
runtime propagation, and direct-path verification are implemented together.
Do not add the illustrative field to a current manifest before that support
lands because the schema correctly rejects unknown fields.

App-owned Tailnet identities must not use reserved Platform control tags such
as `tag:infra`, `tag:infra-agent`, `tag:ops`, `tag:dom0`, `tag:router`,
`tag:bootstrap`, `tag:oob`, or `tag:vm`. Those tags identify controllers,
AI runners, bootstrap/OOB paths, or compiler-managed VM enrollment, not app
resources.

## Registry Shape

```yaml
schema_version: 1
apps:
  nextcloud:
    enabled: true
    placement:
      active_master: k001
      passive_backup: k002
    ingress_mode: tailscale
    resources:
      cloudflare-tunnel-egress: false

  bootstrap-iso-debian:
    enabled: true
    placement:
      builder_box: k001
    ephemeral:
      privileged_approval: true
      expires_at: "2026-06-01T00:00:00Z"
      cleanup_required: true

  user-shell:
    enabled: true
    placement:
      active_master: k001
      passive_backup: ""
    resources: {}
    users:
      - slug: alice
        tailscale_login: alice@example.com
        system_user: alice
        vm_ipv4_address: 192.168.175.20

  music:
    enabled: true
    placement:
      boxes: [k001, k002]
    devices:
      local-audio-endpoint:
        k001:
          mac: b8:27:eb:00:00:01
          ipv4_address: 192.168.150.60
          hostname: k001-streamer
        k002:
          mac: b8:27:eb:00:00:02
          ipv4_address: 192.168.150.60
          hostname: k002-streamer

  torrent:
    enabled: true
    placement:
      active_master: k002
    app_vms:
      torrent:
        k002:
          vm_ipv4_address: 192.168.200.30
```

The registry must use explicit box names, optional resource flags, tags, and
rendered user logins. The compiler rejects app manifests that try to declare raw
router interfaces, VM interfaces, IP addresses, broad wildcard-style
resources, or expired privileged builder approvals.

Box-local physical bridge ports belong in the private registry, not in app
manifests. Use this only for physical downstream ports that intentionally extend
a Platform bridge to external hardware, for example a Raspberry Pi audio
endpoint on the IoT segment:

```yaml
boxes:
  k002:
    dom0_bridge_ports:
      iot:
        - eth3
```

The keys are symbolic Platform bridge keys such as `iot`, `lan`, `bak`, `dmz`,
`usr`, or `ops`; values are dom0 Linux interface names.

App manifests default to `placement_mode: active_passive`, which requires
`active_master` and `passive_backup` when enabled. Small apps that are
intentionally single-site may declare `placement_mode: single_box`; those
require only `active_master` and must not set `passive_backup`. App
active/passive placement describes service deployment only; it is unrelated to
the approved set of AI runners.

## Workflow

Preview and validate:

```sh
ansible/bin/platform-resources --registry path/to/platform-resources.yml lint
ansible/bin/platform-resources --registry path/to/platform-resources.yml show
ansible/bin/platform-resources --registry path/to/platform-resources.yml diff
ansible/bin/platform-resources \
  --registry path/to/platform-resources.yml \
  --app user-shell \
  --inventory-output /tmp/user-shell-app-vms.yml \
  inventory
```

Apply from an approved, pushed commit:

```sh
git pull --ff-only
git status --short --branch
approved_commit="$(git rev-parse HEAD)"
ansible/bin/platform-resources \
  --registry path/to/platform-resources.yml \
  --approved-commit "$approved_commit" \
  apply
```

`apply` refuses to run unless the worktree is clean, `HEAD` equals the supplied
approved commit, and the local branch is synchronized with its upstream.
`apply` compiles the full registry every time. With no `--app`, it converges
all managed resource snippets. With `--app`, it still validates and resolves
ownership against the full registry, but it mutates only resource keys owned by
the selected app. Shared snippets remain when another enabled app still owns
the same normalized resource key.
Before app firewall resources are rendered, `apply` also runs the supported
router convergence playbook for the selected boxes. That keeps dom0 bridge
state, router Xen VIFs, and router interface policy aligned with current
Platform topology before app-specific nftables includes are applied.

Verify before app operations:

```sh
ansible/bin/platform-resources --registry path/to/platform-resources.yml --app nextcloud verify
```

Registry-backed app installers must run the app-scoped `verify` command before
installing, promoting, or starting services. Split installers that run as
`minion` must validate the infra-exported grant instead; they must not read
the private registry.

For split controller installs, `smith` can export an app-scoped grant
after apply/verify:

```sh
ansible/bin/platform-resources \
  --registry path/to/platform-resources.yml \
  --app immich \
  --approved-commit "$approved_commit" \
  grant
```

The grant JSON is sanitized approved state for `minion`. It must not
contain private registry contents, OAuth material, SSH keys, unrelated apps, or
raw rendered firewall rule arrays. Firewall access is exposed only as
app-owned effective resource metadata. Verifiers consume compiler-approved
resource state; they do not trust or authorize an AI runner directly.

## App Lifecycle

Steady and persistent apps may set a durable runtime intent in the private
deployment registry:

```yaml
apps:
  nextcloud-v2:
    enabled: true
    runtime_state: stopped
    placement:
      active_master: k001
```

When omitted, `runtime_state` defaults to `running` for enabled apps.
`enabled: false` keeps its existing meaning: cleanup/remove resource ownership,
not a stopped-but-retained runtime. Use:

```sh
ansible/bin/platform-app list
ansible/bin/platform-app status nextcloud-v2
ansible/bin/platform-app stop nextcloud-v2
ansible/bin/platform-app start nextcloud-v2
ansible/bin/platform-app verify nextcloud-v2
```

The MacBook wrapper dispatches the same operations to the active controller as
`kk app ...`. `status` is a human summary and does not repair. `verify` is a
strict conformance check and also does not repair. `remove` preserves durable
data by default. `destroy` is the data-wiping path and requires explicit
`--wipe-data --yes`.

Deprovision an app by keeping its placement explicit and setting
`enabled: false` in the registry:

```yaml
schema_version: 1
apps:
  nextcloud:
    enabled: false
    placement:
      active_master: k001
      passive_backup: k002
    ingress_mode: tailscale
    resources:
      cloudflare-tunnel-egress: true
```

An app-scoped `apply` with that registry removes only the disabled app's
ownership claims on the selected boxes. Exact duplicate shareable claims keep
one effective snippet with the remaining owners. `verify` compares the desired
ledger, last-applied metadata, keyed target snippets, and live nftables rule
identities.
Disabled apps compile app-resource cleanup scopes so the controller can visit
the target hosts that may contain stale keyed snippets; the target reconciler
still decides removal from snippet owner metadata.

## Apply Provenance

On hosts where platform firewall resources are applied, Ansible writes:

```text
/etc/klokast/platform-resources/desired.json
/etc/klokast/platform-resources/last-applied.json
```

The metadata records the approved commit, registry SHA256, compiler name and
version, and inventory host. These files are operational state and are not
secrets.

Effective nftables snippets are materialized under host-local include
directories keyed by normalized resource identity:

```text
/etc/klokast/app-resources/router-forward.d/*.nft
/etc/klokast/app-resources/vm-input.d/*.nft
```

Legacy aggregate include files remain as empty compatibility anchors:

```text
/etc/klokast/app-resources/router-forward.nft
/etc/klokast/app-resources/vm-input.nft
```

They are not rendered rule outputs. New Platform resource rules are materialized
only through keyed files in the `.d/` include directories.

The desired state includes app-VM metadata consumed by `ansible/bin/platform-map`.
That lets `platform-map validate` treat compiler-managed dynamic domains such
as `k001-usr-alice` as expected while still reporting app VMs that have no
compiled or persisted desired state.

## App Notes

Nextcloud remains a private service on backend and DMZ zone VMs. Its boundary
is the private Tailnet ingress plus router/backend firewall policy.

Per-user app VMs isolate higher-risk user-scoped workloads. The compiler
expands private deployment users into app VM names, Xen guest specs, IPs,
Tailnet hostnames/tags, and router rules inside the `usr` zone.

Torrent is a dedicated Alpine app VM in the DMZ. The VM runs the VPN helper and
qBittorrent directly; completed downloads are pulled by backend/media apps.

The Debian bootstrap ISO builder is a privileged exception. It must be
declared as an ephemeral resource with explicit approval, an expiry time, and
cleanup required. It is not a steady-state application.
