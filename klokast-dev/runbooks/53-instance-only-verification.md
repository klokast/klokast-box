# Instance-only routine verification

Status: installed; unsigned verification is blocked by two live policy comments.
No signed live acceptance is claimed.
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

The candidate helper runs the exact Python candidate constructor from the Mac
promotion helper. It checks the resulting Git tree and permits only a metadata
change in the temporary private fixture. It compares the retained inventory with the sealed inventory, parses
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

## Installed preparation, 2026-09-09

Engine `1b081298c2d97d33557a41ba51e707e20139c3e3`, sealed build operation
`6b135c2023f8`, is activated at private commit
`b10523b90c43781427c0bf8ea6c74627d90ff1ca`. Matching controller tools and
Controller Toolchain v7 are installed. The complete retained source history,
normal controller resolution, source recovery, 14-host inventory, and declared
runner checks passed. Both router access checks passed; k001 used recognized
DERP. The 547 Python tests and sealed Go tests/build passed before promotion.

Plan v8 `c4c7ad8ec9de125efb5f986a479ea0595b2bf6eb77ed207af26b18ec65a8843c`
is deployable and reports six verification-only groups without compatibility
input fields. Authority State remains
`8622695e9361d281620dc5f69d9b556f2ab637f748899985899d929cf3eaf188`.
The installed recovery operation reconstructed and checked 470 retained files
without credentials or activation. Its protected manifest is:

```text
/var/lib/klokast/instance-recovery-manifests/899223012494c5c159ea175b5edc1e90b01b6a7ede34301bbcdf46778645ce62.json
```

Unsigned preflight refused the live policy byte comparison. Only comment lines
3 and 4 differ. Every other line is identical. Public commit `4015dc8` changed
these comments after the earlier Tailnet acceptance. The live comment still
claims that the API is the source of truth; the template correctly identifies
the public template and private instance. The required template comments are:

```text
// The live Tailscale API is observed runtime state. This public template
// and the private instance are the sources for intended policy inputs.
```

The live policy hash is
`f83add11a151ef8a9beb5d09d16bef1eed78f800ddfe67d03165cbc349e3854a`.
The rendered policy hash is
`67bbf6e4cf51badb07c007f76c94c9ade3080a6a348a37ad5f3521e77a2f05a5`.
Keep the byte comparison strict. At this preparation date, the milestone did
not authorize policy repair. On 2026-09-11 the human authorized repairs and a
Tailnet policy update. The bounded maintenance procedure in
[section 11.7.1](../../doc/upstream-instance-target-architecture.md#1171-authorized-comment-repair-2026-09-11)
corrects the comments separately from the read-only verification executor.

After the refused preflight, private input bytes and metadata, both controller
markers, both router configurations, inventory settings, and runner identity
matched their baselines. No verification signature or execution receipt exists
for this milestone. Legacy removal remains false.

An additional test ran the installed verifier in a private mount namespace.
Its clean sparse public checkout excluded `ansible/inventory/`, and its private
input view excluded all three legacy YAML files. The actual installed brokers,
planning, compiler, inventory, router checks, controller resolver, and mapping
consumer ran in that view. Verification reached the same policy-comment
refusal. This proves no additional legacy-input refusal before that gate; it
does not claim successful full verification. Namespace mounts and temporary
public/runtime views were removed. Production legacy files remained present.
The unrelated runtime directory
`/run/klokast/apply-box/huawei-test-20260830T2204Z` predates this work and was
preserved. It is not a runtime copy from this verification attempt.

Controller-held preparation evidence is below:

```text
/home/smith/private/klokast/instance-only-acceptance/1b081298c2d97d33557a41ba51e707e20139c3e3/
```

`policy-inspection/` contains the root-only live and rendered policy, exact diff,
and comment classification. `unsigned-checks/result.json` records unchanged
configuration. Refresh source/recovery evidence and Observation before retrying;
the recorded Plan is preparation evidence, not a fresh approval request.
`mounted-absence-audit/` records the installed namespace test;
`cleanup-result.json` records cleanup and protected manifest checks.
