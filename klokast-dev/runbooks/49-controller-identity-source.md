# Controller Identity Source

This runbook implements the
[controller identity decision](../../doc/upstream-instance-target-architecture.md#114-controller-identity-source-migration).
It adopts the current active and standby identities as one five-scope group.
Live acceptance is pending. Keep the completed connectivity records.

Run Platform commands as `smith` on the active controller, from
`~/src/klokast/klokast-box`. Keep private inputs and evidence there. Run the
human promotion and signing helpers from the trusted MacBook. Continued
migration scope development is authorized; the helpers still require a human
signature for each exact promotion and source action.

## 1. Promote and install

Run the Python suite, sealed Go tests/build, shell checks, and controller
Ansible syntax check for the exact implementation commit. Build with
`ansible/bin/platform-builder build-klokast-cli --box BOX --approved-commit COMMIT`.
Use the existing [engine promotion procedure](40-private-instance-bootstrap.md#14-promote-the-active-engine).
Name the active controller explicitly during promotion: the new automatic
resolver needs the matching installed controller helper.

After activation, install the matching active-controller tools:

```sh
ansible/bin/converge-ops-controller --box BOX -- \
  --tags ops-controller-secret-authority-wrappers,ops-controller-apply
ansible/bin/controller-toolchain-receipt --build-dir "$BUILD_DIR"
```

Require Controller Toolchain v4 with the `controller_ha` component. Update
the MacBook public checkout to the same engine before adoption. Keep both
public checkouts fixed through acceptance. The standby uses its existing HA
guard; do not install credentials or change its role for this migration.

## 2. Save baselines and refresh evidence

Use an owner-only evidence directory under `~/private/klokast` and
`umask 077`. Record public/private commits, the five desired-state file
hashes, the active source pointer and document, both persistent HA marker
files, and the persistent router configuration. Reuse the
[connectivity baseline procedure](48-controller-box-connectivity-source.md#2-keep-the-baselines).
Keep snapshots of file bytes or hashes, modes, and ownership. Do not use
changing observation timestamps or counters as persistent configuration.

The existing Authority State v2 must show both box groups and Tailnet using
the instance. Read-only checks must find the expected configured active and
standby roles and identical `active_box` values. Require authenticated actual
hostnames to agree with both markers and the instance. An unreachable or
unconfigured peer is a refusal; do not repair or promote it during acceptance.

Refresh source synchronization, recovery, and Observation v1 evidence through
the [existing procedure](48-controller-box-connectivity-source.md#3-refresh-evidence-and-select-the-controller-box).
Use fresh evidence when the human is ready. Create Plan v5 with the usual
exact evidence arguments and `--migration-target controller-identity`:

```sh
ansible/bin/platform-plan \
  --migration-target controller-identity \
  --build-dir "$BUILD_DIR" \
  --instance ~/private/klokast/instance \
  --compatibility-deployment ~/private/klokast/deployment.yml \
  --compatibility-registry ~/private/klokast/platform-resources.yml \
  --compatibility-controller-ha ~/private/klokast/controller-ha.yml \
  --observation "$OBSERVATION" \
  --instance-source-receipt "$SOURCE_RECEIPT" \
  --authority-state "$AUTHORITY_STATE" \
  --controller-toolchain-receipt "$TOOLCHAIN_RECEIPT"
```

Require no refusal, exact five-scope coverage, and the sole selected executor
`controller_identity_source_v1`. Both box groups remain verification-only
with executor `none`. Their connectivity target flag does not select the
identity pair; the instance alone selects that pair.

## 3. Adopt, verify, and check replay

Use `klokast-dev/bin/apply-platform-intent` with the exact controller, Plan,
authority, toolchain, source/recovery receipts, Observation, and build paths.
Run `--check` first. Require the closed intent kind
`klokast.controller-identity-source-intent.v1`, the expected pair, equal old
and effective controller configurations, and successful role checks.

Run the same helper without `--check`, enter its approval phrase, and approve
Touch ID. Require one Authority State v3 record linked to the previous v2
record, one identity adoption reference, unchanged connectivity groups, and
a successful execution receipt. Verify baseline equality.

Create fresh evidence and a new identity-target Plan. Its action must be
`verify_instance_authority`. Run unsigned preparation, then signed execution
with `--prove-replay-refusal`. Require `verified`, an unchanged source hash,
and refusal of the exact replay because the nonce was already used.

Check normal resolution on the controller and MacBook:

```sh
ansible/bin/ops-controller-ha resolve-active
ansible/bin/ops-controller-ha resolve-active --controller BOX-ops
ansible/bin/ops-controller-ha run --controller BOX-ops --dry-run-plan -- true
```

The output must select the unchanged active hostname. An explicit standby
target must be refused. Test the controller's fixed status action through
`doas -n /usr/local/sbin/ksa-apply controller-identity-status`; its source must
be `instance_specification_v1`. Keep outputs in the private evidence directory.

## 4. Failures and recovery

A failed signed execution consumes its nonce. Inspect evidence and prepare a
new request. Failure before publication leaves the source unchanged. If the
source was published but receipt storage failed, the operation is incomplete:
inspect its adoption reference and protected archive, then obtain fresh
signed verification. Do not repeat the used request or repoint the source.

If source custody is unavailable, `ops-controller-ha --legacy-recovery`
permits the existing recovery procedures through saved contact configuration.
It cannot run normal dispatch or `resolve-active`. Use the
[controller recovery procedure](../../doc/platform-deploy.md#controller-recovery),
including fencing before promotion. Restore source custody and reconcile
private desired identity before normal dispatch resumes. This migration does
not exercise live switchover, rollback, re-adoption, or IPv6 repair.

## 5. Complete and continue

Save a final identity-target Plan and both connectivity-target Plans. Require
all adopted groups to remain verification-only, exact setting and marker
equality, protected archives, valid receipts, consumed nonces, and removed
runtime copies. Record engine, build, toolchain, source transition, receipt,
baseline, and final Plan paths in an acceptance record here. Only then mark
this milestone `live-verified` in the target architecture.

Commit and push acceptance documentation without automatically deploying that
documentation-only commit. Continue the
[current migration queue](../../doc/upstream-instance-target-architecture.md#current-work-queue)
using the remaining legacy scopes in the final Plan. No new scope-selection
question is required. Preserve retained data and recovery evidence.
