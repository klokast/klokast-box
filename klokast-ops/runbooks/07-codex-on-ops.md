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

The final copy must run as a detached controller job because stopping the
source container also stops terminals within it. Give the operator the new
Tailscale hostname and reconnect command before cutover. Keep the old runner
available after a pre-cutover failure. After destination writes begin, retain
both copies and reconcile them before rollback. Never replace newer sessions
with an older copy.

Retirement removes startup integration and the stale Tailscale identity only
after verification. Retain the stopped source home and service definition as
offline rollback material. A test VM can be deleted only after its declaration
is removed and its data is verified as test-only. Record progress and recovery
instructions in the [private controller journal](../../doc/operations-journal.md).
