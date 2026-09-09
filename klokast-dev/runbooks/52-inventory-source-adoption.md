# Inventory and runner source adoption

Status: `live-verified` on 2026-09-09.
Plan v7, Authority State v5, Controller Toolchain v6, the signed source executor,
and normal inventory consumers passed signed adoption, fresh signed
verification, exact replay refusal, and final unchanged-setting checks.
All six setting groups now use the private instance. The
[acceptance record](#acceptance-record-2026-09-09) holds the evidence.
The [bounded design](../../doc/upstream-instance-target-architecture.md#inventory-and-runner-source-adoption-design)
owns scope, authority, recovery, and acceptance requirements.

Keep the canonical controller checkout at the active engine during review.
Build the clean reviewed commit through the sealed builder from an isolated
public checkout on the active controller as `smith`. Then run:

```sh
klokast-dev/bin/prepare-inventory-source-candidate \
  --build-dir BUILD_DIR --observation FRESH_OBSERVATION
```

The helper verifies the sealed build and clean public/private checkouts. It
creates a temporary controller-private instance fixture with only engine and
schema-reference metadata adjusted for the reviewed binary. It never publishes
that fixture or changes the active source. The sealed `klokast inventory`
renderer derives host selection and runner enablement without legacy input.

`compare-instance-inventory` runs Ansible inventory parsing for the existing
base plus generated box files and the independent instance graph plus generic
upstream group policy. It compares all selected host variables and complete
group memberships. Only `inventory_file` and `inventory_dir` provenance fields
are excluded. Host addresses, connection users, runner enablement, and role
policy must be equal. Unknown or incomplete graphs refuse. These commands
perform no host contact, configuration, or service operation.

Comparison and the retained inventory reader use Ansible's process-local
memory cache. Saved setup facts are runtime observations and must not enter
the desired-state hash. Inventory variables are not filtered by their names;
declared values that resemble facts must still compare exactly. Acceptance
snapshots of inventory settings must also set `ANSIBLE_CACHE_PLUGIN=memory`.
Normal playbooks keep the configured persistent fact cache.

Evidence stays below
`~/private/klokast/inventory-source-candidates/ENGINE/UTC_TIMESTAMP/`.
The safe result contains counts, hashes, and paths. Raw Ansible output and
private projections remain in that directory. The temporary private Git
fixture and temporary inventory copies are removed after comparison. Inputs,
checkout commits, and the source pointer are rechecked before success.

The helper also creates explicitly named review-only source and toolchain
receipts for the temporary fixture. These do not prove installation or private
publication. They cannot pass the live execution path checks. The sealed binary
creates one adoption Plan and eight post-adoption Plans covering all migration
and connectivity targets. The root Plan parser must accept their exact bytes.
The synthetic post-adoption state has no signed anchor and is never published.

## Execution and installation boundaries

Normal consumers select `ansible/execution-inventory/hosts`. This executable
calls the installed `platform-inventory` client. Only generic upstream group
policy is present beside it; legacy host variables and example-box policy are
absent. Ansible must fail if any inventory source cannot be parsed. Before
adoption, the client derives both boxes from checked controller identity and
parses their generated box graphs with the retained base. This includes legacy
host overrides for hosts absent from the sample base. It resolves the normal
MagicDNS suffix before parsing and rechecks controller identity and the source.
After adoption, it returns the checked sealed graph. It has no fallback for a
missing helper, unknown source, failed check, or changed inputs.

The retained inventory remains available through explicit recovery arguments.
For an isolated sealed build before the new client is installed, use the
builder's `--compatibility-inventory` flag. For installation of the matching
controller tools after human engine promotion, use:

```sh
ansible/bin/converge-ops-controller --box k002 \
  --inventory ansible/inventory/hosts.yml -- \
  --tags ops-controller-apply,ops-controller-secret-authority-wrappers
ansible/bin/controller-toolchain-receipt --build-dir BUILD_DIR
```

Do not run the full controller or runner convergence for this source migration.
The explicit installation inventory prevents a dependency on the client that
this same operation installs. After installation, normal invocations use the
source reader. The separate cloud bootstrap inventory remains an explicit
external recovery input.

## Live acceptance

Promote the final reviewed implementation through the existing human engine
promotion workflow. Keep all earlier acceptance records and recovery files.
After promotion and tool installation:

1. Save desired-state file hashes and metadata, controller markers, and both
   routers' persistent configuration hashes. Record the active source.
2. Refresh source and recovery receipts and a read-only Observation v1. Build
   a Plan with explicit `--migration-target inventory` and the matching sealed
   build and Toolchain v6. Run unsigned preparation.
3. Refresh preparation when the human is ready. Use the existing
   `apply-platform-intent` helper and Touch ID for adoption. Require exactly
   one Authority State v5 transition that adds `execution-inventory-v1`, keeps
   the five prior groups and both prior anchors, and changes no settings.
4. Make a fresh verification-only Plan. Approve verification and use
   `--prove-replay-refusal` with the exact signed request. Preserve all receipts.
5. Compare the normal dynamic inventory against the saved values and check
   desired-state files, markers, runner identity evidence, and persistent
   router configuration. Store the final Plan. Require six verification-only
   groups and no continuing `legacy_engine_inventory` or pending runner action.

The executor permits only adoption and verification. It compares controller,
registry, compiler, router-variable, and inventory results; checks both routers
and live runner identities; consumes the nonce before execution checks; and
uses the shared exact-prior-state publication lock. It cannot configure a
router, restart a service, launch or retire a runner, or repair a gateway.
A used request cannot be retried. If publication succeeds but receipt storage
fails, inspect the source and archives and obtain fresh signed verification.

Mark this milestone `live-verified` only after all acceptance checks pass.
Commit and push the evidence references and result; do not automatically deploy
that final documentation commit. Legacy removal, rollback, and recovery-source
switches remain deferred.

## Acceptance stopped before adoption, 2026-09-09

The human promoted engine `87cd6d7b199658f185dd54c8444e56e7ff0642d9`
with private commit `b50cd721eb3e766ad7cbe217918101747aa315d0`.
Matching tools were installed, and Toolchain v6 receipt `ac663859` passed.
The controller saved seven input hashes and file metadata, both controller
markers, and both routers' persistent configuration hashes below:

```text
/home/smith/private/klokast/inventory-source-acceptance/87cd6d7b199658f185dd54c8444e56e7ff0642d9/
```

The normal inventory baseline then found that k002's runner enablement was
false while the retained inventory and sealed projection both specified true.
The reader had parsed the sample base without the generated real hosts.
Adoption preparation stopped. Authority State v4 remained `e2ef84b6`.
No Apply signature, source transition, runner convergence, or router apply was
performed. Keep the failed baseline as evidence; do not adopt against it.

The correction adds joint legacy parsing and comparison through the actual
reader in both source states. Promote the corrected sealed commit, install its
matching tools, and create a new acceptance directory with fresh baselines.
Do not overwrite or resume the failed baseline above.

## Fact-cache correction after promotion, 2026-09-09

Engine `c1324ea36dc3c4aad6361b072c5f5c95558c32f6` was activated with
private commit `5b4add1b4c846d8dfd99c8d1eb8a4d9041d169f0`.
Matching Toolchain v6 receipt `71fb8e48` passed. Its new baseline confirmed
equal inventory settings and preserved k002 runner enablement. Source
ownership remained at Authority State v4 `e2ef84b6`.

Follow-up analysis of the earlier unsigned preparation failure found cached
router facts in the comparison output. Immediate repeated comparisons had
matched because no fact refresh occurred between them. The correction now
prevents cached observations from entering source inventory or comparison.
Controller evidence and the real fact-cache regression result stay below:

```text
/home/smith/private/klokast/inventory-source-acceptance/c1324ea36dc3c4aad6361b072c5f5c95558c32f6/
```

Do not use this baseline for adoption: it includes cached observations.
Promote the corrected sealed engine, install matching tools, and save a fresh
baseline with the cache mode above before signed adoption and verification.

## Acceptance record, 2026-09-09

The human promoted engine
`cf8cf5f73b81a21e1821c5b1a24ff42ea76a7d2d` with private commit
`35577f9235e8a775c9e15422012f76cd1e8a619a`. Sealed build operation
`ec11bcce7d02` passed Go tests, build, and cleanup. The 538-test Python suite
passed with one runner skip; that real Ansible fact-cache test passed on the
controller. Five affected Ansible syntax checks passed. Private comparison
and nine Plan contracts passed before promotion. Matching Toolchain v6 is:

```text
/var/lib/klokast/controller-toolchains/cf8cf5f73b81a21e1821c5b1a24ff42ea76a7d2d/12418d147e06c756ee22ac4a1eb31e13e5bef2305a1dbdd65d149f3d5aa63946.json
```

Fresh baselines and unsigned preparation passed after installation. Signed
adoption finished at `2026-09-09T19:45:14Z`. Authority State v4
`e2ef84b6c703be23e5c74c413799c7c8cde8c4f180f1eed3d24f3feb240eb1de`
became Authority State v5
`8622695e9361d281620dc5f69d9b556f2ab637f748899985899d929cf3eaf188`.
Exact reconstruction verified that only `execution-inventory-v1` and its
signed adoption reference were added, with the required version and transition
metadata. All five prior groups and both prior adoption references remained
unchanged. The new group contains exactly `execution_inventory` and
`deployment.control_plane.airunners.k002-ops-airunner`.

Fresh signed verification finished at `2026-09-09T20:01:50Z`. The MacBook
Apply helper confirmed refusal of the exact signed replay. Verification kept
the source unchanged. Recognized DERP on k001 passed authenticated router
access checks; direct transport remains preferred.

The adoption receipt, verification receipt, and final Plan are controller-held:

```text
/var/lib/klokast/apply-executions/SJF1j-l3q9UzV4TQBQEFQyys/c1f51751f1b1b3bbeb7da2a4ae85c5c083028a947392233ff91c3339ddf7ffe1.json
/var/lib/klokast/apply-executions/vZeeAFWcGSE8j_q8HWgSZYC1/9e806c31a5cfc8af475443bb746dbcb9add83ea383e0d988e7eaa28615f679a0.json
/var/lib/klokast/plans/35577f9235e8a775c9e15422012f76cd1e8a619a/540bae240722275070f2604a1600b0c54ad4dff4756e73d7dc97262a860be6cf.json
```

Final acceptance finished at `2026-09-09T20:08:49Z`. Bound desired-state
files retained their bytes, ownership, and modes; only the exact source-pointer
transition was permitted. Both controller markers, both routers' persistent
configuration, complete registry values, all 14 selected hosts' inventory
values and groups, and the existing runner identity matched their baselines.
The normal inventory reader also matched with generated box files and as a
standalone source. The 11 legacy sample hosts stay outside normal selection;
no sample host was contacted or removed.

Both signed archives contain ten root-owned files with mode `0600` inside
`0700` directories. Intent hashes match the root-owned `0440` receipts. Both
nonces are consumed and bind their exact Plans. Temporary runtime copies are
absent. Old and effective controller and inventory bytes match. Complete
registry values match after parsing their different YAML serialization.

Acceptance evidence stays below:

```text
/home/smith/private/klokast/inventory-source-acceptance/cf8cf5f73b81a21e1821c5b1a24ff42ea76a7d2d/
```

- `input-baseline.json`, `controller-markers-baseline.json`, `baseline/`, and
  `inventory-baseline/`: original settings and metadata.
- `initial-preflight/` and `preflight-audit.json`: successful unsigned preparation.
- `human-adoption-*/` and `human-verification-*/`: refreshed approval evidence
  and checks between the signed steps.
- `final-20260909T200524.974309Z/`: final source, recovery, Observation, and Plan evidence.
- `final-acceptance/acceptance-result.json`: completed result and evidence hashes.
- `final-acceptance/checks.json`, `final-acceptance/routers/`, and
  `final-acceptance/inventory/`: final baseline checks.
- `final-acceptance/signed-receipts.json`,
  `final-acceptance/protected-audit.json`, and
  `final-acceptance/human-replay-confirmation.json`: exact transition,
  archive audit, and human-reported replay refusal.
- `final-acceptance/final-plan-reference.json`: final Plan and empty remaining-scope lists.

### Remaining authority and actions

Final Plan `540bae24` is valid, compatible, deployable, authority-ready, and
healthy, with no refusals. All six groups are verification-only: both box
connectivity groups, Tailnet, controller identity, registry settings, and
execution inventory. `execution_inventory` reports active authority
`instance_specification_v1`. No legacy-owned setting group, continuing legacy
authority, pending runner adoption, or unimplemented action remains.

The Plan still reports `legacy_removal_ready: false`. Source adoption is
complete for the represented settings; the whole upstream/instance migration
is not complete. Keep retained comparison and recovery files, historical
Plans, source records, and receipts. The
[current work queue](../../doc/upstream-instance-target-architecture.md#current-work-queue)
owns the remaining gates. Do not repeat adoption or deploy this documentation
commit automatically.
