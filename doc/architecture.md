# Standard architecture of the boxes
Each box of the Platform is one mini-PC that implements the same 4-layers architecture:

## Layer 1: baremetal Host & Xen hypervisor
- Tailscale tag: `tag:<box>-dom0`
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

### `<box>-router`
- Site router and firewall VM. It enforces firewall accesses for the application containers of the Platform and the other VMs.
- runs Alpine Linux, `nftables` firewall rules, native routing tables, `dnsmasq`, and `dhcpcd`.
- default inter-zone choke point for LAN, DMZ, backend, IoT, user workloads, and WAN
- public application ingress is expected to come through Cloudflare Tunnel from the DMZ, not router DNAT to service VMs

###  `<box>-bak`
For backend services: hosts application containers that are trusted and not internet facing, for example databases and Gitlab CI/CD pipes.
Tailscale tag of these containers: `<box>-bak`.

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
Tailscale tag of these containers: `<box>-dmz`.

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
Tailscale tag of these containers: `<box>-iot`.

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

### `<box>-vpn`
vpn client to give internet access to the box

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

### private registry

The private registry is deployment-owned binding and authority. It answers: “for this specific family/platform, where and how is this app deployed?”

It contains concrete deployment facts:
  - enabled/disabled app state
  - active/passive placement: active_master, passive_backup
  - selected boxes
  - concrete users for per_user_app_vm
  - concrete IP/MAC/hostname for managed devices
  - enabled optional resources
  - local access capabilities
  - builder approvals, expiry, cleanup flags
  - runtime state like running/stopped

Access capability: a provider-neutral app access transport class selected by the Platform resource registry, such as `overlay`, `local-lan`, `vpn-egress`, or `edge-ingress`.
Forbidden transport classes such as `direct-egress`, `direct-ingress`, and deployment-reserved `rg-lan` can be
listed explicitly as prohibited capabilities.

### Resources
- By default, services run as rootless containers inside the shared zone VMs and inherit firewall rules from these VMs.
- New rootless containers inherit the VM zone policy from their shared zone VMs.
- App data, app daemons, and Host-level app services must not run on VMs directly. They must run in containers.
- The service VMs `bak`, `dmz`, `iot` and `usr` are service substrate, not credential-bearing Control TCB.
- `<cloud>-ops` and `<box>-ops` are only for infrastructure and control plane roles. App manifests cannot select it for placement of their service workloads.

- App manifests select placement for the services, among the support resources:

  - shared service zones: `bak`, `dmz`, `iot`.

  - `per_user_app_vm`: dedicated VM for one internal user. Used when a user needs a full Xen VM, not just a container. This is a VM for one user only. That VM can host containers. It can also be other OS than Alpine, such as Ubuntu or Debian.
  Host name: `<box>-usr-<slug>`.
  Tailscale tags: `tag:vm`, plus app-specific Tailnet tags when a separate ACL boundary is needed.

  - `app_vm`: one dedicated VM for one app or appliance. For example: `k002-torrent`. Typical use cases: untrusted or risky service, rootful Docker or non-standard runtime, VPN leak containment, privileged networking, PCI/USB passthrough, different lifecycle or OS profile

  - `managed_iot_device`: an external physical device that the Platform manages or grants network access to. For example: Raspberry Pi audio endpoint, printer, camera bridge, local sensor gateway. This is not a VM and not a container. The app manifest declares the need symbolically. The private registry binds it to concrete facts, such as mac address, ip address, hostname, box.

  - `artifacts`: deployable outputs from a build process, that apps or infrastructure consume. For example: OCI image archives, container images, VM disk images, bootstrap ISOs, checksums, digest lock files.

  - artifact registry/store: "artifacts are not services by themselves. They are deployable outputs."

  - privileged builders: temporary or controlled build environments allowed to perform higher-risk build actions. For example: bootstrap ISO builder, Debian app-VM image builder, OCI image builder needing elevated Podman/buildah access.

### Linux accounts

- `neo`, controlled by the human:
  - on `<cloud>-ops`, `<box>-airunner`, and `<box>-ops`: privileged (via `doas` or `sudo`) to manage runner and controller account access, and for recovery.
  - on dom0 and service VMs: standard administration and recovery account as defined by each machine role.

- `agent`:
  - on `<cloud>-ops` or `<box>-airunner`: runs the AI coding agent and owns its working repository, runner credentials, sessions, and tools. It is a persistent Control TCB authority because it can modify Platform code and execute it as `smith`. It must not store Platform private state, infrastructure-provider credentials, controller deploy keys, broker secrets, or private registries.

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
  - must not read the private registry, OAuth material, deploy keys, broker state, or mutate infrastructure policy.

- `oracle`:
  - unprivileged read-only mapping and verification account on `<box>-ops`.
  - consumes sanitized desired state and checked read-only facts.
  - has no general Ansible SSH key, private registry, deploy key, broker access, remote administration credential, or privilege escalation.

### Infrastructure Services

Infrastructure services manage the Platform. During bootstrap, controller-side services run on `<cloud>-ops`; their target placement is defined below.
One box only is the "Active Controller".

- `airunner`:
  - AI coding agent remote terminal (CLI interface) to the coding agent (e.g. OpenAI Codex CLI), coding agent api key, archived discussions, wrappers.
  - It is a persistent controller authority, part of the TCB.
  - Users are `neo` and `agent`. Tailnet policy allows approved runner identities to connect to `<box>-ops` as `smith`.
  - Runs in `<cloud>-ops` or a dedicated `<box>-airunner` VM with its own Tailnet identity, independent patching, and no controller mounts or private state.
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

- `compiler` (= "resources compiler" or " Infrastructure reconciler"): versioned CLI tooling in `<box>-ops` that renders resources. It enforces approved state within a fixed scope. It doesn't independently choose placement, ownership, policy, or privilege.
  - The broader app/infra contract is the Platform Resource Control Plane described in `doc/platform-resource-control-plane.md`.
  - Account `smith` applies infrastructure security controls from an approved Git commit.
    1. App manifests declare required resources: compute, network, Tailnet, artifact, and privileged-builder needs.
    Deployment-private registries bind those needs to concrete boxes and optional resources.
    2. Compiler renders app-owned manifests, from infra-owned topology data into router and VM nftables rules. (Command: `platform-resources apply` or `compiler apply`).
    3. App installers verify those rules but do not mutate the router or Podman VM firewall baselines. Less-trusted app automation, `minion` account, verifies the applied controls before changing services.
  - The compiler combines: app manifests + private registry + infra topology.
  - The compiler produces:
    - router firewall policy
    - VM firewall policy
    - app VM inventory/metadata
    - Tailnet policy resources/grants to mirror
    - approved app-scoped grants for `minion` account
    - provenance: approved commit, registry hash, compiler metadata

- `mapper` and `verifier`:
  - read-only CLI tooling under the unprivileged `oracle` account in `<box>-ops`.
  - `mapper` (platform mapper, see `ansible/bin/platform-resources`): build inventory of the services, including location (which box or <cloud> machine) with their status: active, passive, installed, etc.
  - `verifier` (platform fact checker): gather health indicators from the platform, such as CPU and RAM usage, and service health.
  - output: `/home/codex/private/klokast/platform-resources.yml`

- `controller` (Ansible controller): versioned CLI tooling in `<box>-ops`.
Only the active controller may mutate the Platform. The active controller is the state-changing execution locus: it runs infrastructure playbooks as user `smith` and scoped application workflows as user `minion`. It is also the credential custodian.
Controller HA is active/standby, not distributed authority: only the active controller may mutate the Platform. Fence the old active controller before promotion, and reseed recreatable provider authority instead of replicating it to standby controllers.
Tailscale tag: `tag:ops`.

The proposed separation of Platform-wide authorization from constrained
site-local execution is specified in `doc/site-executor.md`. It does not change
the current single-controller execution invariant until that document's
deployment and security gates are implemented and validated.

- `broker` (credentials broker): root-owned, versioned, deterministic wrappers on the active `<box>-ops`. It validates narrow actions, uses provider/app credentials without revealing them, enforces the active-controller guard, and appends audit records.

- `builder` (images builder):
  - build trusted artifacts, such as container images, VM images, bootstrap iso, and OCI archives.
  - packaged as `<box>-builder-<purpose>-<id>`: short-lived Xen VM, created by `<box>-ops`, narrowly scoped egress, no controller or broker secrets, destroyed after publishing verified artifacts.
  - The `klokast` Go CLI uses the stricter `platform-builder` profile: a
    sealed Alpine 3.23 template, a unique writable LVM snapshot, no VIF or
    Tailnet identity, and rootless Podman with networking disabled. The active
    controller injects only a Git archive of the synchronized approved commit,
    vendored modules, and a digest-pinned Go OCI archive while the guest is
    stopped. The stopped guest is the authoritative build locus; the
    credential-bearing controller and airunner do not produce deployable CLI
    binaries. The controller also verifies the canonical repository and safe
    upstream branch. The guest binds that repository, ref, and commit into the
    binary and its receipt, and the controller verifies the receipt values.

- `store`: rootless blob-distribution containers in `<box>-bak` on the active- and standby-controller boxes. The store is an untrusted distribution layer, outside the TCB:
  - content-addressed, immutable blobs;
  - no signing keys;
  - no authority to update approved digest locks;
  - consumers must validate immutable content (digests or signatures) against provenance held by the TCB (from the approved controller state), not from the store itself
  - artifacts replicated between the store instances.
  - The persistent volume belongs to the VM/storage substrate, not to the disposable container.
  - The store is not the only source for artifacts required to reconstruct the Platform. Bootstrap and controller images also have an offline or off-platform recovery copy.

- `encrypter` and `uploader` (off-platform archiver): create encrypted backup and archive bundles and send them to an off-platform depot.
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
Applications installed and activated by the user.
For each User Service, xxxx specifies the roles of each box: if the User Service installed, active, or passive ; how data persistence and backup are managed, etc.

Examples of User Services:
- nextcloud: data storage, see `apps/nextcloud-v2/`
- active/passive placement across backend and DMZ VMs
- print server
- Immich: photo storage
- VPN client
- Git server

- optional Cloudflare Tunnel public ingress from the active DMZ VM

## Layer 4: SDN (Software Defined Network)
- The Tailscale overlay network is the management plane for the Platform.
- A container gets its own Tailscale identity only when that service truly needs a separate tailnet identity or ACL boundary; most services should stay behind the VM identity and zone (bak / dmz / iot) firewall.
- The public Tailscale policy template contains topology and grants. Family
  identities and the rendered live policy stay in controller-private state.
  Read `klokast-ops/tailscale/AGENTS.md` for the render, pull, validate, and
  apply workflow.

- Here the Tailscale ACL tags in use:
  - `group:operators`: deployment laptop.
  - `tag:ops`: cloud-based server, especially mandatory during the bootstrap of the Platform.
  - `tag:bootstrap`: the miniPC Linux host during bootstrap phase.
  - `tag:infra`: the Infrastructure Services.
  - `tag:dom0`: the Linux Xen dom0 host on each box.
  - `tag:oob`: out of band access.
  - `tag:vm`: the virtual machines.
  - `tag:dmz`, `tag:back`, `tag:iot`, `tag:usr`: application containers with their own Tailnet identity inside each shared zone.

- Additional per-container Tailnet identities are possible for Infrastructure Services and User Services that need a separate ACL boundary.

# Special nodes

## 1. `og`
- `og` (the "original gangster") is the developer MacBook used to manage the platform.
- Physical location: private deployment metadata
- Tailscale tag: [`group:operators`]
- Preferred access path is direct over Tailscale to the managed host: `og` or `codex` -> Tailscale -> `<box>-dom0` / `<box>-router` / `<box>-bak` / `<box>-dmz` / `<box>-iot` / `<box>-ops` / per-user app VMs such as `<box>-usr-admin`
- For low-level guest recovery and installer work, the operator can still reach Xen consoles from `<box>-dom0`.

## 2. `<cloud>-ops`
- Cloud-based instance provisioned as per `klokast-ops/`, for example: `vultr-ops`, `hetzner-ops`.
- The target trusted infrastructure controller is one reproducible `<box>-ops` Alpine VIRT VM on the selected master box, allowing `cloud-ops` to be powered off after successfull migration.
- Tailscale tag: `tag:ops`
- When starting the deployment from scrap, `<cloud>-ops` operates following roles:
    - the server that runs the Ansible playbooks to provision the boxes.
- As soon as the Platform is up and running, the services fulfilled by `<cloud>-ops` can optionally be migrated to boxes, as per `klokast-box/apps/ops/`. After all services on `<cloud>-ops` have been migrated to boxes, the VPC of `<cloud>-ops` can be destroyed, so as to remove the external dependency that is the cloud provider.

- Services that can run on `<cloud>-ops`:
    - `runner` (coding agent runner): CLI interface to the coding agent, coding agent api key, archived discussions, wrappers ???,
    - `controller` (Ansible controller)
    - `broker` (credentials broker)
    - `compiler` (resources compiler)

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
- one private `klokast-instance`: declared deployment desired state and an
  immutable engine lock, never a fork of the implementation;
- `/etc/klokast`: active-controller secrets and credentials outside Git;
- `/var/lib/klokast`: generated and observed controller state, including
  inventories, facts, plans, provenance, receipts, and verified build outputs;
- application storage: persistent user-service data.

Contract v1 contains only `klokast.yml`, `klokast.lock.yml`, the deployment
document, and the platform-resource document as authoritative inputs. It has no
private app-configuration, extension, generated-state, inventory, or
site-executor interface. Site executors require a later deployment schema
version. See `doc/upstream-instance-target-architecture.md`.

The active controller is the only Platform mutation locus and secret custodian.
Airunners may author reviewed upstream or instance Git changes but do not hold
controller-private state. Deployable `klokast` binaries are built only by the
active controller through the networkless Xen `platform-builder` profile.
The builder-approved binary can create a staged local Contract v1 repository
with `klokast init`. It does not create a remote repository, install Platform
state, or receive secrets. Platform site time is always `Etc/UTC` (GMT), so init
inputs do not contain a timezone.

## files locally stored in the boxes
- unique to each box:
  - `.apkovl` in each `<box>-dom0`: dom0 state, installed Alpine Linux packages, Tailscale state (ssh keys)
  - data of Users Services
  - data of Infrastructure Services
    - secrets stored in `broker` of the active controller, owned by `root` user
- synchronized across boxes:
  - artifacts and their checksums, as built in short-lived builder VMs and replicated between `<box>-bak` store volumes.

## 3rd party cloud services
- Not yet implemented.
- ...

# Initial platform deployment:
The deployment of the platform is automated (Architecture as Code), leveraging `brew`, Terraform, Ansible, some scripts, a Debian bootstrap iso, and a "remote KVM (Keyboard, Visual, Mouse)" device:
  1. On the developer MacBook, `brew` installs Terraform and Ansible, and runs the first playbooks. (Not yet implemented. The code will live in `../homebrew-klokast/` repository, and in `ansible/bin/`.
  2. These build and provision the deployment server (for details, see `klokast-ops/runbooks/80-test-ops-automated-provisionning.md`)
  3. The deployment server loads a Debian bootstrap iso onto the "Remote KVM" device (Remote Keyboard Visual Mouse). (For details, see `/apps/bootstrap-iso-debian/`). The remote KVM also serves as failsafe Out-of-Band access for that node.
  4. The mini PC boots on that iso, then install Alpine seed files onto the SSD. (for details, see `ansible/overview-playbooks/playbooks-1x-bootstrap.md` and `ansible/overview-playbooks/playbooks-2x-dom0.md`
  5. The mini PC reboots
  6. the deployment server then runs Ansible playbooks to build and provision the nodes of the platform. (for details, see `overview-playbooks/playbooks-3x-router.md` and `overview-playbooks/playbooks-4x-podman.md`)

For details about deployment automation, see also `klokast-ops/runbooks/80-test-ops-automated-provisionning.md`

- Deployment playbooks: `klokast-box/ansible/playbooks`

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
- airunner shall not contain secrets
- the active controller is the secrets custodian.
- the passive controller doesn't hold secrets.

User Services are non-authoritative: application code, app manifests, installers, app-local reconcilers, user workloads, and app data.
User Services automation may declare allowlisted intent, consume app-scoped approved grants, and verify or apply target-local app state.
It must not control dom0, VM lifecycle, router or zone firewalls, Tailnet policy, identity minting, private registries, credential brokers, or privileged-builder placement. Unknown intent and undeclared privilege must be rejected rather than ignored.
