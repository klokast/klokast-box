# Platform VM inspection and tests

Run image preparation as `smith` on the target box's own `<box>-ops`. Run the
inspection and deployment commands on the active controller. Inspection and
template tests are explicit operations. They do not replace running VMs.
Inspection writes evidence under `/var/lib/klokast/updates/discovery`.
Downloaded inputs and template artifacts use `/var/cache/klokast/updates`.

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
lock and dom0 build lock reject competing image operations.

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
then reuses that image. Klokast commits, source changes, package checksums, and
whole package-index changes do not trigger a rebuild. The signed package
downloads are still verified on every request. A version change or a missing
or damaged image starts a new build in a disposable, networkless Xen guest.
For runner images, a Codex version or artifact checksum change also requires
a new build. Reuse requires the matching native tool and sandbox test evidence.
Controller and runner profiles also bind their image construction and
finalization code. A change to that code requires new qualification.
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
leave a partly removed obsolete image; it is reported as unknown on retry.

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
running VM, and there is no automatic build schedule.

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

The existing shared and Alpine app-VM provisioning paths still use the older
`lv_podman_template`. These changes retain that LV and its callers. All new
infrastructure profiles use the same isolated image builder; templates contain
no deployment identity, secrets, or application data.

Router provisioning qualifies its template in a networkless Xen guest.
Standalone router state-copy, compatibility, candidate-preparation, and cold
test workflows are retired. Their old artifacts remain retained data; removing
the commands does not authorize deletion of their disks, backups, or identities.

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
