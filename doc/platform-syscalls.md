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

Apply accepts `--dry-run --json` to show its commands without mutation. It
validates selectors and rechecks desired state before each command. Partial
failure remains visible. Retry reconciles the current desired state; it does
not redefine that state.

`platform-source` supplies validated inventory, registry, controller, and
retention views. Legacy registry path arguments are aliases for this view.
They do not read or write the old YAML registry.
`platform-app`, `platform-apply app`, and `kk app` are retired. Application
maintenance belongs to the tools under `apps/<app>/`; see the
[application catalog](../apps/README.md). `platform-guest` state-edit commands
are also removed. Change desired state in Instance, then reconcile it.
Immich registry-writing install and destroy paths still refuse execution.

Automatic application installation from Instance and production application
delivery are not implemented by these development tools. See
[application installation](platform-lifecycle.md#application-installation)
and [application dependencies](klokast-instance-specification.md#application-dependencies)
for the required behavior. Resource compilation and app-scoped grant export
remain Platform functions; application runtime actions do not.

`platform-update` and `platform-router-update` retain explicit inspection,
isolated tests, and first router installation. Automatic VM updates and their
Instance policy are retired. See [VM inspection and tests](platform-updates.md).
`platform-maintenance` retains only network reconciliation and the bounded
IPv6 source reader.

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
removes retired update schedules. Resolve pending dom0 operations before
running it. The migration does not admit a production release.
