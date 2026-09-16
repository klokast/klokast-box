# Instance Authority Verification

Use this procedure to verify the current private-instance authority after an
engine or desired-state change. The legacy inputs are retired. Do not restore
them for routine verification.

Run controller commands as `smith` on the active `<box>-ops` controller from
`~/src/klokast/klokast-box`. Run approval commands on the trusted MacBook.
Do not run these operations on an infra-agent or airunner.

## Prepare current evidence

Use the exact active engine and its sealed build. Controller Toolchain v8,
Authority State v5, all six instance-owned setting groups, the protected
legacy-input recovery archive, and the prior exercise and retirement receipts
must remain available.

Use an engine and controller wrappers with the same freshness policy. Source
receipts and Observations each have a one-hour lifetime. Run slow preparation
before collecting these inputs where possible. Do not change timestamps in
existing evidence. After a refresh, regenerate every receipt and Plan that
binds the replaced input.

Synchronize the private source and keep the reported commit and receipt:

```sh
ansible/bin/platform-instance sync \
  --repo-owner FAMILY --repo-name klokast-instance
```

Recheck source reconstruction without changing the canonical checkout:

```sh
ansible/bin/platform-instance source-recovery-check \
  --private-commit PRIVATE_COMMIT \
  --source-receipt-sha256 SOURCE_RECEIPT_SHA256 \
  --engine-commit ENGINE_COMMIT \
  --build-operation BUILD_OPERATION
```

Refresh the map and create an owner-only Observation:

```sh
ansible/bin/platform-map refresh
umask 077
OBSERVATION="$(mktemp ~/private/klokast/observation.XXXXXX)"
ansible/bin/platform-map export-observation \
  --file .run/platform-map/current.json >"$OBSERVATION"
```

Create fresh `verify` retirement evidence from the current complete consumer
matrix, protected recovery archive, and immutable exercise and retirement
receipts:

```sh
ansible/bin/platform-apply retirement-evidence \
  --phase verify \
  --consumer-matrix CONSUMER_MATRIX \
  --recovery-archive RECOVERY_ARCHIVE \
  --exercise-receipt EXERCISE_RECEIPT \
  --retirement-receipt RETIREMENT_RECEIPT
```

The evidence command must prove that the retired paths are absent, current
consumer settings are unchanged, and the recovery archive still reconstructs
the exact retired inputs. Keep its content-addressed output path.

## Create and review Plan v9

```sh
ansible/bin/platform-plan --legacy-retirement \
  --retirement-phase verify \
  --retirement-evidence RETIREMENT_EVIDENCE \
  --build-dir BUILD_DIR \
  --instance /home/smith/private/klokast/instance \
  --observation "$OBSERVATION" \
  --instance-source-receipt SOURCE_RECEIPT \
  --authority-state AUTHORITY_STATE \
  --controller-toolchain-receipt TOOLCHAIN_RECEIPT
```

Require a valid and deployable Plan v9. It must have six verification-only
source groups, one `verify_legacy_retirement` lifecycle group,
`legacy_removal_ready: true`, and no adoption or deletion action.

On the trusted MacBook, review without a signature:

```sh
klokast-dev/bin/apply-platform-intent \
  --controller ACTIVE_BOX-ops \
  --plan PLAN --authority-state AUTHORITY_STATE \
  --controller-toolchain-receipt TOOLCHAIN_RECEIPT \
  --source-recovery-receipt SOURCE_RECOVERY_RECEIPT \
  --instance-source-receipt SOURCE_RECEIPT \
  --observation OBSERVATION --build-dir BUILD_DIR \
  --retirement-evidence RETIREMENT_EVIDENCE --check
```

When the review passes, run the same command with
`--prove-replay-refusal` instead of `--check`. One Touch ID signature
authorizes only retired-state verification. The helper writes the final JSON
result to stdout and progress to stderr. It must classify the exact replay as
`nonce-reuse` or `expired-intent`. An SSH error or unknown controller message
is a failed replay test.

Review the earliest evidence deadline printed by preflight. It requires at
least 15 minutes to remain for execution; the one-hour signature window does
not extend evidence validity. If review or checks use that reserve, prepare
fresh evidence and a new Plan before signing. An expired or failed consumed
request must not be retried. See `doc/secret-authority.md` for retained private
revalidation diagnostics.

## Acceptance

Verify the new receipt, consumed nonce, audit entry, runtime cleanup, unchanged
Authority State v5, six verification-only groups, and absent retired paths.
Verify that the recovery archive and its manifest did not change. Do not copy
private values into public documentation.

The public record of the completed 2026-09-14 migration is available in Git at
commit `186cfa9`. Controller receipts, Plans, source history, audit logs,
policy recovery material, and the root-only recovery archive remain the
operational evidence.
