# Inventory and runner source adoption

Status: `implemented`; corrective promotion and live acceptance are pending.
Plan v7, Authority State v5, Controller Toolchain v6, the signed source executor,
and normal inventory consumers are implemented. Source ownership remains at
the completed registry milestone until signed adoption succeeds.
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
