# Registry source adoption

Status: `implemented`; live acceptance is pending. The bounded decision is in
[the target architecture](../../doc/upstream-instance-target-architecture.md#registry-source-adoption).
The [compatibility checkpoint](50-registry-migration-checkpoint.md) is active.
Its acceptance record is complete and remains the rollback-checker baseline.

Engine `e05a713` was promoted and private commit `317af096` published the exact
checked registry candidate. Matching Toolchain v5, both controller markers,
and router configuration baselines passed. The first unsigned preflight refused
an unrelated pending airunner action because of a validator vocabulary mismatch.
No source transition or signed execution occurred. Promote the corrected
validator before continuing. Preserve this controller-held attempt:

```text
/home/smith/private/klokast/registry-source-acceptance/e05a713cd32f859d75858ad1943f4ef88edb80f3
```

The directory contains publication synchronization, input and router baselines,
the initial Plan, and the refusal in `initial-preflight/`. New acceptance must
use the corrected engine, matching installed tools, and fresh evidence.

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
