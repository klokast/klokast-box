# Automatic router VM replacement plan

## Goal

Add an unattended and fail-closed update path for each `<box>-router` VM.
The path must first check for new upstream Alpine releases and router package
updates. When an eligible update is required, it must build a new Alpine OS
disk from a generic template, test it, switch to it, verify the complete router
service, and roll back automatically if a check fails.

The updater must not upgrade a running router in place. It must not accept
configuration drift. Ansible and the compiled Instance state remain the source
of the router configuration.

## Scope

This plan covers the router OS, packages, kernel, initramfs, Xen definition,
and the small amount of machine state that must survive an OS replacement.

This plan does not add a general VM migration framework. It does not reuse the
shared DMZ/IoT application-data qualification pipeline. It does not add a new
daemon. It does not discover desired configuration from the Tailscale API.

## Repository findings

1. The current router image is not a generic template.
   `ansible/roles/router-alpine-rootfs` writes directly to
   `/dev/vg0/lv_router`. It embeds the final hostname, the backend bootstrap
   address, the Alpine repositories, and a root bootstrap public key.
2. The production disk, Xen configuration, kernel, and initramfs use fixed
   names. The important fixed paths are `lv_router`, `router.cfg`,
   `router-kernel`, and `router-initramfs`. These names prevent a safe
   side-by-side candidate.
3. `ansible/playbooks/31-vm-router.yml` asks the controller whether the final
   Tailscale peer is online. During replacement, that answer describes the old
   router. It does not prove that the candidate has a valid identity. This
   check is suitable for first installation only.
4. The router configuration is mostly reconstructable. Ansible and the
   resource compiler own the interfaces, DHCP and DNS configuration, firewall,
   policy routes, users, packages, services, and sysctls.
5. The expected retained state is small. It includes Tailscale identity, the
   effective SSH host keys, dhcpcd identity and lease files, and the dnsmasq
   lease database. The State contract below defines the list. Installed paths
   and enabled features still require controller-side confirmation.
6. The generated app firewall includes must be rendered again from approved
   inputs. They must not be copied from the old OS disk as desired state.
7. The signed ops-only IPv6 repair can leave state in
   `/etc/klokast/overlay-ipv6.nft` and
   `/etc/sysctl.d/91-klokast-ops-ipv6.conf`. The normal router role does not
   reconstruct an enabled repair. Automatic replacement must refuse this
   non-default state until a specific reconstruction adapter exists.
8. The current Instance `vm-updates` contract and the `shared-alpine-v1`
   profile explicitly exclude routers. The router executor must stay separate
   from the shared Podman VM executor.
9. The current Alpine asset role selects a fixed Alpine release, but it fetches
   the ISO and its checksum from the same HTTP origin. An unattended updater
   needs an approved and authenticated input identity and a build receipt.

## Recommended design

Use explicit file copying, not a separate state LV, for the first version.
Keep these assets for each box:

- a generic, local, versioned router template;
- the current router OS generation and one prepared candidate cloned from the
  template; after acceptance, retain the previous generation for rollback.

Keep the current production OS generation unchanged during preparation. At
cutover, stop the old router, copy the fixed state allowlist into the stopped
candidate, and boot the candidate with the production MAC addresses. Keep
service state at explicit, normal paths on each OS disk. Packages and generated
configuration remain bound to the accepted release and approved inputs; service
state is writable.

This keeps the existing single-disk layout. It avoids a new filesystem, mount
dependencies, service-path redirection, and a one-time storage migration. The
tradeoff is an offline copy during cutover and, if needed, rollback. Stage the
copy guest and all inputs first, then measure the complete outage in tests.

A state LV would avoid repeated copying, but it would still require state-format
compatibility with the previous service versions. It would not preserve an
unchanged rollback state once the candidate writes to it. Reconsider that choice
only if measured copy time or a changed state requirement justifies it. Do not
implement both storage modes.

## State contract

This is a code-based inventory, not a report of inspected production files.
Confirm paths, enabled features, ownership, permissions, and effective SSH keys
on a test VM and through the approved controller before production use.

| Retained item | Expected path or selection | Reason |
| --- | --- | --- |
| Tailscale identity and preferences | `/var/lib/tailscale/tailscaled.state` | Continue the same enrolled machine without a new auth key. |
| Effective Tailscale SSH host private keys | For each used key type, `/etc/ssh/ssh_host_<type>_key` or `/var/lib/tailscale/ssh/ssh_host_<type>_key` | Keep the SSH identity known to management clients. Confirm the actual Tailscale state root. |
| WAN DHCP client identity | `/var/lib/dhcpcd/duid` | Keep the client DUID. The managed dhcpcd configuration enables `duid`. |
| Stable IPv6 address secret | `/var/lib/dhcpcd/secret` | Keep the secret used by the managed `slaac private` configuration. |
| LAN DHCP assignments | The effective dnsmasq lease file | Keep unexpired client assignments. Set `dhcp-leasefile=` explicitly in the template. |
| WAN DHCP lease history, when present | Exact interface-specific dhcpcd `.lease` and `.lease6` paths | Help reacquire the WAN lease. Preserve timestamps and respect expiry; the upstream server can assign a different lease. |

Tailscale state includes private keys and preferences; the Tailnet API cannot
reconstruct those private keys. Do not preserve enrollment auth keys or call
`logout`, force reauthentication, or enroll a second production identity during
replacement. If encrypted state or Tailnet Lock needs additional persistence,
support and test that exact profile before enabling its unattended updates.
See [Tailscale state storage](https://tailscale.com/blog/encrypting-data-at-rest).

Removing OpenSSH does not remove the need to keep SSH host identity. Tailscale
SSH can prefer system host keys and otherwise use keys in its own state root.
Resolve the effective source per key type, and prevent candidate bootstrap keys
from taking precedence. Public key files can be regenerated from the retained
private keys. See the [Tailscale SSH host-key implementation](https://github.com/tailscale/tailscale/blob/v1.98.10/ssh/tailssh/hostkeys.go);
verify the same behavior for the exact selected package versions.

The dhcpcd lease file modification time is part of lease age, not incidental
metadata. Do not reset it during copying. Keeping the secret, DUID, interface
names, and MAC addresses does not guarantee that the ISP keeps the same address
or prefix. See the [dhcpcd persistent-file contract](https://github.com/NetworkConfiguration/dhcpcd/blob/master/src/dhcpcd.8.in).
The [dnsmasq lease-file option](https://thekelleys.org.uk/dnsmasq/docs/dnsmasq-man.html)
also stores the server DUID when DHCPv6 is used; retain the complete database,
not selected lease lines.

Render hostnames, MAC addresses, static addresses, routes, firewall rules, DNS
configuration, DHCP reservations, users, and service settings from approved
inputs. Do not copy `/etc` or `/var/lib` as a whole. Exclude bootstrap access
keys, package databases, logs, caches, PID files, and sockets. DNS caches, ARP
and neighbor tables, conntrack entries, and established connections are not
retained. Expect a short network outage and some client reconnections.

Keep this list fixed and versioned with the router profile. Missing required
identity files or an enabled stateful feature outside that profile must block
replacement. Do not treat an unknown file as authority to expand the copy list.

## Bootstrap compatibility

Initial installation and replacement must use one router template, package
profile, configuration renderer, and verifier. Keep the existing
`provision-box` phase order: playbook 30 prepares the router, playbook 31 brings
it into service, then the shared VM playbooks run. Refactor
`30-vm-router-alpine-build.yml` and `31-vm-router.yml` to call the common roles.
Do not maintain a separate router OS recipe for bootstrap.

The common build and personalization roles have two lifecycle modes:

- **Initial installation:** select and freeze an approved initial release,
  clone its generic template, render the box inputs, and enroll a new Tailscale
  identity through the existing broker. Create service state at the profile's
  normal paths on the OS disk. Verify the router and record its
  first accepted release before proceeding to dependent guests. No previous
  generation, `update-required` result, or standing replacement policy exists
  yet; the operation uses the existing approved bootstrap authority.
- **Replacement:** use the upstream check and standing replacement authority,
  prepare a candidate from the same recipe, copy the state allowlist after
  stopping the old router, and perform the bounded switch with rollback.

An existing legacy router uses the supervised baseline adoption below.
Missing accepted-release records alone must never select initial installation
or authorize formatting existing disks. Interrupted installation must resume
from its recorded disk and enrollment identities, without resetting service
state or minting a duplicate Tailscale identity.

Bootstrap must work before the local router, shared service VMs, and
`<box>-ops` exist. Build on the available dom0 using its existing WAN access
and the approved bootstrap controller path. Preserve the documented initial
cloud-controller workflow when that machine holds bootstrap authority. This
does not permit a retained cloud airunner to act as a controller. The common
builder must not require the router being installed, a local artifact service,
or an already active in-box update scheduler.

### Safe provisioning reruns

Make playbooks 30 and 31 read the accepted router assignment before changing
disks, boot artifacts, packages, or Xen configuration. After a replacement,
normal convergence must preserve that assignment, including its service state,
package versions, kernel, and initramfs. It must not restore the legacy
`lv_router` paths, reset repositories to an older branch, re-enroll the router,
or invoke the destructive `router_alpine_rebuild` path on an accepted disk.

Apply this protection to the router calls into `xen-guest`, `vm-base`, and
`tailscale-client`, and to the router role itself. Their existing shared-VM
assignment protection does not cover routers. Use the router assignment
contract; keep the shared VM executor's role restriction intact.

`provision-ops-vm` also invokes playbook 31. Keep this workflow usable: it must
converge approved router configuration against the accepted OS release, or
refuse an unsupported change before mutation. All router provisioning and
replacement entry points must use the same installation lock. Nested calls
must reuse the held lock without trying to acquire it again.

### Alpine inputs shared with other bootstrap playbooks

Both router bootstrap and `40-vm-golden-image.yml` use `alpine-virt-assets`.
Give the router build explicit paths for its selected release, architecture,
ISO, extracted APK repository, modloop, kernel, and initramfs. Version these
inputs by their recorded identity and validate them before reuse.

A router build must not overwrite another profile's shared asset paths or
change its release selection through global defaults. Keep the other bootstrap
playbooks functional when adding these parameters. For one release and profile,
initial installation and replacement must produce the same package manifest
and use the same matching kernel and modules. Personalization must not select
additional packages from a moving repository.

## Authority and scheduling

Reuse the existing Instance-owned update schedule, maintenance window,
installation lock, pause control, and signed standing-policy activation. Add
`router` as a closed target type only after the policy reader can dispatch it
to a distinct router profile and distinct router executor.

Do not add `router` to `shared-alpine-v1`. Add a small router-specific release
profile. The shared executor must reject router targets, and the router executor
must reject every other role.

One signed standing policy can then authorize later exact router replacements
without a human for each update. A source change, policy change, target change,
or branch-policy change still requires the normal Instance and signature path.

The first production baseline adoption and complete replacement must be
supervised. Recurring replacements can become unattended only after the same
code passes rollback fault tests and a complete production verification.

## Check whether an update is required

Run a bounded daily check on the active controller at the Instance-owned check
time. Also expose the same check as an on-demand CLI action. Checking must not
build a VM, change the production router, or install packages on it.

Use two upstream inputs:

- Alpine release metadata at `https://alpinelinux.org/releases.json` for stable
  branches, patch releases, first-release dates, and support status. Report
  support separately for the selected repositories; `main` and `community`
  have different support periods. See [Alpine release branches](https://alpinelinux.org/releases/).
- Fresh, signature-verified APK indexes for the selected branch, architecture,
  and `main`/`community` repositories. Resolve the complete router profile,
  including dependencies, with native APK in an isolated scratch root. The
  package set must include the kernel package and all router services. APK
  handles package requests and their dependencies; see the
  [Alpine package handbook](https://docs.alpinelinux.org/user-handbook/0.1a/Working/apk.html).

Reuse the narrow metadata and native version-comparison helpers in
`ansible/lib/platform_update_metadata.py` where their contracts fit. Use
`apk version -t` for package versions, including Alpine package revisions.
Do not inherit the shared updater's fixed production target list.

For each router, compare the resolved candidate inputs with its protected
accepted release manifest. Read-only verification must first confirm that the
installed package set, running kernel, and boot assignment still match that
accepted release. Report a mismatch as drift and block replacement; do not
silently use the changed live state as the baseline.

The decision rules are:

1. Detect new stable branches and patch releases. Select only the next stable
   branch allowed by the existing `tested-stable` policy and its Instance-owned
   delay. Exclude `edge`, testing, and prereleases. Report later branches even
   when they are not yet eligible.
2. Independently check packages in the current branch. A package or dependency
   update can require replacement even when the Alpine release number is
   unchanged. Current-branch package and patch updates have no branch delay.
   Continue these checks while a newer branch waits for eligibility.
3. Require an update when the eligible branch, resolved package set, or selected
   OS/boot input identity changes. For a new patch release, compare the actual
   selected inputs: if the accepted VM already has all relevant updated bytes,
   the announcement alone does not require a rebuild. Unrelated APK index
   changes also do not require a rebuild.
4. If all relevant inputs match, report `unchanged` and skip build and cutover.
   If inputs differ, report `update-required`, with old and new release and
   package versions, including dependency additions or removals.
5. Record availability separately from eligibility. Report a held branch,
   disabled policy, or unavailable/stale upstream metadata as `deferred`, with
   a reason and any known available update. Failed signature checks, an
   unsatisfied package set, unexplained downgrades, or live drift report
   `failed`. Missing evidence must never become an `unchanged` result.
   A held future branch must not defer an otherwise eligible current-branch
   update; report both findings and prepare the eligible update.

Persist the check time, source identities, accepted release identity, candidate
input hashes, package difference, eligibility decision, and reason in protected
controller state. Downloads and cached indexes stay in the existing cache
location. Do not put these reports in the Instance repository or this folder.

An eligible result feeds automatic preparation outside the maintenance window.
Freeze the exact selected inputs for the build and subsequent cutover; do not
resolve newer packages during personalization. Reuse an already tested candidate
with those inputs. After acceptance, repeated checks against the same inputs
must return `unchanged`. New releases and package revisions within the activated
policy do not require a separate human approval for each update.

## Release and template contract

Create a `router-alpine-v1` profile with these properties:

- one supported architecture;
- an explicit Alpine branch policy;
- the exact router base package requests;
- authenticated Alpine ISO and APK inputs;
- a complete resolved package manifest;
- hashes for the OS disk, kernel, initramfs, profile, and build inputs;
- the approved public engine commit in the build receipt.

Build the template locally on each dom0. There are only a few boxes, and the
router disk is small. Local builds avoid a new cross-site artifact distribution
system. Each build must use the same approved inputs and produce a receipt that
binds its local output hashes.

The generic template must contain no box name, topology address, controller
key, Tailscale state, SSH host key, machine ID, dhcpcd DUID or secret, DHCP
lease, or generated firewall include. A native test must mount or boot a
disposable copy and prove these absences.

Parameterize the current rootfs role instead of creating a second unrelated
builder. The role must accept an output LV and versioned boot-artifact paths.
The normal production names become an accepted-generation pointer, not build
destinations.

## Candidate preparation

For ordinary replacements, run these steps for one box at a time. Initial
installation uses the bootstrap mode above. The supervised legacy adoption
must validate and record its source separately before using the common
candidate build and test steps.

1. Acquire the installation lock and verify the active-controller guard.
2. Read the current signed policy and exact Instance-derived router inputs.
   Require a fresh `update-required` decision bound to the accepted generation
   and the frozen candidate inputs before allocating or building a candidate.
3. Verify that the target is healthy, is the accepted generation, and has no
   unsupported overlay IPv6 state.
4. Verify free LVM space for the template, candidate, current and retained
   generations, the disposable copy guest, and bounded recovery scratch space.
   Stage and test the copy guest before the outage; cutover must need no
   downloads or controller connection.
5. Build or select the exact local template and verify its receipt.
6. Clone a new release-named OS LV. Do not overwrite `lv_router`.
7. Render per-box configuration from approved inputs. Split the router role
   into render and activate phases so preparation does not claim production
   addresses or start DHCP, DNS, routing, or firewall service.
8. Boot the candidate with a non-production Xen name and a restricted local
   management VIF. Use the existing dom0 bootstrap path and a candidate-only
   address. Do not copy production state yet. Do not enroll the candidate in
   Tailscale.
9. Use synthetic state to test the fixed copy contract in both directions and
   service-version compatibility. Validate the package manifest,
   kernel, initramfs, OpenRC links, sysctls, `dnsmasq --test`, and
   `nft -c -f`. Check that all rendered files match the approved inputs.
10. Stop the candidate and record the exact disk, boot artifacts, Xen UUID,
    configuration hashes, and test results.

The preparation path must never query Tailscale to obtain the hostname, tags,
addresses, or desired configuration. A Tailscale query can be a read-only
health or collision check only.

## State copy and legacy adoption

Use one small router-specific copy helper with the fixed allowlist above. Do
not add a general discovery, archive, data-classification, or migration engine.
In line with the [filesystem isolation rule](../../doc/architecture.md#guest-construction-and-runtime-state),
run the helper inside a disposable networkless Xen guest. Dom0 handles block
devices and transaction records; it must not mount production filesystems.

The source router and destination router must both be stopped. Attach the source
disk read-only and the destination disk read-write to the copy guest only.
Require a consistent source filesystem. After an unclean stop, use a disposable
block clone for native journal recovery inside the helper, then read the
recovered clone. Never repair or replay a journal on the original source. Do not
execute programs or hooks from either router disk.
Validate fixed paths, file types, size limits, and parent directories. Reject
symlink escapes and unexpected links or device files. Preserve file bytes,
required timestamps, and approved ownership and permissions. Resolve service
accounts against the profile, not by copying account databases.

Stage the complete file set, verify it, flush it, and record copy completion
before the destination can boot. Recovery must resume an interrupted copy from
the recorded source, not boot a partly written destination. Distinguish an
allowed absent WAN lease from a missing required identity file. Remove only
recorded synthetic test state and temporary bootstrap credentials before the
production copy. Keep secrets on the box disks; never send file contents through
the controller, airunner, logs, or repository.

Legacy routers need a supervised baseline record, not a new storage layout.
Inspect the live paths, effective SSH key fingerprints, package versions, and
exact disk and boot identities through the approved controller. Verify the
configuration against approved inputs and record the old generation as the
rollback source. Do not claim a template build receipt for a legacy disk without
evidence. The first replacement then uses the same copy and cutover procedure as
later replacements. Fresh bootstrap already records this baseline.

### Rollback state is not an old snapshot

Once the candidate runs on production networks, it can renew WAN leases, grant
LAN leases, or update Tailscale keys. Booting the untouched old disk can therefore
restore stale state. A state LV would retain these writes, but an older daemon
would still need to read them correctly.

Before cutover, require tests for the exact old/new service pair and enabled
features: old state read by new services, then new-written state read by old
services. Include DHCP grants and renewals, lease expiry, Tailscale key changes,
SSH fingerprints, and interrupted writes. Use synthetic fixtures and disposable
test identities, not production identity on parallel test VMs. Missing or failed
compatibility evidence must defer unattended cutover. Do not add custom state
format converters to make an incompatible release pass.

If the production candidate has started, rollback stops it and uses the same
helper to copy its latest valid allowlisted state back into the stopped old OS
generation. Only service state changes on that disk; its packages, configuration,
and boot artifacts stay unchanged. If the candidate never started, the original
state on the old disk remains the rollback source. Record that distinction
durably before starting the candidate.

If the latest state is unreadable or incompatible, do not silently restore old
keys or leases and claim successful recovery. Keep the router generations
fenced, record a recovery failure, and use the existing console recovery path.
Tests must cover this failure as well as normal automatic rollback. An OS
rollback is not a remedy for arbitrary state corruption.

## Cutover and rollback transaction

The controller can lose its own network path when it replaces the local router.
For this reason, the final switch must be one bounded command on dom0. It must
not depend on a live remote shell after the old router stops.

Implement a small router-specific transaction. Reuse tested low-level helpers
when they have the correct contract, but do not import the shared VM discovery,
application qualification, retained-data mapping, backup, or transfer layers.

The transaction must:

1. Verify the recorded old and candidate disk identities and boot-artifact
   hashes.
2. Write a pending generation record and arm boot recovery.
3. Stop the old router cleanly and confirm that it cannot restart automatically.
4. Copy and verify the state using the staged networkless guest. Stop that guest
   and detach both disks before a router can start.
5. Select a versioned candidate Xen definition with the canonical router name,
   production MAC addresses, and production VIF set.
6. Persist the production-start marker, then start the candidate. At most one
   generation can use the production identity, MAC addresses, and VIFs.
7. From dom0, verify the backend gateway address, expected interfaces, a WAN
   route, DNS forwarding, and the local router health endpoint or fixed probe.
8. Wait for the controller to verify Tailscale management and send an explicit
   acceptance signal. Use a fixed deadline. A heartbeat must not extend it.
9. If the deadline or any check fails, fence and stop the candidate. Apply the
   rollback state procedure above, restore the old Xen definition, start the old
   generation, and verify local service recovery.
10. If acceptance succeeds, atomically record the candidate as accepted, keep
    its autostart definition, persist dom0 state, and disarm recovery.

The transaction must also recover after a dom0 reboot at each pending stage.
It must select only a recorded old or candidate generation. It must never infer
a disk from an LVM name pattern. Reserve a separate bounded recovery interval
for stop, reverse copy, and old-router boot; candidate checks must not consume
it. Keep both generations out of ordinary autostart until recovery selects one.

## Post-boot verification

Before acceptance, run the existing router verification and add these checks.
For initial installation, verify the newly enrolled identity against the
recorded bootstrap result; identity continuity applies to replacements.

- the Tailscale stable machine ID is unchanged, not only the hostname;
- the expected tag and Tailscale SSH state are present;
- the effective SSH host-key fingerprints are unchanged;
- the DUID and stable IPv6 secret match the recorded bootstrap or copy evidence,
  without logging them;
- the exact release package manifest and running kernel match the receipt;
- all production VIFs and MAC addresses match the Instance-derived topology;
- DHCP, DNS, IPv4 forwarding, policy routing, and nftables are active;
- the compiled router resource files match the current compiler output;
- allowed network paths work and representative denied paths stay denied;
- the WAN lease mechanism and persistent lease stores are usable, retained LAN
  leases are respected, and new grants and renewals work;
- controller-to-router Tailscale is direct when the current policy requires it;
- no first-contact root SSH access or candidate key remains.

Run `platform-check` for router, dom0, resources, and updates after acceptance.
Update `platform-map` so it reports the accepted router generation, previous
generation, state-copy completion, and pending transaction state without exposing
private contents.

## Cleanup and retention

Keep exactly one previous accepted router OS generation for rollback. Delete a
generation only in a later cleanup pass after the new generation is accepted
and a fresh verification passes. Each retained OS disk now contains private
identity state. Keep it offline and access-controlled; never boot two copies.
Any later rollback must also use the latest-state procedure above, not simply
boot the retained disk.

Keep the current and previous template inputs and boot artifacts while a router
generation refers to them. Cleanup must use exact recorded identities. It must
not use wildcards or infer ownership from names.

## Implementation sequence

### Milestone 1: contracts and tests

- Add the router release profile and receipt schema.
- Define initial-install and replacement modes and their authority checks as
  specified under Bootstrap compatibility. Reject unknown existing disks as
  initial-install targets and test interrupted-install resumption.
- Add the read-only upstream check, package resolution, and decision report
  defined above. Test unchanged inputs, a package-only update, a dependency-only
  update, a kernel update, a patch release, an eligible branch, a held branch,
  unrelated index changes, unavailable metadata, and invalid signatures.
- Add native tests for generic-template absence rules and exact package inputs.
- Extend the Instance update target contract and dispatch rules without letting
  the shared executor accept routers.
- Add failure tests for wrong role, wrong box, changed policy, stale receipt,
  and a concurrent installation lock.

### Milestone 2: generic build and candidate

- Parameterize `router-alpine-rootfs` for versioned template and candidate LVs.
- Version kernel, initramfs, Xen UUID, and configuration paths.
- Split router configuration into render, activate, and verify phases.
- Add the restricted candidate boot and synthetic-state tests.
- Change first-install Tailscale discovery so it cannot be reused as candidate
  proof.
- Connect playbooks 30 and 31 to the common recipe. Add router assignment checks
  to provisioning and convergence, including `provision-ops-vm` and shared-role
  calls. Parameterize Alpine asset paths without changing other VM profiles.

### Milestone 3: state retention

- Confirm all State contract paths and effective SSH keys on a test VM and then
  through the approved controller inspection path. Set the dnsmasq lease path
  explicitly; keep the same normal service paths in both lifecycle modes.
- Add the fixed networkless copy helper and private copy receipts. Test file
  validation, permissions, lease timestamps, missing required files, allowed
  absent leases, synthetic-state removal, and interrupted-copy recovery.
- Test state compatibility in both directions for the exact service versions.
  A passing forward boot alone is not rollback evidence.
- During bootstrap, create identity state once and record the first accepted
  generation after verification, without requiring replacement policy.
- Add supervised adoption of the legacy generation's baseline. No state LV or
  one-time storage migration is needed.
- Make enabled overlay IPv6 repair a clear blocking finding.

### Milestone 4: transaction and recovery

- Add the small dom0 router cutover command and pending/accepted records.
- Add fixed deadline, rollback, boot recovery, and exact generation selection.
- Test failures before stop, after stop, during candidate boot, during
  controller disconnect, after acceptance, and during dom0 reboot. Include
  forward and reverse copy interruptions and state changes before rollback.
- Measure the outage and recovery time, including copy-guest startup. Verify
  that all recovery inputs remain available without the router or controller.

### Milestone 5: supervised production proof

- Read the current platform map and relevant private operations journal on the
  active controller.
- Adopt the legacy baseline on one box under supervision.
- Run one complete update and one forced rollback.
- Verify service continuity, identity retention, state retention, and exact
  cleanup behavior.
- Repeat the supervised proof for any materially different site topology.
- On a disposable test target, prove the bootstrap compatibility sequence:
  fresh installation before local service VMs or ops exist; interrupted-install
  resumption; router update; then rerun the router provisioning phases and the
  controller provisioning workflow. Confirm the updated assignment and identity
  remain intact. Do not rerun destructive bare-metal phases on a live box.
- Build a newer router release, then run the shared VM template and clone
  workflow on the test target. Verify it still uses its own selected inputs.

### Milestone 6: unattended operation

- Activate the exact signed standing policy for selected router targets.
- Install the Instance-owned schedule on the active controller.
- Connect the daily check to automatic preparation only when an eligible update
  is required. Verify that repeated unchanged checks allocate no candidate.
- Prepare outside the maintenance window; cut over one router at a time inside
  the window.
- Stop the rollout after one failure until the recorded operation is
  reconciled.
- Record results in protected controller state and report concise findings
  through the normal verification path.

## Acceptance criteria

The work is complete only when all these statements are true:

- Bootstrap and replacement use the same router recipe and verification rules,
  and pass the bootstrap compatibility sequence above.
- Fresh bootstrap records the initial accepted release and service state without
  depending on the local router, local ops, or standing replacement policy.
- A provisioning rerun preserves the updated router's accepted disk, boot
  artifacts, package versions, and identity. Other VM builds retain their
  selected Alpine inputs.
- The tool detects eligible Alpine branch, patch, package, dependency, and
  kernel updates, and explains the difference from each accepted router.
- Unchanged effective inputs cause no build or cutover. Missing or invalid
  upstream evidence cannot produce a false "up to date" result.
- A router update creates a new OS generation and never modifies the running OS
  generation in place.
- The candidate is built from authenticated, recorded inputs and a generic
  template with no box identity.
- The old router stays online during build and candidate qualification.
- For qualified state-compatible releases, cutover needs no human action and
  has a bounded automatic rollback on dom0. Unsupported transitions are deferred
  before the old router stops; corrupt state produces an explicit recovery
  failure, not a false rollback success.
- The controller can disconnect during cutover without preventing rollback.
- Tailscale identity, effective SSH host keys, the dhcpcd DUID and IPv6 secret,
  and required lease data survive replacement and rollback.
- Rollback after a candidate state change retains the latest valid state. No
  router boots with a partial copy, and no two routers use the same identity.
- All other router state is reconstructed from approved automation.
- An enabled state that is not reconstructable causes refusal before cutover.
- The shared DMZ/IoT updater cannot select or mutate a router.
- The router updater cannot select or mutate another VM role.
- A successful update keeps one previous generation and removes older proven
  generations through an exact cleanup record.
- A scheduled update can complete on a changed base package or kernel release,
  and the post-update Platform checks have no critical finding.

## Deliberate simplifications

- Build the small template on each dom0. Do not add cross-site transfer.
- Keep state on the OS disk and copy only the fixed allowlist. Do not add a
  state LV, custom state converters, or general retained-data maps.
- Use the dom0 bootstrap path for candidates. Do not mint candidate Tailscale
  identities.
- Preserve a fixed state allowlist. Do not classify every file on the old OS
  disk.
- Use one serial installation lock and update one router at a time.
- Retain one previous OS generation. Do not add canary wait periods or a fleet
  rollout service.
