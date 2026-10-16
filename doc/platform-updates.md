# Platform VM inspection and tests

Run image preparation as `smith` on the target box's own `<box>-ops`. Run the
inspection and deployment commands on the active controller. Inspection and
template tests are explicit operations. They do not replace running VMs.
Inspection writes evidence under `/var/lib/klokast/updates/discovery`.
Downloaded inputs and template artifacts use `/var/cache/klokast/updates`.

## Alpine mirror selection

Each box keeps one primary and one fallback mirror on its controller's retained
1 GiB data LV, `/dev/vg0/lv_ops_data`. The controller mounts it at `/srv/retained`
and binds `download-sources` to `/var/lib/klokast/download-sources`. The private
`alpine.json` record contains the box, country hint, selection time, URLs, and
measured transfer results. No daily benchmark runs. See the
[download source policy](architecture.md#public-download-sources).

From the active controller, as `smith`, set up one declared controller:

```sh
cd ~/src/klokast/klokast-box
ANSIBLE_CONFIG=ansible/ansible.cfg ansible-playbook -vv \
  -i ansible/execution-inventory/hosts ansible/playbooks/66-ops-data.yml \
  --limit BOX-ops
```

Setup records ownership before allocation, attaches the LV, and formats it
inside the controller only. It records the filesystem UUID, updates persistent
boot configuration, and selects mirrors from that box. Repeating this command
reuses the same volume and selection. It refuses unknown disks, changed UUIDs,
other guest attachments, dom0 mount references, and unfinished replacement.
After an interruption, run the same command. Do not delete or format the LV.
For an older controller without a protected assignment, first inspect its root
LV UUID and `/etc/xen/ops.cfg` SHA-256 through Ansible on the active controller.
Pass those exact values as `ops_data_expected_lv_uuid` and
`ops_data_expected_config_sha256` extra variables. Setup uses the existing
controller adoption checks before allocation; it never guesses ownership.

On the target box's own controller, as `smith`:

```sh
ansible/bin/platform-update mirrors --box BOX
ansible/bin/platform-update mirrors --box BOX --refresh --country CN
```

The first command only reads the record. The second runs bounded HTTPS download
samples and replaces the selection after at least two mirrors pass. Omit the
country if unknown. A failed refresh keeps the previous record. To refresh and
apply the choice to the controller repositories together, repeat the setup
playbook with `-e alpine_mirror_refresh=true`; it uses the Instance country.

Image input resolution tries the saved fallback once if the primary download
fails or stalls. It publishes inputs only after APK verification succeeds. A
resumed image build uses its frozen bytes. Other host and container recipes use
the primary at provisioning or explicit convergence; they do not run probes or
silently change package branches. OCI base-image pulls keep their own policy.

Controller replacement reattaches the same data LV. It does not copy its files.
Controller verification checks the filesystem identity and bind mount. If a
mount is missing, mirror reads fail rather than write a new record on the root
disk. Restore the recorded attachment with the setup playbook. A lost or changed
data LV requires operator recovery; automatic setup will not adopt a substitute.
Root-generation retirement never retires this data LV.

To apply the saved choice to existing, reachable hosts without installing or
upgrading packages, run `66-alpine-mirrors.yml` from the active controller with
explicit host limits. It preserves each repository branch and persists only
the repository file on diskless dom0. Exclude stopped guests; their next
provisioning uses the saved choice. It refuses to persist unrelated dom0 edits.

## Inspection

- `ansible/bin/platform-update scan --json` collects VM facts and authenticated
  upstream package metadata. `status --json` reads the last report.
- `ansible/bin/platform-update verify --json` checks recorded shared-VM
  assignments, packages, boot files, and services. An absent assignment is
  reported; the command does not adopt a VM.
- `ansible/bin/platform-update inspect-target --box BOX --role dmz --json`
  reports workload, data, configuration, and storage classification.
- `ansible/bin/platform-update-config-audit --box BOX --role dmz --json`
  compares configuration with the checked-in recipes.
- Use `platform-map` and `platform-check --box BOX --target router` for router
  observations and health checks. Standalone router release comparisons are retired.

## Golden-image builds and isolated tests

`ansible/bin/platform-update prepare --box BOX` provides the shared Alpine
golden image. It requires execution on `BOX-ops`, which can be active or
standby but cannot be fenced. It does not contact the active controller or
require an Instance checkout. An absent local controller has no fallback;
initial controller provisioning is separate work.

The local controller downloads public inputs and assembles the boot environment
as non-root `smith`. This account retains administrative authority; the UID is
not a security boundary. Package installation scripts and filesystem
construction run only inside networkless Xen guests. Dom0 handles opaque
artifacts and guest lifecycle, not package extraction. Bulk inputs move only
between the local controller and its own dom0.

The preflight checks controller identity, the local Tailnet name, required
local tools, disk space, Xen capacity, and pending operations before downloads.
It uses a temporary inventory containing only the matching dom0. It does not
read private Instance inventory or provider credentials. The controller build
lock and dom0 build lock reject competing image mutations.

Provisioning commands request preparation on the target box's existing
controller. They receive only a bounded public qualification receipt. Use
`platform-update image-receipt --box BOX --operation-id ID` to export one
qualified build, and `platform-update image-receipt --box BOX --import` to read
that receipt from standard input. The matching local controller or the active
controller can exchange these receipts. Imports validate the box, build ID,
checksums, qualification, and completed guest cleanup. A conflicting or partial
local record stops the import. Original receipts remain in place. No image,
package archive, Instance checkout, credential, or private journal is copied.
This same interface moves historical public receipts to their owning box;
importing evidence does not authorize deployment.
Read-only receipt export validates the complete record set without taking the
build lock. Imports retain that lock. Deployment checks the actual image files
under the dom0 operation lock, even when a receipt was exported earlier.


Each invocation reads official Alpine release metadata and fresh,
signature-verified package
indexes. It selects the newest stable branch supported by both required
repositories, with no release delay. The command has no `--branch` option.
It fails if current upstream inputs cannot be verified.

Use `--profile air-alpine-v1` for a native runner image, or
`--profile ops-alpine-v1` for a controller image. The default remains
`shared-alpine-v1`. Each profile selects current stable Alpine and current
packages at build time. The runner profile also resolves the current stable
Codex musl package from the official release metadata and verifies its SHA-256
checksum. It tests native tools and unprivileged Codex sandbox execution
without authentication or deployment credentials.

Each box selects versions independently. No cross-box version alignment or
release delay is required. Profiles retain their own package sets and explicit
build commands; image preparation does not synchronize running VMs.

The command compares the selected Alpine branch and complete installed package
name/version set, including dependencies, with the latest qualified image for
the same box, profile, and architecture. If versions match, it verifies the
saved candidate metadata and the root, kernel, and initramfs bytes on dom0,
then reuses that image. Unrelated Klokast commits, package checksums, and whole package-index changes
do not trigger a rebuild. The signed package
downloads are still verified on every request. A version change or a missing
or damaged image starts a new build in a disposable, networkless Xen guest.
For runner images, a Codex version or artifact checksum change also requires
a new build. Reuse requires the matching native tool and sandbox test evidence.
For router images, a Tailscale version, signed archive, OpenRC service, or
retained-state recipe change requires new native qualification.
All profiles bind their image construction and finalization code. A change to that code requires new qualification.
Unsafe paths, unavailable storage, and uncertain verification fail the request.

The package profile contains package names. The built image's `/etc/apk/world`
also contains names without exact version constraints. Its input manifest
records the exact installed versions and checksums. The build verifies that
the root image, kernel, and modules match, then tests normal OpenRC boot,
rootless Podman, personalization, and synthetic data recovery.

Synthetic backup and personalization requests bind the exact image profile
under test. Their optional `image_profile` field accepts only the shared,
controller and runner profiles listed above. An omitted field requires
`shared-alpine-v1`, so existing maintenance requests keep their profile check.
The selected profile does not authorize deployment or data adoption.

After a new build passes controller validation, the command keeps that exact
successful local image and removes checked, unused older images of the same
profile and architecture from that box. This includes legacy copies built on
another box, but only when their original build and qualification records match
and their bytes pass verification. A foreign image cannot be selected as the
current replacement or reused for a new local build request.
Cached public inputs of a retired legacy copy are removed only when their
request matches its retained original build record.
Cleanup preserves referenced images, other profiles, incomplete or
unknown artifacts, and compact build and cleanup records. Cleanup runs under
the existing local controller and dom0 locks. Completed VM
transaction records remain in place, and their image references are retained.
Pending transactions, invalid records, and active test guests prevent cleanup.
Retired test-only infrastructure records do not retain an image after their
disks and boot configuration are gone. Controller migration removes the obsolete
automatic-update policy pointer after preserving its recovery copy; that pointer
does not define development authority. Cleanup uses the deployment lifecycle
property defined in [controller operations](platform-syscalls.md).
The command reports deferred cleanup and returns a nonzero status when references
cannot be established; the successful new image remains.
A failed build never retires the previous image. An interrupted cleanup can
leave a partly removed obsolete image. Repeat explicit cleanup with the recorded
`--keep-image` to resume the exact saved plan. Changed references or checksums
stop deletion and preserve the remaining files.

The JSON result reports `candidate-built` or `candidate-reused`, the build ID,
Alpine branch, profile, architecture, package manifest, dom0 artifact directory,
test results, and cleanup status.
Local controller evidence is in `discovery/builds/BUILD_ID/`, including
`selection.json`, `inputs.json`, `build-result.json`, and cleanup records.
For reuse, the build ID, artifact directory, input checksum, and package
manifest identify the original image. `result_directory` contains the current
request's upstream inputs and `cleanup-verify.json`; `image_result_directory`
identifies the original build evidence. Original test results remain historical.
Reuse does not run image retirement. `previous_cleanup` reports the original
build's cleanup status; the current `cleanup.status` is `not-run`.
The command removes the completed request's input cache after reuse.
Completed new-build input caches are removed after successful retirement.
Diagnostic and failed build inputs remain for inspection.

`--inputs-only` stops before the Xen build. `--test-app static-site-web` tests
the declared application image with synthetic data. Neither diagnostic mode
selects or retires the normal golden image. No build command replaces a
running VM. The [nightly controller coordinator](platform-deploy.md#routine-controller-replacement)
can request preparation and replacement for the declared standby. `ops-controller-nightly --services` extends the same scheduled run to shared,
VPN, router and runner guests. Install this cron option only after live qualification. Each
box prepares one shared image per run. The coordinator skips stopped guests
and waits for each replacement and reboot check to finish. It checks image
cleanup before preparing a profile with an installed image, preserving unused
qualified images for selection. It also cleans each selected profile after its
guests pass, including when the image was reused. Private logs and
results remain in `/var/lib/klokast/ops-nightly`.

Preparation uses a matching-box inventory and the development controller
tools. It does not use the active-only `platform-source` or `platform-inventory`
interfaces. Other inspection commands still require those interfaces.
Controller migration follows
[controller operations](platform-syscalls.md).

New controller and runner VMs use their role-specific qualified images.
`provision-ops-vm --box BOX` builds the current controller profile before
cloning. `airunner provision --box BOX` does the same for a declared runner.
Finalization runs inside a networkless Xen guest and grows the cloned root to
its declared size. It sets public account, network and bootstrap configuration;
enrollment follows through the existing controller broker. Repeated provisioning
preserves an assigned disk. An unknown existing disk is a reconciliation error.
Neither command replaces a running legacy controller.

Alpine app-VM provisioning still uses the older `lv_podman_template`.
Retain that LV for its remaining callers. All new
infrastructure profiles use the same isolated image builder; templates contain
no deployment identity, secrets, or application data.

Router provisioning qualifies its template in a networkless Xen guest.
Standalone router state-copy, compatibility, candidate-preparation, and cold
test workflows are retired. Their old artifacts remain retained data; removing
the commands does not authorize deletion of their disks, backups, or identities.

### Router lifecycle integration

The [router state and recovery contract](architecture.md#router-state-and-recovery)
owns the retention and recovery rules. Native common transaction, finalization,
and root-retirement primitives implement that contract. The common image
profile `router-alpine-v3` selects this layout. Historical
`router-alpine-v2` images cannot select it.

`platform-update prepare --box BOX --profile router-alpine-v3` uses the common
builder and the upstream Tailscale resolver. Its disposable Xen tests migrate
native synthetic state, boot two successive roots with the same data disk,
reboot each root, and verify startup refusal with missing or substituted data.
Release evidence requires every native test. `platform-update update --box BOX
--role router` uses the same frozen-input workflow as shared and VPN guests.
The first replacement requires a protected accepted legacy assignment and its
exact accepted controller topology record. Missing baseline evidence blocks
replacement before allocation. Later replacements reattach the recorded data
disk and never reseed it from an old root. `--resume`, `--rebuild`, and
`--retire-older` use the common transaction and retirement paths.

For an unassigned legacy router whose independent-controller migration is
already accepted, run the explicit baseline playbook on the active controller
as `smith`:

```sh
ANSIBLE_CONFIG=ansible/ansible.cfg ansible-playbook -vv -i localhost, \
  ansible/playbooks/74-router-baseline.yml -e router_baseline_box=BOX
```

It verifies native state, current core configuration and compiler rules before
publishing the baseline and topology binding under the existing locks. It
preserves the original topology in `klokast-router-updates/baseline.json` on dom0.
The existing recovery chain preserves the observed Xen UUID during partial
publication. Repeat the same playbook after interruption; changed source or
conflicting records stop it. An existing unrelated assignment, unfinished
operation, unsupported state or policy drift requires reconciliation first.
Baseline reconciliation cannot allocate storage or enroll another identity.
After it completes, use the common router update command above.

There is no in-place state migration command. The qualified image must
contain the common service finalizer, `vm_router_state.py`, and the existing
`router_personalize.py` configuration renderer. It must have no host identity
and must retain the source numeric service accounts or refuse finalization.
The finalizer accepts frozen router configuration and the storage contract;
it does not restore old account tables or configuration trees.

The common service playbook installs matching common and legacy recovery code
on dom0 before preparation. The legacy `common-contract` read checks the installed
engine; the first common arm requires the same source revision. Router recovery installation
also updates an existing controller network recovery helper to respect the
common owner. Finish pending legacy replacement, cold-test, first-install, and
controller network operations before handoff. The router must have no NIC on
`br-ops`; controller management must use its independent uplink.

The native operation records the exact legacy accepted-assignment and generation
hashes for the first migration. The finalizer writes its result only after the
state copy and mount configuration pass. Dom0 binds that result to the common
request before it permits candidate start. An interrupted finalizer must release
its disk attachments before recovery can start either normal router. Never
bypass the exclusive-attachment check to restore service.

After handoff, use the common assignment and `vm-update-transaction router-owner`
read for current recovery state. Legacy accepted-source and accepted-manifest
reads describe historical evidence. Their old Tailscale generation records
cannot authorize removal of the shared logical device. A pre-start recovery
does not transfer boot ownership back to the legacy engine.

The activation gate requires a disposable native Xen test on the target box,
run from its controller. Test first migration and a later root replacement,
then reboot through the actual OpenRC mount and recovery chain. Interrupt
shutdown, each state-copy stage, handoff, and the candidate start response.
Verify stable Tailscale and SSH identity, latest DHCP state, no duplicate data
attachment, refusal with missing or wrong data, blocked legacy recovery and
retirement, and retention of two common roots. Mocked tests do not qualify
Alpine services, Xen attachment behaviour, or router traffic. Live acceptance
also requires forwarding, isolation, DHCP/DNS, WAN, management, and reboot
checks. A router image cannot be selected without its native qualification.
Converge nightly selection on an installed box only after its first supervised
migration and reboot pass. The coordinator checks the router after other selected
services and uses the same upstream image comparison and persistent volume.

The guest payload `ansible/roles/vm-template-builder/files/vm_router_state_fixture.py`
provides the disposable ext4 and bind-mount part of this qualification. Run it
only inside a networkless test guest with a new zeroed 1 GiB disk at `xvde`.
It uses synthetic identity bytes and two temporary root trees. It is not a
controller command or a test of live Tailscale identity and traffic.

## Storage assessment

The discovery report includes retained-data and storage classification.
`ansible/bin/platform-update retention --json` compares declared retention
with discovery. Neither command deletes data or treats unknown storage as empty.

## Provisioning and boot recovery

First router installation runs through `provision-box` phases 30 and 31.
It calls `provision-router phase --box BOX --phase prepare|accept` under the
parent installation lock. Use `provision-router status --box BOX` to read the
protected first-install and boot assignment state. Resume installation through
`provision-box`; individual installation steps are internal.
The checked-in first-install defaults select the newest supported stable Alpine
branch with no release delay and require selection evidence no older than 30 hours.
These are checked-in first-install defaults, not Instance update policy.

Dom0 record readers and boot recovery preserve accepted assignments and pending
operations. Keep required records, previous disks, backups, and recovery hooks.
Remove a recovery hook only after its dependent boot assignments have been
reconciled through a separate operation.

The retained native dispatcher, its installed module set, and the boot hooks
still read historical records. Their names and storage paths retain the old
`router-updates` spelling for compatibility. This does not provide a controller
workflow to start new replacements or cold tests. Pruning native recovery and
changing commit-based engine identities are separate work.

Before deploying the provisioning extraction, inspect the active controller's
journal and each target's protected records through controller automation.
Do not deploy while a replacement, cold test, or incomplete first installation
needs the old tooling. Complete it with the previous revision first. Qualify
the extracted installer on a designated disposable target before a real first
installation. Unit tests do not qualify a live install.

`74-platform-update-discovery.yml` prepares private inspection directories.
Controller convergence runs the same setup tasks. Controller installation
checks pending operations and preserves accepted records and boot hooks; see
[controller operations](platform-syscalls.md).

## Explicit image cleanup

Run on the target box's own controller as smith:

```sh
ansible/bin/platform-update cleanup --box BOX --profile ops-alpine-v1 --keep-image BUILD_ID --dry-run-plan
ansible/bin/platform-update cleanup --box BOX --profile ops-alpine-v1 --keep-image BUILD_ID
```

This command validates the selected qualified image and removes eligible older
images and associated input caches. It does not download inputs or build an
image. It accepts each supported image profile, including `shared-alpine-v1`
and `vpn-egress-alpine-v1`. It retains images needed by allocated controller disks and
incomplete operations. Repeat cleanup to resume a recorded partial deletion.
Unknown resources remain reported and untouched. Controller disk retention is
owned by [the controller model](architecture.md#controller); retire disks before
cleaning their images.

The nightly coordinator uses `--preserve-qualified` before image preparation.
This keeps unused qualified local candidates available for reuse. It still
removes eligible input artifacts. After image selection and health checks,
the coordinator runs normal cleanup with the selected build ID.

<a id="dmz-creation-and-reconstruction"></a>

## Shared guest creation and reconstruction

Run on the active controller as `smith`. In these commands, set `ROLE` to
`bak`, `dmz` or `iot`:

```sh
ROLE=iot
ansible/bin/platform-update provision --box BOX --role "$ROLE" --dry-run-plan
ansible/bin/platform-update provision --box BOX --role "$ROLE"
ansible/bin/platform-update update --box BOX --role "$ROLE" --rebuild --discard-root-data --dry-run-plan
ansible/bin/platform-update update --box BOX --role "$ROLE" --rebuild --discard-root-data
ansible/bin/platform-update update --box BOX --role "$ROLE" --rebuild
ansible/bin/platform-update update --box BOX --role "$ROLE" --retire-older --dry-run-plan
ansible/bin/platform-update update --box BOX --role "$ROLE" --retire-older
```

`update` creates an absent BAK, DMZ or IoT VM through this provisioning path.
It refuses unknown existing disks and honors declared-stopped guests. All three
roles require the target box's own controller for image preparation.
`provision` resumes incomplete creation from its frozen inputs and verifies an
accepted guest without replacing it.

Provisioning is idempotent: an installed assignment is verified, not replaced.
The default OS root is 8 GiB. A new persistent data LV is 50 GiB; an existing
data LV keeps its size. Capacity checks precede allocation. No data resize is
part of an update. The [architecture](architecture.md#routine-service-vm-replacement)
owns the storage, identity and exclusive-writer contract.

The first transition requires `--discard-root-data` to omit old root-resident
application files, container storage and inactive configuration. Inspect the
root/data mapping first. The protected operation binds this explicit decision
to the old root. It does not copy or archive application files. Existing
separate data disks remain intact. Do not pass this option on routine updates;
resume reads the decision from the recorded operation.

After transition, rootless Podman storage is under `/srv/retained/podman`,
bind-mounted at `/home/neo/.local/share/containers`. Application datasets belong
under `/srv/retained/apps`. Application installers own their bind mounts and
reconstruction; this workflow does not start or recover applications. Running
containers or application writers block replacement. Missing data mounts block
storage consumers instead of creating substitute storage in the root.

`--rebuild` reconstructs even when the image is unchanged. Ordinary unchanged-image
updates perform health checks only. Add `--image IMAGE_ID` to rebuild from an
already qualified local image without fetching release metadata. The selected
image must still pass receipt and current recipe checks. Replacements keep the same data disk and
filesystem UUID and allocate only a new OS root. Retirement keeps the current
and previous accepted roots. It never deletes the persistent data disk. An old
OS root is not a snapshot of data; recovery after candidate boot does not select
the previous root automatically. Interrupted replacement and reboot verification
must resume through the command reported in the private result.

Use the existing 03:00 UTC job and the
[nightly coordinator's qualified role selection](platform-deploy.md#routine-controller-replacement);
do not enable `--services`. The coordinator skips declared-stopped guests.
Nightly execution cannot discard old root-resident data or perform
configuration-only rebuilds.

## Controller and runner lifecycle migration

Use the [common state and authority contract](architecture.md#routine-service-vm-replacement).
Run these commands on the active controller as `smith`. Finish legacy controller
replacement and its reboot check first. Keep active-controller authority and
credential handoff separate. A target must already have its protected
infrastructure assignment; absence does not authorize disk adoption.

```sh
ansible/bin/platform-update update --box BOX --role ops --migrate-root-data --dry-run-plan
ansible/bin/platform-update update --box BOX --role ops --migrate-root-data --image BUILD_ID
ansible/bin/platform-update update --box BOX --role air --migrate-root-data --maintenance --image BUILD_ID
ansible/bin/platform-update update --box BOX --role air --resume OPERATION_ID
ansible/bin/platform-update update --box BOX --role ops --retire-older --dry-run-plan
```

The first controller transition grows its owned mirror LV to 50 GiB in place.
Growth records intent before changing LVM. The networkless finalizer checks and
grows the filesystem to the full device size before copying state. Runner
migration allocates a new 50 GiB data LV. Check capacity
for the retained data and the new root before the maintenance window. An existing
pending operation blocks migration. Do not edit its frozen inputs or choose a
new image on resume. The maintenance decision is frozen with a new runner
operation; it cannot be added to resume.

The image tests use disposable state to verify SQLite integrity, numeric
ownership, links, interrupted copying and refusal to seed over newer data.
Before schedule enablement, also verify first live migration, a later root
replacement, reboot persistence, unchanged machine identity and unchanged data
LV and filesystem UUIDs. Record evidence in the private operations journal.
After verification, controllers keep only the current accepted root. Runners
keep the current and previous accepted roots. An old root is not a data backup.

After migration, ordinary `update --role ops` and `update --role air` use the
same transaction as other VMs. `--rebuild` forces a root replacement for the same
image. Nightly execution never grants first-migration or maintenance consent.
Select `air` in the existing nightly role list only after qualification. Existing
role selections are preserved during controller reconstruction. Initial bootstrap
still uses `provision-ops-vm` or `airunner provision`; complete this explicit
migration before enabling routine updates for that new guest.

## Common VM replacement

From the active controller as `smith`:

```sh
ansible/bin/platform-update update --box BOX --role dmz --dry-run-plan
ansible/bin/platform-update update --box BOX --role dmz
ansible/bin/platform-update update --box BOX --role vpn-egress --image BUILD_ID
ansible/bin/platform-update update --box BOX --role dmz --resume OPERATION_ID
```

The common update command supports `bak`, `dmz`, `iot`, `router`, the standby
`ops`, declared `air` and declared `vpn-egress` guests. It requests a qualified image from the box's own controller,
or checks the selected existing image. A matching installed image causes health
checks only. Configuration changes do not cause replacement or convergence.
A guest declared stopped is skipped. Running application containers stop the
request before disk allocation; application compatibility support is required.

BAK, DMZ and IoT reconstruction use the separate persistent data disk described
above. They preserve the declared machine and maintenance identities and
numeric ownership. Managed configuration comes from frozen Platform inputs.
Historical BAK and IoT replacements resume with their frozen state-copy code.
Legacy root disks outside accepted generation history remain retained; routine
retirement does not adopt or delete them.
VPN reconstruction follows the [service replacement contract](architecture.md#routine-service-vm-replacement).
It installs approved current configuration and the checksum-verified Mihomo
artifact, and preserves only the declared identity inputs. Shared-guest broad
state copying does not apply to the VPN path.

`update --box BOX --role vpn-egress --rebuild` prepares current inputs and
replaces the root even when the image is unchanged. It cannot accompany
`--resume`. `--retire-older` selects checked generation cleanup and is mutually
exclusive with rebuild, image selection and resume. Add `--dry-run-plan` to
inspect its deletion plan. Repeat cleanup to resume an interrupted deletion.

The existing dom0 transaction records hold disk identities, frozen inputs and
reboot status. Controller snapshots are under
`~/private/klokast/vm-replacement-inputs/`. Do not remove an unfinished snapshot
or an old disk. Keep historical service disks and their referenced images until
a separate checked retirement operation removes them. Capacity exhaustion stops
an update before shutdown.

An interrupted request reports its exact resume command. A preflight failure
before the protected operation exists reports a retry command instead; a lost
status response keeps the resume command because allocation is uncertain.
Resume the recorded
operation before requesting another replacement. If a pre-boot timeout restored
the old guest, inspect the recovery record before starting again. After the new
guest starts, recovery keeps the new disk and boot assignment. A failed health
or reboot check stays incomplete; the updater does not automatically roll back.

For a failed attempt that has never started the new VM, inspect its private
result and use `platform-update update --box BOX --role ROLE --resume ID
--abandon-unbooted`. This verifies or recovers the old running generation and
releases the unfinished-operation block. It keeps all candidate disks, images
and audit records. It refuses once normal boot of the new generation was
attempted. A fresh update can then use corrected, qualified inputs.
