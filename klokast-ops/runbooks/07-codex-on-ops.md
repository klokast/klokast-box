# Native Alpine runner operations

Run Platform commands as `smith` on the active `<box>-ops` controller, from
`~/src/klokast/klokast-box`. Controller placement and runner placement are
independent. See [architecture](../../doc/architecture.md#box-air) for the
account, network and storage boundaries.

Declare `<box>-air` in the Instance `airunners` list. Validate, commit
and push the Instance change. Keep the existing runner declared during a
migration. Then run:

```sh
ansible/bin/airunner provision --box BOX --dry-run-plan
ansible/bin/airunner provision --box BOX
ansible/bin/airunner verify --box BOX
ansible/bin/platform-check --box BOX --target air
```

Provisioning resolves current stable Alpine, packages and Codex. It qualifies
the image with the existing isolated builder, then clones and finalizes the
VM. Repeated provisioning preserves an assigned root disk. It refuses an
unknown existing disk. It does not upgrade a running runner in place.

If isolated finalization failed before the guest's first normal boot, correct
and qualify the image first. Use `airunner provision --box BOX --archive-failed`
to retain the failed disk and retry with the current qualified image. Use
`--dry-run-plan` to preview this action. The command requires a recorded failed
finalization, matching disk identity, no running guest and no installed boot
configuration. It renames the failed LV and retains its records under
`/mnt/dom0_data/klokast-infrastructure/failed-air-UUID`. It never archives a
ready guest or deletes the retained disk. Repeat the same command to resume an
interrupted archive operation.

On the MacBook, use `kk air` to open or reattach the shared `main` tmux
workspace through Mosh. Select the Instance with `KLOKAST_INSTANCE` or
`kk --instance PATH air`. Use `kk air --status` to inspect availability,
maintenance, attached clients and running work. `kk air --prefer k002,k001`
saves this device's order and clears its previous selection. `kk air --box BOX`
requires that declared native runner and never falls back. The default order
is `k002`, then `k001`, then other declared native runners by name.

The launcher reconnects to the remembered workspace when it is available.
It skips a maintenance reservation for a new connection, but permits an
existing workspace connection until replacement begins. A pending replacement
blocks all launcher connections. Preferences and selection stay in
`~/.config/klokast/air.json` (or `$XDG_CONFIG_HOME/klokast/air.json`), scoped by
the Instance Tailnet. Connections do not copy discussions or authentication.
Use the MacBook's existing OpenSSH configuration and Tailnet access for `agent`.
Before the first connection to a new runner, run `ssh agent@BOX-air true` on
the MacBook and complete OpenSSH's first-connection setup. The launcher's
availability probes are noninteractive and cannot answer that prompt.

Move a selected discussion from the MacBook:

```sh
kk platform handoff --session SESSION_ID --from k002 --to k001
kk platform handoff --resume OPERATION_ID
```

Finish the selected discussion's turn and background work, then close that
Codex client. The bounded helper waits up to five minutes for the native writer
lock. Codex 0.163.0 cannot archive conditionally at an idle boundary: an archive
request can stop a newly started turn. The helper therefore refuses to force
an open writer to stop. Automatic live-client closure is not supported.

The destination must have its own sign-in to the same ChatGPT account, the same qualified storage
format, and the discussion's repository directory. The adapter checks the
0.163.0 table definitions and migration checksums. It supports legacy rollouts,
paginated SQLite history, selected child threads, goals, queued input, shared
metadata and local file/image references under `/home/agent`. It rejects
unknown schemas, incomplete records, unsafe paths and missing files. Assets
outside that home need a separate supported adapter; they are not omitted.
The native attachment table permits arbitrary client-defined JSON. Only its
tested `localImage` and `local_image` records with a single `path` field are
supported. Other persisted attachment formats stop export for adapter review.
The transfer limit is 256 MiB of files and 350 MiB of encoded discussion data.

The source retains the export under
`~/.local/state/klokast/handoffs/OPERATION_ID/`. It removes only the selected
records and rollout from normal Codex discovery after saving and verifying
that copy. The destination imports with native writer locks held, checks the
result, then starts `codex resume SESSION_ID` in a new window of `main`.
The MacBook records progress under its Klokast configuration and relays the
payload in memory. It does not retain discussion archives. Retries never
replace a changed destination row or asset. A return transfer uses a new
operation and requires the retained earlier source copy to remain unchanged.
If a start is uncertain, inspect its named tmux window and both move receipts.
Do not delete receipts or edit history to force a retry.

Bootstrap a new runner's public repository and independent Git key on the
active controller before handoff:

```sh
ansible-playbook -vv -i ansible/execution-inventory/hosts \
  ansible/playbooks/69-air-user-bootstrap.yml --limit BOX-air
```

Register the displayed public key under `klokast/klokast-box` repository
Settings > Deploy keys, with write access. Keep Instance access on the
controllers. The role preserves existing
repositories, author settings, SSH configuration and Codex settings. Repository
skills remain versioned in the checkout. OpenAI sign-in is a separate operator
step on each runner; no authentication file is copied.

Run `kk doctor --install` on the MacBook to install Mosh if it is missing.
Inside tmux, run `codex`, `codex resume --all`, or `codex resume SESSION_ID`.
Use `Ctrl-b c` for a new window, `Ctrl-b n` for the next window and `Ctrl-b d`
to detach. Detaching closes the Mosh workspace command and returns to the
MacBook shell. Run `kk air` again to reattach. Connections preserve existing
windows, panes and clients.
The `air-workspace` Ansible role delivers the helper and initial shell/tmux
settings. It preserves existing user settings. Converge existing runners with
`ansible-playbook -vv -i ansible/execution-inventory/hosts
ansible/playbooks/69-air-workspace.yml --limit BOX-air` on the active controller.

An interactive `tailscale ssh agent@BOX-air` or Mosh login also opens the same
`main` workspace. The shell defaults use the shared helper and the same tmux
settings on each runner. Codex runs directly as `agent`; no AI service daemon
starts at boot. New user authentication belongs in this runner account. Use
`codex login --device-auth` when no authentication was migrated, then
`codex resume --all` to select a session. The `neo` account provides
maintenance through Tailscale. The temporary OpenSSH bootstrap path
is removed after enrollment.

Reserve planned maintenance from the active controller:

```sh
ansible/bin/airunner maintenance set --box BOX --operation YOUR-ID
ansible/bin/airunner maintenance status --box BOX
ansible/bin/airunner maintenance clear --box BOX --operation YOUR-ID
```

The default reservation is 1800 seconds; `--ttl SECONDS` changes its duration.
A reservation does not stop work. Live clients, detached work, unknown processes
and the recent-use hold block replacement even with `--maintenance`. A pending
replacement does not expire. Resume its existing `platform-update` transaction;
normal `clear` cannot release it. See the [runner lifecycle](../../doc/architecture.md#box-air).

Coordinate a cycle from the MacBook:

```sh
kk platform maintain --box k002 --roles air,ops --session SESSION_ID --return
kk platform maintain --resume OPERATION_ID
```

The command reserves the source, transfers the selected discussion, and calls
the existing checked VM replacement workflow on the active controller. The
first replacement migrates root data to retained storage. Other windows,
attached clients and recent use can stop the cycle; finish them, detach, then
use the printed resume command. Each controller step has a four-hour bound.
The native VM transaction and controller-private input snapshot keep its
resume ID before replacement starts. The MacBook keeps only coordination
progress. Private execution logs stay in the controller operations journal.

When the target is the active controller, the cycle uses planned switching,
updates the target from its active peer, then restores the original controller.
Both controllers must already have independent credentials and recovery
access. `--return` uses a fresh reverse transfer and the same native-writer
boundary check. An uncertain start or conflicting history stops the cycle.
The cycle does not enable nightly AIR updates; enable them only after live
storage, reboot and discussion-preservation qualification.

For a migration, copy runner-owned data through the active controller. Keep
controller private state and provider credentials on the controller. A pilot
uses synthetic repositories and SQLite session data without working runner
credentials. Pre-copy can run while the source is available. Stop source
writers before the final copy. Check SQLite consistency, numeric ownership,
uncommitted files, authentication and Git access before releasing the
destination for work. Copy any runner-visible archives into local read-only
storage on the destination; do not retain a mount of controller private state.

Use the same command for synthetic qualification and working migration:

```sh
ansible/bin/airunner migrate --box PILOT --synthetic --phase precopy --simulate-interruption
ansible/bin/airunner migrate --box PILOT --synthetic --phase precopy
ansible/bin/airunner migrate --box PILOT --synthetic --phase cutover
ansible/bin/airunner migrate --box BOX --phase precopy
ansible/bin/airunner migrate --box BOX --phase cutover --dry-run-plan
ansible/bin/airunner migrate --box BOX --phase cutover
ansible/bin/airunner migrate --box BOX --phase status
```

The first synthetic command must fail. The next command must recover the
copy and preserve the synthetic files and SQLite session. Synthetic migration
does not read working runner credentials. Working migration preserves numeric
ownership and uses a final checksum comparison. It excludes disposable caches
and the old Codex package; the destination uses the image's native tools.
Reinstall tools that were installed only in a Debian cache when needed.

The final copy must run as a detached controller job because stopping the
source container also stops terminals within it. Give the operator the new
Tailscale hostname and reconnect command before cutover. Keep the old runner
available after a pre-cutover failure. After destination writes begin, retain
both copies and reconcile them before rollback. Never replace newer sessions
with an older copy.

The working cutover command starts this detached job and reports its protected
log path. Each transfer has a 30-minute limit. The destination account is
closed to login during copying. Cutover disables source startup at controller
boot before it stops the container. A failed final copy restarts the source
and restores its previous boot startup setting. After destination use starts,
source boot startup stays disabled until retirement or explicit reconciliation.
The `destination-enabled` state marks the start of destination use; later
failures require reconciliation and never restart the old source automatically.
Run `migrate --phase verify` after fixing a verification failure at this boundary.
Do not edit a migration record to permit another copy.

Retirement removes startup integration and the stale Tailscale identity only
after verification. Retain the stopped source home and service definition as
offline rollback material. A test VM can be deleted only after its declaration
is removed and its data is verified as test-only. Record progress and recovery
instructions in the [private controller journal](../../doc/operations-journal.md).

After cutover verification, remove the legacy name from Instance desired state,
validate, commit and push. Then run:

```sh
ansible/bin/airunner retire-legacy --box BOX --dry-run-plan
ansible/bin/airunner retire-legacy --box BOX
```

The source home stays at `/home/agent` on the controller. The stopped service
definition stays under `/var/lib/klokast/airunner-migration/BOX/legacy-service`.
Controller packages remain installed. Runner-only firewall and startup rules
are removed. Restoring this source requires reconciliation with destination
writes and a fresh Tailscale enrollment.

Remove the pilot declaration from the Instance and push that change before
retiring the pilot:

```sh
ansible/bin/airunner retire --box PILOT --test-only --dry-run-plan
ansible/bin/airunner retire --box PILOT --test-only
```

Without `--test-only`, retirement stops the VM and preserves its root LV.
The test-only option checks the synthetic fixture, absence of working
credentials and sessions, VM UUID and LV UUID before it removes the test disk.
It also removes retained failed clones that have matching failed-finalization
records and LV identities and never reached normal boot. It keeps their records.
An unknown or changed resource is preserved for inspection.
