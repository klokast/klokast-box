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
retention views from Instance. Registry path selectors refer to this validated
view. Application maintenance belongs to the tools under `apps/<app>/`; see
the [application catalog](../apps/README.md). Change desired state in Instance,
then reconcile it.

Automatic application installation from Instance and production application
delivery are not implemented by these development tools. See
[application installation](platform-lifecycle.md#application-installation)
and [application dependencies](klokast-instance-specification.md#application-dependencies)
for the required behavior. Resource compilation and app-scoped grant export
remain Platform functions; application runtime actions do not.

`platform-update` provides explicit shared-VM inspection and isolated tests.
`provision-router` provides first router installation through `provision-box`.
See
[VM inspection and tests](platform-updates.md). `platform-maintenance` provides
network reconciliation and the bounded IPv6 source reader.

Credential brokers keep provider credentials private and expose bounded
operations. `ksa-instance-key register-read-key` accepts one public key on
stdin and registers it with read-only access to the private Instance origin.
The static-site broker checks Instance placement before using its credentials.

Controller records are under `/var/lib/klokast`. Reusable artifacts are under
`/var/cache/klokast`. Temporary files use private directories and are removed
by their owner. The repository and `.run/` are not operational state stores.

To converge an existing development controller, use
[`converge-ops-controller`](../ansible/bin/converge-ops-controller). The
[controller setup playbook](../ansible/playbooks/68-ops-development-model.yml)
checks pending operations, preserves recovery records, and installs the
supported development tools. Resolve pending dom0 operations before setup.
Production release admission follows the Platform lifecycle contract.

For local tool installation without controller activation, run
`ansible/bin/converge-ops-controller --box BOX --tools-only -- -e ops_controller_deployment_lifecycle=development`
as `smith` on the active controller. This mode accepts one existing active or
standby controller. Both public checkouts must be clean and at the same commit.
The Go installer verifies the public archive on the execution controller and
copies it to the target. It reuses the controller package and tool installers.
It rejects package drift
and does not allow package pruning.

Tools-only setup does not change controller placement, configure credentials,
run network or service convergence, retire legacy helpers, or activate Platform
operations. Use `klokast check`, `inventory`, and `registry` with an explicit
public test fixture for offline checks. Installed Platform source readers and
image operations still require the active controller. Tool installation alone
does not make a standby controller ready for promotion.
