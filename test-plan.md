# Platform resource destructive test plan

This procedure checks the resource ownership ledger and keyed nftables rules
against an authorized development deployment. It removes and restores application
network claims. Run Platform commands as `smith` on the active controller.
Desired-state changes use the normal private Instance Git workflow.

## Preparation

- Record the baseline Instance commit and confirm a clean worktree.
- Review the affected application declarations and retained data before testing.
- Keep management access and NanoKVM recovery available. Preserve the baseline
  Tailscale management rules and verify controller access after every phase.
- Preserve application data. This test does not use `remove --wipe-data`.
- Keep private evidence on the controller. Record progress and unresolved work
  in the [operations journal](doc/operations-journal.md).

From the active controller's public source checkout:

```sh
umask 077
test_dir="$HOME/private/klokast/tests/resources-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$test_dir"
git -C "$HOME/private/klokast/instance" rev-parse HEAD >"$test_dir/instance-commit"
ansible/bin/platform-instance check
ansible/bin/platform-resources lint
ansible/bin/platform-resources show >"$test_dir/baseline-compiled.json"
ansible/bin/platform-resources verify
```

Check declared application endpoints and controller access to each target.
Use the deployment's actual DNS names and approved inventory. Do not infer
health from a client command that only prints a URL.

## Desired-state transitions

Prepare each transition in the private Instance repository. Validate, commit,
and publish it through the ordinary Git workflow. Synchronize the active
controller with `ansible/bin/platform-instance sync` before each apply.
See [Instance desired state](doc/klokast-instance-specification.md).

Choose applications with overlapping shareable claims for these phases:

1. Remove both applications' network claims while preserving their declared data.
2. Restore the first application's presence and authorized placement.
3. Restore both applications' presence and authorized placement.
4. Remove the first application's claims while keeping the second present.
5. Restore the baseline desired state through a new reviewed commit.

Use `desired-state: absent` with the required retained-data declarations.
When no retained data requires an application entry, remove its declaration.
Do not edit generated resource views. Do not install application runtimes as
part of this network-resource test.

For each synchronized transition, preview and apply from the active controller:

```sh
ansible/bin/platform-resources lint
ansible/bin/platform-resources diff
ansible/bin/platform-resources --approved-commit "$(git rev-parse HEAD)" apply
ansible/bin/platform-resources verify
```

## Checks after each phase

Record the compiled resources, owner sets, target keyed snippets, applied
provenance, and endpoint reachability under the private test directory.

- Exact duplicate shareable claims produce one effective rule with multiple owners.
- Removing one owner keeps the rule while another owner remains.
- Removing the last owner removes the rule.
- Exclusive conflicts stop before apply.
- Unrelated application claims and management access remain usable.
- Persisted snippets and live nftables rules agree with the synchronized Instance.
- The target-local verifier passes on the declared routers and application hosts.

Use app-scoped verification where useful. A full apply reconciles the complete
Instance resource view and supplies the final check of unrelated resources.

## Completion

Restore the baseline desired declarations through the private Instance Git
workflow, synchronize the controller, and run full resource apply and verify.
Confirm the expected application endpoints and target provenance. Check that no
resource apply or Ansible process is still running. Record completion and private
evidence paths in the operations journal.
