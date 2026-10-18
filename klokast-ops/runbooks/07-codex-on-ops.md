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
blocks automatic selection; explicit access prints a warning. Preferences and selection stay in
`~/.config/klokast/air.json` (or `$XDG_CONFIG_HOME/klokast/air.json`), scoped by
the Instance Tailnet. Connections do not copy discussions or authentication.
Use the MacBook's existing OpenSSH configuration and Tailnet access for `agent`.
Before the first connection to a new runner, run `ssh agent@BOX-air true` on
the MacBook and complete OpenSSH's first-connection setup. The launcher's
availability probes are noninteractive and cannot answer that prompt.

If automatic selection fails, the error names the current runner and the failed
check. Connection failures include the SSH exit status and error output. Probe
timeouts, remote helper failures and invalid status responses have separate
messages. Missing runner prerequisites identify the repository or command.
A pending replacement reports its operation ID. A missing `main` tmux session
reports an explicit recovery command for the current runner. Automatic selection
does not open a different runner copy. `kk air --status` also shows pending
replacement IDs and missing prerequisites.

For transparent workspace relocation, run on the active controller as `smith`:

```sh
ansible-playbook -vv -i localhost, ansible/playbooks/69-air-workspace-move.yml \
  -e air_workspace_from=k002 -e air_workspace_to=k001
```

The durable controller job waits for open turns, closes the source clients,
transfers only the discussions open in tmux, and restores window names, indices,
pane layout and selection. Source connections remain in their tmux shells.
The user later runs `kk air`
to attach to the destination. Instance's first native runner is the preferred
default; the operational placement receipt selects exactly one current workspace.
Automatic selection follows that placement until a verified reverse move. A move never copies
the entire Codex home or machine identity. Native control uses only the existing
account's local Codex Unix socket, without a new service or enrollment credential.

Inspect or resume the reported operation on the active controller:

```sh
ansible/bin/air-workspace status --operation OPERATION_ID
ansible/bin/air-workspace move --from k002 --to k001 --operation OPERATION_ID
```

To return the workspace, start a new move with the endpoints reversed. Use a
new operation ID; a completed operation remains a record of its original move.

An interrupted move remains unavailable until resumed. Preserve its receipts
and staged tmux sessions. Never create another active workspace to bypass it.
Destination sessions already present are retained with a `-before-OPERATION`
suffix. Unrelated source discussion archives remain on their original box.
Standby preparation can close idle, empty Codex clients that have no persisted
discussion. A destination with an open discussion or background work stops the
move. Failed test panes retain their diagnostics in the private operation.

To replace the source AIR VM after the move, add
`-e air_workspace_maintain_source=true`. The same job releases the source
reservation after verified workspace completion, then calls the checked AIR
maintenance workflow. Other work or later source activity keeps the reservation
under the [runner activity contract](../../doc/architecture.md#box-air). It does not
return the workspace or replace an OPS VM. Resume with the same move operation
and `--maintain-source`. VM transaction IDs and logs remain controller-private.

The older one-discussion relay remains available from the MacBook:

```sh
kk platform handoff --session SESSION_ID --from k002 --to k001
kk platform handoff --resume OPERATION_ID
```

Finish the selected discussion's turn and background work, then close that
Codex client. The bounded helper waits up to five minutes for the native writer
lock. Codex 0.163.0 cannot archive conditionally at an idle boundary: an archive
request can stop a newly started turn. The helper therefore refuses to force
an open writer to stop. This older relay does not close live clients and cannot
move a workspace after singleton placement is established.

The destination must have its own sign-in to the same ChatGPT account, the same qualified storage
format, and the discussion's repository directory. The helper reads the
managed VPN client environment for non-login account checks and supplies it
to the destination tmux window, including when its server is already running.
The adapter checks the
0.163.0 table definitions and migration checksums. It supports legacy rollouts,
paginated SQLite history, selected child threads, goals, queued input, shared
metadata and local file/image references under `/home/agent`.
Paginated fork transfer also includes its referenced ancestor prefixes, bounded
at the native fork offsets. It does not transfer later ancestor activity.
It rejects unknown schemas, incomplete records, unsafe paths and missing files. Assets
outside that home need a separate supported adapter; they are not omitted.
The native attachment table permits arbitrary client-defined JSON. Only its
tested `localImage` and `local_image` records with a single `path` field are
supported. Other persisted attachment formats stop export for adapter review.
The transfer limit is 256 MiB of files and 350 MiB of encoded discussion data.

The source retains the export under
`~/.local/state/klokast/handoffs/OPERATION_ID/` and keeps the selected records
and rollout in normal Codex discovery. The destination imports with native writer locks held, checks the
result, then starts `codex resume SESSION_ID` in a new window of `main`.
The MacBook records progress under its Klokast configuration and relays the
payload in memory. It does not retain discussion archives. Retries never
replace a changed destination row or asset. A return transfer uses a new
operation and requires the indexed earlier source copy and its retained export
to remain unchanged. The workspace transfer compresses its payload and uses a
bounded upload timeout that accounts for slow SSH links.
If a start is uncertain, inspect its named tmux window and both move receipts.
Do not delete receipts or edit history to force a retry.

Bootstrap a new runner's public repository and independent Git key on the
active controller before handoff:

```sh
ansible-playbook -vv -i ansible/execution-inventory/hosts \
  ansible/playbooks/69-air-user-bootstrap.yml --limit BOX-air
```

Configure the controller's dedicated GitHub App once through the
[runner Git setup](../../klokast-dev/runbooks/26-air-github-app.md). Provisioning
and this bootstrap play register each runner's public key automatically and
verify push access. Keep Instance access on the controllers.
The role preserves existing repositories, author settings, global SSH
configuration, Codex authentication and project trust. It applies shared Codex
execution and TUI preferences. Model and reasoning-effort choices stay local to
each runner because they can change independently. Repository skills remain
versioned in the checkout. OpenAI sign-in is a separate operator step on each
runner; no authentication file is copied.

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
settings on each runner. Maintenance and incomplete moves print a warning on
explicit access. A failed automatic attach leaves an interactive recovery shell.
Use `kk air --box BOX` for explicit access; automatic selection still follows
verified placement. The AIR MOTD is "Did you ever tried to take that test yourself? Deckard?"
Codex runs directly as `agent`; no AI service daemon
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

If an older handoff removed an indexed source discussion before import,
restore it from that runner's verified retained export, as `agent`:

```sh
klokast-codex-handoff recover-source --session SESSION_ID --operation OPERATION_ID
```

The recovery preserves the export and move ledger. It refuses changed copies
and does not start Codex or interrupt another discussion. Resume the existing
controller move after inspecting its operation record; do not start a second move.

The legacy MacBook cycle below applies before singleton workspace placement.
After placement is established, use the controller workspace move above for
AIR maintenance. Controller replacement still requires its own planned switch
and independently prepared peer credentials.

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
