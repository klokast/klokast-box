# Tailnet Authority Pilot

Use this runbook only for the decided `tailnet_policy_inputs_v1` pilot. It
moves these three scopes as one unit:

- `deployment.tailnet.groups.family`
- `deployment.tailnet.groups.operators`
- `deployment.tailnet.magicdns_suffix`

The pilot must not change the policy bytes. Stop if the instance rendering,
legacy rendering, or live policy differs.

## 1. Prepare The Exact Controller Toolchain

Run Platform commands as `smith` on the active `<box>-ops` controller from
`~/src/klokast/klokast-box`. Replace each uppercase placeholder with the exact
value from the prior command. Do not run these commands on the infra-agent.

Pull the committed public engine, build it, and converge its controller tools:

```sh
git pull --ff-only
ENGINE_COMMIT="$(git rev-parse HEAD)"
ansible/bin/platform-builder build-klokast-cli \
  --box BOX --approved-commit "$ENGINE_COMMIT"
ansible/bin/converge-ops-controller --box BOX
```

If the private lock does not select `ENGINE_COMMIT`, use
`promote-private-instance-engine` on the trusted MacBook. First use `--check`.
Then approve the exact promotion with the private-instance Touch ID signer.

Install the separate Platform Apply signer on the trusted MacBook:

```sh
klokast-dev/bin/install-secret-authority-approval-signer \
  --controller BOX-ops --purpose platform-apply
```

Back on the controller, create the exact toolchain receipt:

```sh
BUILD_DIR=/var/lib/klokast/builds/klokast-cli/$ENGINE_COMMIT/BUILD_OPERATION
ansible/bin/controller-toolchain-receipt --build-dir "$BUILD_DIR"
```

Keep the reported receipt path as `TOOLCHAIN_RECEIPT`.

## 2. Create Fresh Evidence And Plan V2

Synchronize the private source and keep the reported commit and source receipt:

```sh
ansible/bin/platform-instance sync \
  --repo-owner FAMILY --repo-name klokast-instance
```

Rehearse source recovery without changing the canonical checkout:

```sh
ansible/bin/platform-instance source-recovery-check \
  --private-commit PRIVATE_COMMIT \
  --source-receipt-sha256 SOURCE_RECEIPT_SHA256 \
  --engine-commit "$ENGINE_COMMIT" \
  --build-operation BUILD_OPERATION
```

Keep the reported path as `SOURCE_RECOVERY_RECEIPT`. Refresh the Platform map
and create one owner-only observation:

```sh
ansible/bin/platform-map refresh
umask 077
OBSERVATION="$(mktemp ~/private/klokast/observation.XXXXXX)"
ansible/bin/platform-map export-observation \
  --file .run/platform-map/current.json >"$OBSERVATION"
AUTHORITY_STATE="/var/lib/klokast/authority-states/$(sed -n '1p' /var/lib/klokast/active-authority-state).json"
```

Create Plan v2:

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

Keep the reported path as `PLAN`. The initial Plan must contain three
`adopt_instance_specification` actions in the exact atomic group.

## 3. Check And Adopt

On the trusted MacBook, run the helper with `--check`. It displays the exact
ten-minute intent and does not sign or mutate state:

```sh
klokast-dev/bin/apply-platform-intent \
  --controller BOX-ops \
  --plan PLAN --authority-state AUTHORITY_STATE \
  --controller-toolchain-receipt TOOLCHAIN_RECEIPT \
  --source-recovery-receipt SOURCE_RECOVERY_RECEIPT \
  --instance-source-receipt SOURCE_RECEIPT_PATH \
  --observation OBSERVATION --build-dir BUILD_DIR --check
```

Run the same command without `--check`. Review the exact intent, type the
required phrase, and approve Touch ID. Keep the reported successful execution
receipt. The live policy bytes must stay unchanged. The active authority state
must become `instance_specification_v1` for all three scopes.

Create fresh source, recovery, observation, toolchain, and Plan v2 evidence.
The new Plan must contain only `verify_instance_authority` for the atomic
group. A second adoption is a failure.

## 4. Prove Forward Rollback And Re-adoption

Use the fresh verification Plan and the successful adoption receipt with the
same MacBook helper:

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

Review and approve this second Touch ID action. Rollback creates a new forward
authority state and restores the exact saved policy preimage. It does not edit
or delete old evidence.

Create a fresh Plan v2. It must propose the same three-scope adoption again.
Run the helper with `--prove-replay-refusal` and approve the third Apply
action. The helper must complete re-adoption and then confirm that the
controller refuses the same signed intent. Create one final Plan v2. It must
use verification actions.

Keep all authority states, source-recovery receipts, Plans, intents,
executions, rollback material, toolchain receipts, and audit records. Remove
only the temporary observation file after acceptance. Do not remove the
legacy Tailnet shadow.
