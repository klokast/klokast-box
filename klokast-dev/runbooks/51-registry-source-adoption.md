# Registry source adoption

Status: `implemented`; signed adoption passed on 2026-09-08. Signed
verification and exact replay refusal are pending. The bounded decision is in
[the target architecture](../../doc/upstream-instance-target-architecture.md#registry-source-adoption).
The [compatibility checkpoint](50-registry-migration-checkpoint.md) is active.
Its acceptance record is complete and remains the rollback-checker baseline.

Engine `e05a713` was promoted and private commit `317af096` published the exact
checked registry candidate. Matching Toolchain v5, both controller markers,
and router configuration baselines passed. The first unsigned preflight refused
an unrelated pending airunner action because of a validator vocabulary mismatch.
No source transition or signed execution occurred in that attempt. Preserve
this controller-held evidence:

```text
/home/smith/private/klokast/registry-source-acceptance/e05a713cd32f859d75858ad1943f4ef88edb80f3
```

The directory contains publication synchronization, input and router baselines,
the initial Plan, and the refusal in `initial-preflight/`.

The attempt to promote validator correction `1dc2dd6` then exposed a Mac
candidate-selector omission for the published `inactive-apps` field. It stopped
before approval or private publication. Combined correction `db61bc7` passed
the actual Mac candidate generator, installed controller promotion preflight,
both sealed-engine checks, and the metadata-only diff. The human promoted it
to private commit `366efc194d2efd82080d59f87222129eb309b51e`. Matching controller
tools were installed before the new acceptance baselines were captured.

## Scope and authority

Adopt both boxes' substrate settings and every saved disabled-app field as
one `registry-settings-v1` group. Keep absent-app data declarations and all
prior source groups. This operation changes source ownership only. It does
not enable apps, remove data, change router settings, or replace execution
inventory. Direct transport is preferred; verified DERP is acceptable.

The sealed `klokast registry --instance PATH --json` command derives the full
registry and sorted scope set from checked instance bytes. It has no legacy
input, app selector, or box selector. It preserves empty strings, empty maps,
false selections, omitted defaults, and disabled apps without a manifest.
Its output and the expanded Plan contain private settings: keep them on the
controller or trusted MacBook. Share only evidence paths, hashes, and counts.

`platform-registry` is the installed read client. The root
`ksa-apply registry-source-status` action checks the active controller,
protected adoption Plan, exact scopes, sealed engine, and current private
inputs before returning instance values. It accepts no caller path or
executable. A missing helper or invalid source evidence is a refusal.
Normal compiler and lifecycle reads use this client. Legacy writers refuse
after adoption and direct the operator to the private publication workflow.

This root action has read authority over private configuration and protected
source evidence. The separate signed executor has authority to publish one
source record after verification. Neither interface accepts a command or
performs a router apply. The candidate helper runs as `smith`, holds no new
credentials, and writes only private preparation evidence.

## Build, prove the candidate, and promote

Run the Python suite, Go tests, affected shell checks, and Ansible syntax
checks. Build the exact clean public commit in the existing sealed builder.
Keep the canonical controller checkout at its active engine during building.
A clean isolated public checkout can run these controller commands:

```sh
ansible/bin/platform-builder build-klokast-cli --box BOX --approved-commit COMMIT
klokast-dev/bin/prepare-registry-source-candidate --build-dir BUILD_DIR
```

Candidate preparation checks the sealed renderer, complete legacy-object
equality, full and box-only compiler equality, and both router variable sets.
It creates a temporary private Git fixture, removes it after the check, and
stores the candidate and protected logs below
`~/private/klokast/registry-source-candidates/ENGINE/UTC_TIMESTAMP/`.
Its JSON result contains paths, hashes, and counts. It does not publish the
private candidate, change the active source, or run a router command.

Use the existing [human engine-promotion workflow](40-private-instance-bootstrap.md#14-promote-the-active-engine).
Promote the exact reviewed public commit and sealed build operation. Keep the
checkpoint as the previous engine. After activation, install the matching
controller tools and create their Toolchain v5 receipt:

```sh
ansible/bin/converge-ops-controller --box BOX -- \
  --tags ops-controller-secret-authority-wrappers,ops-controller-apply
ansible/bin/controller-toolchain-receipt --build-dir BUILD_DIR
```

Toolchain v5 adds `platform_registry` to the existing component set. Use the
same public commit on the MacBook and controller through acceptance.

## Publish the private candidate and prepare adoption

Refresh the candidate after promotion. Review its exact private diff on the
trusted MacBook, then use `klokast-dev/bin/publish-private-instance` to publish
it. Keep both sealed-engine checks. The old and new private documents must
express the same settings; only source representation is extended.

Take desired-state, controller-marker, and persistent router-configuration
baselines after promotion. Use the same read-only configuration capture
workflow as controller identity acceptance. Keep these baselines through
adoption and verification. Refresh source, recovery, and observation evidence
when the human is ready. Create the Plan with all normal evidence arguments
and the explicit selector:

```sh
ansible/bin/platform-plan --migration-target registry \
  --instance "$HOME/private/klokast/instance" \
  --compatibility-deployment "$HOME/private/klokast/deployment.yml" \
  --compatibility-registry "$HOME/private/klokast/platform-resources.yml" \
  --compatibility-controller-ha "$HOME/private/klokast/controller-ha.yml" \
  --instance-source-receipt SOURCE_RECEIPT \
  --observation OBSERVATION --authority-state AUTHORITY_STATE \
  --controller-toolchain-receipt TOOLCHAIN_RECEIPT --build-dir BUILD_DIR
```

Require Plan v6 and executor `registry_source_v1`. Both box groups, Tailnet,
and controller identity must already use the instance. All unselected groups
must report verification with executor `none`. The registry group must cover
all substrate defaults, saved inactive-app fields, and retained-data scopes.
Three schema-version fields and the fixed controller account/path are engine
policy evidence. Execution inventory remains a continuing legacy authority.

Run unsigned preparation through `platform-apply preflight`. It verifies both
routers through readable runtime copies of the effective registry and stores
protected evidence. Use `apply-platform-intent` on the MacBook with the Plan,
source, recovery, observation, authority, toolchain, and build paths. The
existing Touch ID signer authorizes the exact ten-minute intent.

## Acceptance and failure handling

Require one Authority State v4 transition that adds only the registry group
and its original signed adoption reference. Preserve the identity adoption
reference and all prior groups. Check the desired-state and persistent router
baselines. Then create a fresh registry verification Plan, sign verification,
and use `--prove-replay-refusal` to confirm refusal of the exact signed bytes.
Store the final Plan and repeat the unchanged-setting checks.

The signed executor consumes its nonce before execution-time live checks.
It rechecks controller roles and every bound input after verification. Source
publication uses the shared local lock and exact prior-state check. A failed
request needs a new nonce and fresh evidence. A failure before publication
leaves the source unchanged. If publication succeeds but receipt storage
fails, the operation is incomplete: inspect the source and protected archive,
then obtain fresh signed verification. Do not retry the used request or run
automatic network repair.

The compiler's `--compatibility-registry` mode permits explicit comparisons
and verification. It refuses apply, grants, and output mutations. Historical
box and IPv6 interfaces remain available for their prior source versions.
A registry-adopted source refuses legacy box ownership changes until an
explicit recovery design supports them. No rollback exercise is required.

Mark this milestone `live-verified` only after signed adoption, signed
verification, replay refusal, and unchanged-setting checks pass. Record the
controller-held evidence references here and in the target architecture.
Commit and push acceptance documentation without deploying that documentation
commit. Continue with the final Plan's remaining execution-inventory setting
groups under the user's existing migration authorization.

## Interrupted acceptance, 2026-09-08

Engine `db61bc775a0e30babd202be7ab96a26533d79fad` is active with the private
commit above. The sealed build operation is `fb2dcfa3f83a`. Its 524 Python
tests, sealed Go tests/build, and relevant shell and Ansible syntax checks
passed. The controller-held acceptance directory is:

```text
/home/smith/private/klokast/registry-source-acceptance/db61bc775a0e30babd202be7ab96a26533d79fad
```

Signed adoption completed at `2026-09-08T05:28:46Z`. It changed only the
registry group's source and preserved the original controller identity
adoption reference. All five source groups now use the instance. The source
changed from
`0b34c848ef2008f74259593ce7c869473a16cfa1e4f130b47eb49b556c0d8005`
to `e2ef84b6c703be23e5c74c413799c7c8cde8c4f180f1eed3d24f3feb240eb1de`.
The successful adoption receipt is:

```text
/var/lib/klokast/apply-executions/NB7AQ6VjWIqy3t6eLv8WB-EI/d92b3a6b02975db644e83d4387e8c59f5ff448f55060f2100aab7c4e340af2d7.json
```

`after-adoption-checks.json` records exact source-transition, desired-state
file and metadata, controller-marker, normal registry-reader, and persistent
router-configuration checks. The human interrupted the remaining approval
workflow. Inspection found no receipt for verification Plan
`2e8d0bc3346295d9ca9b6a0cd6f887db75b0cc6d99b1b379ffe5c796a1547828`.
Its preparation nonce `oP-9WS0-17EvMUKhmMg-Qn0Z` was unused and expired;
its temporary runtime copies were removed. Keep these artifacts.

Restarting adoption correctly refuses the changed active source. The original
`prepare-verification` step also refuses to overwrite an existing attempt.
After inspection, run `resume-registry-verification.py` in the directory
above as `smith` on the active controller. It accepts no arguments. It checks
the original helpers, adoption receipt and source transition, copies the
original configuration baselines into a new attempt directory, checks current
settings, and refreshes source, recovery, observation, Plan, and unsigned
preparation evidence. Each invocation creates separate evidence and returns
an `approval_args` object. It does not sign or execute a request.

Pass those seven arguments to the matching MacBook `apply-platform-intent`
helper with `--prove-replay-refusal`. Require action `verify_instance_authority`
and executor `registry_source_v1`. If preparation or approval is interrupted
again, inspect any execution receipt and start fresh verification when needed.
Do not repeat adoption or reuse a signed request. After successful signed
verification and exact replay refusal, repeat the unchanged-setting checks
and store the final Plan before marking acceptance complete.

`interrupted-acceptance-inspection.json` records the inspection and resume
helper hash. Each `resumed-verification-*/` directory retains its copied
baseline hashes, configuration checks, refreshed evidence references, and
unsigned preparation result. This documentation checkpoint requires no
engine promotion or controller-tool installation.

The resume helper passed unsigned live verification in
`resumed-verification-20260908T204536.571713Z/`. Plan
`f1f5855da98458a5dd532dc8a0e05e1fbf4959b54dbbc2e495655465839b624a`
reports all five groups as verification-only. The protected archive check
passed for nonce `IcNLbmT43JW4azi6CirZrUIh`: seven root-owned files, directory
mode `0700`, file mode `0600`, equal complete registry and controller values,
unused nonce, and no runtime copies. The source remained unchanged. This is
unsigned preparation evidence; refresh it when the human starts approval.
