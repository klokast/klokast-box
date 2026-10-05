# Development controller operations

Run these commands as `smith` on the configured active `<box>-ops` controller.
The development tools require the root-owned deployment property
`/etc/klokast/deployment.json` to contain `schema_version: 1` and
`lifecycle: development`. They reject a production or unknown property.
Production release admission is a separate workflow.

- `platform-plan [--observation FILE] --json` previews the current Instance.
- `platform-apply network --box BOX` applies the declared Tailnet policy and
  box access. Tailnet changes use conditional writes and retain a private
  preimage for recovery.
- `platform-apply guests --box BOX [--role bak|dmz|iot]` reconciles shared guests.
- `platform-apply app --app nextcloud-v2` uses the existing application adapter
  and publishes its resource grant. Unsupported adapters and automatic absent
  application removal are refused before execution.

Apply accepts `--dry-run --json` to show its commands without mutation. It
validates selectors and rechecks desired state before each command. Partial
failure remains visible. Retry reconciles the current desired state; it does
not redefine that state.

`platform-source` supplies validated inventory, registry, controller, and
retention views. Legacy registry path arguments are aliases for this view.
They do not read or write the old YAML registry.
The old `platform-app` and `platform-guest` state-edit commands are removed.
Change desired state in Instance, then reconcile it. Immich registry-writing
install and destroy paths refuse execution until an adapter is implemented.

`platform-update` and `platform-router-update` use declared schedules and
selected targets. Update readiness still requires tested candidate bytes,
complete data accounting, native recovery tests, and an active controller.
Use `platform-update pause`, `resume`, and `policy status`. Use
`policy ready --recovery-operation BOX=ID` for each selected shared-VM box.
No operation signature is required. These tools must not adopt guests with
unknown workloads or data.

Credential brokers keep provider credentials private and expose bounded
operations. `ksa-instance-key register-read-key` accepts one public key on
stdin and registers it with read-only access to the private Instance origin.
The static-site broker checks Instance placement before using its credentials.

Controller records are under `/var/lib/klokast`. Reusable artifacts are under
`/var/cache/klokast`. Temporary files use private directories and are removed
by their owner. The repository and `.run/` are not operational state stores.

For an existing development controller, run the checked-in
[development model migration](../ansible/playbooks/68-ops-development-model.yml)
on that controller with `ops_controller_deployment_lifecycle=development`.
It checks pending controller operations, pauses updates, retains recovery
records, installs the new tools, removes obsolete authorization helpers, and
resumes enabled work after validation. Resolve pending dom0 operations before
running it. The migration does not admit a production release.
