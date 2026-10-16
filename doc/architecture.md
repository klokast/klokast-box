# Architecture

Each box of the Platform is one mini-PC that implements the same four-layer architecture:

1. baremetal Host & Xen hypervisor
2. Virtual Machines
3. Services
4. SDN (Software Defined Network)

Ansible playbooks, Platform tooling, and wrappers automate the lifecycle of the Platform, including bootstrap, reconciliation, maintenance, and updates.

The codebase has two main Git repositories:

1. the public upstream repository `klokast/klokast-box`, which contains the reusable Platform implementation;
2. one private Instance repository (`<family-name>/klokast-instance`) for each Platform deployment, which contains deployment-specific desired state such as boxes, placement, enabled applications, and capabilities.

The Platform lifecycle determines which implementation is allowed to perform privileged operations:

- in **development**, Platform code can be changed and deployed freely;
- in **production**, privileged operations execute only code from the admitted Platform release.

See [Platform lifecycle](platform-lifecycle.md) and [Threat model](threat-model.md).

## Layer 1: baremetal Host & Xen hypervisor

- Tailnet hostname: `<box>-dom0`
- Tailscale tag: `tag:dom0`

Host Operating System:

- baremetal diskless Alpine Linux Xen dom0;
- pure PVH hypervisor;
- QEMU is not installed;
- persistence over reboot uses `lbu commit`.

Storage:

- the SSD EFI partition carries GRUB, kernel, initramfs, modloop, APK boot repository, runtime APK cache, and apkovl persistence;
- LVM holds guest logical volumes.

Diskless persistence:

- `.apkovl` is used only for small dom0 runtime state such as `/etc`, selected administration state, and Tailscale state;
- large persistent assets and guest storage remain outside `lbu`;
- `/mnt/dom0_data` is LVM-backed persistent storage used for Xen images and related artifacts.

Networking and administration:

- dom0 hosts the Xen bridges;
- `neo` is the local human administration and recovery account;
- Tailscale is the normal remote management path;
- NanoKVM and local console access provide an independent recovery path;
- the root password is locked.

Package policy:

- `/etc/apk/world` defines the steady-state dom0 package set;
- temporary maintenance dependencies may be installed in RAM by approved Platform automation;
- maintenance automation restores the steady-state package set before persistent state is committed.

## Layer 2: Virtual Machines

- Tailscale tag: `tag:vm`
- Xen runs several VMs on each box.
- Shared zone VMs (`<box>-bak`, `<box>-dmz`, and `<box>-iot`) use Alpine Linux and rootless Podman.
- Applications may use dedicated VMs when a shared-zone VM does not provide a sufficient security or lifecycle boundary.

Dedicated application VMs remain members of the Platform zone model. They do not create independent security policy.

### `<box>-router`

Site router and firewall VM.

Responsibilities:

- routing between Platform networks;
- inter-zone firewall enforcement;
- WAN egress policy;
- DHCP and DNS where applicable;
- enforcement of network resources compiled by the Platform.

It runs Alpine Linux with `nftables`, native routing, `dnsmasq`, and `dhcpcd`.

It is the normal choke point between workload zones, local networks, and WAN.

Public application ingress normally enters through an approved edge connector such as Cloudflare Tunnel in the DMZ rather than direct router DNAT to service VMs.

#### Router state and recovery

The common VM lifecycle treats the router OS root as replaceable. One 1 GiB
ext4 data LV on the same box SSD holds its retained state at `/srv/retained`.
The protected assignment binds the data LV path, LVM UUID, size, filesystem
UUID, box, and numeric service accounts. The filesystem UUID alone does not
authorize attachment. Exactly one router or isolated finalization guest can
attach this disk at a time. This separation supports OS replacement; it is
not a backup against SSD failure.

The router keeps one logical Tailscale and SSH identity across OS roots.
Retained files are the native `tailscaled.state`, existing Tailscale fallback
SSH and `/etc/ssh` host keys, `/etc/machine-id` when present, dhcpcd DUID and
privacy secret, and dnsmasq leases. Replacement does not enroll another
Tailscale device or delete the existing registration. WAN lease caches are
disposable. Packages, service definitions, account configuration, network
configuration, and firewall rules come from the qualified image and frozen
Platform and Instance inputs. They do not come from the old root.

The first common OS replacement migrates the fixed state list from the exact
accepted legacy root after shutdown. There is no separate in-place migration
command. A networkless Xen guest reads the old filesystem read-only, initializes
only the newly allocated blank data LV, and verifies copied bytes, modes and
numeric owners. Dom0 does not mount guest filesystems. Later replacements
attach the same data LV and never seed it from an older root. Unknown disks,
missing state, changed identities, and unsupported identity storage stop the
operation. The guest verifies the expected filesystem and bind mounts before
networking, DHCP, DNS or Tailscale can start. A missing mount must not create
replacement identity files on the OS root.

An unassigned legacy router needs explicit baseline reconciliation through the
active controller's Ansible workflow before replacement. Existing controller
authority and installation, topology and router locks govern this action.
Verify current Instance policy, native state, numeric ownership, the exclusive
running root, boot artifacts and the independent controller uplink. Record the
observed generation and bind it to the accepted six-interface topology. Preserve
the original topology in the protected baseline record; do not rewrite historical
evidence or infer ownership from an LV name. Persist the observed Xen UUID for
boot recovery. Interrupted publication permits only the absent or exact recorded
assignment and the recorded source root. Conflicting records fence boot. This
action does not allocate a disk, copy state, enroll a device or restart the router.

Recovery has one durable boundary: the common transaction records `starting`
and selects the candidate boot definition **before** it asks Xen to start the
candidate. Before this boundary, recovery can restart the recorded old root.
After this boundary, including a lost Xen response or failed health check,
recovery preserves the candidate and the latest retained state. Resume or
repair that selection. Do not automatically restore an old root or copy old
state over the data disk. An abandoned migration before this boundary needs
a new operation and a fresh stopped-source copy; it cannot reuse a stale seed.

The existing active controller owns replacement through Platform authority.
The common transaction pointer becomes the router boot owner when the first
replacement is armed, under both common and legacy transaction locks. Legacy
recovery delegates to that owner, including after pre-start recovery. Legacy
topology recovery stops writing the boot definition. Historical generation
records retain their original meaning and remain available for inspection;
legacy mutation and identity retirement are closed after handoff. A conflicting
or damaged pointer fences router autostart. An unconverted router continues to
use its existing boot recovery.

Keep the current and previous common OS roots. Root retirement never selects
the retained data LV, deletes the logical Tailscale device, or removes historical
legacy records and disks. These are separate recovery resources. The threat
boundary remains the isolated filesystem guest: a compromised old router can
supply hostile filesystem data, but it cannot make dom0 execute guest code or
copy arbitrary old files into the new OS. No new enrollment authority or
long-running service is introduced.

See [router lifecycle integration](platform-updates.md#router-lifecycle-integration)
for the current tooling gate and required native qualification.

### `<box>-bak`

Backend service VM.

Role:

- backend Podman host;
- private and stateful service workloads;
- no public exposure by default;
- rootless Podman workloads under `neo`.

The VM uses `tag:vm`. A workload receives a separate Tailnet identity only when a separate identity or ACL boundary is required.

### `<box>-dmz`

DMZ service VM.

Role:

- public-facing connectors and reverse proxies;
- application frontends intended to receive external traffic;
- rootless Podman workloads under `neo`.

The VM uses `tag:vm`. A workload receives a separate Tailnet identity only when required.

### `<box>-iot`

IoT service VM.

Role:

- isolated middleware for local hardware and sensors;
- integration with low-trust LAN devices;
- no direct WAN exposure by default;
- rootless Podman workloads under `neo`.

### `<box>-ops`

Infrastructure and control-plane VM.

Role:

- hosts the active or standby Platform controller;
- stores controller-private state;
- hosts privileged Platform automation;
- holds infrastructure credentials through restricted controller-side mechanisms;
- runs the resource compiler, mapper, brokers, and other Platform tooling;
- does not install or run Codex; the runner VM owns that runtime;
- is not an application host or public ingress point.

The privileged portions of `<box>-ops` are part of the Integrity TCB.

Ops has an independent management uplink. Its `eth1` connects to dom0's existing
`br-wan`, on the same physical NIC and cable as the router. It obtains IPv4 by
DHCP and IPv6 by SLAAC. Only this interface supplies default routes. Upstream
DHCP must accept another guest MAC and address. `eth0` stays on private
`br-ops`, with ops at `192.168.125.10` and dom0 at `192.168.125.1`. It has no
default route or IPv6 router advertisements. The router has no NIC on this link.
Dom0 does not route or NAT ops traffic.

Ops uses the box's declared public bootstrap DNS, with Tailscale DNS handling
preserved. It does not depend on the workload router, delegated router IPv6,
or the VPN proxy for management or recovery. Its host firewall is installed
before the uplink starts. It denies unsolicited input and all forwarding,
permits required DHCP, ICMP, Tailscale and declared controller egress, and
limits underlay recovery SSH to dom0 on `eth0`. Workloads receive no uplink NIC.
The named attacker is a compromised upstream LAN device or workload that tries
to reach controller services. The host firewall and Tailnet policy enforce this
boundary; the shared physical link does not isolate layer-two or link failures.
Router and dom0 compromise remain accepted Integrity-TCB risks.

The active controller owns this topology change through existing Platform
mutation authority. A network revision preserves disks, identity and authority;
it changes only network configuration and the protected boot definition.
Immutable router generation records remain available after a new topology
record is published. See [controller network migration](platform-deploy.md#controller-network-migration).

New controller and runner images select the latest stable Alpine release and
current packages at build time. Controller tool setup selects current stable
Go and Ansible collection releases. Runner builds select current stable Codex.
Do not put fixed OS or package versions in their desired configuration. Record
resolved versions and verified artifact checksums as build evidence; image
reuse requires those inputs to match.

### `<box>-air`

Optional Platform-owned AI runner VM. The ordered Instance `airunners` list
selects its placement. It is independent of application manifests.

- Xen guest name `air`; Alpine Linux; 2 vCPUs, 4096 MiB RAM, a 50 GiB root LV and a separate 50 GiB data LV after lifecycle migration.
- Runs Codex directly as unprivileged `agent`. Preserve its numeric UID and GID
  and `/home/agent` during migration. `neo` provides maintenance and recovery;
  root password login is locked.
- Uses the `usr` bridge and reserved address `192.168.175.11`. The resource
  compiler rejects workload allocations at this address.
- Uses exactly `tag:airunner` and a fresh single-use enrollment credential from
  the existing controller broker. Never transfer another runner's Tailscale state.
- Uses the existing isolated Alpine builder with profile `air-alpine-v1`.
  Image reuse binds the native Codex version and artifact checksum. Images
  contain no credentials or machine identity.
- The Platform compiler derives network rules from Instance runner placement.
  Existing managed firewall includes have reserved Platform ownership. Permit
  DNS, web and Tailscale transport, operator SSH/Mosh, and controller maintenance.
  Remove temporary bootstrap SSH after enrollment.

The VM boundary contains a compromised runner process or development dependency.
It does not change lifecycle authority. Development runner access to the active
controller remains available; production authorization is a separate mechanism.
Controller credentials and private state stay on `ops`. Runner-visible archives
use a local read-only copy. No AI service daemon is required.

### `<box>-vpn-egress`

Optional Platform-owned shared VPN proxy for explicitly declared VMs on the
same box. Instance `vpn-wan-egress` and `vpn-egress.clients` own its placement
and client authority. The Platform compiler grants exact client source IPs on
TCP 7890 and TCP/UDP 53. The gateway occupies reserved DMZ address `192.168.200.41`, with
1 GiB RAM, two vCPUs and an 8 GiB root disk. Its identity has exactly `tag:vm`
and `tag:vpn-egress`. It has no controller credentials or authority.

Mihomo runs as an unprivileged account in this Xen VM. Its proxy and DNS listeners
bind the DMZ address. DNS listens on port 1053; the existing guest firewall
redirects declared clients' TCP/UDP port 53 requests to it. This needs no new
process capability. Its management API binds loopback. It has no TUN device,
subnet advertisements, exit-node role, or packet-forwarding role. Web clients
use an explicit proxy; their ordinary local and Tailnet paths stay available.
For a controller, membership in `vpn-egress.clients` permits an explicit
`platform-vpn-egress with-proxy` download. The command uses a temporary
loopback-only SSH forward through the existing same-box gateway maintenance
path. It runs as the invoking `smith`, including on the matching standby,
and adds no listener on the gateway's Tailnet interface. It grants no deployment
or promotion authority. Ops receives no global proxy or VPN DNS setting.
Other declared clients reconstruct their managed DNS, login environment and
GitHub proxy settings from Instance. Removing a client removes those settings
at convergence.
Instance `boxes.<box>.routing-profile` selects a public domain-list URL,
bootstrap DNS addresses, and HTTPS DNS endpoints. The Instance contract owns
these fields. Platform rules reject private, loopback, link-local and Tailnet
destinations before checking the domain list. List matches use the VPN; other
public destinations use direct access. A null list selects direct web access.
Normal DNS always uses the selected HTTPS endpoints through the VPN, without
direct fallback. Bootstrap DNS serves only gateway and relay initialization.

Routing profiles apply when a VM is created or replaced. Freeze the selected
profile in its existing protected assignment and operation inputs. A running
VM, an accepted replay, and explicit maintenance retain the installed profile.
Instance edits do not trigger work. Current client grants and gateway removal
remain effective at explicit convergence. Network execution must use the
installed gateway's DNS endpoints until replacement selects its new profile;
an installed profile cannot grant clients or enable an undeclared gateway.
Historical assignment data is lifecycle input, not a new authorization source.

Mihomo updates the installed domain-only URL daily through the VPN, with a
bounded cache identified by URL. An unavailable refresh retains that URL's
last usable list. A newly selected URL cannot inherit a different list's cache.
Subscription rules, listeners, providers and scripts cannot grant authority.
The named malicious list provider can misclassify public destinations, but
cannot replace private-network rejection, client grants, or DNS transport.

For workload clients, convergence verifies gateway DNS before selecting it. These clients
register only the gateway as their managed OpenResolv source at each network
startup. Their guest output firewall rejects ordinary DNS to other addresses.
Tailscale keeps local Tailnet DNS handling. Gateway outage fails closed for normal
DNS; it does not select public fallback servers. Generated `/etc/resolv.conf`
and `/run/resolvconf` are runtime output, not replacement configuration inputs.
The public list maintainer controls only public destination classification.
A compromised list can misclassify public traffic; it cannot replace the
Platform's private-network rejection, client grants, listeners, or router rules.

The named attackers are an undeclared VM, a malicious proxy client, and a
compromised VPN process or provider. Exact router flows and guest input rules
exclude undeclared clients. Guest output rules prevent the proxy account from
opening private-network connections. Xen contains guest compromise; router
rules limit its cross-zone access. A same-zone guest with root can spoof an
underlay address; source-IP access is not a new authenticated identity boundary.
Do not authorize a same-zone client when that distinction is required. A
compromised gateway can observe proxy destinations and interrupt downloads;
HTTPS certificate checks and artifact signatures/checksums remain mandatory.

The active controller installs the proxy through existing Ansible authority
and the shared installation lock. It retains the protected subscription source
for reconstruction and delivers the credential-bearing URL to the guest.
Mihomo downloads proxy definitions through direct HTTPS every 24 hours, with
exact-host bootstrap DNS and a private native cache. Platform policy grants
outbound TCP 5000 for subscription HTTPS in addition to existing TCP 443.
Controller uplink egress includes this provider port for reconstruction seed downloads. A compromised VPN process
can read this subscription URL, as it can read relay credentials. The provider
cannot change Platform listeners, routes, DNS policy, client grants or firewalls.
Native parsing and Platform-owned type exclusions and transport overrides replace
the controller's per-field subscription validation; guest and router firewalls
continue to constrain destinations and ports. Native connection failures trigger
relay tests; there is no scheduled relay testing or traffic-rate monitor.
The controller freezes a validated initial list for each new root.
A fixed fallback payload keeps the VPN group nonempty if its subscription cache
is missing or damaged and downloading fails. It contains one unusable SS entry
on loopback port 1, which the service firewall rejects. Native subscription data
replaces this placeholder when loading succeeds. This prevents Mihomo v1.19.25
from selecting its built-in direct fallback for an empty group. Ordinary
convergence preserves the guest cache. Manual refresh requests the native update
through the existing authenticated loopback API and controller authority. Image preparation runs on
the target box's own controller. Enrollment uses a fresh single-use key.
Removing a client revokes its proxy access on the next convergence, including
established connections at the guest input filter. Stopping the gateway does
not affect the maintenance path. Disabling the capability stops the VM and
removes autostart while retaining its disk. Reconstruct it from the qualified
image and protected controller subscription. See [VPN egress](vpn-egress.md)
for operations and offline-client limits.

### Routine service VM replacement

The active controller coordinates image preparation, replacement and checks for
`bak`, `dmz`, `iot`, `router`, `ops`, declared `air` and declared `vpn-egress` guests. Preparation runs on each
target box's own controller. Shared guests use one `shared-alpine-v1` image per
box; the gateway uses `vpn-egress-alpine-v1` and the router uses `router-alpine-v3`.
Controllers use `ops-alpine-v1`; runners use `air-alpine-v1`. Controller replacement retains its
standby-only authority checks. These roles share replacement, recovery, retirement,
image dispatch and scheduling.

A service replacement uses the existing protected VM transaction and boot
recovery records. It freezes the public revision, Instance revision, image and
configuration for an unfinished operation. Current controller authority and
Instance placement are checked independently before disk transitions. A change
to configuration alone does not allocate a disk or restart a guest. A guest
declared stopped remains stopped during its nightly invocation.

The initial shared-guest workflow supports empty guests and stopped retained
workloads. Running application workloads require application compatibility
support before automatic replacement. Stopped containers, images, volumes and
numeric ownership remain retained data. Their absence from active application
declarations is not permission to delete them or start them.

BAK, DMZ and IoT creation and replacement use the same qualified shared image
and Ansible configuration. The OS root is disposable. A separate data LV is mounted at
`/srv/retained`; it holds rootless Podman storage and application datasets under
`apps/`. Bind-mount Podman storage at its stable guest path. Runtime storage
remains volatile. Storage consumers must refuse an absent or incorrect data
mount. OS packages come from the image; guest configuration does not upgrade
packages. Container image updates and application recovery are separate work.

A BAK, DMZ or IoT replacement reuses the exact recorded data LV and filesystem
UUID. It does not copy or snapshot data. Stop the old VM and prove exclusive disk use
before attaching the data disk to the candidate. Only recorded persistent data
may occur in both generations; OS roots must remain distinct. Preserve numeric
ownership and the small machine, SSH and Tailscale identity records. Reconstruct
managed configuration from approved inputs, not from the previous root.
If the old guest has no SSH host keys, the new root must also have none. Tailscale
SSH is the maintenance path.

Controller and runner state uses a 50 GiB retained data LV. Grow an existing
controller mirror LV without changing its LV UUID or filesystem UUID. Keep
controller roots at their existing size and runner roots at 50 GiB. First
migration requires explicit `--migrate-root-data`. Copy state only while the old
VM is stopped, inside the networkless finalizer. Complete the offline filesystem
check and growth to the assigned device size before copying state. Verify copied
content, numeric ownership and metadata before recording completion. Bind the migration to its
source identity and operation. Resume that operation after an interrupted copy.
A different first migration must not reuse its completed seed. Routine replacement
attaches the same data LV and never restores old root data over it.

For `ops`, migrate the existing controller state allowlist: read and recovery
keys, private working files, frozen operation inputs, journals, audit records,
update history and download-source selection. Bind writable directories, including
`/home/smith/private/klokast`, so atomic file replacement works. Reconstruct the
Instance checkout and generated controller tools there from approved sources;
do not seed source checkouts from the old root. Provider mutation credentials
and HA authority markers are excluded. Recheck the live standby, current placement
and credential absence before shutdown. Authority handoff remains a separate
operator workflow under the [controller model](#controller).

For `air`, retain `/home/agent`, same-machine Tailscale state, and the runner archive
copy. Keep the archive bind read-only. Preserve numeric account identities and
leave `agent` outside privileged groups. Reconstruct native tools and managed
configuration from the qualified image and frozen inputs. Nightly execution skips
an air guest if any process has the agent UID, including shells and background
jobs. Check before image preparation and again before shutdown. An explicit new
operation may record `--maintenance` to permit the planned stop.

Both roles require the exact retained mount and binds before Tailscale, SSH or
cron starts. Common boot assignment records become the sole generation owner
at migration. Old provisioning and replacement commands must refuse that owner;
historical unfinished operations keep their frozen implementation. Generation
retirement excludes retained volumes. Controllers and runners keep only the
current accepted root after reboot verification.

The first BAK, DMZ or IoT transition can explicitly discard root-resident
development data with `--discard-root-data`. Bind this decision to the source root UUID in the
protected operation. It never authorizes deletion of a separate data volume.
Nightly execution cannot grant this permission. Retain the current and previous
accepted OS roots and their boot evidence; persistent data is excluded from
generation retirement. An older root is not a point-in-time data backup.

Dom0 treats guest filesystems as opaque. Filesystem work runs in a networkless
Xen guest. Historical BAK and IoT operations keep their frozen state-copy
implementation. Legacy disks outside accepted generation history remain retained.
VPN creation and replacement use the same image personalization and Ansible
configuration code. VPN reconstruction preserves
only Tailscale identity, machine ID, SSH host keys and required numeric account
identities. Account definitions, networking, DNS, firewall, OpenRC service and
VPN configuration come from Platform code, Instance and protected controller
inputs. Packages come from the qualified image. Mihomo comes from the approved,
checksum-verified artifact. Caches are reconstructed. Required private artifacts
and the selected domain list must be validated and frozen before shutdown.

An explicit VPN rebuild replaces the root even when the image is unchanged.
It can repair package and managed configuration drift. Conflicting ownership,
unexpected persistent storage or unavailable maintenance access stops it.
The operation snapshot binds implementation modules, Instance, rendered
configuration and required artifacts. Resume uses these frozen inputs while
checking current authority independently. Historical operations retain their
verified implementation and recovery rules; missing inputs are never inferred.

VPN retention keeps the current root and one previous accepted generation.
Checked deletion plans bind LV UUIDs, ownership, boot checksums and live
references before removal. Incomplete operations and unknown resources stay.
Interrupted cleanup resumes its recorded plan. Retired image references are
released, but audit evidence stays; accepted replay cannot restore references.
An incomplete replacement blocks fresh work and reports its resume command.
After the new guest starts, recovery preserves that generation and does not
select an older disk automatically. Automatic recovery of an unreachable VM
is outside this workflow. The transaction assignment owns VPN boot and runtime
intent; the original infrastructure assignment remains historical.


### Dedicated VPN

`<box>-household-vpn>` is the household/admin client VPN gateway.

See:

- [Household VPN](../apps/household-vpn/README.md)

### `<box>-usr-<slug>`

Dedicated per-user application VM.

The `usr` zone contains dedicated per-user VMs. Applications must request a
user-specific hostname and cannot request a shared host in this zone.
Addresses `192.168.175.10` and `192.168.175.11` are reserved; neither is an
application endpoint.

Role:

- belongs to the `usr` zone;
- hosts private workloads for one internal user;
- has no public exposure by default;
- has no backend, LAN, or IoT access unless explicitly authorized;
- may run an approved guest OS such as Debian or Ubuntu.

Hostname:

```text
<box>-usr-<slug>
```

## Layer 3: Services

### App manifests

An application manifest is public, app-owned intent describing what an application requires without containing deployment-private bindings.

An application may declare requirements such as:

- compute type;
- workload zone;
- network flows;
- Tailnet identity or grants;
- storage;
- artifacts;
- privileged or dedicated execution requirements.

Application manifests must not contain deployment-specific authority such as:

- concrete box placement;
- private IP assignments;
- router interface names;
- real MAC addresses;
- private user identities unless intentionally public;
- secrets or provider credentials;
- arbitrary firewall programs;
- arbitrary privileged commands.

The application manifest requests resources. It does not grant them.

### Resources

Services run as rootless containers inside shared zone VMs by default.

The main application resource types are:

- shared service zones: `bak`, `dmz`, `iot`;
- `per_user_app_vm`;
- `app_vm`;
- `managed_iot_device`;
- artifacts;
- controlled builders.

A dedicated VM is used when an application requires a materially different isolation or lifecycle boundary, for example:

- untrusted code execution;
- rootful Docker;
- privileged networking;
- PCI or USB passthrough;
- VPN leak containment;
- another OS or kernel boundary.

Shared service VMs in `bak`, `dmz`, and `iot`, and dedicated workload VMs
in `usr`, are not credential-bearing infrastructure-control environments.

Application manifests cannot place workloads in `ops`.

The private Instance selects deployment-specific placement and authorized capabilities.

The Platform Resource Control Plane translates app requirements and Instance policy into concrete infrastructure resources.

### Linux accounts

#### `neo`

Human administration and recovery account.

On infrastructure machines it may have privilege escalation appropriate to the machine role.

`neo` is primarily a human recovery path and is not the normal autonomous control interface.

#### `agent`

Runs the `admin-agent` on `<box>-air` or an approved `<cloud>-ops` runner.
Legacy `<box>-ops-airunner` containers are accepted during migration.

It owns the AI runtime, its sessions, tools, public source checkout, and credentials needed for those purposes.

The privileges of `agent` depend on the Platform lifecycle mode.

In **development**:

- the admin-agent may modify Platform source;
- it may add or change privileged automation;
- it may deploy from the mutable development tree;
- it may obtain direct administrative access needed for Platform development;
- no production Integrity-TCB guarantee is provided.

In **production**:

- the admin-agent may inspect state, diagnose problems, propose changes, and invoke existing Platform syscalls;
- it may write proposed Platform code but cannot make that code privileged or executable as part of the Platform;
- it does not have a general-purpose `smith` shell, unrestricted infrastructure SSH, arbitrary Ansible execution, or equivalent privileged programming interface;
- it does not hold controller-private infrastructure credentials.

There is no separate `development-agent`. The same admin-agent operates under different privileges according to the lifecycle mode of the Platform deployment.

#### `smith`

Privileged infrastructure execution account on a controller.

`smith` may:

- execute infrastructure automation;
- manage Platform topology;
- apply firewall and network state;
- manage Xen resources;
- invoke credential brokers and builders;
- perform other privileged operations implemented by the Platform.

In production, `smith` is an implementation identity behind the Platform's privileged API. The admin-agent does not receive unrestricted access to it.

In development, direct use of `smith` may be permitted because development intentionally does not enforce the production code-integrity boundary.

Running as the non-root UID of `smith` does not remove its administrative
authority. Development controllers permit root escalation. Image input
preparation runs with this UID; downloaded package installation scripts run
only inside the isolated build VM.

#### `minion`

Restricted application-automation account.

It may perform app-local lifecycle actions using sanitized, app-scoped Platform grants.

It must not independently change infrastructure policy, Xen state, router policy, or Platform credentials.

#### `oracle`

Read-only inspection and verification account.

It receives only the access required to collect or consume sanitized Platform observations.

It has no general privilege escalation or infrastructure mutation authority.

### Infrastructure Services

Infrastructure Services operate the Platform.

During bootstrap, the controller and admin-agent runtime may initially run on `<cloud>-ops`.

After the first box is ready:

- the active controller normally moves to `<box>-ops`;
- the admin-agent normally runs in `<box>-air`;
- an approved cloud admin-agent runtime may remain online without controller-private credentials.

Only one controller is active at a time.

### `airunner`

`airunner` is the runtime environment for the admin-agent.

It contains:

- the AI coding/administration client;
- the model/API credentials required by that client;
- public Platform source;
- agent sessions and tools;
- runner-specific Git credentials where required.

It does not need controller-private Instance data or infrastructure provider credentials.

Its authority depends on lifecycle mode:

```text
development:
    admin-agent
        -> mutable Platform source
        -> development administrative interfaces
        -> infrastructure

production:
    admin-agent
        -> Platform syscalls
        -> admitted Platform implementation
        -> infrastructure
```

The production airunner is therefore not part of the Integrity TCB merely because it makes administrative decisions.

Compromise of the production admin-agent may cause misuse of capabilities already exposed to it, but must not permit the attacker to redefine the privileged mechanisms themselves.

Multiple airunners may exist, although the active set should remain small.

Migration preserves runner-owned repositories, uncommitted work, sessions,
configuration, skills and the same operator's runner credentials. Stop source
writers before the final copy and validate SQLite databases and ownership before
destination use. Before destination writes, a failed cutover leaves or restores
the source. After destination writes, keep both copies and reconcile before
rollback. Retired container data and its service definition remain offline
rollback material. Controller placement and naming do not change with runner
migration.

### `klokast`

`klokast` is the Platform's versioned Go tooling.

Its responsibilities include Platform and Instance contracts, validation, derived views, diagnostics, and other deterministic control-plane functions.

The private Instance owns deployment-specific desired state. `klokast` does not acquire authority merely by interpreting a Git repository.

Observation is runtime evidence and never becomes desired-state authority.

Production authority comes from:

- the admitted Platform release;
- authorized Instance policy;
- the privileged interfaces exposed by that release.

`klokast` may contain schemas, canonical templates, public catalogs, and application manifests required for deterministic Platform behavior.

The exact CLI commands are implementation details and may evolve without changing the architecture.

### Platform syscalls

Platform "syscalls" are the API that exposes safe and bounded operation for the autonomous administration of the Platform and its boxes and applications, for tasks such as install, update, inspect, reconciliate, backup, restore.

### Resource compiler

The resource compiler renders authorized application and Instance intent into concrete infrastructure configuration.

It may produce:

- router firewall policy;
- VM firewall policy;
- application VM metadata;
- network identities and grants;
- app-scoped grants used by application automation.

The compiler does not decide which new authority should exist.

Application manifests describe requirements. Instance state provides deployment-specific authorization and placement. Platform topology provides concrete infrastructure bindings.

The compiler converts these inputs into enforceable state.

### Mapper and verifier

The mapper observes Platform state.

The current discovery tooling collects facts from sources such as:

- Tailscale;
- cloud providers;
- Xen;
- storage;
- Podman;
- Platform services.

The resulting observations are used for:

- diagnostics;
- dynamic inventory;
- verification;
- reconciliation.

A missing or unreachable target must not be replaced silently with stale facts.

Observation remains evidence only.

It cannot independently:

- change desired state;
- select new placement;
- grant capabilities;
- create Platform authority.

See [Platform Map](platform-map.md).

### Controller

The controller runs on `<box>-ops`.

The active controller owns Platform-wide mutations. Each configured, unfenced
box controller also owns image preparation for its own box, as described below.

It is the execution locus for:

- privileged infrastructure automation;
- Platform syscalls;
- infrastructure reconciliation;
- credential brokers;
- app-scoped workflows.

It is also the main custodian of active controller credentials.

Controller HA is active/standby for Platform-wide authority.

Local image preparation includes public input downloads, template construction,
isolated qualification, same-box reuse, and checked cleanup of unused images.
Cleanup can retire legacy image copies on that box after it verifies their
original build evidence and all local references. A replacement image must
have been built and qualified on the local box.
Both active and standby controllers can do this work independently. The local
controller identity must match the target box. A fenced controller cannot do
this work. These checks constrain supported operations; they do not contain a
compromised development account with root access.

Local image preparation does not require the active controller, Instance
credentials, or a new account. Its inputs and qualification records stay on
the local `ops`; images stay on its dom0. Only public build receipts may be
copied between controllers for local history or deployment consumers. No input archives or image disks
are distributed across boxes. If the local `ops` is absent, preparation stops;
first-controller provisioning is a separate workflow.

The local authority does not include deployment to running VMs, topology,
enrollment, credential brokers, or controller promotion. Those operations
retain the active-controller requirement. The nightly controller coordinator may
request image preparation for the declared standby at 03:00 UTC. The active
controller alone coordinates this work; standby scheduled invocations skip. See [image preparation](platform-updates.md#golden-image-builds-and-isolated-tests).

Before another controller becomes active, the previous active controller must be fenced.

Controllers are rebuilt from the public implementation repository and the
private Instance repository. Do not copy secrets, archives, or controller
history between boxes. Each controller has its own Tailscale identity,
Instance read key, and scoped provider credentials. The operator installs
separate credentials only after promotion. Before promotion, revoke and remove
the old controller's provider mutation credentials and fence it. Check the
destination's Git, Instance, recovery access, and public tools before this
step. Then promote the destination, install its independent credentials from
the workstation, and verify them. A promoted controller awaiting credentials
is an incomplete handoff; the handoff command must report a resumable operator
step and must not report completion. Credentials remain root-protected;
Platform-wide mutation workflows still require the
active-controller guard. Local image workflows use the same guard with an
explicit matching-box requirement.
Install the public Tailnet policy tools on both controllers. Keep Tailnet policy
API credentials only on the active controller. Install Freebox credential tools
only on controllers for boxes in France.

Fencing is a human recovery action. The guard prevents accidental concurrent
operations through the installed tools; it does not contain a compromised
controller root account with provider credentials. Revoke the failed
controller's provider clients and repository keys when it cannot be trusted.

Controller placement in Instance and the local HA marker must agree before
normal operations. Promotion does not require the failed controller's files.

See [Platform deployment and controller recovery](platform-deploy.md).

Routine controller replacement preserves the identity of the same controller;
it is separate from credential rotation. The active peer replaces only the
standby, using an explicitly selected image built and qualified on the target
box. Image preparation, authority handoff, and replacement are separate
operations. Protected infrastructure records on the target dom0 bind the
image, configuration, exact disks, and progress. Unknown disks require an
explicit one-time adoption record before replacement.

First migrate allowlisted state to retained storage through the
[common lifecycle](#routine-service-vm-replacement). Routine replacement reuses
that volume and copies only the small machine identity records inside a
networkless Xen guest on that box. Dom0 does not mount guest filesystems. Never boot both
copies of an identity. Reconstruct managed configuration from public code and
Git-approved Instance. Do not restore an old active marker or provider mutation
credentials. After replacement boot, rollback must preserve newer private state
or refuse automatic recovery. Apply the root retirement rules below after verification.
The runner has a separate disk and lifecycle; controller replacement grants
no authority to stop, copy, or change it.

An unfinished replacement freezes its image, public implementation commit,
Instance commit, and rendered configuration. Execute its playbooks from verified
controller-local source snapshots. Recheck current controller authority before
destructive actions; a historical snapshot cannot authorize a former standby.
If a frozen reader cannot read its own protected pre-boot record because of a
code defect, the active controller can stage a corrected recovery reader from
approved public code. Use it only to recover and abandon that exact unbooted
attempt. Keep the frozen request and inputs unchanged. Install corrected boot
recovery only after recovery completes, then use a new replacement operation.
After acceptance, these inputs are historical evidence. Explicit configuration
updates use `converge-ops-controller` and record verified convergence in the
protected assignment. A source change alone does not replace a controller disk.

Retain only the current accepted controller root and its boot files after reboot
verification. Keep the source root until replacement and reboot checks pass.
Incomplete operations retain all required resources. The retained data volume
is excluded from root retirement.
After a current controller generation is accepted, retirement can also remove
an abandoned candidate root whose protected transaction proves recovery before
candidate boot. Keep its audit records and retained data.
Retirement records an exact plan before deletion and verifies disk UUIDs,
ownership, boot checksums, and absence of guest, mount, and loop references.
An untagged legacy controller root requires its protected adoption record and
unchanged LV UUID. New ownership tags stop its retirement.
Unknown resources stay in place. Keep immutable common audit records and checked retirement receipts; accepted
replay must not restore retired references. Completed legacy controller records
are compacted after checked retirement.
Failed qualification cleanup has its own evidence and does not imply success.

The nightly coordinator uses existing locks and bounded jobs, waits for their
completion, and keeps private results. It may retire old resources, prepare a
qualified standby image, and replace the standby when the build ID differs.
It performs no configuration-only convergence, authority handoff, credential
installation, or automatic rollback after boot. An incomplete replacement or
reboot verification blocks a new replacement and reports its resume command.

### Credential broker

Credential brokers are deterministic privileged Platform components.

They perform narrow operations using credentials without exposing those credentials to callers.

The admin-agent should request semantic actions rather than receive the underlying provider, enrollment, or infrastructure secrets.

Examples include:

- minting a scoped machine identity;
- applying provider configuration;
- creating short-lived credentials;
- operating an external service through a narrowly scoped API.

Brokered credentials remain outside AI model context whenever practical.

### Builders

Different builders produce different artifact types.

#### Platform builder

Builds Platform binaries or other Platform artifacts.

In production, artifacts that become privileged Platform implementation are usable only through the Platform-release admission process.

Build isolation, reproducibility, and network restrictions are implementation and supply-chain controls; they are not a separate runtime authorization system.

#### Platform image builder

Builds OCI images and related deployment artifacts.

Inputs should be pinned or authenticated where appropriate and outputs should have stable artifact identities.

#### Bootstrap ISO builder

Builds generic bootstrap media.

The resulting image contains no permanent box identity or reusable enrollment credential.

Builder privilege remains bounded by the Platform mechanism that implements it.

### Artifact store

Artifact storage is a distribution mechanism, not an authority source.

Stored artifacts should be immutable or content-addressed where practical.

Possession of an artifact in the store does not by itself make that artifact trusted.

Production Platform artifacts must still belong to the admitted Platform release or another authorized artifact source.

### Encrypter and uploader

Off-platform backup may use separate encryption and upload roles.

A useful boundary is:

- encryption happens before data leaves the trusted Platform environment;
- upload components receive ciphertext only;
- off-platform storage receives no plaintext recovery material;
- cloud upload credentials should be limited to the required storage operation.

A general implementation is not required by this architecture.

### User Services

User Services are applications installed and activated by the user.

Public app manifests define their supported resource requirements.

The private Instance selects:

- presence;
- placement;
- enabled features;
- capabilities;
- retained datasets.

The Platform supplies authorized resources and app-scoped inputs. Application
code owns configuration, installation, verification, backup, and removal.
Keep that code under `apps/<app>/`. Foundation automation must not dispatch
application-specific lifecycle commands or require an adapter for each app.

Installing an application starts with its declaration in the Instance. This
requirement applies in development and production. See
[Platform lifecycle](platform-lifecycle.md#application-installation) for the
production boundary and [Instance desired state](klokast-instance-specification.md#application-dependencies)
for explicit dependencies.

Examples include:

- Nextcloud;
- photo storage;
- print services;
- VPN clients;
- Git hosting;
- media applications;
- public connectors.

### Shared application helpers

The Platform may provide small helpers for application-independent deployment
mechanisms. Applications keep ownership of their configuration, policy, and
lifecycle. Shared helpers must not dispatch application-specific lifecycle
commands or depend on an application's implementation. Each application must
remain installable without another application merely to obtain helper code.

A helper operates within the caller's existing authority. Importing or invoking
it grants no additional authority. Privileged operations, resource grants, and
infrastructure credential access remain with the existing Platform mechanisms
and brokers. Code admission follows [Platform lifecycle](platform-lifecycle.md).

The [helper catalog](../ansible/lib/app_support/README.md) owns the supported
interfaces, source locations, extraction rules, and delivery instructions.
Application runners remain application-owned as described below.

### Target-local application runner

A target-local application runner belongs to the application that uses it.
The Platform does not require a common application orchestrator or runner.

It is distinct from the privileged Platform controller.

A target-local runner may:

- receive app-scoped desired state;
- validate its grant;
- render local container configuration;
- start or stop approved application runtime;
- report machine-readable status.

It cannot create infrastructure authority or grant itself new Platform resources.

Artifact transfer and delivery of app-scoped desired state use existing
Platform resource mechanisms and application-owned tooling. This delivery
does not require a separate shared application component. For the current
Nextcloud runner, see [Nextcloud v2](../apps/nextcloud-v2/docs/architecture.md).

### Application presence and retained data

The Instance declares whether an application is desired.

Removing an application does not implicitly authorize destruction of durable user data.

Durable datasets and reconstructable runtime state are distinct.

Data destruction is an explicit operation.

Application backup, promotion, and runtime repair remain app-specific where necessary.

## Layer 4: SDN (Software Defined Network)

### Zones, realms, and capabilities

Workload zones are:

- `bak`;
- `dmz`;
- `iot`;
- `usr`.

`ops` is control-only.

Application manifests cannot request normal workloads in `ops`.

External network realms include:

- `wan`;
- `household`;
- `admin`;
- `ap-uplink`.

The Instance declares which connectivity capabilities are available and enabled for each box.

Applications request symbolic flows.

The Platform resolves those requests into concrete:

- interfaces;
- IP addresses;
- router policy;
- VM firewall rules;
- Tailnet grants.

Enabling a box capability does not itself open an application port.

A concrete application flow must also be authorized.

Unknown resource fields, unsupported capabilities, and missing required capabilities fail closed.

### Overlay management plane

Tailscale is the current overlay and remote-management network.

Most application containers inherit their VM's network identity.

A container receives a separate Tailnet identity only when an independent identity or ACL boundary is required.

Current Platform identities include roles such as:

- operator;
- controller;
- admin-agent runtime;
- dom0;
- VM;
- out-of-band recovery;
- app-specific identities.

Application identities must not use control-plane tags that would give them infrastructure authority.

The overlay provides connectivity and identity transport. It does not itself define Platform authorization.

## Special nodes

### 1. `og`

`og` is the trusted deployment MacBook used by the human to manage the Platform.

It is the root user-interaction point for high-authority decisions.

The human command interface on `og` is
[`kk platform`](../klokast-dev/README.md#platform-commands-with-kk-platform).
It is intended to provide human-understandable operations such as:

- add a box;
- install an application;
- approve a new application capability;
- admit a new production Platform release;
- perform recovery actions.

High-authority operations should use strong local user authentication, preferably hardware-backed and biometric where available.

The human authorizes semantic intent rather than generated nftables, Ansible, Xen, or shell implementation details.

`og` may also use direct console or operator access for recovery.

The production admin-agent does not inherit those human recovery permissions.

### 2. `<cloud>-ops`

`<cloud>-ops` is a temporary or optional cloud-hosted infrastructure machine.

During bootstrap it may temporarily host:

- the active controller;
- the admin-agent runtime.

When controller authority moves into `<box>-ops`, the cloud machine must lose controller-private state and controller credentials.

If retained as an admin-agent runtime, it receives only the permissions appropriate to that role and the deployment lifecycle mode.

A cloud host must not continue to present itself as the active controller after its controller role ends.

### 3. Out-of-band access

`oob` is the NanoKVM device used for pre-boot and emergency recovery.

It supports:

- BIOS/UEFI interaction;
- bootloader interaction;
- local console access;
- virtual USB media;
- bootstrap and recovery ISO loading.

It is operational recovery infrastructure rather than a normal application or controller execution environment.

The human may physically move the device between boxes when needed.

# Persistence

Persistent state is divided by purpose and authority.

### Public Platform implementation

`klokast/klokast-box` contains:

- generic Platform implementation;
- schemas;
- Platform tools;
- public application manifests;
- automation;
- templates.

In development, a deployment may execute directly from mutable Platform source.

In production, source existing in this repository has no privileged authority until it belongs to the Platform release admitted for that deployment.

### Private Instance repository

The Instance repository contains deployment-specific durable desired state.

It is not:

- an audit log;
- observed runtime state;
- secret storage;
- arbitrary executable Platform code.

Authority-expanding Instance changes require the applicable human authorization.

Routine reconciliation does not require the human to re-authorize the concrete operations needed to implement already-authorized Instance state.

`klokast-box` defines the public Platform implementation and the capabilities Klokast deployments can provide.

Each deployment has a private **Instance**, stored in `klokast-instance.json`, describing its desired state: boxes, placement, applications, capabilities, and other deployment-specific policy.

The Instance contains intent, not executable Platform code or observed runtime state. The Platform validates and reconciles that intent according to the currently admitted Platform release.

### Public download sources

The Platform derives public download sources from each box's validated
Instance `country`. Source endpoints belong to the public Platform
implementation. Instance contains the country, not mirror URLs. Generated
inventory supplies the selected sources to the target's installation tasks.
Only the selected box's settings and public inputs are needed on a standby
controller; its private Instance repository is not replicated for downloads.

Go module downloads use `https://goproxy.cn` for `CN` and
`https://proxy.golang.org` for other countries. Both use the signed
`sum.golang.org` checksum database, which the module proxy can relay.
The checked-in `go.sum` remains binding. A malicious mirror must not be able
to replace a recorded dependency or disable verification. A failed download
stops the operation; it does not permit an unchecked source or a cross-box
module cache transfer. Verifier build records include the selected sources.

Alpine mirror selection runs on the box, once during setup or on explicit
refresh. Instance `country` orders a small public list of approved HTTPS
mirrors. Bounded download samples select a primary and a fallback; ping alone
does not measure transfer performance. A missing country does not prevent
selection. Normal downloads reuse the saved choice without periodic probes.

The controller keeps this observed choice at
`/var/lib/klokast/download-sources/alpine.json`, backed by its retained data
volume. It does not belong in Instance or in dom0's persistent configuration.
The volume is separate from the controller OS root. Replacement reattaches
the same LV and filesystem UUID; generation retirement must never delete it.
The first consumer is mirror-selection state. Other controller state keeps
its existing preservation contract until explicitly migrated.

Ansible passes the selected mirror to host repository configuration and to
containers that install Alpine packages. Image builds use the mirror of the
box where downloads occur. Container runtime downloads use their host box's
choice. Alpine requests use direct HTTPS, outside the web proxy. Repository
branches, upstream signing keys, APK signature verification, frozen input
checksums, and image qualification remain binding. A transport failure can
try the saved fallback once; it cannot substitute different frozen bytes.
No new daemon or scheduled benchmark is required.

Country selects transport, not package versions or controller authority.
Each box can resolve recent versions independently. Further package and
container registry sources must use explicit Platform mappings and retain
their upstream verification rules. Container mirror mappings are not yet
implemented.

### Controller secrets

Each controller's credentials and secrets remain outside Git, under controller-owned storage such as `/etc/klokast` or another root-protected location.

The admin-agent should not receive raw credentials when a brokered operation is sufficient.

### Controller operational state

Generated state such as:

- observations;
- dynamic inventory;
- audit records;
- temporary build state;
- recovery information;
- rollback material

belongs in controller operational storage such as `/var/lib/klokast`.

Generated state does not become desired-state authority.

### Application storage

User-service data remains in application-specific persistent storage and follows the retention, backup, and recovery rules of the corresponding application.

### Operations journal

Short operational notes and agent handoffs may use the active-controller [operations journal](operations-journal.md).

The journal provides context only and grants no authority.

Platform site time is `Etc/UTC`.

# Platform lifecycle

The lifecycle model has two modes: development and production.

## Development

Development is intended for rapid Platform iteration.

The admin-agent and human developer may modify privileged code and automation directly.

A development deployment may execute from a mutable source tree.

The production Integrity-TCB guarantee does not apply.

Development should therefore use credentials and assets appropriate to that reduced guarantee.

## Production

Production executes only an admitted Platform release.

The admitted release contains the privileged Platform implementation, including relevant:

- executors;
- compilers;
- schemas;
- playbooks;
- automation.

The admin-agent may invoke operations supplied by that release but cannot make newly generated privileged code executable.

A new Platform release changes the Integrity TCB and requires human admission.

Development work may produce a release that is later admitted to a production deployment, but a production deployment does not become development by changing a runtime flag.

See [Platform lifecycle](platform-lifecycle.md).

# Desired-state and Apply flow

The Platform separates four concepts:

1. **Platform implementation** — which privileged mechanisms exist;
2. **Instance desired state** — what this deployment is authorized to contain and permit;
3. **Observation** — what currently exists;
4. **Apply** — execution (using Platform syscalls) that reconciles authorized desired state using the allowed Platform implementation.

Conceptually:

```text
                       human
                         |
          +--------------+--------------+
          |                             |
   admits Platform                 authorizes durable
      release                       Instance authority
          |                             |
          v                             v
 approved Platform              authorized Instance
 implementation                    desired state
          |                             |
          +--------------+--------------+
                         |
                         +------ observed runtime state
                         |
                         v
                       apply
                         |
                  validate operation
                         |
                         v
               approved automation
                         |
                         v
                     runtime
                         |
                         v
              observation + audit
```

Apply may be initiated by:

- the admin-agent;
- scheduled automation;
- the Klokast application;
- recovery workflows;
- other approved Platform mechanisms.

Apply does not create new authority.

If the requested result needs a capability not authorized by current policy, the operation stops and requests the appropriate human authorization.

Apply also does not decide which Platform code is trusted.

In production, if an operation requires a privileged mechanism not present in the admitted release, the admin-agent may diagnose the gap and propose code, but that code becomes executable only through a new human-admitted Platform release.

Routine operations inside existing authority require no additional human authorization.

Examples include:

- recompiling and applying firewall policy;
- replacing or restarting a failed service;
- A/B updating an application container;
- renewing certificates;
- running backups;
- applying routine package updates;
- restoring authorized state after drift.

See:

- [Apply specification](apply-specification.md)
- [Threat model](threat-model.md)

# External dependencies and trust boundaries

| External system | Trust boundary |
| --- | --- |
| GitHub | Hosts public Platform source and private Instance source. A public branch does not become production Platform authority by itself. Production code authority comes from Platform-release admission. Private Instance changes remain subject to Instance authorization policy. |
| Tailscale | Provides overlay connectivity, identities, and remote-management transport. Tailnet membership does not by itself grant arbitrary Platform mutation authority. |
| Cloudflare Tunnel | Provides optional public ingress through approved DMZ connectors. Tunnel credentials do not grant controller authority. |
| Cloud providers | May provide bootstrap compute or optional admin-agent runtimes. The provider controls its hosted machine; controller authority exists only while the machine has that explicit role. |
| Package and image providers | Supply software executed within defined Platform scopes. Their release/authentication mechanisms are part of the supply-chain trust accepted for that scope. |
| Off-platform storage | May store encrypted recovery and backup data. It should not receive plaintext or general Platform authority. |

External-service credentials remain outside application workloads and, where practical, outside the admin-agent.

# Initial Platform deployment

Bootstrap establishes the first controller and permanent box identities.

Typical flow:

1. The trusted Mac starts the Platform bootstrap workflow.
2. A temporary `<cloud>-ops` machine may initially host the controller and admin-agent.
3. The controller prepares a generic bootstrap ISO for NanoKVM.
4. The box boots the installer and exposes its local onboarding interface.
5. The human selects or confirms the box identity and authorizes onboarding.
6. The box receives a temporary bootstrap network identity.
7. Platform automation installs the diskless Alpine/Xen host.
8. The bootstrap media is detached.
9. The box boots its permanent dom0 environment and receives its final identity.
10. The controller provisions the router and required service VMs.
11. `<box>-ops` becomes the normal controller location and the temporary controller is fenced.
12. Any retained cloud machine is reduced to its intended non-controller role.

A production bootstrap installs an admitted Platform release.

A development bootstrap may operate directly from development source.

# Cybersecurity

Cybersecurity is a primary design constraint of the Platform.

The normative trust model is defined in [Threat model](threat-model.md).

The central rule is:

> Human-approved code defines privileged mechanisms; authorized policy defines their permitted scope; the admin-agent chooses when to invoke them.

The **Integrity TCB** contains the minimum components whose compromise can redefine or bypass Platform security policy.

Typical members include:

- dom0 and Xen;
- router enforcement;
- privileged controller components;
- Platform policy, compiler, and executor code;
- approved privileged automation;
- infrastructure credentials and authorization keys;
- the production code-admission mechanism.

User workloads are outside the Integrity TCB.

In production, the admin-agent is also outside the Integrity TCB: it operates the Platform but must not be able to redefine its privileged mechanisms.

Applications and user agents may request capabilities but must not directly control:

- dom0 or Xen;
- router or infrastructure firewall policy;
- Platform credentials;
- infrastructure identities;
- privileged Platform code.

The Platform additionally recognizes a separate **Financial TCB** for workloads such as Bitcoin and Lightning where compromise can authorize spending of financial assets.

The Financial TCB is not defined by ordinary Platform isolation alone and must be minimized according to the custody/signing architecture of the financial service.

The Platform favors:

- virtualization and isolation;
- least privilege;
- a small Integrity TCB;
- deterministic privileged mechanisms;
- bounded semantic APIs;
- autonomous operation inside already-authorized boundaries;
- backups and recovery;
- fail-closed handling of unknown authority.

Security mechanisms should be introduced where they preserve a real trust boundary rather than to add ceremony to routine operations.
