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
- `ansible/bin/platform-router-update inspect --box BOX` collects router facts.
  `check-legacy --box BOX` and `check-template --box BOX` compare the recorded
  router with upstream releases. Results are diagnostic and cannot launch an update.

## Isolated application component test

`ansible/bin/platform-update prepare --box BOX --branch v3.24` builds and tests
an isolated shared-VM template. `--inputs-only` stops before the Xen test.
`--test-app static-site-web` tests the declared image with synthetic data.
These commands do not replace a running VM.

Router template, state-copy, candidate-preparation, and compatibility tests
are under `platform-router-update`. Use `--help` for their explicit inputs.
They create disposable disks or networkless Xen guests; they are not read-only.
Keep their matching cleanup commands. The explicit cold first-install test
interrupts the selected router: it requires an approved outage.
Do not use it as a routine health check.

## Storage assessment

The discovery report includes retained-data and storage classification.
`ansible/bin/platform-update retention --json` compares declared retention
with discovery. Neither command deletes data or treats unknown storage as empty.

## Provisioning and boot recovery

First router installation runs through `provision-box` phases 30 and 31.
It uses the first-install functions in `platform-router-update`.
The checked-in first-install and diagnostic defaults select tested stable Alpine
branches at least 21 days old and require selection evidence no older than 30 hours.

Dom0 record readers and boot recovery preserve accepted assignments and pending
operations. Keep required records, previous disks, backups, and recovery hooks.
Remove a recovery hook only after its dependent boot assignments have been
reconciled through a separate operation.

`74-platform-update-discovery.yml` prepares private inspection directories.
Controller convergence runs the same setup tasks. Controller installation
checks pending operations and preserves accepted records and boot hooks; see
[controller operations](platform-syscalls.md).
