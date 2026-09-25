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
5. The expected non-reconstructable state is small:
   - the Tailscale machine state;
   - the dhcpcd DUID and lease data;
   - the dnsmasq DHCP lease database.
   The implementation must confirm the exact installed paths before it moves
   production data.
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

Use three assets for each box:

- a generic, local, versioned router template;
- one immutable candidate OS generation cloned from that template;
- one small persistent router-state LV that is attached to exactly one router
  generation at a time.

Keep the current production OS generation unchanged during preparation. At
cutover, stop the old router, move the state LV to the candidate, and boot the
candidate with the production MAC addresses. Rollback performs the reverse
operation.

The state LV is simpler than copying private files during every update. It also
keeps dom0 from mounting the router OS filesystem. Use a stable filesystem
label and a fixed mount point. Configure these service paths explicitly:

- Tailscale uses a state file on the state LV;
- dnsmasq uses a lease file on the state LV;
- dhcpcd uses a state directory on the state LV, with the exact method selected
  after the installed package behavior is verified.

Do not put generated configuration, package state, SSH bootstrap keys, SSH host
keys, logs, caches, or application data on this volume.

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

The first production state migration and the first complete replacement must be
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
key, Tailscale state, SSH host key, machine ID, DHCP lease, or generated
firewall include. A native test must mount or boot a disposable copy and prove
these absences.

Parameterize the current rootfs role instead of creating a second unrelated
builder. The role must accept an output LV and versioned boot-artifact paths.
The normal production names become an accepted-generation pointer, not build
destinations.

## Candidate preparation

For one box at a time:

1. Acquire the installation lock and verify the active-controller guard.
2. Read the current signed policy and exact Instance-derived router inputs.
   Require a fresh `update-required` decision bound to the accepted generation
   and the frozen candidate inputs before allocating or building a candidate.
3. Verify that the target is healthy, is the accepted generation, and has no
   unsupported overlay IPv6 state.
4. Verify free LVM space for the template, candidate, state LV, and one previous
   generation.
5. Build or select the exact local template and verify its receipt.
6. Clone a new release-named OS LV. Do not overwrite `lv_router`.
7. Render per-box configuration from approved inputs. Split the router role
   into render and activate phases so preparation does not claim production
   addresses or start DHCP, DNS, routing, or firewall service.
8. Boot the candidate with a non-production Xen name and a restricted local
   management VIF. Use the existing dom0 bootstrap path and a candidate-only
   address. Do not attach the production state LV. Do not enroll the candidate
   in Tailscale.
9. Use synthetic state to test mount behavior. Validate the package manifest,
   kernel, initramfs, OpenRC links, sysctls, `dnsmasq --test`, and
   `nft -c -f`. Check that all rendered files match the approved inputs.
10. Stop the candidate and record the exact disk, boot artifacts, Xen UUID,
    configuration hashes, and test results.

The preparation path must never query Tailscale to obtain the hostname, tags,
addresses, or desired configuration. A Tailscale query can be a read-only
health or collision check only.

## One-time state migration

The first release needs one supervised migration from state inside the old OS
disk to the router-state LV.

1. Inventory the exact live service paths and metadata on the router through an
   approved controller playbook.
2. Create and format the small state LV with a stable label.
3. Test the migration with synthetic files in a disposable, networkless Xen
   guest.
4. Stop the old router inside the bounded cutover transaction.
5. Use the disposable networkless guest to copy only the approved state paths
   from a read-only old OS disk to the new state LV.
6. Record hashes and metadata before either production generation starts.
7. Boot the new generation with the state LV. Verify the same Tailscale machine
   identity, the expected WAN client identity, and valid DHCP lease storage.
8. On failure, stop the candidate, attach the state LV to the old generation,
   and start the old generation.

After this migration, every later update moves the state LV. It does not copy
state between OS disks.

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
3. Stop the old router.
4. Attach the router-state LV only to the candidate.
5. Select a versioned candidate Xen definition with the canonical router name,
   production MAC addresses, and production VIF set.
6. Start the candidate.
7. From dom0, verify the backend gateway address, expected interfaces, a WAN
   route, DNS forwarding, and the local router health endpoint or fixed probe.
8. Wait for the controller to verify Tailscale management and send an explicit
   acceptance signal. Use a fixed deadline. A heartbeat must not extend it.
9. If the deadline or any check fails, stop the candidate, restore the old Xen
   definition, reattach the same state LV, and start the old generation.
10. If acceptance succeeds, atomically record the candidate as accepted, keep
    its autostart definition, persist dom0 state, and disarm recovery.

The transaction must also recover after a dom0 reboot at each pending stage.
It must select only a recorded old or candidate generation. It must never infer
a disk from an LVM name pattern.

## Post-boot verification

Before acceptance, run the existing router verification and add these checks:

- the Tailscale stable machine ID is unchanged, not only the hostname;
- the expected tag and Tailscale SSH state are present;
- the exact release package manifest and running kernel match the receipt;
- all production VIFs and MAC addresses match the Instance-derived topology;
- DHCP, DNS, IPv4 forwarding, policy routing, and nftables are active;
- the compiled router resource files match the current compiler output;
- allowed network paths work and representative denied paths stay denied;
- the WAN lease mechanism and persistent lease stores are usable;
- controller-to-router Tailscale is direct when the current policy requires it;
- no first-contact root SSH access or candidate key remains.

Run `platform-check` for router, dom0, resources, and updates after acceptance.
Update `platform-map` so it reports the accepted router generation, previous
generation, state LV identity, and pending transaction state without exposing
private contents.

## Cleanup and retention

Keep exactly one previous accepted router OS generation for rollback. Delete a
generation only in a later cleanup pass after the new generation is accepted
and a fresh verification passes. Never delete the router-state LV.

Keep the current and previous template inputs and boot artifacts while a router
generation refers to them. Cleanup must use exact recorded identities. It must
not use wildcards or infer ownership from names.

## Implementation sequence

### Milestone 1: contracts and tests

- Add the router release profile and receipt schema.
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

### Milestone 3: persistent state

- Confirm exact dhcpcd and dnsmasq state paths on a test VM and then through the
  approved controller inspection path.
- Add the router-state LV, mount contract, service configuration, and private
  metadata checks.
- Add the networkless one-time migration helper and synthetic native tests.
- Make enabled overlay IPv6 repair a clear blocking finding.

### Milestone 4: transaction and recovery

- Add the small dom0 router cutover command and pending/accepted records.
- Add fixed deadline, rollback, boot recovery, and exact generation selection.
- Test failures before stop, after stop, during candidate boot, during
  controller disconnect, after acceptance, and during dom0 reboot.

### Milestone 5: supervised production proof

- Read the current platform map and relevant private operations journal on the
  active controller.
- Run the one-time state migration on one box under supervision.
- Run one complete update and one forced rollback.
- Verify service continuity, identity retention, state retention, and exact
  cleanup behavior.
- Repeat the supervised proof for any materially different site topology.

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

- The tool detects eligible Alpine branch, patch, package, dependency, and
  kernel updates, and explains the difference from each accepted router.
- Unchanged effective inputs cause no build or cutover. Missing or invalid
  upstream evidence cannot produce a false "up to date" result.
- A router update creates a new OS generation and never modifies the running OS
  generation in place.
- The candidate is built from authenticated, recorded inputs and a generic
  template with no box identity.
- The old router stays online during build and candidate qualification.
- Cutover needs no human action and has a bounded automatic rollback on dom0.
- The controller can disconnect during cutover without preventing rollback.
- The Tailscale machine identity and required lease data survive replacement.
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
- Keep one persistent state LV. Do not implement general retained-data maps.
- Use the dom0 bootstrap path for candidates. Do not mint candidate Tailscale
  identities.
- Preserve a fixed state allowlist. Do not classify every file on the old OS
  disk.
- Use one serial installation lock and update one router at a time.
- Retain one previous OS generation. Do not add canary wait periods or a fleet
  rollout service.
