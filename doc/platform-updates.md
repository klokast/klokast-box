# VM images and replacement

This document describes Platform image preparation, inspection, VM creation
and VM replacement. [Dom0 updates](dom0-updates.md) describes the diskless
host bundle and its activation.

Run image preparation as `smith` on the target box's own `<box>-ops`. Run the
inspection and deployment commands on the active controller. Inspection and
template tests are explicit operations. They do not replace running VMs.
Inspection writes evidence under `/var/lib/klokast/updates/discovery`.
Downloaded inputs and template artifacts use `/var/cache/klokast/updates`.
This directory is on the root volume of the controller VM, not in RAM.

Each image preparation keeps one copy of each Alpine package in
`/var/cache/klokast/updates/packages/<name>/`. A copy stays until a
preparation resolves another version of that package. A later preparation
downloads the signed indexes again, lets `apk` check each copy against them,
and downloads only the packages that are absent or changed. A replacement of
the controller VM root volume removes these copies; the next preparation
downloads them again.

AIR input resolution and release freshness checks require the owning
controller's [public GitHub metadata token](../klokast-dev/runbooks/27-github-metadata.md).

## Alpine mirror selection

Each box stores its primary and fallback mirror selection on the controller's
retained data LV, `/dev/vg0/lv_ops_data`. The controller mounts it at `/srv/retained`
and binds `download-sources` to `/var/lib/klokast/download-sources`. The private
`alpine.json` record contains the box, country hint, selection time, URLs, and
measured transfer results. No daily benchmark runs. See the
[download source policy](architecture.md#public-download-sources).

Before common lifecycle migration, set up one declared controller from the
active controller, as `smith`:

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
samples from the official mirror list and replaces the selection after at
least two mirrors with current indexes pass. Omit the
country if unknown. A failed refresh keeps the previous record. Before common
lifecycle migration, you can also repeat the setup playbook with
`-e alpine_mirror_refresh=true` to refresh the choice and apply it to controller
repositories; it uses the Instance country. After migration, use the mirror
command and the [common lifecycle](#controller-and-runner-lifecycle-migration).

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
not a security boundary. The boot environment of a golden-image build contains
only the builder tools (`BUILDER` in `ansible/lib/vm_template_inputs.py`) and
their dependencies. The image packages are in the input capsule, thus the
package set of a profile does not change the size of the boot environment.
Package installation scripts and filesystem
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
indexes. It selects the newest published stable branch, with no release delay.
Support-calendar dates do not change that selection. The selected branch must
provide the required architecture, both repositories and verified packages.
Unavailable or invalid inputs stop the build; they do not select an older branch. The command has no `--branch` option.
It fails if current upstream inputs cannot be verified.

Use `--profile air-alpine-v1` for a native runner image, or
`--profile ops-alpine-v1` for a controller image. The default remains
`shared-alpine-v1`. Each profile selects current stable Alpine and current
packages at build time. The runner profile also resolves the current stable
Codex musl package from the official release metadata and verifies its SHA-256
checksum. It also resolves current stable Claude Code from Anthropic's signed
APK index with the pinned key. It tests native tools, unprivileged Codex
sandbox execution and Claude Code execution without authentication or
deployment credentials.

Each profile also resolves the latest upstream stable Tailscale release from
`pkgs.tailscale.com`. The controller verifies the upstream signature of the
archive with `tailscale-distsign` and freezes the archive, the binary hashes,
and the checked-in OpenRC service file. No profile requests the Alpine
`tailscale` package: an Alpine stable branch keeps an older Tailscale release.
The build guest installs the two binaries, the `tailscale` account, and the
service file. On the router, the binaries are in `/usr/local`. On all other
images, they are at the Alpine package paths `/usr/sbin/tailscaled` and
`/usr/bin/tailscale`, and the service accepts the same `/etc/conf.d/tailscale`
settings. These profiles request `openresolv` explicitly, because the Alpine
package no longer supplies it. An image-built VM gets a new Tailscale release
only through image replacement. Guests without a managed image must use the
supported image replacement workflow before client convergence. Client
convergence does not install Alpine Tailscale as a fallback.

The [dom0 bundle](dom0-updates.md#tailscale-in-the-bundle) carries its own
upstream Tailscale release.

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
For runner images, a Codex or Claude Code version or artifact checksum change
also requires a new build. Reuse requires the matching native tool and sandbox test evidence.
For all images, a Tailscale version, signed archive, or OpenRC service change
also requires a new build. For router images, a retained-state recipe change
requires new native qualification. An image frozen with the Alpine Tailscale
package is not reused.
All profiles bind their image construction and finalization code. A change to that code requires new qualification.
Unsafe paths, unavailable storage, and uncertain verification fail the request.

The package profile contains package names. The built image's `/etc/apk/world`
also contains names without exact version constraints. Its input manifest
records the exact installed versions and checksums. The build verifies that
the root image, kernel, and modules match, then tests normal OpenRC boot,
personalization, and synthetic data recovery. It tests rootless Podman and
installs the container configuration only when the frozen package set contains
`podman`; the runner profile does not.

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
unknown dom0 artifacts, and the compact build and cleanup records of complete
builds. Cleanup runs under
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
After dom0 cleanup applies its plan, the controller removes each build
directory with incomplete evidence, for example from a failed or interrupted
build. It keeps the directory of each image that dom0 still holds or that a VM
references, the newest 10 build directories for diagnosis, reuse records, and
the builds of other boxes and profiles. The cleanup field `removed_builds` names
the removed directories. Image reuse reports the remaining incomplete
directories as one count.
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
its declared size. It sets public account and network configuration;
enrollment follows through the [common enrollment transport](architecture.md#shared-guest-image-and-enrollment). Repeated provisioning
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
retirement, and retention of only the current common root. Mocked tests do not qualify
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
The checked-in first-install defaults select the newest published stable Alpine
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
An archived erased legacy VPN gateway keeps its source-image reference and audit
record. Its completed retirement does not block cleanup of other images.
Unknown resources remain reported and untouched. Controller disk retention is
owned by [the controller model](architecture.md#controller); retire disks before
cleaning their images.

The nightly coordinator uses `--preserve-qualified` before image preparation.
This keeps unused qualified local candidates available for reuse. It still
removes eligible input artifacts. After image selection and health checks,
the coordinator runs normal cleanup with the selected build ID.

## Image packages and enrollment transport

The [shared image and enrollment contract](architecture.md#shared-guest-image-and-enrollment)
owns the package and enrollment rules. The builder removes APK payloads after
installation and initramfs generation. Package metadata remains inventory
evidence; it does not mean APK can run in the guest. Python, doas and Tailscale
SSH remain. The controller uses its APK only in a separate resolver root.
Controller convergence refuses a package change on an image root; the
[replacement checks](#common-vm-replacement) compare the installed packages
with the qualified image. Native APK audits are unavailable without APK and
must not report success. Package inventory and normal replacement checks read
the retained database.

Application installers that use host APK need migration before use on these
images. That migration is separate work. Runtime configuration still uses the
existing writable OS root.

The enrollment key is absent from public configuration, images, persistent
assignments and logs. A one-shot guest task runs only with an explicit
enrollment boot parameter. Its input disk and a separate result disk bind the
attempt to the assigned Xen UUID and public configuration checksum. The
permanent boot recipe contains neither temporary disk. Dom0 remains an existing
Integrity-TCB boundary; it receives only the key for the assigned guest. No
enrollment daemon, credential service or network listener is added.

Resume interrupted creation through the creation command of the same box and
role. Resume a recorded attempt without another key; after loss of volatile
state, probe the same root without a key before requesting one. Normal boots and
replacements preserve identity and do not enroll again. Legacy unfinished
operations keep their original frozen source.

## Appliance image lifecycle

The [print](architecture.md#print-appliance) and
[exit](architecture.md#exit-appliance) appliances share this lifecycle. They
share one fixed dom0 driver and build workflow; the driver accepts only these
two kinds.

The Platform-owned `platform-print-appliance` and `platform-exit-appliance`
commands build on the owning controller, with
verified versioned APK inputs and the existing networkless Xen build transport.
Package installation and filesystem interpretation happen inside a disposable
builder VM. Qualified images are cloned onto dom0 LVM; dom0 never mounts the
guest filesystem. The new appliance boots a read-only OS root. Bounded temporary
storage holds jobs and logs. A small writable partition on the same disposable
disk holds only its Tailscale state across reboots.

The controller manages the appliance through Xen and external checks, without
guest shell access. Rebuilding discards all runtime state and enrolls a fresh
identity after it revokes the old offline registration. Superseded and failed
image bytes are removed after their Xen and loop references are gone; only
diagnostic logs, manifests and operation metadata remain. There is no image
rollback. A failed replacement stays unavailable until a corrected new image is
built.

## Replacement contract

The [architecture](architecture.md#routine-service-vm-replacement) owns the
principles of the common VM lifecycle. This section describes how each role
applies them.

Shared guests use one `shared-alpine-v1` image per box; the gateway uses
`vpn-egress-alpine-v1` and the router uses `router-alpine-v3`. Controllers use
`ops-alpine-v1`; runners use `air-alpine-v1`.

A service replacement uses the existing protected VM transaction and boot
recovery records. It freezes the public revision, Instance revision, image and
configuration for an unfinished operation. Current controller markers and the
Instance controller list are checked independently before disk transitions. A
guest declared stopped remains stopped during its nightly invocation.

### Configuration identity

Each accepted root records two identities: its qualified image and its
configuration identity. The configuration identity is a SHA-256 of the public
files that configure the role after boot, which are the post-boot playbooks,
their roles and the files that these roles copy, and of the declared inputs of
that root: its Instance inventory values, its group policy and the role values
that the replacement freezes. `RECIPES` in `ansible/lib/vm_configuration_identity.py`
is the exact list; a repository test compares it with the playbooks. An update,
also a nightly one, replaces the root when either identity differs, and uses
the same image when only the configuration changed. Otherwise it runs health
checks only, so a commit that changes neither identity does not allocate a disk
or restart a guest. Inputs that another workflow changes in place are not part
of the identity: the controller checkout (`controller-checkout-update`) and the
Platform resource rules on routers and controllers (`platform-resources`).

### Update history

Each dom0 keeps `/mnt/dom0_data/klokast-update-history/MACHINE.jsonl` for
itself and for each VM of its box that a common replacement updates. Each line
is one accepted update: the UTC time, the operation, the kind (`image`,
`configuration`, `image and configuration`, `rebuild` or `first record`), the
previous and the new image and Platform commit, the added, removed and changed
packages, and the names of the changed configuration components. The packages
include the Tailscale, Codex and Claude Code versions outside APK. A value that
is not known, such as the packages of a root without a retained receipt, is
`null`. A dom0 has no configuration identity, so its `configuration` is `null`.
A `seeded` entry was made from an older operation record.

The step that accepts the update writes the entry, once for each operation.
Reboot tests, failed or abandoned updates, rollbacks and unchanged runs write
no entry. The initial creation of a VM writes no entry. The history is evidence
for people: no update decision reads it, and a failed write is a job warning
that does not stop the update. `machine-status` validates it before display.
The files are readable by every account on dom0, so they hold component names
and never Instance values. No rule deletes them
([log retention](../ansible/ansible.md#log-retention)).
[`kk status`](../klokast-dev/README.md#runner-and-maintenance-commands) shows it.

### Boot history

Each dom0 keeps `/mnt/dom0_data/klokast-boot-history/MACHINE.jsonl` for itself
and for each declared VM of its box. Each line is one event with the boot ID of
the kernel and the UTC time.

The `klokast-boot-history` one-shot of dom0 writes a `boot` line at each start,
with the kernel and the dom0 update or reboot operation that was pending, and a
`stop` line when a clean stop begins. No service waits for it, and a failed
write does not stop the start or the stop. A boot without a `stop` line, before
a later boot, means a power loss, a crash or a forced reboot. The `stop` line
does not prove that the stop completed.

The nightly coordinator of the active controller reads the current boot of each
declared running machine before the updates, and adds a line when its boot ID
is new. The VM history thus has only the last boot before each check, without
its end. The reason is that a VM root is disposable and that a hook in the VM
start path could stop a guest from starting. The check also adds a dom0 boot
that the one-shot missed; the end of such a boot is not known. The clock of a
dom0 can be wrong before NTP runs, so the check corrects a recorded start time
that differs by more than 60 seconds, once, with a new line for the same boot
ID. `kk boots` shows an accepted update of a VM as the cause of a boot when the
update was accepted within one hour after the boot and before the check that
saw it.

The files are append-only evidence for people: no Platform decision reads them.
They are readable by every account on dom0 and hold no Instance values. No rule
deletes them. A failed check does not change the update result;
[`kk status`](../klokast-dev/README.md#runner-and-maintenance-commands) shows
it, and `boot-history check` on the active controller repeats it. The controller
validates each returned line; a machine can state only its own current boot.
[`kk boots`](../klokast-dev/README.md#runner-and-maintenance-commands) shows the
history. Provisioning installs the one-shot; on an existing dom0 use
`ansible/playbooks/74-dom0-boot-history.yml`.

### Shared guest storage

The initial shared-guest workflow supports empty guests and stopped retained
workloads. Running application workloads require application compatibility
support before automatic replacement. Stopped containers, images, volumes and
numeric ownership remain retained data. Their absence from active application
declarations is not permission to delete them or start them.

BAK, DMZ and IoT creation and replacement use the same qualified shared image
and Ansible configuration. The OS root is disposable. A separate data LV is
mounted at `/srv/retained`; it holds rootless Podman storage and application
datasets under `apps/`. Bind-mount Podman storage at its stable guest path.
Runtime storage remains volatile. Storage consumers must refuse an absent or
incorrect data mount. OS packages come from the image; guest configuration does
not upgrade packages. Container image updates and application recovery are
separate work.

A BAK, DMZ or IoT replacement reuses the exact recorded data LV and filesystem
UUID. It does not copy or snapshot data. Stop the old VM and prove exclusive
disk use before attaching the data disk to the candidate. Only recorded
persistent data may occur in both generations; OS roots must remain distinct.
Preserve numeric ownership and the small machine, SSH and Tailscale identity
records. Reconstruct managed configuration from approved inputs, not from the
previous root. If the old guest has no SSH host keys, the new root must also
have none. Tailscale SSH is the maintenance path.

The first BAK, DMZ or IoT transition can explicitly discard root-resident
development data with `--discard-root-data`. Bind this decision to the source
root UUID in the protected operation. It never authorizes deletion of a
separate data volume. Nightly execution cannot grant this permission. An older
root is not a point-in-time data backup. Historical BAK and IoT operations keep
their frozen state-copy implementation. Legacy disks outside accepted
generation history remain retained.

### Retained controller and runner state

Controller and runner state uses a 50 GiB retained data LV. Grow an existing
controller mirror LV without changing its LV UUID or filesystem UUID. Keep
controller roots at their existing size and runner roots at 50 GiB. First
migration requires explicit `--migrate-root-data`. Copy state only while the old
VM is stopped, inside the networkless finalizer. Complete the offline filesystem
check and growth to the assigned device size before copying state. Verify copied
content, numeric ownership and metadata before recording completion. Bind the
migration to its source identity and operation. Resume that operation after an
interrupted copy. The engine starts another operation only when none is
unfinished, so a partial copy of another operation from the same source is
abandoned: the new operation deletes it and copies again. A different first
migration must not reuse its completed seed. Routine replacement attaches the
same data LV and never restores old root data over it.

For `ops`, migrate the existing controller state allowlist: read and recovery
keys, private working files, frozen operation inputs, journals, audit records,
update history and download-source selection. Bind writable directories,
including `/home/smith/private/klokast`, so atomic file replacement works.
Reconstruct the Instance checkout and generated controller tools there from
approved sources; do not seed source checkouts from the old root. Preserve
explicitly allowlisted provider files directly from the stopped same-controller
root into its new root. This preserves file ownership, permissions and normal
atomic credential updates without restoring managed `/etc` trees. Retain Git
keys with the other SSH keys. HA markers are excluded; replacement writes a new
standby marker that names the current active box. Recheck the live standby and
active markers before shutdown. Authority handoff remains a separate workflow
under the [controller model](architecture.md#controller).

For `air`, the [retained home](airunner.md#retained-home) lists the retained
state, and the [activity check](airunner.md#runner-activity) protects running
agents.

Both roles require the exact retained mount and binds before Tailscale, SSH or
cron starts. Common boot assignment records become the sole generation owner
at migration. Old provisioning and replacement commands must refuse that owner;
historical unfinished operations keep their frozen implementation. Generation
retirement excludes retained volumes. Controllers and runners keep only the
current accepted root after reboot verification.

### VPN gateway reconstruction

VPN creation and replacement use the same image personalization and Ansible
configuration code. VPN reconstruction preserves only Tailscale identity,
machine ID, SSH host keys and required numeric account identities. Account
definitions, networking, DNS, firewall, OpenRC service and VPN configuration
come from Platform code, Instance and protected controller inputs. Packages
come from the qualified image. Mihomo comes from the approved, checksum-verified
artifact. Caches are reconstructed. Required private artifacts and the selected
domain list must be validated and frozen before shutdown.

An explicit VPN rebuild replaces the root even when the image is unchanged.
It can repair package and managed configuration drift. Conflicting ownership,
unexpected persistent storage or unavailable maintenance access stops it.
The operation snapshot binds implementation modules, Instance, rendered
configuration and required artifacts. Resume uses these frozen inputs while
checking current authority independently. Historical operations retain their
verified implementation and recovery rules; missing inputs are never inferred.
The transaction assignment owns VPN boot and runtime intent; the original
infrastructure assignment remains historical.

### Root retirement and recovery

Each accepted service replacement deletes the older roots of that role. Only
the current accepted root stays, because recovery after acceptance rebuilds
a root and does not select an older one. Retain the boot evidence of the
current root; persistent data is excluded from generation retirement.
Checked deletion plans bind LV UUIDs, ownership, boot checksums and live
references before removal. Incomplete operations and unknown resources stay.
Interrupted cleanup resumes its recorded plan. Retired image references are
released, but audit evidence stays; accepted replay cannot restore references.
An incomplete replacement blocks fresh work and reports its resume command.
After the new guest starts, recovery preserves that generation and does not
select an older disk automatically. Automatic recovery of an unreachable VM
is outside this workflow. The [controller root retirement](controller.md#root-retirement)
rules add the controller-specific cases.

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
part of an update. Each guest gets 4096 MiB of memory and 2 vCPUs at creation
and at each reconstruction. These sizes are Platform parameters: the Instance
and the inventory do not set them. The [architecture](architecture.md#routine-service-vm-replacement)
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

An update replaces the root when the image or the
[configuration identity](#configuration-identity) changed, and
otherwise performs health checks only. `--rebuild` reconstructs even when both
are unchanged, for repair. Add `--image IMAGE_ID` to rebuild from an
already qualified local image without fetching release metadata. The selected
image must still pass receipt and current recipe checks. Replacements keep the same data disk and
filesystem UUID and allocate only a new OS root. An accepted replacement then
runs the checked retirement, which keeps only the current accepted root. A
failed retirement does not fail the update; the result has
`retirement.state: deferred` and the `--retire-older` command to repeat. It
never deletes the persistent data disk. An old
OS root is not a snapshot of data; recovery after candidate boot does not select
the previous root automatically. Interrupted replacement and reboot verification
must resume through the command reported in the private result.

Use the existing daily update job and the
[nightly coordinator's qualified role selection](platform-deploy.md#routine-controller-replacement);
do not enable `--services`. The coordinator skips declared-stopped guests.
Nightly execution cannot discard old root-resident data or force the rebuild
of an unchanged root. Like `kk update`, it replaces a root whose configuration
identity changed.

## Controller and runner lifecycle migration

Use the [common state and authority contract](architecture.md#routine-service-vm-replacement).
Run these commands on the active controller as `smith`. Finish legacy controller
replacement and its reboot check first. Keep active-controller authority and
credential handoff separate. A target must already have its protected
infrastructure assignment; absence does not authorize disk adoption.

```sh
ansible/bin/platform-update update --box BOX --role ops --migrate-root-data --dry-run-plan
ansible/bin/platform-update update --box BOX --role ops --migrate-root-data --image BUILD_ID
ansible/bin/platform-update update --box BOX --role air --migrate-root-data --image BUILD_ID
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
After reboot verification, controllers and runners keep only the current accepted
root. An old root is not a data backup.

After migration, ordinary `update --role ops` and `update --role air` use the
same transaction as other VMs. `--rebuild` forces a root replacement even when the
image and the configuration are unchanged. Nightly execution never grants first-migration or maintenance consent.
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

Requests from the MacBook use `kk update BOX-ROLE`; see
[MacBook commands](../klokast-dev/README.md#runner-and-maintenance-commands).
That command starts `ansible/bin/vm-image-update start --box BOX --role ROLE`
on the active controller. The job waits for the shared update lock, then runs
this same update. With the lock held, a new job fast-forwards the controller
checkout to upstream `main`. If the checkout moved while the job waited, the
worker restarts on the new source and checks again; the job keeps its
operation ID and its twelve-hour bound. `vm-image-update cancel --box BOX
--role ROLE` requests the stop of a job before it changes the VM. A waiting job
stops at once. A running job ends its current stage, for example the image
build and its tests, and stops at its last check before it freezes the
replacement inputs. That check and the cancel use one lock in the job
directory. When the check passes, it writes `target-changing.json` there, and
each later cancel refuses. A cancelled job leaves no VM transaction; a
completed image build stays available for a later request. Read the job with
`vm-image-update status --box BOX --role ROLE`; it shows `cancel_requested`
until the job stops.
`vm-image-update list` shows, for each target, the latest job when it is not
complete.
Its private record and log are in
`~/private/klokast/air-maintenance/OPERATION_ID/`. `vm-image-update start`
[selects the job](../klokast-dev/README.md#runner-and-maintenance-commands)
itself, and a restarted job resumes its recorded VM transaction. It does not
accept `--operation`. The job cannot grant `--migrate-root-data`,
`--discard-root-data`, `--rebuild` or image selection; use the command above
for those.

The common update command supports `bak`, `dmz`, `iot`, `router`, the standby
`ops`, declared `air` and declared `vpn-egress` guests. It requests a qualified image from the box's own controller,
or checks the selected existing image. When the installed image and
[configuration identity](#configuration-identity) both match, the
update performs health checks only. When only the configuration changed, it
replaces the root with the same image. `--dry-run-plan` reports the installed
and the declared configuration identity.
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
replaces the root even when the image and the configuration are unchanged. It cannot accompany
`--resume`. `--retire-older` selects checked generation cleanup and is mutually
exclusive with rebuild, image selection and resume. Add `--dry-run-plan` to
inspect its deletion plan. Repeat cleanup to resume an interrupted deletion.
Cleanup of each role also removes checked abandoned candidate roots after
source recovery and acceptance of the current generation. It keeps their
audit records and never selects a data volume. A data volume that an
abandoned candidate allocated stays; examine and remove it separately.

A replacement names each volume that it creates
`vmupd_ROLE_OPERATION_root` or `vmupd_ROLE_OPERATION_data`. A retained data
volume keeps the name from the operation that created it. Volumes from before
this rule have no role in their name (`vmupd_OPERATION_root`) and keep that
name; the checked retirement accepts the two forms.

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
the old guest, inspect the recovery record before starting again. A
[`kk update` job](../klokast-dev/README.md#runner-and-maintenance-commands)
that restarts on such an operation does this itself: when the boot assignment
names its operation with stage `recovered`, the job abandons it as below, then
starts a new attempt with new idle and source checks. After the new
guest starts, recovery keeps the new disk and boot assignment. A failed health
or reboot check stays incomplete; the updater does not automatically roll back.

For a failed attempt that has never started the new VM, inspect its private
result and use `platform-update update --box BOX --role ROLE --resume ID
--abandon-unbooted`. This verifies or recovers the old running generation and
releases the unfinished-operation block. It keeps all candidate disks, images
and audit records. It refuses once normal boot of the new generation was
attempted. A fresh update can then use corrected, qualified inputs.
If the restored controller or runner already used retained storage, omit
`--migrate-root-data` on that fresh update. The protected previous release and
live storage checks identify it. A failed first migration still requires the
explicit migration option. A defect in a frozen pre-boot reader requires the
bounded repair described in the [controller model](architecture.md#controller);
do not edit its snapshot or request.

## Repair a read-only router verifier in development

If a booted router has passed its frozen identity, storage, configuration and
reboot checks, but a defect in the frozen read-only network verifier blocks
acceptance, preserve the running generation. Do not roll back or edit its
snapshot. Publish the corrected verifier, then run on the active development
controller:

```sh
ansible/bin/platform-update repair-router-verification --box BOX --operation ID
```

This command requires the unchanged Instance commit and exactly one pending,
booted router with completed reboot evidence. It verifies the frozen snapshot
and uses its original probe and native helper implementation. Only the
read-only network Ansible verifier comes from the current clean, committed
public checkout. The frozen probe checks identity, image and exact configuration
again after that verifier. Existing frozen native actions then record the
checks and complete acceptance. It cannot allocate disks, start a guest, change
configuration, or change the requested image or policy. The private snapshot's
`verification-repair.log` records the verifier commit and result. A failure after
native checks were recorded can use the original normal resume command.

The command refuses production deployments. Production recovery requires its
admitted implementation. The authority and frozen-input rules are owned by the
[controller model](architecture.md#controller).

AIR replacements with local workspace checkpoints verify tmux restoration before
accepting the new generation. An interrupted restore keeps the VM operation
pending and the [runner closed](airunner.md#runner-closure). Resume that exact
VM operation. If the closure has no checkpoint, the runner cannot recreate it. Completion then
releases the runner and records `workspace: not restored: checkpoint missing`
in the job result, the job reason and the log. Native histories stay on the
retained disk; reopen them with `codex resume` or `claude --resume`. Scheduled AIR
work skips a closed runner and cannot retire its recovery generation.
The MacBook and runner update clients use the same controller transaction; see
[runner commands](../klokast-ops/runbooks/07-codex-on-ops.md#air-image-updates).
