# Router release inspection

The router update profile is `router-alpine-v2`. It is separate from the shared
Podman VM profile. The shared build and replacement entry points continue to
reject routers.

Run router commands as `smith` on the active controller. The inspection command
uses the verified execution inventory and Ansible. It records package versions,
boot identities, effective SSH key fingerprints, and state-file metadata in
`/var/lib/klokast/updates/discovery/router`. It does not copy private state
contents. The command does not adopt a baseline or change a router.
It reports missing legacy baseline evidence as `adoption_findings`. A clear
inspection is still not an adoption receipt. In particular, the current router
must declare `/var/lib/misc/dnsmasq.leases` as its dnsmasq lease file, have no
first-contact root key, and have complete identity and boot evidence. The
readiness check also requires the production Tailnet tag, all three effective
SSH host keys, and file metadata that the fixed state-copy guest can read.
Inspection records the service account IDs and checks each retained file against
its allowed owners. Symlinked state paths and unsafe parent directories fail
inspection.
The common router recipe enables Alpine's NTP client. Update checks reject
inspection timestamps that are ahead of the controller. Correct a legacy router
clock through the bounded controller playbook before retrying; do not relax the
freshness check to conceal clock drift.
Baseline readiness also requires a locked root password, no running OpenSSH
server, and no OpenSSH server packages, executable, or service links. Removing
the root key alone is not sufficient. Older inspection records without these
checks cannot establish readiness.
Inspection compares the four core router files (interfaces, dhcpcd, dnsmasq,
and nftables) with the common Ansible templates rendered from execution
inventory. Missing evidence, unsafe file metadata, or a different checksum
blocks readiness. Inspection also compiles the current verified Instance
resources, uses its managed DHCP reservations and access settings in the common
renderer, and compares every router firewall include with that output. Missing,
changed, or extra includes block readiness. The supported profile requires an
empty dnsmasq include directory; an additional DNS feature needs an explicit
reconstruction adapter.
The dom0 inspection compares the legacy Xen file with the common template and
execution inventory. It also records the live Xen UUID and compares the running
domain's boot paths, disk, memory, vCPUs, and ordered network attachments with
that file. Duplicate or unsupported Xen statements fail inspection. A file on
disk alone cannot prove the running generation's identity.

```sh
ANSIBLE_CONFIG=ansible/ansible.cfg ansible-playbook -vv -i localhost, \
  ansible/playbooks/74-router-update-inspection-setup.yml
ansible/bin/platform-router-update inspect --box boxa
ansible/bin/platform-router-update resolve --branch v3.23
ansible/bin/platform-router-update build-template --box boxa \
  --inputs-directory /var/cache/klokast/updates/router/INPUT_OPERATION
```

`resolve` uses native APK in a fresh scratch root. It verifies repository and
package signatures and freezes the complete dependency closure, including
`linux-virt`, in `/var/cache/klokast/updates/router`. It executes no package
scripts. A failed resolution cannot publish a complete input manifest. These
inputs are build evidence; they do not authorize replacement.

`resolve-initial` reads the verified Instance version policy and selects the
newest supported stable Alpine branch that meets its configured delay. It does
not require an enabled or activated replacement policy. It requires the active
controller and activated engine. It freezes packages through the same `resolve`
path and writes `selection.json` beside `inputs.json`. A changed policy or
engine during resolution prevents publication of the selection receipt.
For a selected initial build, pass `--initial-selection` to `build-template`.
This checks the saved input identity against the current verified policy,
Alpine release metadata, branch delay, and activated engine before it allocates
build resources. A changed source requires a new `resolve-initial` run.
These commands select and qualify build inputs only. The provisioning wrapper
uses their frozen selection for the first router installation.

`provisioning-status --box BOX` reads the protected dom0 accepted, pending,
and first-install records through the installed router recovery reader. It
requires the active controller and exact activated engine. A pending
replacement blocks provisioning. The result is stored in a private controller
operation directory. Install and qualify the recovery reader through its
approved playbook before this read; a missing reader or unreachable dom0 is
an error, not an empty router target. The status alone does not prove that an
unrecorded legacy disk is safe to reuse. The phase 30/31 connection must also
check the declared legacy LV before any first allocation.

`provision-box` phase 30 checks the live WAN bridge, installs the approved
recovery reader, reads the protected status, and runs the
`provision-initial-phase --phase prepare` command. It saves one controller
pointer before retained preparation.
Phase 31 resumes the same operation through enrollment, offline cleanup,
verification, and first acceptance, then runs the read-only accepted-router
playbook. A provisioning rerun verifies an existing accepted generation and
does not run the former builder, package installers, or Xen guest renderer.
The native path remains unqualified until the exact engine is activated and
tested. Do not run these phases on a live box before that qualification.

An accepted template generation records the template operation and exact
release receipt checksum. The first-install record checks that checksum before
acceptance. A later update check must load that exact controller release,
verify it against the accepted generation and live router, and refuse a
missing or changed receipt. A template operation ID or receipt alone does not
authorize a replacement.
The template operation also stores its profile snapshot. Read-only checks must
validate an older accepted release with that snapshot, then resolve candidate
inputs with the current approved profile.
`check-template` reads the protected accepted generation, validates its stored
release against the native template result, and verifies the running router
through the accepted-router playbook and fresh inspection. It rechecks the
accepted source, policy, engine, profile, and release before it stores a
decision. For an authorized `update-required` result, it also records the
exact frozen input source operation and checks that its contents match the
decision. Later preparation must require both records and recheck their
policy, accepted generation, input identity, and age before allocation.
The command needs the exact activated engine and a verified Instance
inventory. It does not allocate or replace a router. Native qualification of
this command remains required before it can support unattended replacement.

`preflight-replacement` reads one saved `update-required` decision and its
frozen input source. It rechecks the current protected accepted generation,
signed policy, source bytes, profile, and activated engine. It reports only
`validated-input-source`; it does not grant disk allocation or cutover.
`prepare-replacement` now runs fresh live router checks, validates the
historical accepted release, and rechecks the saved decision, frozen source,
signed policy, and compiler output. It stages the exact job and bounded boot
parts first, then repeats the live and source checks before it grants dom0
allocation. It checks the retained result and the live old router again after
preparation. A retry uses the same operation ID and recorded disk.

```sh
ansible/bin/platform-router-update check-template --box boxa
ansible/bin/platform-router-update preflight-replacement --box boxa --check-operation CHECK_ID
ansible/bin/platform-router-update prepare-replacement --box boxa --check-operation CHECK_ID --template-operation TEMPLATE_ID
ansible/bin/platform-router-update stage-replacement-generation --box boxa --operation-id PREPARATION_ID
```

Replacement uses an automated A/B sequence. Prepare B offline while A serves
traffic. A and B have separate root-disk LVs in the same volume group.
Stop A, copy its retained DHCP state, boot B with the production network,
enroll B with its own Tailscale identity, then verify the complete service.
Accept B only after these checks pass. On failure or timeout, dom0 stops B,
copies its latest valid DHCP state back if B could have changed it, and boots
A with A's unchanged Tailscale identity. Only one router runs at a time.
Dom0 owns the fixed recovery deadline, including if the controller disconnects.

### Identity contract for A/B replacement

Each router generation must have its own Tailscale device registration and SSH
host keys. Keep the previous generation's registration usable for the full
rollback retention period. Never copy Tailscale state or SSH private keys between
generations. The same generation keeps its identity across reboots and retries.
These are separate lifetimes: Tailscale identifies a device through its machine
key, independently of its name. See [Tailscale identity](https://tailscale.com/docs/concepts/tailscale-identity).

Keep the logical box router role stable in approved topology. Give each device
a deterministic generation name and record its exact device ID, addresses, and
SSH fingerprints in protected generation evidence. Tailscale requires unique
machine names; see [machine names](https://tailscale.com/docs/concepts/machine-names).
The initial router retains `<box>-router`. A replacement uses
`<box>-router-<24-hex-generation-id>`; this name fits the 63-character limit
for the maximum supported box ID. The VM auth-key broker permits this exact
suffix with only `tag:vm`.
Controller inventory, enrollment brokers, policy checks, and Platform Map must
resolve the accepted or explicitly pending generation from that evidence.
A matching hostname or tag alone is insufficient. Retain the expected router
tags and permissions; a new generation must not gain additional access.

B's enrollment belongs inside the bounded A/B transaction, after A stops.
Reuse the brokered single-use enrollment and pinned first-contact transport
from initial installation. Bind the enrollment attempt and result to B's exact
operation, disk, and Xen identity. An uncertain attempt must be reconciled
before minting another key. Remove first-contact access and verify the final
runtime before acceptance. Enrollment failure, loss of controller access, or
control-plane failure must trigger local rollback without requiring enrollment
or a Tailscale API call to restart A. The deadline must cover enrollment,
first-contact cleanup, any required B reboot, and full service verification.
Dom0 holds the router transaction lock for this sequence. It records a single
enrollment attempt before the controller mints a key. The controller sends a
bounded, operation-specific enrollment result through a validated sideband
record; it cannot change the accepted assignment. Dom0 verifies that result,
including a new device ID, the exact generation name, `tag:vm`, SSH, the state
checksum, and usable Tailnet addresses. The result must differ from A's device
ID and match B's pinned first-contact host keys. Dom0 then
stops B, removes temporary access in a networkless finalizer, verifies the
runtime package and identity records, and restarts B. The controller then
checks the complete service and sends separate acceptance evidence. Dom0
commits B only after that evidence matches the final runtime. A missing or
uncertain signal, a cleanup failure, or a deadline causes local rollback.

Copy only the fixed DHCP state set between generations: dhcpcd DUID, IPv6
secret, and dnsmasq leases. Preserve LAN lease expiry and test old/new/old DHCP
compatibility. Do not copy WAN lease files: start each selected generation
with fresh WAN DHCP negotiation under the same MAC address and DUID. This does
not guarantee a different address. If B could have started, reverse copy
removes A's pre-cutover WAN cache before A starts. If B never started, A can
resume with its own unchanged state. Test acquisition failures
and allow time for DHCP in both the cutover and recovery budgets. See the
[dhcpcd lease-file contract](https://github.com/NetworkConfiguration/dhcpcd/blob/master/src/dhcpcd.8.in).
Reconstruct Tailscale preferences from
approved inputs and verify each generation against its own enrollment evidence.
After rollback, retain the failed B identity until the operation is reconciled.
Revoke a retired identity only through exact recorded device evidence, after
fencing and a fresh accepted-router check. Never delete the retained rollback
identity as a stale offline device. Cleanup must also exclude active and pending
identities and remain safe if interrupted.

The DHCP-only state-copy v2 path and its qualification scripts implement the
new transfer boundary in source. Their requests and receipts reject v1 evidence.
Replacement preparation now retains the frozen first-contact package set and
seeds pinned backend-only SSH access on B's detached LV. The proposed generation
binds this access evidence and records the qualified final package set as a
later requirement. The cutover model now has separate enrollment, offline
retirement, restart, and final-check phases. Dom0 verifies the enrollment source
before it arms the operation and persists one attempt before A stops. It
rejects a changed result or reuse of A's device ID. The offline replacement
finalizer now stages its boot inputs before cutover. After B stops, it uses a
read-only private job slot in a networkless Xen guest. Dom0 checks its stopped
disk, enrolled state, final packages, and detached result before B can restart.
Recovery fences an interrupted cleanup guest. This path has source tests only.
The controller signal issuer, exact-device inventory, and native recovery
proof remain open. Cutover must stay disabled until these gates pass.
Do not enable replacement or scheduling until these gates pass. Initial-install
retries continue to retain their existing enrollment.

The proposed generation is not a cutover request. Before stopping A, the dom0
adapter requires an offline preflight for the exact retained B disk, exact
old/new service compatibility, and forward/reverse copy qualification. The
`klokast.router-readiness.v4` contract binds these separate records to the
generation pair and engine. `candidate-preflight.json` checks boot artifacts,
the frozen temporary package set, OpenRC links, configuration syntax, rendered
files, service identity absence, pinned first-contact access, and the detached
disk. It must report that B has not booted as a router. The proposed generation
records the qualified final runtime package set as a required post-enrollment
result; it does not claim that temporary access was already removed.
Networkless preparation and synthetic compatibility guests can run while A
serves traffic; they do not start B as a second router. Disposable diagnostic
results alone do not authorize cutover. The controller must validate their
exact release pair and bind them to the proposed transaction.

`stage-replacement-generation` publishes the offline preflight beside the
proposed generation after it validates retained preparation, boot artifacts,
the detached LV, and the running accepted router. It rechecks grant expiry
before publication. A retry completes a missing record but refuses changed
records. The controller recomputes and verifies the fetched preflight before
reporting success. This source path still needs exact-engine native proof.
The compatibility record producer and controller cutover issuer remain open.

Both lifecycle selectors use the same support and first-release age check.
Replacement permits only the adjacent stable branch and continues package
checks on the current branch while a later branch is held. Fresh installation
has no predecessor. Neither path introduces a second branch-delay setting.

## Explicit package requests

The profile declares names without versions. Native APK selects dependencies
from the selected branch's official `main` and `community` repositories. The
frozen input record contains exact versions and hashes for one build. These
records do not select versions for future builds.

The previous 16 requests had these uses. The new profile has 14 APK requests;
it installs Tailscale from the signed upstream archive. This audit does not
claim that each request adds a package absent from Alpine's base.

| Request | Use in the current recipe |
| --- | --- |
| `alpine-base` | Official base, OpenRC, and APK database and tools. |
| `ca-certificates` | TLS trust for management connections. |
| `dhcpcd` | WAN addressing, leases, DUID, and stable IPv6 secret. |
| `dnsmasq` | DNS forwarding and local DHCP service. |
| `doas` | Restricted management privilege escalation. |
| `e2fsprogs` | Native ext4 creation and recovery in the isolated build and copy guests; retained in the current template. |
| `iproute2` | Routing configuration and native network probes. |
| `linux-virt` | Matching guest kernel and modules. |
| `mkinitfs` | Matching initramfs construction inside Xen. |
| `nftables` | Router firewall and native syntax verification. |
| `openssh` | Temporary first-contact support; removed before runtime acceptance. |
| `openssh-keygen` | Effective SSH host-key verification after server removal. |
| `python3` | Ansible and the checked guest helpers. |
| `tailscale` | Previous APK source for overlay identity and steady-state SSH management; now an upstream binary component. |
| `tailscale-openrc` | Previous APK source for service integration; now a checked OpenRC file in the frozen component. |
| `tzdata` | Timezone files; all Platform machines use UTC. |

Some tools also serve the disposable build guest. Removal needs a native proof
that both bootstrap and replacement still work. The current audit does not
remove them or add a package-count limit. APK continues to own dependencies.
The resolver selects the latest upstream stable Tailscale release separately
from the selected Alpine branch. Its archive and OpenRC file have separate
hashes in the frozen input record.

Detailed package differences stay in protected check evidence. The report names
their scope: build inputs compared with build inputs, or a legacy runtime
compared with new build inputs. Neither is a final runtime comparison. Explicit
request additions and removals are separate fields. Legacy inspection cannot
establish an approved request list, so that comparison is unknown for a legacy
source. Routine reports can show the Alpine transition without listing all
dependency changes.

## Qualification and preparation

### Upstream Tailscale source integration

The router must select the latest stable Linux amd64 archive from
`https://pkgs.tailscale.com/stable/?mode=json`. Tailscale documents these
[static Linux binaries](https://tailscale.com/docs/install/static).
Freeze one explicit version and archive identity during input resolution.
Do not use a mutable latest URL during the build, personalization, or boot.

Verify the archive through Tailscale's own
[`distsign` verifier](https://github.com/tailscale/tailscale/tree/main/cmd/distsign).
Its embedded root keys authenticate the current signing-key bundle, which
authenticates the archive signature. A SHA-256 file from the download server
alone is not this signature proof. Do not implement another signature protocol
or accept an unsigned fallback.

The verifier source is in `tools/tailscale-distsign`. It imports the upstream
`distsign` package at `v1.102.4` and pins its dependencies in `go.sum`. The
controller role downloads the official [Go 1.26.6 toolchain](https://go.dev/dl/)
with its pinned SHA-256 checksum. It builds only committed source as a dedicated
`tailscale-build` account. This account fetches modules through the Go module
proxy and checksum database, then compiles in a networkless user namespace. The build account
has no Platform credentials, signing keys, guest disks, or enrollment authority.
The installed binary and exact source tree are bound in a root-owned build record.
The same ops-controller role runs when a new controller VM is provisioned from
the shared Alpine template. The generic shared template does not receive this
controller-only compiler.

Run each archive download as an unprivileged process with a private temporary
directory, a fixed deadline, and only the official package origin. Keep the
verified archive and source evidence in the protected update cache. The
router input resolver invokes this verifier, checks its build record, and
freezes the archive, binary hashes, and OpenRC file before template build.

Install the verified upstream binaries only while building the generic router
template. Record their versions and hashes separately from APK's package
database. Remove the Alpine `tailscale` binary package from the new recipe;
do not overwrite APK-owned files and then claim that the installed APK manifest
proves the upstream binaries. The template must retain the required service
account, OpenRC service, state directory, and existing state paths. Select or
render the service integration explicitly and verify it in the native tests.

The release and generation contracts must bind both the native APK manifest and
the upstream Tailscale component. Verification must check both binary hashes
and the running daemon version. Changes to the upstream stable version must
trigger candidate preparation independently of Alpine branch age. Compatibility
and reverse-state tests must use those exact binaries. Self-update on a running
router remains prohibited.

The common bootstrap builder must not claim latest-upstream compliance until
the native installation, service, and rollback checks pass.

### Generic template qualification

The decision contract compares effective package inputs with an accepted
release and fresh live verification. An unrelated index change or patch
announcement does not require a build. Missing metadata defers the decision.
Invalid signatures, downgrades, and drift fail the decision. A held future
branch does not block an eligible current-branch package update. Repository
support is reported separately for `main` and `community`.

The fixed state-copy primitive is in `ansible/lib/router_state.py`. It is for a
stopped source and destination inside a disposable networkless Xen guest. It
preserves bytes, permissions, ownership, and lease timestamps. The caller must
keep both routers fenced during an interrupted copy. The receipt stays on box
storage. The primitive has no authority to attach disks or start a router.

The retained replacement clone uses the current accepted assignment and its
own recorded LV UUID. An interrupted opaque copy can resume only on that LV.
An LV whose UUID was not recorded stops automatic preparation for supervised
reconciliation. Preparation keeps the running router and its autostart state
unchanged.
Both lifecycle modes verify the same qualified template and networkless boot
inputs through `router_preparation_assets.py` before they use a clone.
The replacement dom0 preparation helper and its stage and execution playbooks
enforce an exact accepted assignment, the saved checked input source, and a
fresh controller grant. The helper checks that the accepted router is still
running and that its disk, boot artifacts, Xen definition, and autostart link
match protected records before it allocates a candidate. It then uses the
common networkless preparer and retains the clone. The controller issuer now
performs fresh live service inspection before the grant. This source path has
no production cutover authority and still needs exact-engine native proof.
Before a new LV allocation, dom0 checks free VG space for that LV and free
persistent filesystem space for the fixed offline copy slots plus a recovery
margin. An exact retry with a recorded allocated LV does not require a second
LV reservation.

`stage-replacement-generation` checks the retained preparation, the current
signed source, and the running accepted router again. Dom0 verifies the same
records and disk identity, then copies the exact template kernel and initramfs
to a versioned generation path. The proposed generation record keeps the old
production topology and a new Xen UUID. This step does not start the candidate,
publish an accepted assignment, or grant cutover. The first-install boot and
replacement staging paths use the same boot-artifact copier.

`build-template` uses a clean public checkout and inputs frozen at that exact
commit. It takes the installation lock and builds a generic partitioned router
disk in a disposable networkless Xen guest. Dom0 writes a fixed MBR to the new
scratch disk. It does not mount a guest filesystem or change its APK world for
this build. Package scripts, filesystem creation, and filesystem inspection
run inside Xen. The kernel and modules come from the signed `linux-virt`
package. This path does not use an ISO or the shared Alpine asset paths.
The controller copies the frozen capsule, bootstrap kernel, and initramfs as
bounded parts. Dom0 checks each part, assembles each original file, and checks
its frozen size and SHA-256 hash before it starts a build guest. This permits
the standard Ansible transfer to work on a slow Tailnet path without changing
the signed inputs or giving the build guest network access.
The frozen router world also includes `openssh`, which the existing first-contact
bootstrap role needs. It does not add packages while it personalizes a clone.
After rendering, a separate networkless finalization step removes the
first-contact OpenSSH package closure with native APK. It cannot add or change
a package, remove a runtime world request, or refresh a repository. Unknown
dependency removals fail qualification. The generic disk keeps its bootstrap
packages; the disposable clone proves the final runtime package set and a locked
root account without an OpenSSH server. Release v2 records that exact runtime
manifest and its native tests. Live release verification compares against this
runtime manifest. Earlier v1 receipts lack this evidence and cannot qualify a
finalized router.

The same offline finalizer can check an enrolled initial-install disk inside
networkless Xen. Its caller must first prove management access, stop the router,
remove only its recorded first-contact key, and record the fixed state-set
checksum in the protected bootstrap operation. Finalization requires that
checksum and the qualified runtime package manifest. It verifies that key and
lease bytes, ownership, permissions, and lease times remain unchanged. A retry
can accept the already finalized package set only with the same recorded state
checksum. Changed state, missing identity, a remaining key, or a different
package manifest fails. The helper grants no boot or acceptance authority;
bootstrap orchestration and native enrolled-disk qualification remain required.

The template must have the exact resolved package closure and no machine or
service identity. A disposable copy then boots with its own kernel and initramfs
to test modules and service syntax, followed by a normal OpenRC boot. The CLI
writes a release receipt only after these tests succeed and the engine commit
matches the controller's signed policy source. When the public implementation
is ahead of the approved engine, the CLI reports a qualified template with
`engine_approved: false` and no release receipt. A release receipt is build
evidence, not an accepted router assignment or replacement authority.
For a diagnostic build while the checked inventory source still uses the old
engine, `build-template --compatibility-inventory` renders the exact box into
the retained Ansible inventory. This option still requires the active
controller and qualified template inputs. It never publishes a release receipt
or grants replacement authority. Use the checked inventory for an approved
build after engine activation.

A fourth isolated boot tests configuration on a fresh disposable clone. The
controller renders synthetic inputs through the normal router Jinja templates.
The `router_personalize` helper checks template provenance, the exact package
set, and absence of existing identity before it creates the management account,
configuration files, and service links. Native `dnsmasq` and `nft` validate the
rendered files. The test then creates synthetic links and an upstream namespace
inside the networkless guest. Native dhcpcd obtains a WAN lease; dnsmasq assigns
a LAN lease and answers its DNS name. An offline Tailscale daemon creates a
machine key. Its native development store API seeds a synthetic logged-out
profile; normal preference updates must survive restart, and the machine key
must stay unchanged. An unenrolled profile alone is not persisted by
[Tailscale's profile manager](https://github.com/tailscale/tailscale/blob/v1.90.9/ipn/ipnlocal/profiles.go).
The test login server is a refused localhost port. This test cannot enroll a machine or copy
production state. It is not proof of complete candidate boot or compatibility
between old and new service versions. The result records these limits.

The router rootfs role now has only the generic template path. Router
provisioning selects it through the controller lifecycle command. Protected
baseline adoption, native compatibility qualification,
the router cutover executor, boot recovery, signed policy dispatch, and the
unattended schedule must pass their qualification gates before replacement is
enabled. The Instance contract accepts a router target, but no activated signed
policy names one. The
template test does not prove old/new service-state compatibility or rollback.
The native first-install preparer refuses an existing legacy LV and records
the selected new LV before it writes to that disk. It does not resize or
format an unassigned router disk.

The diagnostic candidate preparation command clones a new 2 GiB LV from a
qualified template, prepares the selected initial-install or replacement
configuration in a networkless guest, checks the selected package set and
service syntax, and retires that exact test LV. Initial-install keeps the
template's first-contact packages. It also seeds the approved temporary SSH
key and backend address on that disposable disk, then records the key,
interface, firewall, temporary SSH configuration, and generated host-key
hashes. The firewall permits SSH only from the dom0 backend address to the
router backend address. OpenSSH and nftables pass native syntax checks. The
helper also checks OpenSSH's effective settings so a missing config include or
an extra listen address fails closed. The test guest has no VIF, and the
candidate is retired without enrollment.
Replacement retires the first-contact package closure offline. Neither test
enrolls a machine or creates an accepted generation. Use inputs and a template
built from the same clean engine commit:

```sh
ansible/bin/platform-router-update test-candidate-preparation --box boxa \
  --inputs-directory /var/cache/klokast/updates/router/INPUT_OPERATION \
  --template-operation TEMPLATE_OPERATION

ansible/bin/platform-router-update test-candidate-preparation --box boxa \
  --inputs-directory /var/cache/klokast/updates/router/INPUT_OPERATION \
  --template-operation TEMPLATE_OPERATION --mode initial-install
```

If LVM refuses allocation before the LV exists, the private disk record stays
`planned`. Inspect that exact LV on the controller. When it is absent, run
`74-router-candidate-test-abort.yml` with the exact box and operation ID to
record `aborted`. An existing LV needs its recorded UUID and a separate exact
retirement check. The successful diagnostic records `retired` after it stops
the guest, detaches the result disk, and removes its own LV.

Each template operation uses an exact directory under
`/mnt/dom0_data/klokast-router-templates` on dom0. The controller stores bounded
evidence under `/var/lib/klokast/updates/discovery/router`. Failed operations
retain their recorded disks for diagnosis. A guest that cannot be confirmed
stopped also retains its attachments. Do not remove these resources until the
recorded Xen UUIDs and attachments are reconciled. Successful qualification does
not install an autostart entry or modify the production router.

Template qualification requires at least 5 GiB free on `/mnt/dom0_data` before
allocation. The declared dom0 data LV size is 32 GiB; the storage role grows an
existing smaller LV and its mounted ext4 filesystem without shrinking it.

After diagnosis, the controller can reclaim only the large temporary disks of
one failed operation. Cleanup checks its lifecycle record, exact Xen names and
UUIDs, candidate absence, and loop attachments. It keeps logs and the lifecycle
record:

```sh
ANSIBLE_CONFIG=ansible/ansible.cfg ansible-playbook -vv \
  -i ansible/execution-inventory/hosts \
  ansible/playbooks/74-router-template-cleanup.yml \
  -e router_cleanup_box=boxa -e router_cleanup_operation=OPERATION_ID
```

After a successful build, the build role uses the same guarded helper with
`--kind scratch`. It removes only the disposable test disk and temporary block
slots after it fetches all qualification records. The generic OS disk and its
kernel and initramfs stay available for controlled release use. For an older
qualified operation that still has scratch storage, run the cleanup playbook
with `-e router_cleanup_kind=scratch`.

Run the repository tests without contacting the Platform:

```sh
python3 -m unittest discover -s ansible/tests -p 'test_router_*.py'
python3 -m unittest discover -s ansible/tests -p 'test_vm_template_inputs.py'
```

For authority and scheduling, see [VM updates](platform-updates.md). For the
filesystem isolation rule, see [guest construction](architecture.md#guest-construction-and-runtime-state).

## Provisioning protections

Router phase 30 first checks the live dom0 WAN bridge. The controller then
installs the activated recovery reader and reads the protected `accepted.json`,
`pending.json`, and `installation.json` records under
`/mnt/dom0_data/klokast-router-updates`. A pending record blocks provisioning.
Before retained preparation, the controller saves the exact initial operation,
source, and template IDs. If the first installation is interrupted, a rerun
must use those IDs and the dom0 record; a missing pointer blocks automatic
recovery. The native preparer refuses an existing legacy or unassigned router
disk. It cannot treat an absent accepted record as a blank target.

Phase 31 completes the recorded installation, then checks the accepted boot
assignment and router service and release state. The installed, versioned
`verify-boot-assignment` command checks the protected record, boot files, Xen
definition, and running generation. A later provisioning run performs those
checks without changing the router. Direct calls to the former rootfs builder,
Xen renderer, VM base, Tailscale client, and enrollment still refuse an
accepted assignment.
For a first accepted template generation, the protected assignment reader also
checks that the verified installation record names that same generation.

Run the router service verifier by itself from the active controller when a
read-only check is needed:

```sh
ansible-playbook -i ansible/execution-inventory/hosts \
  ansible/playbooks/74-router-verification-only.yml -e router_update_box=<box>
```

It reads current compiled Instance router inputs, then checks the exact managed
core file hashes, service and route state, firewall, DNS, and Tailscale status.
It does not select an OS generation.

`provision-box` and `provision-ops-vm` use the same installation lock. Nested
shell calls reuse its inherited descriptor. Another process must wait until the
holder exits. These wrappers refuse an absent or unsafe lock file.

The router role has `converge`, `render`, `activate`, and `verify` phases.
`render` writes configuration without package installation, live sysctl changes,
interface changes, service starts, or restart notifications. The explicit
normal dnsmasq lease path is `/var/lib/misc/dnsmasq.leases`. These phases do not
supply candidate authority or bypass assignment checks.

The initial-install lifecycle proves Tailscale SSH after enrollment, then
retires temporary OpenSSH access offline before first acceptance. The accepted
runtime keeps `openssh-keygen` to verify retained SSH host keys. Legacy routers
with remaining first-contact access require supervised reconciliation before
baseline adoption. DERP is a valid management path for normal provisioning.
The separate signed IPv6 repair requires direct transport, as defined in
[Secret Authority](secret-authority.md).

The Alpine asset role still serves other VM profiles. Router bootstrap uses the
generic template's frozen Alpine inputs and does not invoke that ISO asset role.

## Copy guest boundary

The v2 primitive copies only the three files in the
[A/B identity contract](#identity-contract-for-ab-replacement). It does not open
Tailscale or SSH state while copying. It validates both fixed destination WAN
cache paths before any changes and removes them before publishing a complete
receipt. Both forward and reverse operations use this contract. Interrupted
cache removal must resume before the destination boots. Source cache files and
unrelated destination files stay unchanged.

Bootstrap finalization uses a separate read-only snapshot of the enrolled
identity, SSH keys, DHCP state, and WAN caches. This preserves the existing
first-contact retirement guarantee without adding identity files to the copy
list. Inspection still checks that the running generation's identity is usable.

`router-state-copy-guest` refuses execution outside a networkless Xen guest.
It uses fixed source, destination, scratch, and result VBDs. It checks the box
and router hostname on both filesystems. It checks the source filesystem with
native `e2fsck -fn`. If journal recovery is necessary, it copies the source
partition to the scratch VBD and repairs only that copy. It mounts the source
read-only with journal replay disabled. A failed copy has no complete result
receipt. The operation must keep both routers fenced until the guest has
stopped and all VBDs are detached.

The source staging playbook is
`ansible/playbooks/74-router-state-copy-source.yml`. It writes the two public
source files to the controller cache for an exact `router_copy_operation`.
It does not create, attach, or boot a VM.

The synthetic qualification command uses frozen authenticated router packages
to assemble a disposable boot environment. It takes the installation lock and
runs six networkless Xen boots on new, fixed test disks:

```sh
ansible/bin/platform-router-update test-state-copy --box boxa \
  --inputs-directory /var/cache/klokast/updates/router/INPUT_OPERATION
```

The dom0 test helper writes a fixed MBR only to its new 64 MiB scratch files.
It does not require `sfdisk`, install packages, run the broad dom0 maintenance
lock, upgrade Xen, or reboot dom0. The test keeps its exact private operation
record and detached-disk cleanup rule if a guest or controller command stops.

The test creates synthetic state, interrupts and resumes a forward copy, changes
the candidate state, stops with an open unlinked file, recovers a scratch clone,
copies the latest state back, and verifies both disks. It checks file
bytes, service ownership, permissions, LAN lease timestamps, independent
identity bytes on both generations, destination WAN cache removal, and that
each read-only source disk stays unchanged. It records each boot's
duration. The result binds the frozen
package inputs and the test engine commit. It is copy evidence only; synthetic
state does not prove that old and new service versions can read each other's
formats. Production dispatch and service compatibility remain required.

Successful tests remove their five exact disk files after Xen domains and loop
attachments are absent. Failed tests retain them for diagnosis. Logs and records
remain under `/mnt/dom0_data/klokast-router-copy-tests/OPERATION_ID`. After
diagnosis, reclaim a failed operation with
`ansible/playbooks/74-router-state-copy-test-cleanup.yml`, using the exact
`router_copy_test_box` and `router_copy_test_operation` variables and limiting
the play to that box's dom0.

## Native old/new/old qualification

The v2 diagnostic tests exact old/new DHCP versions and checks that forward
and reverse copies preserve distinct opaque identity fixtures. It rejects v1
records. It does not enroll Tailnet devices or prove the complete A/B lifecycle.

The diagnostic command below tests the live router against an existing
qualified template. The source can be an unadopted legacy router, an accepted
legacy router, or an accepted template generation. Run it on the active
controller from a clean checkout:

```sh
ansible/bin/platform-router-update test-compatibility --box boxa \
  --inputs-directory /var/cache/klokast/updates/router/INPUT_OPERATION \
  --template-operation TEMPLATE_OPERATION
```

The command takes the installation lock and requires a fresh, clear source
inspection. An accepted source must match its protected assignment before and
after the test. For a template source, the command also verifies its recorded
release, native build evidence, accepted manifest, and live state. The native
copy host also reads that
assignment before it snapshots the source and after it tests the copies. It
refuses a changed assignment or a different installed recovery engine. A pending
assignment fails inspection. Dom0 checks the live Xen UUID, configuration, boot
hashes, and exact recorded two GiB source LV again. It takes
a read-only LVM snapshot with a one GiB COW reserve, copies its opaque blocks to
private storage, verifies the copy, and retires the snapshot by recorded UUID,
origin UUID, tag, and path. An uncertain snapshot is retained for reconciliation.
An accepted template with a historical v1 state profile can be verified as
source provenance. New template inputs, releases, and DHCP copy records still
require the v2 state contract.
Dom0 does not mount either guest filesystem.

A networkless preparation guest removes the production identity from the copy,
checks its packages and configuration against inspection, and installs only the
fixed synthetic probe. It personalizes and finalizes a separate template copy.
Neither production runlevels nor local startup hooks run on the old copy. The
old software then creates native synthetic state. The fixed copy helper moves
that state to the new copy and later moves the new writer's state back. Each
runtime uses its recorded kernel and exact package set. The test checks DHCP
renewal and new grants, DNS, lease expiry, DUID and privacy secret continuity,
and copied timestamps. Each runtime verifies its own distinct Tailscale and
SSH fixture bytes. These opaque bytes never start a Tailscale daemon. Before
new and old runtime tests, the destination WAN cache must be absent; native
DHCP then acquires a lease.

The receipt states `enrollment_tested: false` and cannot authorize replacement.
Real per-generation enrollment still needs separate native qualification. Interrupted state writes and reboot recovery also need
their own transaction qualification.

The replacement test clone uses a new two GiB LV named
`/dev/vg0/routergen_OPERATION_ID`. The allocation record binds its UUID, unique
ownership tag, template checksum, and lifecycle stage before template copying.
Dom0 copies opaque bytes only. The guest performs all filesystem and package
work. This tests the candidate storage primitive without accepting a generation
or granting production boot authority.

Private test files and the candidate LV record remain under
`/mnt/dom0_data/klokast-router-compatibility/OPERATION_ID`. Successful tests
remove them after every recorded guest stops and all loop devices detach.
Failed tests retain them. LV cleanup checks the exact UUID, tag, size, mount state,
and Xen block backends before removal. After diagnosis, use
`ansible/playbooks/74-router-compatibility-cleanup.yml` with
`router_compatibility_box` and `router_compatibility_operation`. Cleanup requires
a retired snapshot, a detached lifecycle record, and the exact recorded file
identities. It refuses live guests, changed files, or attached disks. Operations
interrupted before a detached record require explicit reconciliation first.

If a snapshot create was interrupted before the UUID was recorded, inspect that
one LV through the controller first. The cleanup playbook accepts
`router_compatibility_snapshot_uuid` for this case. It checks the planned path,
origin UUID, unique tag, read-only attributes, and COW reserve against the exact
observed UUID before retirement. It does not select a snapshot by name alone.
A complete allocation record can then be cleaned after all domain and loop
checks pass. A partial allocation record still needs manual reconciliation.
For an interrupted candidate `lvcreate`, first inspect that exact LV through
the controller. `router_compatibility_candidate_uuid` lets the cleanup playbook
retire the explicitly inspected UUID only if the planned path, ownership tag,
size, independent allocation, and native detachment checks also match. An absent
allocation record or an unrecorded disk never permits inferred cleanup.
Candidate retirement reads protected accepted and pending records under the
local transaction lock. It refuses to remove an LV if either record refers to
its path or UUID. Diagnostic cleanup cannot delete a disk after it becomes a
router generation.
Use the original qualification revision to clean older operations that used
regular files for both test disks.

## Candidate configuration rendering

`render-candidate` writes a private personalization input on the active
controller. It uses the selected box's execution inventory, the normal router
role templates and defaults, and the current verified resource compiler output.
It does not connect to the router or activate services:

```sh
ansible/bin/platform-router-update render-candidate --box boxa \
  --inputs-directory /var/cache/klokast/updates/router/INPUT_OPERATION
```

The result binds the input manifest, exact file hashes, compiler registry hash,
and implementation commit. The command checks the compiler output again after
rendering and refuses a change. The personalization helper accepts only its
fixed core files and bounded keyed `.nft` files under the compiler-owned
`router-forward.d` directory. Other extra paths, nested paths, and path escapes
are rejected before writing a clone. Inspection and rendering select firewall
includes through the same compiler adapter. The rendered result has no
replacement authority. Native candidate preparation and service verification
must still pass before use.

The candidate preparation guest is a networkless Xen job for one new clone.
It accepts one hashed initial-install or replacement job, a writable disk, and a
separate result slot. It mounts only the candidate root, runs the common
preparation helper, then unmounts the root before it writes success. It has no
old router disk, production VIF, enrollment key, or cutover command. The dom0
dispatcher must retain the exact disk and connect its offline evidence to the
A/B transaction before production use.

`platform-router-update test-candidate-preparation --box BOX
--inputs-directory INPUTS --template-operation TEMPLATE` stages that guest
from one frozen router input set. On dom0 it clones the exact generic template
to a new operation LV, boots the guest without VIFs, verifies the prepared
files, and retires the diagnostic LV after the guest stops. The result is test
evidence only. A failed or uncertain cleanup keeps its exact disk record for
reconciliation. This test does not publish a generation or use the production
router identity.

The shared dom0 runner, `router_candidate_preparation.py`, records the exact
disk, job, Xen configuration, and result slot before it starts the preparation
guest. It does not allocate or delete a disk. After an interruption, it can
recover a complete result from a stopped guest without another preparation
boot. A changed input, attached disk, incomplete result, or conflicting
completion record causes refusal. This recovery applies only to preparation;
it does not authorize a production boot or repeat enrollment. The diagnostic
dispatcher still retires its test disk. The production first-install issuer
must own disk retention and the installation-stage checks.

The common preparation helper, `ansible/lib/router_candidate.py`, accepts only
`initial-install` and `replacement` on a fresh generic clone inside networkless
Xen. Both modes use the same configuration and account recipe. Initial mode
keeps the frozen first-contact packages but does not add a key or enroll a
machine. Replacement mode removes the qualified first-contact package closure.
The verifier checks the exact package world, kernel module directory, generated
files and modes, default service links, locked accounts, and absent identity.
Extra firewall includes and DNS configuration fail verification.

The first-install record advances through `planned`, `allocated`, `prepared`,
`enrolled`, and `verified` under the dom0 router lock. The planned record fences
legacy provisioning before LV creation. Allocation records the native UUID
before any template copy. It binds one operation, engine,
policy selection receipt, release, LV UUID, preparation result, enrollment
result, and proposed generation.
A first accepted assignment binds the checksum of that complete verified
installation record. Later reads reject a changed selection or stage record.
A missing or changed record cannot authorize a second disk or identity. The
first accepted-generation writer requires the matching verified record. The
record itself grants no installation authority: the issuer must prove the
controller grant, physical disk, enrollment, runtime state, and service checks
before it advances a stage. Preparation stops at `prepared`; enrollment
advances only to `enrolled`. Offline retirement, final boot, acceptance, and
the phase 30/31 connection exist as source but still require exact-engine
native qualification.

`platform-router-update prepare-initial --box BOX --inputs-directory INPUTS
--template-operation TEMPLATE` prepares and retains one first-install disk.
It requires an activated engine, current bootstrap policy selection, and an
approved release from the common builder. It uses the same renderer and preparation guest as diagnostic testing and the
same bounded boot-input assembler as the template builder. It stages all
inputs first, records the frozen input source operation for later offline
finalization, then rechecks the engine, policy, and compiler inputs before it
issues a 15-minute preparation grant. No standing replacement policy is needed.

The dom0 command refuses an accepted, pending, configured, or running router,
an existing legacy router LV, or an unassigned router generation. It records
the installation before allocation and retains the new disk on success or
failure. It never starts the production router or enrolls a Tailnet identity.
Use `--operation-id OPERATION` to resume the exact saved preparation. A partial
copy can resume only with its recorded UUID and before preparation has started.
An unrecorded native UUID needs explicit reconciliation. A completed preparation
uses its recorded result; an enrolled installation cannot return to preparation.
The preparation result includes the public SSH host keys generated on that
clone. The controller stores them in the operation's mode `0600`
`initial-known-hosts` file with the alias `router-initial-OPERATION`. A retry
must preserve the exact pin. Initial enrollment must use this file and alias
with strict OpenSSH host verification; network key discovery cannot establish
the clone's identity. Private host keys stay on the disk. The version 2
first-contact receipt includes these public keys; older hash-only receipts
cannot provide this first-connection proof.
This command is an integration step, not the completed bootstrap workflow. It
still needs native qualification before connection to normal provisioning.

`platform-router-update start-initial --box BOX --operation-id OPERATION`
starts only that prepared installation. The active controller reads its
preparation and pinned public host keys, stages the approved inventory's Xen
memory, vCPUs, bridges, and MAC addresses, then issues a five-minute grant
for the exact boot request. Dom0 copies the release's kernel and initramfs to
the operation's versioned generation directory, records the boot definition,
disk UUID, and artifact hashes, then starts the guest without an autostart
definition. A retry checks the same live Xen UUID, disk, boot files, and VIFs;
it does not start another guest or reset the disk. If enrollment has begun, a
boot retry refuses until that attempt is reconciled. This command does not
mint a Tailnet key, retire first-contact access, verify services, or publish
an accepted router. It must not run on an existing production router.

`platform-router-update enroll-initial --box BOX --operation-id OPERATION`
uses the host-key pin from the prepared clone for first-contact OpenSSH. It
checks the guest before dom0 records one enrollment intent. Only a new intent
permits one call to the existing scoped `ts-authkey-vm` broker. Ansible sends
that single-use key to the guest through standard input. The guest writes it
to a mode `0600` file under `/run`, calls `tailscale up` with
`--auth-key=file:PATH`, and removes the file when the command ends. Tailscale
documents the `file:` form in its [CLI reference](https://tailscale.com/docs/reference/tailscale-cli/up).
The key is never a command argument and is not written to the controller
operation receipt. The command does not use `--force-reauth`.

A retry checks the same running guest and its recorded attempt. If that guest
has no verified Tailnet identity, it refuses another key mint until the
uncertain attempt is reconciled. If the original guest is running with the
expected identity, the same machine ID can advance the installation to
`enrolled`. This does not accept the router: first-contact access still needs
offline retirement and the final runtime needs verification. No accepted
assignment or router autostart is written by enrollment.

`platform-router-update finalize-initial --box BOX --operation-id OPERATION`
stages an exact cleanup job and networkless boot files while the enrolled
router runs. The active controller rechecks the engine and compiled inputs,
then issues separate short grants to stop the exact Xen guest and to run the
offline cleanup guest. Dom0 records stop intent before it shuts down that
guest. The cleanup guest mounts only the stopped router disk and a bounded
result slot. It snapshots the latest service state on the disk, removes the
recorded first-contact key and OpenSSH package closure with offline APK,
restores the approved runtime configuration, and checks the final package
manifest. It does not enroll or start the router. An incomplete cleanup keeps
the disk stopped and its recorded result slot for reconciliation. A retry
must bind the same job and enrolled machine; it cannot mint another identity.
The command reports `offline-finalized` only after dom0 verifies the stopped
disk and cleanup evidence. `platform-router-update boot-final-initial --box
BOX --operation-id OPERATION` then issues a fresh grant for that exact cleanup
receipt and enrolled installation. Dom0 verifies the original Xen definition,
versioned boot files, disk, and complete cleanup result. It records final
boot intent before `xl create`, or reconciles the same running guest on retry.
It reports `running-unverified`; it does not publish a router assignment or
install autostart. Router-service checks and first accepted-generation
publication remain separate steps. Do not use this source path on a live
router before exact-engine native qualification.

`platform-router-update verify-initial --box BOX --operation-id OPERATION`
checks that dom0 still runs the exact final Xen guest. On the router, it uses
the common service and network verification role, checks controller Tailscale
reachability and management SSH, and compares the running kernel, installed
APK manifest, upstream Tailscale files, managed configuration, stable DUID,
IPv6 secret, and effective SSH host keys with the frozen release and offline
identity evidence. It also checks the enrolled machine ID and confirms that
first-contact access is absent. The protected result records only checksums
and bounded status, not secret contents. This command does not accept the
router or install autostart. Its Ansible and native path still needs
exact-engine qualification before use on a live router.

`platform-router-update accept-initial --box BOX --operation-id OPERATION`
stages the exact runtime expectation and verified result before it issues a
short acceptance grant. Dom0 checks the enrolled installation, offline
cleanup, live final Xen guest, frozen release, and grant. It records acceptance
intent, advances the installation to `verified`, publishes one template
generation and the first accepted assignment, then installs the matching Xen
definition and managed autostart link. A retry keeps the same generation and
identity. If power fails after the accepted pointer but before autostart is
persisted, the dom0 boot-recovery prerequisite reconstructs only that exact
accepted initial definition before Xen guest autostart. An unaccepted disk
cannot use this recovery path. This source flow still needs exact-engine
native qualification and connection to normal provisioning; do not run it on
a live router before those gates pass.

The native compatibility playbook uses this helper on two separate template
clones, one for each mode. It requires both exact preparation results before
it runs service tests. The initial-install clone uses the preallocated recovery
scratch slot only during preparation. It is unmounted before a state-copy guest
can use that slot for journal recovery. The
result is diagnostic evidence. Retained candidate preparation, A/B cutover,
initial enrollment and resumption, and accepted-assignment
convergence are still separate integration gates.

The controller-side candidate generation assembler binds a proposed template
generation to one approved release receipt, one replacement preparation result,
one recorded candidate LV, versioned boot artifacts, and the accepted router
topology. It rejects changed package or configuration evidence, an initial
installation result, missing identity-absence proof, and reused Xen identity.
The result is a checked generation record only. A dom0 issuer must still verify
the native resources and signed authority before it publishes that record.

The legacy baseline assembler accepts only a fresh inspection with no blocking
finding. Its proposed record binds the observed production LV, kernel,
initramfs, live Xen identity, packages, service accounts, and only the router
configuration files that the inspector compared with current Ansible and
compiler output. It does not claim a template build or record uninspected
files as approved state. It does not publish an accepted assignment.
The inspector also records the stable Alpine branch and requires exactly the
`main` and `community` repositories for that branch. The protected generation
retains this branch. The update decision treats a validated legacy generation
as a source that needs its first approved template, even if installed package
versions match the selected closure. It still refuses live package, kernel,
boot, branch, or configuration drift before that decision.
The read-only `legacy_live` adapter accepts fresh router and dom0 reports only
when they match the sealed accepted assignment and generation. After adoption,
it checks the running core files against the accepted generation. A newer engine
can render different candidate files without changing that running disk. A new
baseline adoption still requires a match with the current compiled templates.
The adapter returns no retained state bytes or SSH key material. It
does not grant build or cutover authority.
After supervised adoption, `platform-router-update check-legacy --box BOX` uses
that adapter from the active controller. It reads the protected current source
before and after the check, reads the verified Instance schedule and any
activated signed policy twice, fetches official release metadata, and freezes
authenticated package inputs for the selected
branch. It stores a private decision report and returns a short status. The
`update-required` report also binds the exact frozen input source operation.
The command does not create a candidate or grant replacement authority. It refuses
an unapproved engine, an unadopted router, or changed source and authority records.
If the current Instance schedule is verified but its standing update policy is
not activated, the command uses that schedule's branch timing for a read-only
diagnostic. The diagnostic adds the selected router only to an in-memory,
disabled comparison policy. Its decision is deferred and cannot authorize
preparation or cutover. An activated policy must name the router target before
the normal update-required decision can be issued.

## Cutover order and failure model

`ansible/lib/router_transaction.py` defines the router-specific durable order.
The retained preparation request stays in the operation's `request.json`.
The later A/B authorization has its own `transaction-request.json` in the
same operation directory. The transaction reader requires that second record;
it cannot reinterpret preparation as a cutover request.
It requires an adapter for protected records, exact Xen resources, native copy,
local probes, controller acceptance, and boot recovery. The ordering module
alone is not a production executor. Run its model tests without Platform access:

```sh
python3 -m unittest discover -s ansible/tests -p 'test_router_transaction.py'
```

The same test is available through
`ansible/playbooks/74-router-transaction-model-test.yml`. The model covers power
loss at each cutover record, partial forward and reverse copies, an accepted
pointer written before the pending record is updated, and unreadable latest
state. It requires a separate fixed recovery budget. Controller acceptance
cannot extend the cutover deadline.
Dom0 reconstructs B's service target from the prepared generation, exact
enrollment result, offline cleanup result, and frozen release. It requires the
controller's full-service proof for that target before it accepts B. The
acceptance record binds the enrollment, cleanup, and service proof hashes.

The production-start marker is written before starting the candidate. Rollback
then uses the candidate's latest state even if its start result is uncertain.
A second marker is written before restarting the old OS after rollback. Once
that marker exists, reboot recovery must preserve the old OS's new writes and
must not copy the candidate's now-older state over them. Failed state recovery
fences both generations. Unconfirmed fencing is reported separately and requires
console recovery. The native dom0 adapter and boot-service tests must also pass
before this ordering can authorize a production switch.

The generation contract keeps legacy inspection provenance separate from a
qualified template. A legacy record does not claim a template build receipt.
Each record binds one router LV UUID and size, bounded boot artifacts, Xen UUID,
ordered production VIFs, packages, service account IDs, and configuration hashes.
A transaction requires different disk paths, LV UUIDs, and Xen UUIDs for its two
generations, with the same production VIF set. Candidate provenance must match
the transaction engine. Record validation alone grants no execution authority.

SSH inspection and compatibility tests derive each public key from the actual
retained private key before calculating its fingerprint. A stale `.pub` sibling
cannot substitute for that key. A native test covers this case and an unreadable
private key.

The dom0 adapter uses root-owned generation records, an atomic accepted pointer,
and a separate pending record under `/mnt/dom0_data/klokast-router-updates`.
It checks LV identities, live Xen assignments, and both Xen inventory and block
backend records before copying. It preallocates opaque scratch storage. A
networkless copy guest receives the source read-only and the target writable.
Its private receipt stays on the box; public results contain only the complete
receipt checksum and operation identity. Full host copy time is recorded
separately from guest execution time.

The adapter requires a short-lived, root-staged controller authorization before
arming. Recovery uses the already protected pending operation and does not need
an available controller or an unexpired grant. The accepted pointer takes
precedence over a stale pending phase. The adapter tests cover real atomic
records with simulated Xen failures. The model playbook includes the adapter,
native guard, copy receipt, persistent-record, engine-loader, and supervisor tests.

`router-update-transaction` loads a closed, checksum-verified module set from the
engine recorded in the pending operation. A newer installation cannot replace
that recovery engine. The bounded local supervisor stops all worker processes
before it starts recovery. It reserves the exited worker PID until those
processes are stopped. A killed worker cannot leave an `xl` command running
during rollback.

`74-router-recovery-setup.yml` installs this chain only from a clean checkout of
the activated engine. It refuses installation during a pending transaction,
persists the versioned code with LBU, and puts the recovery oneshot before Xen
autostart. A recovery failure fences router autostart and allows other guests to
boot. The authority issuer and native fault tests must be complete before the
production cutover path is enabled.

The read-only native guard qualification can run from a clean qualification
checkout. It compares fresh inspection with dom0 LV, boot, live Xen, and kernel
block-backend evidence. An adopted legacy source must match its protected
accepted assignment before and after the test. It must refuse the still-attached
source disk. It does not stop a guest or adopt the baseline:

```sh
ansible/bin/platform-router-update test-dom0-guards --box boxa
```

For a supervised legacy baseline, run `platform-router-update adopt-legacy
--box BOX` on the active controller from a clean checkout of the activated
engine. The command holds the installation lock, installs the matching dom0
recovery engine, inspects the running router, and assembles one legacy generation
from the fresh evidence. It then stages a grant that expires after five minutes.
The dom0 command checks the disk, boot files, Xen definition, autostart link,
and running guest before it publishes the accepted assignment. It does not stop
or rebuild the router. A failed check leaves the accepted assignment absent.
The command returns the protected controller evidence directory and generation
checksum. Review the private operation log and run the router verification and
Platform Map checks after adoption.

For an accepted router, `74-router-accepted-verification.yml` compares the
running service checks, installed APK database, running kernel, and managed
configuration file hashes with the protected current generation. Playbook 31
runs the same checks for every accepted router.
The root engine refuses to project this manifest during a pending operation.


## Generation status in Platform Map

The installed versioned command has a read-only `map-status` action. Platform
Map collects this action from dom0 and reports the current and previous
router generations, pending operation, and per-direction state-copy status.
It verifies copy receipts without attaching a disk or reading retained files.
The projection omits receipt contents and private diagnostics. Changed pointers
cause refusal, so a report cannot join two different operations.

This reader must be installed through the approved recovery-engine workflow.
An absent reader or an engine that does not support this action produces an
unavailable map field, not a healthy or current-router claim. See the
[Platform Map fields](platform-map.md#current-json-template).
