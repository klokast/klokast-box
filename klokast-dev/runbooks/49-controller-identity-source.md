# Controller Identity Source

This runbook implements the
[controller identity decision](../../doc/upstream-instance-target-architecture.md#114-controller-identity-source-migration).
It adopts the current active and standby identities as one five-scope group.
Live acceptance completed on 2026-09-07. Signed adoption, signed verification,
exact replay refusal, and controller and MacBook resolver checks passed. See the
[acceptance record](#acceptance-record-2026-09-07). Keep the completed
connectivity records. Do not repeat adoption; later checks use fresh
verification-only Plans and requests.

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

Execution consumes the nonce after the request lifetime and signature checks,
before live verification. An expired request can therefore be refused before
its nonce is consumed. Inspect evidence and prepare a new request in either
case. Never reuse an expired signature. Complete each approval before the
displayed `expires_at` time. Source and observation evidence expire after
30 minutes; each signed request has a ten-minute lifetime.

Failure before publication leaves the source unchanged. If the
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

## Acceptance record: 2026-09-07

Status: `live-verified`. All acceptance gates passed. The active
controller remained `k002-ops` and the standby remained `k001-ops`.

The human promoted and activated engine
`c8f863b6ad85fcd59e8a9e2ab08664458b98c776` with private commit
`d43aaab4f651ec2ab8949ab7e38c9b363da084e7`. All 512 Python tests, the sealed
Go suite and binary build, shell checks, and both controller and router
Ansible syntax checks passed. Sealed operation `0bbf316a5323` completed
cleanup with no remaining resources. Its directory is:

```text
/var/lib/klokast/builds/klokast-cli/c8f863b6ad85fcd59e8a9e2ab08664458b98c776/0bbf316a5323
```

Matching controller tools were installed before the baselines. Toolchain v4
includes all 11 components, including the installed controller resolver.
Activation and toolchain receipts are:

```text
/var/lib/klokast/engine-activations/d43aaab4f651ec2ab8949ab7e38c9b363da084e7/d0af1e20b034c43ca924e866ee026b8a087157de0460b02eaec4b1540e42ae9c.json
/var/lib/klokast/controller-toolchains/c8f863b6ad85fcd59e8a9e2ab08664458b98c776/e850441dcea03bb7ea72d87b70d72314c4de280d0049cf81ba133d88cbff8d92.json
```

Unsigned preparation passed before each signed action. Adoption completed at
`2026-09-07T15:58:25Z` with result `success`. One forward Authority State v3
record added exactly the five-scope controller identity group. All three
existing connectivity groups retained their sources and scopes. The prior
and resulting source documents are:

```text
/var/lib/klokast/authority-states/312370d38e672aea648fcc3d278ad41ce4383441d12fa5b66dad8420682d4aef.json
/var/lib/klokast/authority-states/0b34c848ef2008f74259593ce7c869473a16cfa1e4f130b47eb49b556c0d8005.json
```

Fresh signed verification completed at `2026-09-07T16:02:39Z` with result
`verified`. The source hash remained unchanged. The MacBook helper refused
the exact replay because its nonce was already used. Both executions report
recovery `not_needed`. Adoption and verification receipts are:

```text
/var/lib/klokast/apply-executions/mGM-kHPX5rc6kHgo6kHJunPn/34fc15b10f261ef2b0e453bd9e78a89ccb73248857c56fcd982b97d6055fa311.json
/var/lib/klokast/apply-executions/-Znt7Mijzi5Zk2LG6yGw48Ac/ba8d5b669f445619ceb092dfaaf387f9398a27d6239a341f41947484837b9101.json
```

Controller inspection verified canonical receipt hashes, the exact source
transition and adoption reference, original Plans, protected intent and
configuration archives, stable role evidence, and both consumed nonce
bindings. All archive files remain root-owned with mode `0600`, inside
root-owned mode `0700` directories. Readable runtime copies were removed.
Old and effective controller configurations were equal. The executor ran
verification commands only.

All five desired-state files, their modes and owners, and the prior immutable
source document retained their baseline values. Both persistent HA markers
were unchanged. All 138 persistent configuration entries on each router
matched the original baseline, after-adoption snapshot, and final snapshot.
No controller role, account, credential, application, data, router setting,
or service state was changed by this source operation.

The controller's normal automatic and explicit resolution selected the same
active hostname. Explicit dispatch to the standby was refused. The fixed
root status action returned source `instance_specification_v1`. The human
then confirmed automatic and explicit resolution on the MacBook: both
returned `k002-ops`. The explicit dispatch dry run selected
`tailscale ssh smith@k002-ops cd ~/src/klokast/klokast-box && true`.
The MacBook had pulled documentation commit `711d255`; its implementation
files were unchanged from the promoted engine. The controller remained on
`c8f863b`. This separate check completed the final acceptance gate.

Final Plans for controller identity, default non-controller connectivity,
and active-controller connectivity, respectively, are:

```text
/var/lib/klokast/plans/d43aaab4f651ec2ab8949ab7e38c9b363da084e7/9866249d6fa9283d5e3abef23dcee53d04333c36d6a595b3fe45e5bf7ee9bc5b.json
/var/lib/klokast/plans/d43aaab4f651ec2ab8949ab7e38c9b363da084e7/6be8d68a62b8c93159670dd6d4829380c53f9d1b262fe4dcd25dac614d43de42.json
/var/lib/klokast/plans/d43aaab4f651ec2ab8949ab7e38c9b363da084e7/b7f08ad36202d5b3a50b99b6f0b7a288e26e0c176c55755c940ed3d14ac283a9.json
```

All three Plans are valid, compatible, healthy, deployable, and
authority-ready, with no refusal. All four adopted groups are
verification-only. Unselected box and identity groups have executor `none`.

Private evidence remains on the controller under:

```text
/home/smith/private/klokast/controller-identity-acceptance/c8f863b6ad85fcd59e8a9e2ab08664458b98c776
```

The parent directory holds implementation, activation, toolchain, original
baseline, and expired-attempt evidence. The successful attempt is in
`retry-20260907T155325Z/`. Paths below are relative to that retry directory.

| Evidence | Contents |
| --- | --- |
| `input-baseline.json`, `controller-markers-baseline.json`, `baseline/` | Original post-promotion input, marker, and router baselines. |
| `human-acceptance-programs.json` | Hashes of controller-local evidence helpers. |
| `adoption-20260907T155325Z/` | Fresh source, recovery, observation, Plan, and unsigned adoption evidence. |
| `after-adoption/`, `after-adoption-checks.json` | Exact source transition, receipt, resolver, and unchanged-setting checks. |
| `verification-20260907T155846Z/` | Fresh verification evidence. |
| `signed-execution-verification.json` | Receipt, archive, nonce, cleanup checks, and the human's replay confirmation. |
| `final/`, `controller-markers-final.json`, `resolver-checks-final.json` | Final router, role, and controller resolver checks. |
| `final-20260907T160454Z/` | Three final Plans and `remaining-legacy-settings.json`. |
| `controller-acceptance-result.json` | Historical controller result before the MacBook check. |
| `macbook-resolver-checks.json` | Human-reported MacBook automatic, explicit, and dispatch dry-run results. |
| `acceptance-result.json` | Final `live-verified` result with all acceptance gates complete. |

The first human request expired before execution. Inspection found no source
change, consumed nonce, execution receipt, or runtime residue for that
attempt. Its evidence was retained, and the successful retry used fresh
evidence with the exact original baselines. No lifetime or permission was
weakened. Live switchover, rollback, re-adoption, and direct-IPv6 repair remain
deferred. Acceptance documentation is not automatically deployed.

### Remaining legacy-owned settings

The final identity Plan has 22 legacy-source actions, including adoption
actions whose executor is still `unimplemented_action`. The complete list
and the continuing execution-inventory record are stored in
`final-20260907T160454Z/remaining-legacy-settings.json`.

| Setting group | Remaining actions |
| --- | --- |
| Application declarations, retained-data intent, and disabled-app placement, resource, device, and runtime configuration | 13 |
| Box bridge ports, DHCP reservations, and shared-guest intent | 4 |
| Controller account and repository-path compatibility fields | 2 |
| Legacy schema metadata | 3 |
| Execution inventory | One continuing authority outside those actions |

Continue the current migration queue without another scope-selection
question. These findings do not authorize data deletion or disposal of
recovery evidence.
