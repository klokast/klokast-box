# Instance-only routine verification

Status: implementation under test. No live acceptance is claimed.
The [design](../../doc/upstream-instance-target-architecture.md#117-instance-only-routine-verification)
owns the dependency audit and gates. Baseline: engine `cf8cf5f`, Authority
State v5 `8622695e`, final Plan `540bae24`. All six groups already use the
instance. This procedure performs no source adoption.

From an isolated public checkout on the active controller, build the reviewed
commit through `platform-builder build-klokast-cli`. Keep the canonical
controller checkout at the active engine during candidate review. Run:

```sh
klokast-dev/bin/prepare-inventory-source-candidate --instance-only \
  --build-dir BUILD_DIR --observation FRESH_OBSERVATION
```

The candidate helper adjusts only the engine metadata in a temporary private
fixture. It compares the retained inventory with the sealed inventory, parses
Plan v8 with the root verifier, and compares inventory, compiler, mapping,
controller resolution, and router-variable consumers in isolated public views.
The absent view has no old inventory tree or legacy YAML inputs. The source
broker replies are sealed projection fixtures in this test. The result is
unsigned review evidence and cannot authorize live execution. All private
outputs remain on the controller. Temporary views are removed.

Test the exact Mac promotion candidate with the existing promotion helper.
Synchronize the canonical controller checkout to the reviewed candidate at
the promotion handoff, as required by that helper. Obtain human engine
promotion, then install the matching controller tools with the existing narrow
installation tags. Do not converge routers, runners, or applications for this
milestone. Create Controller Toolchain v7 with `controller-toolchain-receipt`.

Capture new configuration, private-input metadata, source, controller-marker,
runner, and effective-consumer baselines. Refresh source/recovery receipts and
Observation v1, then store Plan v8:

```sh
ansible/bin/platform-plan --instance-only \
  --build-dir BUILD_DIR --instance /home/smith/private/klokast/instance \
  --observation OBSERVATION --instance-source-receipt SOURCE_RECEIPT \
  --authority-state AUTHORITY_STATE \
  --controller-toolchain-receipt TOOLCHAIN_RECEIPT
```

Plan v8 rejects compatibility input and migration-target flags. It requires
complete Authority State v5 and exact six-group ownership. Its only setting
actions use `instance_verification_v1`. It has no comparison findings,
comparison-input fields, or selected migration target. It retains
`legacy_removal_ready: false` and the limited `standard_substrate_v1` scope.

Use the resulting evidence paths with `platform-apply preflight`. This checks
the installed source brokers, actual Ansible and compiler consumers, both
controller roles, every declared runner, both routers, and rendered/live
Tailnet policy. It rechecks inputs and source after verification. It does not
configure networking, restart services, or repair a failed check.

Use the same arguments with `platform-apply recovery-manifest`. It validates
retained source history and adoption evidence, records exact engine/build and
input references, and copies retained input/evidence files into a temporary
controller-only tree. It verifies every reconstructed hash and removes the
tree. The root-only manifest records credential-reseed procedures; it copies
no credentials and activates no recovered source.

On the trusted Mac, use `apply-platform-intent` with these evidence paths and
`--check` for unsigned review. When preparation passes, use the same helper
with `--prove-replay-refusal`. One Touch ID signature authorizes verification
of all six groups. No adoption signature is required. The executor consumes
the nonce before execution checks. A successful execution stores Receipt v6
with result `verified` and the unchanged Authority State hash. A failure or
incomplete receipt requires inspection and fresh approval; a used request
cannot be retried.

After success, store a fresh Plan v8. Compare settings and source against the
baselines, inspect protected archives and receipts, confirm the consumed nonce,
and check runtime cleanup. Record exact evidence references here. Commit and
push this acceptance update without deploying the documentation-only commit.
Legacy deletion, recovery-source activation, controller switching, runner
retirement, and direct-IPv6 repair remain outside this procedure.
