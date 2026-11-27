# Native Alpine runner operations

Run Platform commands as `smith` on the active `<box>-ops` controller, from
`~/src/klokast/klokast-box`. The runners do not depend on which controller is
active. See [architecture](../../doc/architecture.md#box-air) for the
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

Provisioning resolves current stable Alpine, packages, Codex and Claude Code. It qualifies
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

On the MacBook, use `kk air` to open or reattach the `main` tmux workspace of
a runner through Mosh. Each runner keeps its own workspace and its own Codex and
Claude Code discussions. No command moves a discussion to another runner.
Select the Instance with `KLOKAST_INSTANCE` or `kk --instance PATH air`. Use
`kk air --status` to inspect availability, closure, attached clients and
running work. `kk air --prefer k002,k001`
saves this device's order and clears its previous selection. `kk air --box BOX`
requires that declared native runner and never falls back. The default order
is `k002`, then `k001`, then other declared native runners by name.

The launcher reconnects to the remembered workspace when it is available.
It skips a runner that a replacement or dom0 update has
[closed](../../doc/airunner.md#runner-closure), and tells which operation
closed it. That runner also refuses the connection itself. Preferences and
selection stay in `~/.config/klokast/air.json` (or `$XDG_CONFIG_HOME/klokast/air.json`), scoped by
the Instance Tailnet. Connections do not copy discussions or authentication.
Use the MacBook's existing OpenSSH configuration and Tailnet access for `agent`.
Before the first connection to a new runner, run `ssh agent@BOX-air true` on
the MacBook and complete OpenSSH's first-connection setup. The launcher's
availability probes are noninteractive and cannot answer that prompt.

If no runner is usable, the error names each runner and its failed check.
Connection failures include the SSH exit status and error output. Probe
timeouts, remote helper failures and invalid status responses have separate
messages. Missing runner prerequisites identify the repository or command.
A closed runner names the operation that closed it. `kk air --status` also
shows these operation IDs and the missing prerequisites.

The [log retention policy](../../ansible/ansible.md#log-retention) deletes Codex
discussions after 14 days without change.

Bootstrap a new runner's public repository and independent Git key on the
active controller before its first use:

```sh
ANSIBLE_CONFIG=ansible/ansible.cfg ansible-playbook -vv -i ansible/execution-inventory/hosts \
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
The image includes `python3`, `py3-yaml`, `py3-pygithub` and the Alpine
`ansible` package. Run repository tests on the runner, for example
`python3 -m unittest discover -s ansible/tests`. Check a changed playbook with
`ANSIBLE_CONFIG=ansible/ansible.cfg ansible-playbook --syntax-check -i localhost, ansible/playbooks/NAME.yml`.
Do not run unreviewed test code on a controller as `smith`.
Use `Ctrl-b c` for a new window, `Ctrl-b n` for the next window and `Ctrl-b d`
to detach. Detaching closes the Mosh workspace command and returns to the
MacBook shell. Run `kk air` again to reattach. Connections preserve existing
windows, panes and clients.
The `air-alpine-v1` image contains the helper, the shell and tmux settings, and
the controller resolver that `air-update` uses: `/etc/bash/klokast-air.sh` and
`/etc/tmux.conf` are root-owned image files. The runner home keeps no startup
file; see [retained runner state](../../doc/airunner.md#retained-home). To change
one of these files, change its source and replace the runner root with
`kk update BOX-air`; see [runner configuration](../../doc/airunner.md#runner-configuration).

An interactive `tailscale ssh agent@BOX-air` or Mosh login also opens the same
`main` workspace. The shell defaults use the shared helper and the same tmux
settings on each runner. A closed runner refuses the workspace and names the
operation that closed it. A failed automatic attach leaves an interactive
recovery shell. Use `kk air --box BOX` to open one specific runner. The AIR MOTD is "Did you ever take that test yourself, Deckard?"

To open a shell without tmux, for example to read login errors, give the
command `bash --noprofile --norc`. It does not read `/etc/bash/klokast-air.sh`,
which opens the workspace:

```sh
mosh agent@BOX-air -- bash --noprofile --norc
```

Codex and Claude Code run directly as `agent`; no AI service daemon
starts at boot. New user authentication belongs in this runner account. Use
`codex login --device-auth` when no authentication was migrated, then
`codex resume --all` to select a session. For Claude Code, run `claude` and
complete its sign-in once per runner, then use `claude --resume` to select a
session. The image installs `/usr/local/bin/claude`; managed settings in
`/etc/claude-code/managed-settings.json` disable its self-updater, select the
image `ripgrep` and select the classic renderer. The classic renderer writes
answers to tmux history, so `Ctrl-b [` scrolls Claude Code and Codex the same way. The `neo` account provides
maintenance through Tailscale. The temporary OpenSSH bootstrap path
is removed after enrollment.

Show the runner closure and activity from the active controller:

```sh
ansible/bin/airunner maintenance status --box BOX
```

Only a runner replacement or a dom0 update closes a runner, at its final
activity check. The closure does not expire: resume the operation that it
names. The next runner replacement or dom0 update removes a closure whose
operation its journal no longer has pending. See the
[runner closure](../../doc/airunner.md#runner-closure).

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

## AIR image updates

Set up each controller's [public metadata token](../../klokast-dev/runbooks/27-github-metadata.md)
before image preparation or the final release freshness check.

The MacBook command is the common [`kk update BOX-ROLE`](../../klokast-dev/README.md#runner-and-maintenance-commands)
request. This section describes the runner-specific checks.
From the MacBook, use `kk update k001-air`. Use the existing Instance selection
with `KLOKAST_INSTANCE` or `kk --instance PATH update k001-air`. Check progress
with `kk status k001-air`. Run `kk update k001-air` again to restart a failed
or interrupted job, also when SSH delivery is uncertain: the controller selects
the existing job and does not create a second one.
The controller job continues when the MacBook sleeps or disconnects. The command
lists the stages, then prints one line with the UTC time when the stage changes.
Each UTC time is the time that the controller recorded for the check or the
stage, not the time of the command. A job record that has no recorded time
shows `--:--:-- UTC`. A time from a different UTC day includes the date.
It ends when the job completes or fails. Ctrl-C stops only the display. Use
`--detach` to return after the request; that response does not confirm
completion. Status shows each passed check, for example
`Discussions on k001-air are not active: OK` and
`No other update work is ongoing: OK`. While a check waits, status shows the
wait reason and the number of busy processes that block the update. You can
stay attached: after the final check, the update detaches each tmux client
and prints when the workspace returns. A runner image older than this
behavior waits for the clients to detach. If a Claude Code or Codex discussion is busy or waits for an
answer, the reason names its tmux window and its activity, for example
`Claude Code discussion is active or needs attention: PID 26421 in tmux window main:2 "claude" is waiting`
or
`Codex discussion is active or needs attention: tmux window main:6 "codex" stopped on a system error`.
A window in a detached tmux session also gives the command that shows it, for
example `tmux window 1:2 "claude" of detached session "1" (tmux attach -t 1)`.
A command that an agent started and that still runs, for example a background
Bash command of Claude Code, is named with its PID and the agent window:
`agent work runs outside a turn: PID 4120 bash runs for Claude Code PID 26421 in tmux window main:2 "claude"`.
Answer or close that discussion, or wait for that command or stop it. Idle
discussions, shells, editors and other programs do not block the update; the
replacement ends them. The
[runner activity rules](../../doc/airunner.md#runner-activity) list what
blocks.
If the runner stays attached or busy for 15 minutes in all, the job stops
before it changes the VM. It ends as `Cancelled: stopped after 15 minutes:`
with the last wait reason. The next `kk update` starts a new job. An earlier
VM attempt that the job abandons opens the runner again at once.
If work starts after the check passed and before the image step, the job
waits again within the same 15 minutes. The last check waits for the shared Platform update lock. During
the update, status shows the current stage, for example
`image build: build the candidate in a networkless Xen guest on k001`,
`booting the replacement k001-air` or
`running health checks on the replacement k001-air`. The job publishes these
stages through the `KLOKAST_PROGRESS` file in its operation directory. The
image build reports only its own stages. It does not report download speed or
per-test progress. A failed status returns a nonzero exit code.

Before a new worker starts, the active controller checks its public checkout
and fast-forwards clean `main` to upstream. Local changes, unpublished commits,
divergent history, and fetch failures stop the request with a specific error.
Image preparation also updates the target box controller to the selected public
revision. Joining a running job leaves its source in place; an unfinished VM
transaction continues from its saved source snapshot.

From a runner, use `air-update`, `air-update status`, or
`air-update resume OPERATION_ID`. This updates the full image, including current
stable Codex and Claude Code. It does not update packages in the running VM.
Active turns, approval waits and agent work outside a turn prevent shutdown; the
update detaches the tmux clients itself. Recent use adds no delay. Manual jobs wait for
up to twelve hours; failures keep the operation and its private log for resume.

The existing 03:00 UTC coordinator skips busy runners until the next night.
An idle runner can be updated without a healthy spare. That runner is briefly
unavailable during replacement; other boxes and their routers are not update
prerequisites. The target must still pass its own health and recovery checks.
A new schedule selects `air` by default. An installed schedule keeps its saved
[role selection](../../doc/platform-deploy.md#routine-controller-replacement);
add `air` there with the existing schedule playbook, preserving all other
selected roles. Do not enable a runner until same-machine checkpoint,
replacement, reboot and restore have passed on that runner. Interrupted restoration keeps the runner closed.
Use SSH recovery access and the reported VM resume command. Do not delete the
runner closure or start another discussion copy to bypass recovery.

Session and window names, indices, layouts, directories and active selections
are restored. Each idle Codex pane restarts with `codex resume SESSION_ID`, and
each idle Claude Code pane with `claude --resume SESSION_ID`. A Claude Code pane
that shows the agent view resumes the conversation that moved to the
background. A pane with another program restores as a shell. Idle Claude Code
background sessions and clients outside tmux are not restarted: `claude agents`
lists the stopped background sessions, and `claude --resume` opens them.
Shell-local variables and running process memory are not restored.
