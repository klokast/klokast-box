# Controller-managed VM updates

## Delivery status

This delivery implements discovery, the Instance policy contract, and signed
policy activation with `pause` and `resume`, candidate template construction,
and offline base-image boot tests. A dom0 disk-switch transaction and boot
recovery helper are implemented but are not connected to a production executor.
It does not implement unattended VM replacement. `adopt` and `run` are not available.
The existing guest installer remains in use. Do not activate automatic
replacement or treat a report as an
accepted template or release assignment.

The public `shared-alpine-v1` package profile includes the kernel, Tailscale,
Podman, and required base tools. Discovery compares all installed packages,
including dependencies, with signed indexes for the installed explicit branch.
It records the next adjacent stable branch as a build requirement. It does not
resolve or install a dependency closure or download application images. The
separate `prepare` command freezes the full base-package closure for a build.

The separate Python safety rules test artifact identity, kernel/module
agreement, dependency independence, the maintenance cutoff, and pre-acceptance
versus post-acceptance recovery. They do not prove a successful production
replacement. The separate dom0 helper is described below.

Normative input and state ownership rules are in the
[Instance specification](klokast-instance-specification.md#shared-vm-update-intent).
[Secret Authority](secret-authority.md#standing-vm-update-authority) owns
activation and execution authority. The private instance remains read-only on
the controller.

## Discovery commands

Run as `smith` on the active controller from `~/src/klokast/klokast-box`:

```sh
ANSIBLE_CONFIG=ansible/ansible.cfg ansible-playbook -vv \
  -i localhost, ansible/playbooks/74-platform-update-discovery.yml
ansible/bin/platform-update scan
ansible/bin/platform-update status --json
ansible/bin/platform-update verify
ansible/bin/platform-check --box BOX --target updates
```

The setup playbook creates the discovery directories. After this code is in
the approved controller engine, use
`-e '{"platform_update_discovery_scheduled":true}'` to enable two OS cron entries.
The default leaves the schedules disabled. Discovery runs daily at 00:10 UTC.
Evidence verification runs hourly at minute 20. Commands have time limits.
There is no replacement schedule or new daemon.
Only the explicitly active controller can run discovery. The existing
`platform-check-remote` dispatcher can request the `updates` health target.

Before engine promotion, keep candidate source in a separate public checkout
on the controller. Do not pull candidate source into the fixed approved
checkout: the installed source reader requires that checkout to match the
private engine lock. A candidate `platform-update` uses the fixed approved
mapper and verifies its source reader before collecting facts. Candidate code
does not become a sealed engine through this inspection test.

`scan` refreshes the Platform map, inventories every configured or observed
Xen VM, and collects fresh facts from running guests. Stopped VMs
stay stopped. Unclassified guests remain visible as unsupported profiles. Missing
or failed guest collection does not reuse old facts. The collector records
OS, architecture, packages, running kernel and Tailscale versions, module
directories, configuration hashes, template markers, mounts, capacity,
numeric runtime ownership, subordinate IDs, container image IDs, and volume
mounts. It does not emit container environment variables or credentials.

`scan --existing-map` uses a map no more than two hours old, but still collects
new guest facts. Use it only to repeat a failed discovery check. The normal
daily command always refreshes the map. An installation lock prevents two
concurrent scans. A new scan invalidates the preceding report before remote
work starts.

Reports and scan logs are owner-only files under
`/var/lib/klokast/updates/discovery`. Download staging uses
`/var/cache/klokast/updates`. `scan.log` contains controller-local Ansible
diagnostics. The CLI returns 1 for critical findings and 2 for command failure.
JSON reports use stable finding codes. An incomplete report must not be treated
as successful discovery.

`status` reports discovery older than 30 hours and verification older than two
hours. `verify` currently records a critical `release.unverified` finding: the
accepted-release verifier is not yet implemented. It never converts a recent
scan into proof of a healthy replacement.

## Signed policy setup

After approved engine promotion and matching controller toolchain convergence,
run `74-platform-update-authority.yml` from the active controller to create
the root-owned policy state directory. The installed `ksa-apply` must match
the approved source. Candidate code cannot activate a policy for an older
engine.

Use `platform-update policy prepare` with the same seven fresh evidence
arguments as `ksa-apply preflight`: `--plan`, `--authority-state`,
`--controller-toolchain-receipt`, `--source-recovery-receipt`,
`--instance-source-receipt`, `--observation`, and `--build-dir`. This returns
an exact intent for human review. The human signs it on the trusted Mac with
`sign-secret-authority-intent --purpose platform-apply --intent FILE`.
Then call `platform-update policy activate --approval-intent FILE
--approval-signature FILE.sig --signer-id human-platform-apply` on the
controller. The activation has a one-hour lifetime and a single-use nonce.
Changed evidence requires fresh preparation and a new signature.

`platform-update policy status` revalidates the accepted standing policy.
`platform-update pause` sets a local restriction, including when policy is
revoked. `platform-update resume` revalidates current authority before it
removes that restriction. These commands add no VM replacement schedule.
The result explicitly reports `replacement_executor_available: false` until
the production executor is implemented.

## Candidate template construction

Run `platform-update prepare --box BOX --branch v3.23` as `smith` on the active
controller. The source must be clean, committed, and match its public upstream.
Candidate source can use a separate checkout; private inventory still comes
from the installed approved source reader. This operator command does not use
standing policy to select or replace a production VM.

`prepare --box BOX --branch v3.23 --inputs-only` verifies the package closure
and assembles the disposable boot environment without starting Xen. Each run
gets a new operation ID. A failed or incomplete run is never reused.

The controller resolves all base packages from fresh signed indexes, records
exact versions and SHA-256 checksums, and verifies native APK signatures. Native
`apk extract` runs as unprivileged `smith` without package scripts to assemble
a disposable kernel and initramfs. Installation scripts, filesystem creation,
and initramfs generation then run in a new Xen guest with no network interface
or production credentials. The guest receives read-only package input and four
new writable output disks. It has 4096 MiB of RAM, two vCPUs, and a 25-minute
construction deadline. The generic root image is 4 GiB and contains no
application image store. This does not set the capacity of a production VM's
OS or retained-data volume. A second networkless guest has five minutes to boot a
copy of the root image with its matching kernel and initramfs. It tests module
availability, unenrolled Tailscale startup, a rootless Podman container made
from installed BusyBox files, and kernel support for nftables. The original
generic image receives no test account or runtime state. Construction occurs
before a replacement window; it does not consume the separate 30-minute
replacement and 30-minute recovery budgets.

Dom0 reads bounded raw output bytes and verifies their checksums. It never
mounts the generated filesystem. The root image, matching kernel, and initramfs
remain under `/mnt/dom0_data/klokast-vm-templates/candidates/OPERATION`. Candidate
and cleanup evidence remains on the controller under
`/var/lib/klokast/updates/discovery/builds/OPERATION`. This is unprivileged build
evidence, not an accepted release record. Package downloads and disposable
bootstrap files remain in `/var/cache/klokast/updates/build-OPERATION`.

The Ansible build job survives an SSH disconnect and stops the disposable VM
at its deadline. Failure keeps root-owned staging for inspection. A dom0 reboot
does not restart the builder: it has no autostart entry. Confirm that its exact
recorded UUID is absent before removing interrupted staging. Automated reboot
cleanup, production configuration checks, and application compatibility tests
remain required. Base-image boot evidence cannot pass release validation alone.

Application containers are not downloaded or updated. This path installs base
packages, including Tailscale and Podman, into the new generic image. It does
not enroll Tailscale or copy machine credentials into that image.

## Dom0 transaction and recovery

`74-platform-update-recovery.yml` installs the root-only
`vm-update-transaction` helper and an OpenRC boot check. Do not install it as
proof that automatic replacement is ready. The signed controller executor,
data adoption, configuration staging, network fencing, and application checks
must supply its inputs before production use.

The helper accepts only `bak`, `dmz`, and `iot`. Its protected request records
the policy and release hashes, distinct old and candidate Xen UUIDs, LV UUIDs
and sizes, exact Xen definitions, and kernel/initramfs hashes. The candidate definition must include its recorded UUID. It checks live
disk attachments, guest device names, and write modes before each switch. Dom0 never mounts a guest filesystem.
Old and candidate writable LVs must be separate ordinary volumes. Recovery
selects the unchanged old volumes; it does not merge snapshots. This requires
capacity for both generations and a verified data copy before candidate boot.

The request, journal, and active role pointer stay under
`/mnt/dom0_data/klokast-vm-updates`. They are generated operation records,
outside the instance repository and diskless apkovl. Setup and execution verify
that these directories are on the writable ext4 data LV; an absent mount,
RAM filesystem, read-only mount, or different device is refused. The selected `/etc/xen`
definition and autostart link are derived configuration. Only the current role
pointer can select its boot assignment; historical records cannot override it.
An interrupted operation must retain both disk generations and its boot files.

Before old-guest shutdown, the helper starts a bounded local recovery process.
The process identity includes its PID, start time, boot ID, and operation ID.
It waits at most 30 minutes for acceptance, then allows up to 30 minutes for
recovery. It does not require the controller, DNS, a backend VM, or a download.
Native commands and lock waits have time limits. Nested limits retain the
outer deadline, including time already spent waiting for the operation lock.
A permanent daemon is not added. Recovery completes a pending graceful old-VM
shutdown before restarting that VM. It never force-stops old writers or treats
a guest with an outstanding shutdown request as recovered.

Acceptance is written and synced before the new boot assignment is published.
After acceptance, recovery can republish the new assignment but cannot select
old data. The boot check runs before normal Xen autostart. If a record is
invalid or recovery fails, it removes shared-VM autostart entries and reports a
critical error. Router and controller autostart remain available. Existing
OpenRC dependencies are retained. See the upstream
[OpenRC service guide](https://github.com/OpenRC/openrc/blob/master/service-script-guide.md)
for dependency ordering.

The unit suite covers interrupted shutdown and start, interrupted acceptance,
changed boot artifacts and disk identities, missing recovery processes,
expired budgets, foreign disk attachments, and stale role pointers.
`74-platform-update-recovery-test.yml` runs an additional disposable Xen test
using a previously boot-tested candidate. It allocates two new OS/data LV
pairs and tests recovery from stopped, booted, and accepted stages. It repeats
these stages through the boot-recovery entry point in a new process after
stopping the disposable guest. Another case interrupts control immediately
after the native old-guest shutdown request, then starts boot recovery from
the pending journal. The accepted case also checks a synthetic
post-acceptance write. This tests restart from persistent records, not a
physical reboot or OpenRC ordering. It does not install the production helper, change `/etc/xen`, or test real applications.
The test runs the native detached watchdog, with its process identity checks,
operation locks, command budgets, and journal dispatch. It substitutes only
the test domain name, assignment directory, and apkovl persistence interface.
Add `-e '{"recovery_watchdog_expiry":true}'` to test the full 30-minute
replacement deadline. This additional case leaves the candidate unaccepted and
requires the watchdog to restore the old guest without a controller recovery
command. It keeps both data markers unchanged. The test stops its workers
before removing its disks, including after a failure. It publishes success
only after cleanup passes. The receipt includes candidate and test-code
checksums. This mode has a 70-minute limit to allow the full replacement and recovery budgets plus test
setup. A physical dom0 reboot remains a separate acceptance gate.

On 2026-09-17, operation `61ab8c3297a98b8828a5e86f` passed all three native
Xen cases on k002-dom0 with candidate `0d66858d5082567778a3d3b3`. Test data
markers stayed unchanged. Cleanup removed the disposable guest and all four
test LVs, and verified that production domain UUIDs stayed unchanged. No
production VM was replaced and the production boot hook was not installed.
The final code at `2b9684f` passed the same native cases in operation
`407a52ac0bbe921c4e828c0b`, including cleanup. The relevant unit suites passed
74 tests. Native watchdog expiry and physical dom0 reboot remain untested at that commit.

To repeat on an approved test target, run from the active controller's public
candidate checkout. Use a new random 24-character lowercase hex operation ID
and an existing base-boot-tested candidate ID:

```sh
ANSIBLE_CONFIG=ansible/ansible.cfg ansible-playbook \
  -i /home/smith/src/klokast/klokast-box/ansible/execution-inventory/hosts \
  ansible/playbooks/74-platform-update-recovery-test.yml --limit BOX-dom0 \
  -e 'recovery_box=BOX recovery_operation_id=NEW_ID recovery_candidate_id=CANDIDATE_ID'
```

The test requires 10 GiB of free LVM capacity and 3 GiB of free Xen memory.
Evidence stays under `/mnt/dom0_data/klokast-vm-recovery-tests/OPERATION`.
If interrupted, use its `resources.json` to check exact domain and LV identities
before cleanup. Do not reuse the operation or remove disks still attached to a
guest. This test does not authorize a production update.

## Package evidence

Use the official [release metadata](https://alpinelinux.org/releases.json),
[release support policy](https://alpinelinux.org/releases/),
[package indexes](https://dl-cdn.alpinelinux.org/alpine/), and
[security databases](https://secdb.alpinelinux.org/). `main` and `community`
have separate support dates. Community does not inherit the main support date.

Each scan uses a new empty APK cache, explicit HTTPS branch repositories,
installed Alpine signing keys, native index signature verification, and native
`apk version -t` comparisons. It records checksums for downloaded inputs and
signing keys. It does not change the controller package database. It reads
bounded APK v2 index records without extracting archive paths. Multiple package
versions use native APK comparison. Unknown package
formats, conflicting identities, missing packages, failed signatures, failed
security downloads, and old evidence produce unknown or blocked results.

`packages-current` applies only to the package comparison. It does not prove
application health, matching boot artifacts, approved configuration, adopted
storage, or permission to replace a VM. Guest template markers are evidence
only; they cannot establish accepted release authority.

## Replacement and recovery acceptance gates

The following work is required before enabling replacement:

1. Complete the restricted replacement executor under the signed standing
   policy. Keep general Apply contracts unchanged.
2. Qualify the base-image build and boot evidence against each target's approved
   application images and generated configuration. Construction and offline
   base tests are implemented above. Application compatibility and production
   network policy tests remain required before accepting a release.
3. Implement controlled retained-data adoption and fixed catalog maintenance
   adapters. Require a verified backup, ownership and mapping checks, writer
   quiescence, and retention of the original disk. Block unknown data and
   unrecorded container changes.
4. Connect the dom0 transaction above to approved input staging, network
   fencing, data copying, and the signed executor. Verify recovery independence
   and capacity for separate old and candidate disks before stopping the guest.
5. Persist acceptance on dom0 before production access or background work.
   Verify a 24-hour healthy canary for each template before wider rollout.
   Use one installation-wide operation lock and stable box/role ordering.
6. Demonstrate one unattended replacement and a failed replacement with local
   recovery. Verify unchanged app versions and preserved data. Exercise
   controller disconnection and dom0 reboot at each transaction stage.

Until those gates pass, a VM problem uses the existing approved provisioning
and recovery procedures. Do not manually create an accepted-release record.
Do not roll back a data checkpoint after production acceptance: that can lose
new writes. A post-acceptance failure must stop further rollout and report a
critical finding. Recovery before acceptance must use only the recorded old
VM, OS disk, boot files, and checkpoint. Keep the router and controller running.

For console access, see [Platform deployment recovery](platform-deploy.md).
The boot-time recovery check must run before normal guest autostart and
must require no controller, backend VM, internet, DNS service in the target, or
package download.
