# Platform VM inspection and tests

Run commands as `smith` on the active `<box>-ops` controller. Inspection and
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
golden image. Each invocation reads official Alpine release metadata and fresh,
signature-verified package
indexes. It selects the newest stable branch supported by both required
repositories, with no release delay. The command has no `--branch` option.
It fails if current upstream inputs cannot be verified.

The command compares the selected Alpine branch and complete installed package
name/version set, including dependencies, with the latest qualified image for
the same box, profile, and architecture. If versions match, it verifies the
saved candidate metadata and the root, kernel, and initramfs bytes on dom0,
then reuses that image. Klokast commits, source changes, package checksums, and
whole package-index changes do not trigger a rebuild. The signed package
downloads are still verified on every request. A version change or a missing
or damaged image starts a new build in a disposable, networkless Xen guest.
Unsafe paths, unavailable storage, and uncertain verification fail the request.

The package profile contains package names. The built image's `/etc/apk/world`
also contains names without exact version constraints. Its input manifest
records the exact installed versions and checksums. The build verifies that
the root image, kernel, and modules match, then tests normal OpenRC boot,
rootless Podman, personalization, and synthetic data recovery.

After a new build passes controller validation, the command keeps that exact
successful image and removes checked, unused older images for the same box, profile, and
architecture. It preserves referenced images, other profiles, incomplete or
unknown artifacts, and compact build and cleanup records. Cleanup runs under
the existing locks. Protected VM transaction records or a standing update
policy still prevent setup cleanup. The command reports deferred cleanup and
returns a nonzero status in that case; the successful new image remains.
A failed build never retires the previous image. An interrupted cleanup can
leave a partly removed obsolete image; it is reported as unknown on retry.

The JSON result reports `candidate-built` or `candidate-reused`, the build ID,
Alpine branch, profile, architecture, package manifest, dom0 artifact directory,
test results, and cleanup status.
Controller evidence is in `discovery/builds/BUILD_ID/`, including
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

This command requires the development controller tools, including
`platform-source` and `platform-inventory`. Controller migration follows
[controller operations](platform-syscalls.md).

The existing shared, ops, and Alpine app-VM provisioning paths still use the
older `lv_podman_template`. This build step does not remove that LV or change
those callers. Migration to the newer builder is separate work. The target
is one build mechanism with role-specific profiles; templates contain no
deployment identity, secrets, or application data.

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
