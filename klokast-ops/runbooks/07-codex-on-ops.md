# Native Alpine runner operations

Run Platform commands as `smith` on the active `<box>-ops` controller, from
`~/src/klokast/klokast-box`. Controller placement and runner placement are
independent. See [architecture](../../doc/architecture.md#box-air) for the
account, network and storage boundaries.

Declare `<box>-air` in the ordered Instance `airunners` list. Validate, commit
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

Use `tailscale ssh agent@BOX-air` to open the runner. Codex runs directly as
`agent`; no AI service daemon starts at boot. New user authentication belongs
in this runner account. Use `codex login --device-auth` when no authentication
was migrated, then `codex resume --all` to select a session. The `neo` account
provides maintenance through Tailscale. The temporary OpenSSH bootstrap path
is removed after enrollment.

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
closed to login during copying. A failed final copy restarts the source.
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
An unknown or changed resource is preserved for inspection.
