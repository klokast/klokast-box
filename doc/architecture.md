# Standard architecture of the boxes
Each box of the Platform is one mini-PC that implements the same 4-layers architecture.

## Top-level control flow

The Platform has two main control flows:

1. the desired-state and Apply flow;
2. the artifact build and distribution flow.

These flows keep desired state, observed state, human approval, build authority, and runtime state separate.

### Desired-state and Apply flow

The public `klokast-box` repository contains the Platform implementation. The private instance repository contains the desired state for one installation.

The private `klokast.lock.json` selects one approved `klokast-box` engine commit. The active controller builds the `klokast` binary from this commit. It uses the sealed, networkless `platform-builder`.

The architectural control flow is:

```text
public klokast-box engine
        +
private Instance and engine lock
        |
        v
sealed klokast binary
        +
Instance source receipt
        +
Authority State
        +
Controller Toolchain receipt
        +
current Observation
        |
        v
immutable Plan
        |
        v
human review and signed Apply intent
        |
        v
verified Platform Apply
        |
        v
compiler + Ansible + app reconcilers
        |
        v
Platform runtime
        |
        v
new Observation
        |
        +----> input to the next Plan
```

The Instance is desired state. An Observation is observed state. An Observation is evidence only. It must not become desired state.

The sealed `klokast` binary creates the Plan from the approved inputs and evidence. The Plan defines the exact operations that an Apply can perform.

The human reviews the Apply intent and signs it on the trusted workstation. The approval applies to one exact Plan.

Before an Apply, the root executor verifies the required evidence. This includes the active-controller state, Plan, sealed engine, controller toolchain, source receipts, Observation, signature, expiry, and single-use nonce.

If an input changes or expires, the controller must create new evidence and a new Plan. An old approval does not authorize a different Plan.

Only the active controller can execute the Apply. The diagram shows the control-loop design. It does not mean that one general Apply executor can perform all infrastructure and app changes.

The current signed Apply path accepts closed, versioned operations:

- Plan v8 verifies Instance v1 authority. It does not authorize general runtime changes.
- Plan v9 controls legacy-input retirement. After retirement, its `verify` phase proves that the old inputs stay absent.
- The direct overlay IPv6 repair uses a separate, restricted signed operation bound to Plan v8.

Historical migration contracts remain for recovery, tests, and historical evidence. They are not a normal authority fallback. Compiler, Ansible, and app workflows have their own fixed scopes. A new instance commit or a signature alone does not authorize an unsupported operation.

After the change, the mapper and verifier can create a new Observation. This closes the control loop without changing the desired-state authority.

For the normative rules, see [Klokast Instance Specification v1](klokast-instance-specification.md) and [Secret Authority](secret-authority.md).

### Artifact build and distribution flow

Deployable artifacts have a separate build and distribution flow.

The control flow is:

```text
public source
        +
digest-pinned upstream inputs
        |
        v
artifact-specific build boundary
        |
        v
built artifact
        +
provenance
        +
digest or checksum lock
        |
        v
artifact store or controlled distribution path
        |
        v
target verification
        |
        v
target load or installation
```

The build boundary depends on the artifact type. There is not one build environment for all artifacts.

The deployable `klokast` Go binary uses the sealed, networkless Xen `platform-builder`. The controller injects the approved source, vendored Go modules, and the digest-pinned Go build image. The builder has no network interface.

The current app OCI image workflow uses `platform-image-build` on the active controller as `smith`. It uses digest-pinned upstream images. It builds with Podman and Buildah chroot isolation, then creates an OCI archive. It does not use the sealed Xen Go builder.

The OCI flow is:

```text
digest-pinned upstream image + app build context
        -> controller-local build
        -> local image ID + OCI archive
        -> images.lock.yml: digest + archive_sha256
        -> transfer to target
        -> archive SHA-256 check
        -> target-local podman load
```

The lock's built-image `digest` field records the local Podman image ID. The archive checksum identifies the transferred bytes. These are different checks. The current loader checks the archive and image presence. It does not independently compare the target image ID with the locked image ID. If the archive checksum is absent, the loader can compute it from the local archive. That fallback checks transfer integrity only. The strict `verify` command requires the archive checksum in the lock.

The bootstrap ISO has another build boundary. Its checked-in workflow runs Debian `live-build` in a temporary, rootful privileged Podman container on a backend VM. It requires a privileged approval with an expiry and cleanup requirement. The container builds a generic ISO without the box name or bootstrap key. It sends the ISO and SHA-512 checksum directly to NanoKVM, then stops and is removed.

The build workflow records immutable artifact identity. Depending on the artifact type, this can include an image digest, an archive SHA-256 checksum, a build receipt, or another approved provenance record.

The artifact store is a distribution service. It is not an authority. An artifact does not become trusted because the store contains it.

A distribution path can also transfer an artifact directly from the active controller to its target. The trust rule is the same for both distribution methods.

The target must verify the artifact against approved provenance before it uses the artifact. For the current OCI workflow, the target verifies the OCI archive SHA-256 checksum before `podman load`.

The target must not use a mutable tag, store contents, or an unverified downloaded file as the source of artifact authority.

See the [sealed Go builder](secure-builder.md), [OCI workflow](../ansible/bin/platform-image-build), and [bootstrap ISO builder](../apps/bootstrap-iso-debian/builder-container.md) for the implemented checks and cleanup rules.

## Layer 1: baremetal Host & Xen hypervisor
- Tailnet hostname: `<box>-dom0`; Tailscale tag: `tag:dom0`
- Host Operating System:
  - baremetal diskless Alpine Linux Xen dom0
  - pure PVH hypervisor
  - QEMU is not installed
  - persistence over reboot via `lbu commit`
- Storage:
  - the SSD EFI partition carries GRUB, kernel, initramfs, modloop, APK boot repository, runtime APK cache, and apkovl persistence
  - LVM holds the guest logical volumes
- Diskless persistence boundary:
  - `.apkovl` is for small dom0 runtime state only: `/etc`, selected admin home paths, and Tailscale state.
  - Mounted persistent storage must stay outside `lbu`. In particular, `/mnt/dom0_data` is the LVM-backed dom0 data volume for Xen images and related artifacts, so `/etc/apk/protected_paths.d/lbu.list` must exclude it as `-mnt/dom0_data` and must never include `+mnt/dom0_data`.
- Networking:
  - Xen bridge host
  - local `neo` account for administration and recovery
  - NanoKVM recovery requires local console login as `neo` with a per-box password known to the human operator. Tailscale SSH is the normal remote management path, but it is not sufficient as the only dom0 access path. The `root` password must be locked; blank root console access is forbidden.
- Package policy:
  - `/etc/apk/world` is the exact steady-state dom0 package allowlist.
  - `openssl` is a boot requirement. Alpine's diskless initramfs adds it so that `modloop` can verify its signature.
  - Phase 20 installs the complete runtime and recovery package set together and removes all other world entries.
  - Image acquisition and guest disk maintenance packages can exist in RAM only during a checked-in maintenance block. The block must restore the exact world and remove the RAM-only APK unlock before any `lbu commit`.
  - The APK pre-commit hook rejects package transactions unless the reviewed workflow creates the RAM-only unlock. This is a guardrail, not a security boundary, because the controller still has root authority on dom0.

## Layer 2: Virtual Machines
- Tailscale tag: `tag:vm`
- On each host, Xen runs several Virtual Machines.
- The Shared zone VMs (`<box>-<zone>`: `<box>-bak`, `<box>-dmz`, and `<box>-iot`) run rootless Podman, based on the `Alpine Linux VIRT` Operating System.
- Specific apps or services can require additional dedicated VMs: promote a workload to a dedicated app VM only when the shared zone VM cannot safely provide the needed boundary, such as untrusted code execution, rootful Docker, PCI/USB passthrough, VPN leak containment, privileged host networking, or materially different lifecycle. Dedicated app VMs still belong to a zone/security policy; they do not replace the zone model.

### Guest construction and runtime state

[Controller-managed VM updates](platform-updates.md) adds inspection of base
packages, a closed shared-VM update policy contract, signed policy activation,
and local pause/resume controls. The isolated template builder, retained-data
adoption, signed replacement executor, and local recovery path remain required before
automatic replacement can be enabled. The
[Instance specification](klokast-instance-specification.md#shared-vm-update-intent)
owns state placement and release-assignment rules; the update runbook does not
create another desired-state source.

The steady-state guest lifecycle is:

```text
versioned Alpine template
        -> clone onto dom0 LVM
        -> attach disks and network resources
        -> boot
        -> finalize hostname, machine identity, network, and access
```

The current shared Podman workflow builds a generic Alpine VIRT template on dom0. The template has no guest-specific Tailnet state or SSH host keys. Each clone clears copied machine state before enrollment. Steady-state convergence locks root and removes first-contact SSH after the approved management path works. Other guest profiles use their checked-in image workflows.

`platform-guest` applies and verifies compiled runtime intent for existing shared guests. A stopped guest retains its disk, Xen definition, boot artifacts, and Tailnet registration. Its autostart link is removed. The compiler rejects a running app that requires a stopped shared zone.

The old `platform-guest start` and `stop` commands write legacy registry intent.
The verified registry guard blocks these writes after Instance v1 adoption.
The current schema records shared-guest intent in
`boxes.<box>.substrate.shared-guests.<role>.runtime-state`. This is declared
intent, not observed status or permission for an unsupported execution action.
An instance change must use a supported human publication and execution path.
Do not create a legacy registry to bypass that boundary.

See [shared guest provisioning](../ansible/overview-playbooks/playbooks-4x-podman.md) and [platform-guest](../ansible/bin/platform-guest).

### `<box>-router`
- Site router and firewall VM. It enforces firewall accesses for the application containers of the Platform and the other VMs.
- runs Alpine Linux, `nftables` firewall rules, native routing tables, `dnsmasq`, and `dhcpcd`.
- default inter-zone choke point for LAN, DMZ, backend, IoT, user workloads, and WAN
- public application ingress is expected to come through Cloudflare Tunnel from the DMZ, not router DNAT to service VMs

###  `<box>-bak`
For backend services: hosts application containers that are trusted and not internet facing, for example databases and Gitlab CI/CD pipes.
The VM uses `tag:vm`. An optional separate container identity uses its approved service tags.

Role:
- backend Podman host
- private service workloads
- no public exposure by default
- rootless Podman workloads under `neo`

Administration:
- `neo` account
- privilege escalation via `doas`
- steady-state remote access should use the current management plane without
  coupling service design to a specific provider

### `<box>-dmz`
For public/edge-facing connectors: hosts application containers that are internet facing, for example the frontends for NextCloud and Wordpress.
The VM uses `tag:vm`. An optional separate container identity uses its approved service tags.

Role:
- DMZ Podman host
- future public-facing services or reverse proxies
- rootless Podman workloads under `neo`

Administration:
- `neo` account
- privilege escalation via `doas`
- steady-state remote access should use the current management plane without
  coupling service design to a specific provider

### `<box>-iot`
For Internet of Things middleware: hosts application containers that manage untrusted hardware in the LAN networks of the Platform, for example IOT hub, weather sensors, printer, and video surveillance webcams.
The VM uses `tag:vm`. An optional separate container identity uses its approved service tags.

Role:
- IoT Podman host
- isolated middleware and device-facing workloads
- no direct WAN exposure by default
- rootless Podman workloads under `neo`

Administration:
- `neo` account
- privilege escalation via `doas`
- steady-state remote access should use the current management plane without
  coupling service design to a specific provider

### `<box>-ops`
box infrastructure and control plane applications.

Role:
- trusted infrastructure automation VM for the selected master box
- holds private Platform state and controller-side credentials under `smith` account
- installs TCB tooling such as Ansible, Tailscale wrappers, and policy tooling
- not an app host and not a public service ingress point

Administration:
- `smith` has infrastructure authority and root escalation
- `minion` is the less-trusted app automation account
- `oracle` is the unprivileged read-only mapping and verification account

### Dedicated VPN and torrent guests

`<box>-household-vpn` is the checked-in household and admin client VPN gateway in the DMZ. Router policy sends selected client WAN traffic through this guest. Mihomo provides the TUN path and DNS. `<box>-torrent` is a separate DMZ app VM with qBittorrent and its own Mihomo VPN path. UID-scoped firewall rules prevent qBittorrent from using another egress path.

These are dedicated app resources. A generic `<box>-vpn` is not the current app naming model. See [Household VPN](../apps/household-vpn/README.md) and [Torrent](../apps/torrent/README.md).

### `<box>-usr-<slug>`
For VMs of type `per_user_app_vm`, based on Ubuntu, Debian, or another approved guest profile.

Role:
- per-user app VM in the `usr` zone
- private workloads for one internal user
- no public exposure by default
- separate routed user workload zone; no backend/LAN/IoT access unless declared
- per-user app VMs use the box-scoped Tailnet hostname
  `<box>-usr-<slug>.<tailnet>` on every box

Administration:
- `neo` account
- privilege escalation via `sudo` on Debian app VMs
- steady-state remote access should use the current management plane without coupling service design to a specific provider

## Layer 3: Services

### App manifests

An app manifest is app-owned, public/reviewable intent. It says what the app needs, without deployment-private details: for example: “this app needs a backend in bak, ingress in dmz, port 8080 from dmz to bak, and optionally a Tailnet identity.”

It can declare:

- compute needs: rootless Podman workload, per_user_app_vm, app_vm, managed_iot_device
- network flows: zone-to-zone, LAN-to-zone, device-to-zone, WAN egress
- Tailnet needs: hostname defaults, tag defaults, grants
- artifact needs
- privileged builder needs with rationale

It should not contain:

- concrete box placement
- IP addresses
- router interface names
- real MAC addresses
- real user identities unless intentionally public
- secrets
- provider tokens
- broad firewall rules

### Instance authority and derived registry

The private `klokast-instance.json` and `klokast.lock.json` are the desired-state authority. Public app manifests declare needs. The private instance binds supported needs to boxes, app placement, features, and retained datasets.

The resource compiler consumes a derived registry in its existing internal format. It does not read the instance JSON directly. The sealed `klokast registry` resolver performs the translation. The root source-status workflow verifies the adopted authority and engine before `platform-registry read` returns this view to normal consumers.

The canonical old `platform-resources.yml` path selects this verified source. It does not require a live YAML file. An alternate registry cannot replace adopted authority. Legacy registry writers must fail after adoption.

Fields such as `active_master`, `passive_backup`, `runtime_state`, and compiler access capabilities belong to the internal registry representation. They are not additional Instance v1 inputs. In particular, Instance v1 must not contain observed `running` or `stopped` status. Compatibility files and migration interfaces remain only for explicit recovery, tests, and historical artifacts.

See [Instance projection rules](klokast-instance-specification.md#projection-compatibility-and-observation) and the [verified registry reader](../ansible/bin/platform-registry). Older registry examples in detailed runbooks must not become a second desired-state authority.

### Resources
- By default, services run as rootless containers inside the shared zone VMs and inherit firewall rules from these VMs.
- New rootless containers inherit the VM zone policy from their shared zone VMs.
- Shared-zone app workloads use rootless containers by default. Durable data uses declared volumes or approved persistent storage.
- Checked-in exceptions include native qBittorrent and Mihomo in dedicated app VMs, and native nginx for local ingress on `<box>-dmz`. These exceptions do not permit arbitrary host services. Their app manifests and Platform workflows define placement and network scope.
- Dedicated app VMs contain workloads that need a separate kernel or privileged networking. A native service on a shared VM shares that VM's kernel and compromise boundary; local ingress does not provide a separate VM boundary.
- The service VMs `bak`, `dmz`, `iot` and `usr` are service substrate, not credential-bearing Control TCB.
- `<cloud>-ops` and `<box>-ops` are only for infrastructure and control plane roles. App manifests cannot select them for placement of their service workloads.

- App manifests request the following resource types. The private instance selects supported placement:

  - shared service zones: `bak`, `dmz`, `iot`.

  - `per_user_app_vm`: dedicated VM for one internal user. Used when a user needs a full Xen VM, not just a container. This is a VM for one user only. That VM can host containers. It can also be other OS than Alpine, such as Ubuntu or Debian.
  Host name: `<box>-usr-<slug>`.
  Tailscale tags: `tag:vm`, plus app-specific Tailnet tags when a separate ACL boundary is needed.

  - `app_vm`: one dedicated VM for one app or appliance. For example: `<box>-torrent`. Typical use cases: untrusted or risky service, rootful Docker or non-standard runtime, VPN leak containment, privileged networking, PCI/USB passthrough, different lifecycle or OS profile

  - `managed_iot_device`: an external physical device that the Platform manages or grants network access to. For example: Raspberry Pi audio endpoint, printer, camera bridge, local sensor gateway. This is not a VM and not a container. The app manifest declares the need symbolically. Platform-owned resolution supplies the approved device and network bindings to the compiler.

  - `artifacts`: deployable outputs from a build process, that apps or infrastructure consume. For example: OCI image archives, container images, VM disk images, bootstrap ISOs, checksums, digest lock files.

  - artifact registry/store: "artifacts are not services by themselves. They are deployable outputs."

  - privileged builders: temporary or controlled build environments allowed to perform higher-risk build actions. For example: bootstrap ISO builder, Debian app-VM image builder, OCI image builder needing elevated Podman/buildah access.

### Linux accounts

- `neo`, controlled by the human:
  - on an active `<cloud>-ops`, `<box>-ops`, and `<box>-ops-airunner`: privileged (via `doas` or `sudo`) to manage runner and controller account access, and for recovery.
  - on dom0 and service VMs: standard administration and recovery account as defined by each machine role.

- `agent`:
  - on `<cloud>-ops` or the `<box>-ops-airunner` container: runs the AI coding agent and owns its public implementation repository, runner credentials, sessions, and tools. It is a persistent Control TCB authority because it can modify Platform code and use the approved remote-terminal path to `smith`. It must not clone the private instance repository or store Platform private state, infrastructure-provider credentials, controller deploy keys, broker secrets, or private registries.

- `smith`:
  - on `<box>-ops`: privileged via `doas` or `sudo`; this is the main Control TCB Unix account, reached by `agent` through the approved remote-terminal path:
    - owns private Platform state,
    - runs Ansible playbooks, runs compiler apply / current platform-resources apply,
    - mints identities through root wrappers,
    - applies Tailnet policy, controls dom0/router/firewall changes,
    - invokes broker/builder actions.

- `minion`, controlled by the deterministic automation that consumes approved intent & operates app lifecycle:
  - less-trusted app automation account on `<box>-ops`.
  - install/start/verify apps using sanitized grants under approved state.
  - must not read the private instance or full registry view, OAuth material, deploy keys, or broker state. It must not change infrastructure policy.

- `oracle`:
  - unprivileged read-only mapping and verification account on `<box>-ops`.
  - consumes sanitized desired state and checked read-only facts.
  - has no general Ansible SSH key, private instance, full registry view, deploy key, broker access, remote administration credential, or privilege escalation.

### Infrastructure Services

Infrastructure services manage the Platform. During bootstrap, the controller
and coding runner can first run on `<cloud>-ops`. After the first box is ready,
the controller moves to `<box>-ops` and the runner can move to
`<box>-ops-airunner`. An approved `<cloud>-ops` runner can remain online after
its controller authority and private state are removed.
One box only is the "Active Controller".

- `airunner`:
  - AI coding agent remote terminal (CLI interface) to the coding agent (e.g. OpenAI Codex CLI), coding agent api key, archived discussions, wrappers.
  - It has persistent code-authoring authority and an approved controller terminal path. It is part of the TCB, but it is not the credential custodian.
  - Users are `neo` and `agent`. Tailnet policy allows approved runner identities to connect to `<box>-ops` as `smith`.
  - Runs in `<box>-ops-airunner` or `<cloud>-ops`. Each runtime has its own Tailnet identity and no controller-private mounts or private state. The controller-container runtime shares the `<box>-ops` kernel and compromise domain.
  - Instance Specification v1 lists exact runner identities in priority order. It requires `tag:airunner` on `<box>-ops-airunner` and `tag:infra` on `<cloud>-ops`. Every listed runner remains desired and online. The order does not implement automatic failover.
  - More than one runner can be active, but the approved set should stay small because each runner can modify the Git repository and control the Platform.
  - Ideally, `airunner` and active `controller` are located on the same box to reduce latency. However, this might not be practical, for example if the active controller is located in a country where the connection to the LLM server is not stable.
  - Required packages: codex, npm, mosh, git
  - Notable files present in the `agent` account:
    - `~/.codex/auth.json`
    - `~/.codex/config.toml`
    - `~/.codex/installation_id`
    - `~/.ssh/config`
    - `~/.ssh/github-klokast-box`
    - `~/.ssh/github-klokast-box.pub`
    - `~/.ssh/known_hosts`
    - Codex sessions, history, and logs
    - Codex caches, plugins and temp files
    - OpenAI API env files

- `klokast` (Go contract and planning engine):
  - `klokast` is a versioned Go CLI. It is the contract and planning engine for the Platform.
  - It reads the private Instance Specification and other approved evidence. It validates these inputs and produces deterministic derived output.
  - The main commands are:
    - `init`: create a private instance from input values and the canonical instance template.
    - `check`: validate an instance and its engine lock.
    - `plan`: validate the required authority and evidence, and create a deployment Plan.
    - `doctor`: compare the desired instance state with an Observation and report findings.
    - `registry`: create a read-only resource registry view from the instance.
    - `inventory`: create a read-only inventory view from the instance.
    - `version`: report the identity of the engine binary.
  - Each deployable binary contains its engine repository, Git ref, and Git commit. The engine uses this identity when it validates the instance and produces derived output.
  - The binary contains the public data that is part of its contract:
    - Instance Specification schemas;
    - the canonical instance template;
    - the cloud-provider catalog;
    - public application resource manifests.
  - The private instance owns deployment-specific desired state. The `klokast` engine does not own this state.
  - An Observation contains observed state. The `klokast` engine can use an Observation as evidence, but the Observation does not become desired state.
  - A Plan does not change the Platform. The active controller performs approved changes from verified authority.
  - The `klokast` engine and the resource compiler have different roles. The engine validates Platform contracts and produces Plans and derived views. The resource compiler renders approved resource state into concrete infrastructure configuration.
  - Deployable `klokast` binaries are built only through the sealed, networkless `platform-builder` profile.

- `compiler` (= "resources compiler" or " Infrastructure reconciler"): versioned CLI tooling in `<box>-ops` that renders resources. It enforces approved state within a fixed scope. It doesn't independently choose placement, ownership, policy, or privilege.
  - The broader app/infra contract is the Platform Resource Control Plane described in `doc/platform-resource-control-plane.md`.
  - Account `smith` applies infrastructure security controls from an approved Git commit.
    1. App manifests declare required resources: compute, network, Tailnet, artifact, and privileged-builder needs.
    The private instance binds supported needs to concrete boxes and optional resources. The verified resolver produces the compiler registry view.
    2. Compiler renders app-owned manifests, from infra-owned topology data into router and VM nftables rules. (Command: `platform-resources apply` or `compiler apply`).
    3. App installers verify those rules but do not mutate the router or Podman VM firewall baselines. Less-trusted app automation, `minion` account, verifies the applied controls before changing services.
  - The compiler combines public app manifests, the verified derived registry, and Platform-owned topology. It does not choose a new desired-state authority.
  - The compiler produces:
    - router firewall policy
    - VM firewall policy
    - app VM inventory/metadata
    - Tailnet policy resources/grants to mirror
    - approved app-scoped grants for `minion` account
    - provenance: approved commit, registry hash, compiler metadata

- `mapper` and `verifier`:
  - `oracle` is the restricted read-only role for sanitized mapping and verification. It has no general remote administration credential.
  - The current controller discovery entry point is `ansible/bin/platform-map`. Remote fact collection uses checked-in controller and Ansible workflows.
  - The mapper discovers Tailnet, cloud-provider, Xen, storage, and Podman state. It combines these facts with verified desired-state views to report findings and produce dynamic Ansible inventory.
  - Each refresh removes the previous summary and per-host facts before collection. An unreachable host has no new facts. Old facts must not replace missing evidence.
  - The private summary is `.run/platform-map/current.json`. `export-observation` reads that summary and emits a narrow, redacted Observation. It does not refresh facts.
  - Verifiers check desired state against evidence and report health or conformance. Observation remains evidence only. It cannot select placement, grant permissions, or change desired state.
  - See [Platform Map](platform-map.md) for collection scopes, output files, and findings.

- `controller` (Ansible controller): versioned CLI tooling in `<box>-ops`.
Only the active controller may mutate the Platform. The active controller is the state-changing execution locus: it runs infrastructure playbooks as user `smith` and scoped application workflows as user `minion`. It is also the credential custodian.
Controller HA is active/standby, not distributed authority: only the active controller may mutate the Platform. Fence the old active controller before promotion, and reseed recreatable provider authority instead of replicating it to standby controllers.
Tailscale tag: `tag:ops`.

Controller HA separates recovery evidence from reusable credentials. The standby has its own controller tooling and machine identity. State synchronization copies selected private operational state and approved grants. It also copies Authority State history, Plans, toolchain receipts, execution and recovery records, rollback material, audit logs, and consumed nonce records. These records preserve the accepted history and replay refusals. They do not grant active-controller authority.

Normal HA synchronization does not copy root-held Tailscale OAuth material, GitHub App private keys, or Cloudflare tunnel tokens. It excludes the private-instance read key, controller GitHub keys, and the active machine's Tailscale state. Standby sanitation removes the listed credentials. Promotion requires fencing the former active controller and establishing one active controller. The human must reseed required credentials from the trusted workstation before credential-backed operations.

State synchronization is not a complete controller backup. Its fixed copy list does not include every source or engine receipt directory. Recovery must use retained evidence and the checked-in source, engine, and credential recovery procedures. Initial controller migration is a separate approved workflow and can transfer credentials; it is not standby replication.

See [controller HA implementation](../ansible/bin/ops-controller-ha) and [controller recovery](platform-deploy.md#controller-recovery).

The proposed separation of Platform-wide authorization from constrained
site-local execution is specified in [Site Executors](site-executor.md). It does not change
the current single-controller execution invariant until that document's
deployment and security gates are implemented and validated.

- `broker` (credentials broker): root-owned, versioned, deterministic wrappers on the active `<box>-ops`. It validates narrow actions, uses provider/app credentials without revealing them, enforces the active-controller guard, and appends audit records.

- `builder` (artifact build workflows):
  - The build boundary depends on the artifact. The [artifact flow](#artifact-build-and-distribution-flow) describes the Go, OCI, and bootstrap ISO paths.
  - A Xen build guest uses the name `<box>-builder-<purpose>-<id>`. This naming rule does not mean that every current artifact build runs in a Xen guest.
  - The `klokast` Go CLI uses the stricter `platform-builder` profile: a
    sealed Alpine 3.23 template, a unique writable LVM snapshot, no VIF or
    Tailnet identity, and rootless Podman with networking disabled. The active
    controller injects only a Git archive of the synchronized approved commit,
    vendored modules, and a digest-pinned Go OCI archive while the guest is
    stopped. The guest boots to run the build, then stops before result collection.
    The guest is the authoritative build locus. The controller and airunner
    do not compile deployable CLI binaries. The controller also verifies the canonical repository and safe
    upstream branch. The guest binds that repository, ref, and commit into the
    binary and its receipt, and the controller verifies the receipt values.
  - The resulting sealed binary is the `klokast` contract and planning engine described above.

- `store` (design role): rootless blob-distribution containers in `<box>-bak` on the active- and standby-controller boxes. A general replicated store workflow is not implemented in this repository. Current builds can use direct archive transfer. The store design is an untrusted distribution layer, outside the TCB:
  - content-addressed, immutable blobs;
  - no signing keys;
  - no authority to update approved digest locks;
  - consumers must validate immutable content (digests or signatures) against provenance held by the TCB (from the approved controller state), not from the store itself
  - artifacts replicated between the store instances.
  - The persistent volume belongs to the VM/storage substrate, not to the disposable container.
  - The store is not the only source for artifacts required to reconstruct the Platform. Bootstrap and controller images also have an offline or off-platform recovery copy.

- `encrypter` and `uploader` (proposed off-platform archiver): create encrypted backup and archive bundles and send them to an off-platform depot. This repository does not implement a general encrypter/uploader workflow. The intended boundary is:
  - `encrypter`: versioned CLI in `<box>-ops`.
  - `uploader`: rootless container in `<box>-bak` that receives ciphertext only. It has:
    - no plaintext or encryption private key;
    - a spool containing only authenticated encrypted bundles;
    - egress restricted to the selected storage endpoint;
    - an append-only or write-only cloud credential;
    - no inbound network service;
    - idempotent object names and atomic completion markers.
  - An inactive uploader replica is on the standby-controller box for failover.

### User Services
Applications installed and activated by the user. Public manifests define supported resource needs. The private instance selects supported placement, features, and retained data. App-specific workflows define runtime, backup, and promotion behavior.

Examples of User Services:
- nextcloud: data storage, see `apps/nextcloud-v2/`
- active/passive placement across backend and DMZ VMs
- print server
- Immich: photo storage
- VPN client
- Git server

- optional Cloudflare Tunnel public ingress from the active DMZ VM

### Target-local application runner

`klokast-node` is a Go CLI for target-local app operations. It is separate from the controller's `klokast` contract engine. Its current command allowlist supports `nextcloud-v2` and `openclaw`; it is not a general site executor.

For Nextcloud v2, the controller prepares Platform resources and exports an app-scoped grant. The app workflow validates this grant and sends a desired JSON bundle to the selected backend and DMZ targets. The bundle includes the grant hash, image configuration, target role, and runtime intent.

The local runner validates the bundle, renders Podman kube YAML, and calls the installed app handler. It uses an operation lock and bounded timeout and writes machine-readable status. OpenRC starts or stops the rendered pods. The periodic Nextcloud v2 verifier reports conformance; it does not repair.

This app-local path cannot grant infrastructure resources or replace Platform authority. Existing apps also use controller-side Ansible workflows. See [Nextcloud v2 architecture](../apps/nextcloud-v2/docs/architecture.md) and [klokast-node](../cmd/klokast-node/main.go).

### Application presence and retained data

Instance v1 declares an app `present` or `absent`. A present app has supported placement. An absent app can retain manifest-defined datasets with `retention: preserve`. To retain data after removal, keep the absent app entry and its data bindings; remove placement and features.

Logical datasets identify durable user data. They do not include every runtime or identity volume. For example, Music's `library` dataset includes audio files and playlists, but not reconstructable player or Tailnet state.

Removal preserves durable data by default. Data destruction is a separate explicit operation. Omission of an app or dataset never authorizes deletion of unknown or undeclared storage. Backup freshness and app promotion have app-specific checks; they are separate from controller HA.

`platform-app` provides common lifecycle entry points, but support differs by app. Some adapters provide only status. Legacy commands that write the registry are blocked after Instance v1 adoption. A listed command does not imply that the current authority model permits that write.

See [Instance data lifecycle](klokast-instance-specification.md#application-and-data-lifecycle), [platform-app](../ansible/bin/platform-app), and [the app catalog](../apps/README.md).

## Layer 4: SDN (Software Defined Network)

### Zones, realms, and capabilities

Workload zones are `bak`, `dmz`, `iot`, and `usr`. The `ops` zone is control-only. App manifests cannot request workloads or network resources in `ops`.

Network realms identify endpoints outside workload zones. `wan` is upstream internet. `household` is the local client realm. `admin` covers AP-management and client networks. `ap-uplink` is the access-point Ethernet handoff. New manifests must use the explicit realm instead of the transitional `lan` alias.

Instance v1 declares available and enabled box connectivity. It does not select app flows. The resolver translates its names into compiler names:

| Instance v1 | Compiler view |
| --- | --- |
| `overlay` | `overlay` |
| `local-ap-uplink` | `ap-uplink` |
| `direct-wan-egress` | `direct-egress` |
| `edge-tunnel-ingress` | `edge-ingress` |
| `direct-wan-ingress` | `direct-ingress` |

The compiler vocabulary also includes `local-lan`, `vpn-egress`, and reserved `rg-lan`. These are not extra accepted Instance v1 values. The adapter derives the prohibited set from the supported compiler vocabulary. Instance v1 supports Tailscale as its overlay provider.

App manifests request symbolic flows, such as realm-to-zone or device-to-zone access. Platform-owned topology resolves these requests to interfaces, addresses, router rules, and VM firewall rules. An enabled capability alone does not open a port. Unknown fields and missing required capabilities cause refusal.

The local-client path can use router DHCP and DNS, a dedicated household VPN gateway, and DMZ-local HTTPS ingress. Public ingress uses an approved edge tunnel. These paths remain subject to declared resource rules.

See [Topology Boundary](platform-resource-control-plane.md#topology-boundary) and [Instance connectivity](klokast-instance-specification.md#connectivity-capabilities) for the exact contracts.

### Overlay management plane

- The Tailscale overlay network is the management plane for the Platform.
- A container gets its own Tailscale identity only when that service truly needs a separate tailnet identity or ACL boundary; most services should stay behind the VM identity and zone (bak / dmz / iot) firewall.
- The public Tailscale policy template contains topology and grants. Family
  identities and the rendered live policy stay in controller-private state.
  Read [Tailscale workflows](../klokast-ops/tailscale/AGENTS.md) for the render, pull, validate, and
  apply workflow.

- Here the Tailscale ACL tags in use:
  - `group:operators`: deployment laptop.
  - `tag:ops`: the active and standby controller identities. A cloud bootstrap host has this tag only while it has a controller role.
  - `tag:bootstrap`: the miniPC Linux host during bootstrap phase.
  - `tag:infra`: infrastructure services and approved coding runners that do not have controller authority.
  - `tag:airunner`: approved coding-runner containers inside `<box>-ops`.
  - `tag:dom0`: the Linux Xen dom0 host on each box.
  - `tag:oob`: out of band access.
  - `tag:vm`: the virtual machines.
  - `tag:dmz`, `tag:back`, `tag:iot`, `tag:usr`: application containers with their own Tailnet identity inside each shared zone.

- Additional per-container Tailnet identities are possible for Infrastructure Services and User Services that need a separate ACL boundary.

IPv6 downstream routing is disabled by default. One closed recovery action can
route one residential-gateway `/64` only to the active controller's `ops`
network. It does not enable IPv6 on `bak`, `dmz`, `iot`, `usr`, household, or
admin networks. The action keeps IPv4 and Tailscale DERP available for
recovery, and it requires a direct IPv6 path to the peer router before it can
change the active site.

# Special nodes

## 1. `og`
- `og` (the "original gangster") is the developer MacBook used to manage the platform.
- Physical location: private deployment metadata
- Tailscale tag: [`group:operators`]
- Platform workflows use `og` -> active `<box>-ops` -> approved target workflow. The human can use permitted direct operator access and Xen consoles for recovery. An airunner does not inherit those direct access permissions.
- For low-level guest recovery and installer work, the operator can still reach Xen consoles from `<box>-dom0`.
- For high-authority workflows from `og`, prefer separate Apple-native,
  non-exportable Secure Enclave keys protected by Touch ID. Use one identity
  for each authority scope. These are OpenSSH file-signing keys, not passkeys;
  private key material does not leave the Mac. A private Apple `ssh-agent` can
  run only for one bounded signing operation when native OpenSSH must select
  one of several CryptoTokenKit identities. Do not use an ambient agent for
  these approval keys.

## 2. `<cloud>-ops`
- This is a temporary cloud-based bootstrap host provisioned by
  `klokast-ops/`, for example `hetzner-ops` or `vultr-ops`.
- The checked-in `cloud-providers.json` catalog defines supported `<cloud>`
  prefixes. The system hostname and Tailscale machine name must both equal the
  exact `<cloud>-ops` identity.
- During initial bootstrap, it can run both the coding agent and the Ansible
  controller. It then has `tag:ops`, controller credentials, and controller
  private state because it is the active controller.
- After the controller moves to `<box>-ops`, fence and remove the old controller
  authority. If the cloud host stays temporarily as a coding runner, re-enroll
  it with `tag:infra` and remove all Platform private state, OAuth material,
  controller credentials, broker state, and compiler authority.
- The machine tag follows the active role. A cloud host must not keep
  `tag:ops` after it stops being the controller.
- Destroy the cloud host and its VPC only after removing its identity from the
  desired airunner list. A listed cloud runner must remain online with
  `tag:infra`.

## 3. Out of Band access
- `oob` is the remote keyboard/video/mouse device used for pre-boot recovery, BIOS changes, and ISO bootstrapping.
- It is operational tooling, not part of the steady-state compute platform.
- `oob` is a "Sipeed NanoKVM Cube" remote KVM (Keyboard, Visual, Mouse) device.
- Tailscale tag: `tag:oob`
- Tailscale machine name: `oob`
- Hostname: deployment-specific; the public topology name is `oob`
- User name: `root`
- Use it for failsafe recovery access to a box, by:
  - emulating a physical keyboard to run text commands, typically into `<box>-dom0` shell, emergency shell, grub bootloader shell, and UEFI setup screen.
  - emulating a bootable USB drive to boot the box on, typically `apps/bootstrap-iso-debian` or a standard live ISO like Gparted or Debian.
- `oob` is connected
  - to its box via HID (Human Interface Device: USB Keyboard and Mouse) input and HDMI output (Visual).
  - to only one box at a time. We have one `oob` device only. Human can plug it to the box that needs it.
- `ansible/bin/nanokvm-virtual-media` is `oob` bash CLI wrapper.
  - Features:
    - gather facts about `oob`
    - load and unload a bootable ISO to the target box (one ISO only can be mounted at a time)
    - transfer file (especially scripts and ISO files) to or from `oob`
    - download an ISO from internet into `oob`
    - delete an ISO from `oob`
    - run text commands on the target box via an emulated keyboard
    - recover `oob` if it has become unresponsive
    - reset the `oob` root and web UI password, even if its original value is unknown.
  - Limitation: the commands outputs are not visible, because the wrapper doesn't read and OCB the `oob` video stream output, and it is not connected to the box console port.
  - Password pre-requisite:
    - For optimal operations, Human should have saved the `oob` web UI password into `ops` as per `klokast-ops/runbooks/61-nanokvm-credentials-into-ops.md`
    - But `ansible/bin/nanokvm-virtual-media` can operate all its features without password, using tailscale-ssh root access to `oob` as a workaround hack
  - Recovery: if `oob` fails, check its recovery runbook: `klokast-ops/runbooks/60-nanokvm-recovery-skill.md`
- NanoKVM official documentation:
  - `https://github.com/sipeed/NanoKVM`
  - `https://wiki.sipeed.com/hardware/en/kvm/NanoKVM/introduction.html`

# Persistence

Persistence uses separate assets with separate authority:

- public `klokast-box`: generic implementation, schemas, CLI, public app
  manifests, automation, and the canonical instance template;
- one private instance repository: declared deployment desired state and an
  immutable engine lock, never a fork of the implementation;
- `/etc/klokast`: active-controller secrets and credentials outside Git;
- `/var/lib/klokast`: generated and observed controller state, including
  inventories, facts, plans, provenance, receipts, and verified build outputs;
- application storage: persistent user-service data.

Klokast Instance Specification v1 contains only `klokast-instance.json` and
`klokast.lock.json` as authoritative inputs. `klokast.lock.json` binds the
private instance to the approved `klokast` engine identity. The sealed engine
binary contains its repository, Git ref, and Git commit, and validates the
instance against that identity. The instance file owns private
topology, membership, connectivity-capability, controller, airunner, app, and
retained-data intent. It has no secrets, generated state, observed status,
inventory, or site-executor interface. The
[Klokast Instance Specification v1](klokast-instance-specification.md) owns
the normative JSON contract and CLI behavior. [Secret Authority](secret-authority.md)
owns signed Platform Apply, replay, rollback, receipt, and recovery rules.

Legacy controller input retirement passed signed execution and signed
verification on 2026-09-14. The live `deployment.yml`, private
`platform-resources.yml`, and `controller-ha.yml` paths are absent. Normal
consumers use Instance Specification v1. The canonical old registry path can
still select adopted authority; it does not require a file. The root-only
recovery archive, historical Plans, and explicit compatibility inventory remain
available. The public acceptance narrative is in Git at commit `186cfa9`.
Controller-held receipts, Plans, audit logs, source history, policy recovery
material, and the recovery archive remain operational evidence and must not be
deleted as documentation cleanup.

The active controller is the only Platform mutation locus and secret custodian.
The human authors and pushes private instance changes from a trusted
workstation. This human-only rule applies to the private instance repository,
not to the public implementation repository. The controller has a clean
deployment checkout with a root-held read-only deploy key and a disabled push
URL. Airunners may author and push reviewed public implementation changes, but
they do not clone the private instance repository or hold controller-private
state. Deployable `klokast` binaries are built only by the active controller through the
networkless Xen `platform-builder` profile.
Exact human and controller procedures are in
[Private Instance Bootstrap](../klokast-dev/runbooks/40-private-instance-bootstrap.md).
Platform site time is always `Etc/UTC` (GMT), so instance inputs do not contain
a timezone.

## Private instance and engine lifecycle

The human creates an empty private repository. A temporary GitHub App registers that repository and the controller's read-only deploy key. It has Administration permission and no Contents permission. The approved sealed engine creates the initial instance from the canonical template. The human reviews, commits, and pushes the instance from the trusted workstation. The temporary App access is then removed and its credential is retired.

The controller activates only the approved private source. Its deploy key stays root-held and read-only. Activation produces an immutable receipt. Pulling a new public commit does not change the private engine lock or approve that commit for the installation.

For engine promotion, the controller first builds the candidate public commit through the sealed builder. The Mac helper shows the engine and schema changes. The human signs the exact transition, creates and pushes the private lock commit, and requests controller activation of the approved candidate tree.

Promotion receipts and activation receipts record the accepted transition. Forward rollback selects the previous engine recorded in the activation receipt and creates a new private commit. It never rewinds or force-pushes private `main`.

For the full protocol, see [Controlled Engine Promotion](secret-authority.md#controlled-engine-promotion) and [Private Instance Bootstrap](../klokast-dev/runbooks/40-private-instance-bootstrap.md).

## files locally stored in the boxes
- unique to each box:
  - `.apkovl` in each `<box>-dom0`: dom0 state, installed Alpine Linux packages, Tailscale state (ssh keys)
  - data of Users Services
  - data of Infrastructure Services
    - secrets stored in `broker` of the active controller, owned by `root` user
- synchronized across boxes:
  - selected controller recovery state through the HA workflow;
  - app data and backups through app-specific workflows;
  - artifacts and their checksums through their distribution paths. Replication between `<box>-bak` store volumes is a design role, not a general implemented service.

## External dependencies and trust boundaries

| External system | Granted capability and trust boundary |
| --- | --- |
| GitHub | Hosts public implementation source and private instance source. Engine and schema locks select approved source. Controller instance access is read-only. App credentials have separate purposes: temporary instance registration or scoped app publishing. |
| Tailscale | Provides the current overlay, machine enrollment, identity, SSH access rules, and connectivity. Root wrappers restrict key purpose, name, tags, and lifetime. Tailnet membership does not replace signed Apply approval. |
| Cloudflare Tunnel | Provides optional public ingress from approved DMZ connectors. Its edge terminates public TLS and can read HTTP traffic. Tunnel credentials do not grant controller or recovery access. |
| Vultr and Hetzner | Provide bootstrap compute or approved cloud runners. A bootstrap controller holds authority only during that role. A retained runner uses `tag:infra` and has no controller-private state. The cloud provider controls its hosted machine. |
| Upstream image and package providers | Supply build inputs. Digest and checksum verification bind selected bytes. A registry tag or mirror alone cannot approve an artifact. |
| Off-platform storage | Proposed destination for encrypted recovery and backup bundles. The storage role receives ciphertext and restricted upload authority. A general storage integration is not implemented here; app-specific backups have their own workflows. |

Provider credentials stay outside Git and outside airunners. Brokered actions use scoped credentials internally. External availability can affect source fetches, enrollment, ingress, or recovery downloads. Console recovery and retained reconstruction artifacts provide separate recovery paths.

See [operator setup](../README.md), [Secret Authority](secret-authority.md), [Cloudflare ingress](cloudflare.md), and [cloud provisioning](../klokast-ops/terraform/README.md).

# Initial Platform deployment

Bootstrap establishes the first controller and box identities before steady-state operation:

1. The trusted Mac launches the checked-in cloud provisioning workflow. The Homebrew installation flow is a packaging goal; it is not implemented in this repository.
2. A temporary `<cloud>-ops` controller runs Terraform and Ansible workflows and holds bootstrap authority. The coding runner and controller have separate account responsibilities.
3. The controller loads a verified generic Debian Live ISO onto NanoKVM. The ISO contains neither a box name nor an enrollment key.
4. The box boots the ISO, gets DHCP, and exposes the local `kk.local` and `klokast.local` onboarding portal through mDNS.
5. The human enters the private box ID and bootstrap enrollment key. The identity wrapper should mint a short-lived, single-use key for the approved name and tags. Reusable enrollment keys are legacy debt.
6. The box joins the Tailnet as `<box>-bootstrap` with `tag:bootstrap`. A name collision requires a new approved name; a suffixed identity is not the intended box identity.
7. `provision-box` verifies access and requires confirmation of the box and target disk before the destructive install. It writes and verifies the Alpine diskless seed on the SSD.
8. The workflow detaches the ISO before reboot. NanoKVM supports automatic detach; a checked manual fallback is available.
9. The box reboots into Alpine. Dom0 convergence first uses the bootstrap identity, then performs the handoff to permanent `<box>-dom0` identity.
10. The controller creates the router and service guests and finalizes their identities. It provisions `<box>-ops`, transfers controller authority through the approved migration workflow, and fences the old controller.
11. A retained cloud runner is enrolled with `tag:infra` after its controller credentials and private state are removed. Normal operation uses the active `<box>-ops` controller.

The Mac authors private instance changes and signs high-authority intents. The airunner authors public implementation changes and provides a remote terminal to `smith`. Platform state inspection and changes execute on the active controller. Use a checked-in dispatcher such as `platform-check-remote`, or enter the controller before running its workflows. An infra-agent must not inspect Platform nodes through direct root SSH.

The Mac can use its permitted operator and console recovery paths. Its Tailnet permissions do not grant the airunner the same access. Existing entry points include `provision-box`, `reinstall-box`, `platform-check-remote`, and `kk app`; this repository does not implement `kk box up`.

See [human bootstrap instructions](../README.md#7-provision-the-first-box), [provision-box](../ansible/bin/provision-box), [bootstrap playbooks](../ansible/overview-playbooks/playbooks-1x-bootstrap.md), and [dom0 playbooks](../ansible/overview-playbooks/playbooks-2x-dom0.md).

# Cybersecurity: TCB, Service Plane, secrets, agents security

Cybersecurity is highest priority of the Platform.
The Platform shall minimize authority, attack surface, the Trusted Computing Base (TCB), and the number of components whose compromise can compromise the Platform.
The implementation shall prioritize adherence to best practices, isolation (especially via virtualization), and the use of automation (to avoid drift).

Security-sensitive behavior must be deterministic, reviewable, least-privileged, and fail closed.

The TCB includes:
- `airunner`, especially their code-authoring identities, as airunner can modify Platform code.
- active `controller` (`<box>-ops` or `<cloud>-ops`);
- code repository of the Platform (The code of the Platform Upstream);
- code repository of the private deployment-state (the code of the Platform Deployment)
- (resource) compilers, brokers, and automation that apply topology, identity, network,
  host, or privileged-workload policy;
- recovery and promotion mechanisms that can restore or transfer authority.

Exposure of secrets must be minimal:
- secrets shall not be committed to git
- airunner shall not contain Platform secrets. Its runner-owned GitHub and LLM credentials stay within its separate scope.
- the active controller is the secrets custodian.
- the standby controller has no replicated reusable provider or controller Git credentials. Private recovery evidence can remain on standby under the HA rules.

User Services are non-authoritative: application code, app manifests, installers, app-local reconcilers, user workloads, and app data.
User Services automation may declare allowlisted intent, consume app-scoped approved grants, and verify or apply target-local app state.
It must not control dom0, VM lifecycle, router or zone firewalls, Tailnet policy, identity minting, private registries, credential brokers, or privileged-builder placement. Unknown intent and undeclared privilege must be rejected rather than ignored.
