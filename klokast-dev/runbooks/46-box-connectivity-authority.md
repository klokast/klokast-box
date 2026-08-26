# First Box Connectivity Authority

Use this runbook only for the first `box_connectivity_v1` source change. The
action selects the unique box that does not host the active controller. It
must not operate on the controller box, applications, shared guests, or
Tailnet policy.

Run Platform commands as `smith` on the active `<box>-ops` controller from
`~/src/klokast/klokast-box`. Run approval commands on the trusted MacBook.
Do not run Platform commands on an infra-agent.

## 1. Prepare The Controller

Use section 1 of
[`45-tailnet-authority-pilot.md`](45-tailnet-authority-pilot.md) to pull the
public commit, build the exact sealed engine, promote the private engine lock,
install the controller tools, and create a Toolchain v3 receipt. Use the new
public commit for all steps.

On the controller, run the Ansible syntax check before you create evidence.
Set the repository-local configuration and role path explicitly:

```sh
ANSIBLE_CONFIG="$PWD/ansible/ansible.cfg" \
ANSIBLE_ROLES_PATH="$PWD/ansible/roles" \
  ansible-playbook --syntax-check ansible/playbooks/32-platform-box-access.yml
```

The canonical engine build in section 1 runs `go test -mod=vendor
-buildvcs=false ./...` inside the pinned builder image. The controller does not
need a separate Go installation.

## 2. Convert Authority State V1

Read the active state path on the controller:

```sh
AUTHORITY_STATE="/var/lib/klokast/authority-states/$(sed -n '1p' /var/lib/klokast/active-authority-state).json"
```

On the trusted MacBook, prepare without approval first:

```sh
klokast-dev/bin/apply-platform-intent \
  --controller BOX-ops --authority-state AUTHORITY_STATE \
  --convert-authority-state --check
```

Run the same command without `--check`. Review the exact box list and approve
Touch ID. The operation creates Authority State v2. It preserves the current
Tailnet source and sets each box connectivity group to
`legacy_platform_resources`. It does not change network configuration.

Read the new active state path on the controller. Keep the old v1 file.

## 3. Create Plan V3

Use section 2 of `45-tailnet-authority-pilot.md` to synchronize the private
source, prove source recovery, refresh the Platform map, and export a fresh
observation. Then run `platform-plan` with the active Authority State v2 and
Toolchain v3 receipt:

```sh
ansible/bin/platform-plan \
  --build-dir "$BUILD_DIR" \
  --instance ~/private/klokast/instance \
  --compatibility-deployment ~/private/klokast/deployment.yml \
  --compatibility-registry ~/private/klokast/platform-resources.yml \
  --compatibility-controller-ha ~/private/klokast/controller-ha.yml \
  --observation "$OBSERVATION" \
  --instance-source-receipt SOURCE_RECEIPT_PATH \
  --authority-state "$AUTHORITY_STATE" \
  --controller-toolchain-receipt TOOLCHAIN_RECEIPT
```

The Plan must be `klokast.plan.v3`. It must select one box. That box must be
the unique box that does not host the active controller. Its action group must
contain exactly five scopes and use `box_connectivity_v1`. The Tailnet group
must be verification-only. All unrelated changes must use
`unimplemented_action`.

## 4. Check And Apply One Box

On the trusted MacBook, run:

```sh
klokast-dev/bin/apply-platform-intent \
  --controller BOX-ops \
  --plan PLAN --authority-state AUTHORITY_STATE \
  --controller-toolchain-receipt TOOLCHAIN_RECEIPT \
  --source-recovery-receipt SOURCE_RECOVERY_RECEIPT \
  --instance-source-receipt SOURCE_RECEIPT_PATH \
  --observation OBSERVATION --build-dir BUILD_DIR --check
```

The preflight compiles the unchanged registry and the instance-derived
effective registry. It ignores only `registry_path` and `registry_sha256` in
the comparison. It also runs the selected router action in Ansible check mode.
It does not sign or change the source record.

Run the same command without `--check`. Review the selected box, five scopes,
and all input hashes. Approve Touch ID. Keep the successful execution receipt.
The operation stores root-only rollback inputs before it changes the source
record. It then applies and verifies only `<selected-box>-router`.

Create a fresh observation and Plan v3. The selected group must now be
verification-only. Verify controller access and every declared connection
path before rollback acceptance.

## 5. Prove Rollback And Reapply

Use the fresh verification Plan and the successful adoption receipt:

```sh
klokast-dev/bin/apply-platform-intent \
  --controller BOX-ops \
  --plan PLAN --authority-state AUTHORITY_STATE \
  --controller-toolchain-receipt TOOLCHAIN_RECEIPT \
  --source-recovery-receipt SOURCE_RECOVERY_RECEIPT \
  --instance-source-receipt SOURCE_RECEIPT_PATH \
  --observation OBSERVATION --build-dir BUILD_DIR \
  --rollback-execution-receipt ADOPTION_EXECUTION_RECEIPT
```

Approve Touch ID. The rollback creates a new forward Authority State v2
record, applies the saved old input to the same router, and verifies it.

Create a fresh adoption Plan v3. Reapply with
`--prove-replay-refusal`. The helper must complete the source change and then
prove that the controller refuses the same signed request. Create one final
Plan v3. The selected group must be verification-only.

Keep all old registries, authority states, Plans, intents, execution receipts,
rollback material, Toolchain receipts, and audit records. Do not authorize the
controller box. That box needs a separate human decision.
