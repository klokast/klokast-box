# Platform VM inspection and tests

Automatic operating-system updates for `router`, `bak`, `dmz`, and `iot` VMs
are retired. There is no supported scheduled preparation, live replacement,
or update-adoption command for these roles. The Instance no longer accepts
`vm-updates`. Remove that obsolete field before validating an older Instance.
This does not change application updates or production Platform release admission.

Run the retained commands as `smith` on the active `<box>-ops` controller.
Inspection writes evidence under `/var/lib/klokast/updates/discovery`; downloaded
inputs and template artifacts use `/var/cache/klokast/updates`. These historical
path names do not imply that automatic updates are enabled.

## Inspection

- `ansible/bin/platform-update scan --json` collects VM facts and authenticated
  upstream package metadata. `status --json` reads the last report.
- `ansible/bin/platform-update verify --json` checks retained shared-VM
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
remain under `platform-router-update`. Use `--help` for their explicit inputs.
They create disposable disks or networkless Xen guests; they are not read-only.
Keep their matching cleanup commands. The explicit K001 cold first-install test
also remains: it interrupts the real router and requires an approved outage.
Do not use it as a routine health check.

## Storage assessment

The discovery report includes retained-data and storage classification.
`ansible/bin/platform-update retention --json` compares declared retention
with discovery. Neither command deletes data or treats unknown storage as empty.

## Provisioning and retained recovery

First router installation still runs through `provision-box` phases 30 and 31.
It uses the retained first-install functions in `platform-router-update`.
The checked-in first-install and diagnostic defaults select tested stable Alpine
branches at least 21 days old and require selection evidence no older than 30 hours.
The historical selection record format remains readable; its schedule flags are
always disabled. It no longer reads an Instance update schedule.

Dom0 record readers and boot recovery remain where accepted assignments,
first installation, tests, or unfinished historical operations need them.
Installed command dispatchers cannot start new replacement transactions.
Recovery records, previous disks, and backups are not deleted by retirement.
Remove a recovery hook only after its dependent boot assignments have been
reconciled through a separate operation.

`74-platform-update-discovery.yml` prepares inspection directories and removes
all six retired VM/router cron entries. Controller convergence runs the same
tasks. `74-platform-update-retire-legacy-cron.yml` can remove just those entries.

`74-platform-update-retirement.yml` also checks for pending operations and
installs recovery dispatchers without new replacement commands. Run it under
the existing controller installation lock. It preserves all accepted records,
boot hooks, and installed versioned router engines. A legacy controller can run
this retirement playbook without adopting the newer development-controller model.
Upgrade that model separately before using newer controller commands.
