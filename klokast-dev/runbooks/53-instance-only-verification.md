# Instance-only routine verification

Status: Plan v8 signed verification and exact replay refusal passed on
2026-09-12. The separate Plan v9 removal exercise passed on 2026-09-13.
Signed final retirement and signed retired-state verification passed on
2026-09-14. See the [acceptance record](#retirement-and-verification-2026-09-14)
for final evidence and the replay-message reporting limitation.
The [design](../../doc/upstream-instance-target-architecture.md#117-instance-only-routine-verification)
owns the dependency audit and gates. The historical Plan v8 acceptance used
engine `1b08129` and Authority State v5 `8622695e`. All six groups use the
instance. The Plan v8 procedure below performs no source adoption and cannot
authorize retirement. Its absence evidence covers the named consumers, not
every supported app-controller command or application health.

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
matched their baselines. At this preparation checkpoint, no verification
signature or execution receipt existed for this milestone. Legacy removal
remained false.

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

## Authorized repair, 2026-09-11

The human permitted repairs and authorized the Tailnet policy update. Public
maintenance commit `05b0aa8` supplies
`ansible/playbooks/69-repair-policy-comments-20260911.yml`. It ran from the
isolated controller review checkout, using the installed renderer, input
validator, active-controller guard, and credential broker. The canonical
checkout, activated engine `1b08129`, private commit `b10523b`, and installed
Toolchain v7 remained unchanged. This maintenance procedure installs no tool.

Preparation passed with `policy_comment_mode=prepare`, then the separately
authorized `execute` mode completed one conditional policy POST. Only the
two comments recorded above changed. Exact API read-back matched rendered
hash `67bbf6e4`; Authority State stayed `8622695e`. This is a maintenance
result under the explicit task authorization, not a signed Apply receipt.
Do not repeat the repair: the protected attempt directory forbids another
write, including after an uncertain attempt.

Protected root-only evidence is in:

```text
/var/lib/klokast/policy-comment-repair-20260911/
```

It contains the exact preimage, candidate, API read-back, ETags, Plan input
references, attempt record, and maintenance result. Files are root-owned
`0400` within a `0700` directory. The runtime copy was removed.

Fresh controller-held preparation evidence is in the `20260911/` child of
the acceptance directory above. `repair-prepare.log`, `repair-execute.log`,
and `repair-audit/` record the operation and protected-file checks.
Before/after comparisons passed for private input bytes and metadata,
controller markers, router configurations, all 14 inventory hosts, and the
declared runner identity. The maintenance candidate passed all 558 Python
tests and its Ansible syntax check. The activated engine's sealed Go tests
and build remain those of operation `6b135c2023f8`; no Go code changed.

The [policy ownership decision](../../doc/upstream-instance-target-architecture.md#1172-tailnet-policy-file-ownership)
defines the template and instance as authored inputs, rendered HuJSON as a
private generated artifact, and API output as observed state. General policy
updates still need a closed signed action; this repair does not add one.

Normal installed preflight passed for all six verification-only groups using
Plan v8 `b7f402a4613a193cfb914ce1a90b82ad006fa8ce1711e9cd9a0fdaee1409bf9a`.
The protected recovery manifest
`f03fa4232a392f7803b7d74b5b9dbaeb3368bd9bb4787a970510d60bee016062`
reconstructed 474 retained files. The maintenance archive also contains a
`recovery-supplement.json` with hash
`8f6570d41641f4d961b90f71222541ed9fd4a362ec0dcace097867ae2aea5f4e`.
It binds that instance manifest to the maintenance commit and ten checked
repair artifacts, including the procedure source and policy preimage. Its
separate temporary reconstruction passed. No credentials were copied and no
recovery source was activated.

The installed verifier then passed in a private mount namespace with all three
legacy YAML files and the old inventory tree absent. Its complete verification
evidence matched the normal preflight exactly. Controller resolution and the
actual mapping consumer also matched; the mapping hash remained `e586c915`.
`mounted-absence-audit/` records this successful check. Production legacy inputs
remained present, and the namespace and temporary public view were removed.
`completion-audit.json` records final input, protected-evidence, and cleanup
checks. The unrelated August Huawei runtime directory remains preserved.

At this checkpoint, signed verification and exact replay refusal were pending. The
unsigned preparation had no execution receipt; its later signed intent is
permanently consumed after the failed revalidation below. Use fresh evidence
with the existing trusted-Mac helper; do not treat this maintenance result or
the unsigned checks as signed acceptance. After successful signing, complete
the receipt, nonce, final Plan, and unchanged-state checks above, then record
acceptance in a documentation-only commit without deploying it.

## Signed attempt expired during revalidation, 2026-09-11

The trusted-Mac helper signed the fresh intent, but execution stopped at
`Plan v3 inputs changed during exact revalidation`. The verifier had already
consumed nonce `e2VJO5AZI1DRyn0UcbTe9AH2`, so that intent must never be retried.
The controller audit found no execution receipt, no authority transition, and
the unchanged Authority State hash `8622695e`. No Platform mutation occurred.
Create a new Observation, source and recovery receipts, Plan v8, and approval
before another signed attempt.

## Signed acceptance, 2026-09-12

Fresh source and recovery receipts, Observation, and Plan v8 passed unsigned
preflight. The Plan hash was
`b3316b4fb9f911b18928c3d318a696a1707422a97c37341995ec6bb56d7fd5f8` and it
contained six verification-only groups. The isolated absence check also
passed with the legacy YAML inputs and old inventory tree unavailable.

The trusted Mac signed the new intent. The installed executor completed all
six verification groups and stored Execution Receipt v6:

```text
/var/lib/klokast/apply-executions/BAvO1ifehW6hIbtCP2d94yCJ/e78bc4948c1d490ef65b0cce63ae013f8e3d635c8e13132967d4a988fed66dc6.json
```

Receipt hash: `e78bc4948c1d490ef65b0cce63ae013f8e3d635c8e13132967d4a988fed66dc6`.
Authority State remained
`8622695e9361d281620dc5f69d9b556f2ab637f748899985899d929cf3eaf188`.
The nonce was consumed and the helper confirmed exact replay refusal. The
recognized DERP notice for the k001 router was informational; access checks
passed and direct transport is optional.

The signed receipt is root-owned and canonical. No Authority State record,
network configuration, service restart, application repair, or legacy-input
deletion occurred. `legacy_removal_ready` remains false. The remaining work is
the separate recovery and deletion gates recorded in section 11.7; this
milestone is live-verified for the consumers tested: installed source readers,
inventory, compiler, mapping, controller resolution, router checks, and Tailnet
verification. It did not test every supported app wrapper with inputs absent.

## Legacy retirement exercise, 2026-09-13

The exercise used engine `079019624ad811978a761ba3889401ec845d5f60`, with private
commit `909699ab8db34e3ead708587f70164e8dca2cdae` and matching Controller
Toolchain v8. Plan v9 adds the closed `legacy-input-retirement-v1` lifecycle
group to six verification-only source groups. Instance Specification v1 and
Authority State v5 are unchanged. Plans v1–v8 cannot authorize retirement.

The trusted Mac signed the exercise. Execution Receipt v7 reports
`exercised`, with `recovery_result: restored`, at `2026-09-13T05:45:05Z`:

```text
/var/lib/klokast/apply-executions/5nTqjywn-5bO9tTTGiItL18_/bd20bc19836d33aabc42a470102e2db3f8f14114d6340337b1a1b7988c6349ed.json
```

The receipt hash and restored input bytes, ownership, modes, sizes, and link
counts were checked on the controller. The three approved backups also matched
their recorded metadata. The human confirmed refusal of the exact signed
replay. Authority State remains
`8622695e9361d281620dc5f69d9b556f2ab637f748899985899d929cf3eaf188`.
The exercise Plan retains `legacy_removal_ready: false`:

```text
/var/lib/klokast/plans/909699ab8db34e3ead708587f70164e8dca2cdae/376ad6cec0b9dbbd3776c71fc26649d1576d2e27b96ed08e6b3e452f160aaeb4.json
```

The protected recovery archive contains the exact three live input files,
not the obsolete backups:

```text
/var/lib/klokast/legacy-input-recovery/a084037311ebf953e13f311af23fa7476446f531835645f0ac0a4a236bc4d032
```

After restoration, fresh source and recovery receipts, consumer comparison,
and Observation refresh passed. The effective settings hash stayed
`6aa99282bf52ef16dc219e046a8e73ffbd4b62f998c6b98890f3ce55f16a1672`.
The Observation was refreshed at `2026-09-13T05:54:45Z`. The controller holds
the fresh comparison under
`/home/smith/private/klokast/evidence/retire-consumers.eOeMDN/` and the
Observation at
`/home/smith/private/klokast/evidence/retirement-final-observation.json`.

Coverage limit: `compare-instance-input-absence` records app compiler views,
or declared absence, under the nine app names. It does not invoke those nine
app wrappers. Its `platform-check` entry reuses box-config output, and its
Tailnet entry compares projected inputs rather than invoking the renderer.
These entries must not be treated as complete wrapper execution evidence.
Direct status, verify, approved-runtime-apply, and legacy-write-refusal tests
still need complete absence coverage before final retirement approval.

### Additional backup scope refusal

Fresh `retirement-evidence --phase retire` refused with
`another platform-resources backup exists; review the exact retirement set`.
No final-retirement evidence or signature was produced. No live input or
backup was removed by this preparation. The final deletion allowlist remains:

- `platform-resources.yml.20260611T010611Z.bak`
- `platform-resources.yml.20260611T011900Z.bak`
- `platform-resources.yml.20260611T015423Z.bak`

The following nine additional files existed in `/home/smith/private/klokast`
at the refused preparation. Their later authorized deletion is recorded below.
All were regular files with one link and owner/group `1002:1002` at inspection.
This list records evidence; it does not authorize deletion or relocation.

| Additional file | SHA-256 |
| --- | --- |
| `platform-resources.yml.20260611T020145Z.bak` | `ebb17e0e3a4c87350219246b95136662762bfd7356a321596e5562eb14fd3da4` |
| `platform-resources.yml.20260611T073643Z.pre-k001-reboot.bak` | `d0d289b6b877ca79a5637a65c3faaf6472cea2546c344174985a36f00de265a7` |
| `platform-resources.yml.20260611T091728Z.bak` | `40f7cb2c5f8856a0a94a62c5d1363bd5ad545ff41519f848856379ee0ac297cc` |
| `platform-resources.yml.20260719T075235Z.bak` | `15e21c63dce00ba5ffda5420238dc47b6bf2a828f95e837642b6b0368806ad1f` |
| `platform-resources.yml.20260719T080153Z.bak` | `f51f618f853aff02f882a76fc93ad8497245170e0812e90a27dc12787de340cf` |
| `platform-resources.yml.20260808T110239Z.bak` | `f51f618f853aff02f882a76fc93ad8497245170e0812e90a27dc12787de340cf` |
| `platform-resources.yml.20260820T152236Z.bak` | `d265a35661d9e93d16e18302061991b83ea5bd5b120eafb1e1f94bfdc438090e` |
| `platform-resources.yml.20260823T235503Z.elementary-connectivity.bak` | `9acca978533a92022a4648c8333699332ce9a7cc8e19c7516f03b78792acc64d` |
| `platform-resources.yml.pre-ap-20260608T053746Z.bak` | `6eafeb8c30194b42fc431729d26641fae876a7f9891b44b03e255c064c26242d` |

Do not move these files to bypass the guard or expand its allowlist without
human approval. The exercise does not check for extra backup names; final
preparation does. Its success therefore does not establish final removal
readiness. Follow the [current work queue](../../doc/upstream-instance-target-architecture.md#current-work-queue).
This record is a documentation-only checkpoint, not final retirement acceptance.

### Authorized extra backup deletion, 2026-09-13

After reviewing the nine-file list above, the human explicitly authorized
permanent deletion instead of a separate recovery archive. Maintenance commit
`ed2dbf468b5694fbd5feb6997a3f6d97093650b0` supplies
[`69-delete-extra-registry-backups-20260913.yml`](../../ansible/playbooks/69-delete-extra-registry-backups-20260913.yml).
This is separate task authorization, not a signed Apply intent or permission
to delete the three live inputs. The signed retirement backup allowlist is
unchanged.

The maintenance task ran from an isolated public checkout on the active
controller. Preparation checked all nine exact names, hashes, ownership,
modes, sizes, and link counts before deletion. Execution held the installed
authority-publication lock, repeated the checks, and removed only those nine
files. No backup contents were archived. Deletion is permanent; the maintenance
record cannot restore these files.

The post-deletion audit confirmed that all nine paths are absent and that
only the original three approved backups match the backup pattern. Those
three backups and the three restored live inputs still match the signed
exercise receipt. The immutable recovery archive passed validation before and
after deletion with the same hash `a0840373`. Authority State remains
`8622695e`. The canonical checkout, active engine `0790196`, private commit,
and installed tools were not changed.

Root-owned metadata-only evidence is in:

```text
/var/lib/klokast/extra-registry-backup-deletion-20260913/
```

The directory is `0700`; its twelve audit files are `0400`. `before.json`
records exact target and preserved-file metadata, `attempt.json` records the
human task authorization, nine per-file records identify completed deletions,
and `result.json` reports `deleted`. A prior attempt prevents automatic retry.

All 582 Python tests passed, with two Ansible-dependent tests skipped on the
runner. The eight focused maintenance tests also passed on the controller,
as did Ansible syntax validation and live preparation. The extra-backup scope
blocker is resolved. Complete direct consumer absence coverage and the
separate signed retirement and verification gates remain required.

## Retirement and verification, 2026-09-14

Engine `85f351ffd1d5fc7b91f77817ddc88bd1f9cce70f`, sealed build operation
`459ddc9133c5`, is active at private commit
`26f4fb0e552db8b922d7f77cfb66095e7a0db726`. Matching Controller Toolchain v8
is installed. Authority State v5 remains
`8622695e9361d281620dc5f69d9b556f2ab637f748899985899d929cf3eaf188`.

The human signed final retirement and then a separate retired-state
verification. Both Receipt v7 hashes, protected intent hashes, root ownership,
`0440` receipt modes, consumed nonce records, and runtime cleanup passed
controller inspection. The two operation runtime trees are absent.

| Operation | Finished (UTC) | Receipt |
| --- | --- | --- |
| Final retirement | 2026-09-14 07:08:13 | `/var/lib/klokast/apply-executions/e2Q4Y_yMo4KR9C73veKz44Wa/54072c3910af44568fa104994ed47189a62c705fe25fd1d60b9b9657a441c1a7.json` |
| Retired-state verification | 2026-09-14 07:41:56 | `/var/lib/klokast/apply-executions/e0yU2p33WUcO1i5ZE-RxkAKe/2bbd91bb1c26043468dc8327892e33de55dcc96d1051230517560fe02cf4a25a.json` |

The three live YAML paths and the original three approved backups are absent,
with no links at those paths and no remaining matching registry backup.
The backups were permanently deleted. The three live input files remain
recoverable from the protected archive `a0840373` recorded above. The installed
archive validator rechecked its three exact files and metadata after signed
verification; the archive directory remains root-owned `0700`, with root-owned
`0400` archive files. `dom0-console.yml`, `platform-users.yml`, and
`openclaw-vpn.yml` remain present. Application data is outside the deletion
scope. Source history, adoption evidence, policy recovery material, Plans,
receipts, and audit logs remain required recovery inputs.

The direct command matrix now invokes all nine supported app wrappers,
`platform-check`, inventory parsing, mapping, compiler, Tailnet rendering,
and controller resolution. Both absent-entry and declared-app fixtures passed
on the controller, including repeatability. Approval, image build, source
broker, and remote app-runtime boundaries are explicit fixtures. This proves
controller dependency closure and dispatch from approved intent; it does not
claim live application installation or application health. Live signed
verification separately checked adopted source, routers, policy, controller
roles, runner identities, and effective settings. Recognized DERP is accepted.

The post-retirement matrix is at
`/home/smith/private/klokast/evidence/verify-retired-consumers.PhIEFD/output/result.json`.
Its hashes match the pre-retirement matrix:

- Effective settings: `6aa99282bf52ef16dc219e046a8e73ffbd4b62f998c6b98890f3ce55f16a1672`.
- Command matrix: `49dc1d06877b35f473bc43508e2fcfce158f4baa52fecc9ca1857f675f8fbaff`.
- Complete matrix file: `08591b9e8148e83b91585a7c042b8e8ff5a8e4e7fd1900a6f466eaf9da240b34`.

Fresh source synchronization and source recovery followed signed verification.
Final Plan v9 is stored at:

```text
/var/lib/klokast/plans/26f4fb0e552db8b922d7f77cfb66095e7a0db726/e30552a5b47a14698e161b437b2352a65dad362a2ddeb46a9e75579cac14c264.json
```

It is valid and deployable at creation, requires `legacy_removal_ready: true`,
keeps six source groups verification-only, and selects only
`verify_legacy_retirement` for the lifecycle group. Both legacy-input and
approved-backup states are `absent`. The immutable verify evidence is
`/var/lib/klokast/legacy-input-retirement-evidence/1513771f821e1b4ed2dac2f6fb10022378bd28bc4179162ae26b693e0133f4c7.json`.
Final source receipt `148ccb68` and recovery receipt `b9d99a3e` are stored
under their standard roots for the same private commit. The final Plan is
read-only reporting evidence; the signed verification receipt above binds
Plan `346bde9f`.

The 585-test Python suite passed, with three environment-dependent skips.
Both controller absence fixtures and 22 focused controller tests passed.
Sealed vendored Go tests and build passed with Go 1.24.13. Relevant Ansible
syntax and shell syntax checks passed; shellcheck retained its existing
warnings. Exact private candidate comparison passed before promotion.

### Replay reporting limitation

Both Mac helpers submitted the exact signed request again after successful
execution. Both replay commands returned failure, but the helper reported
`controller returned an unexpected replay refusal`. This establishes refusal,
not the helper's expected nonce-specific diagnostic. The helper removed its
temporary replay stderr on exit, so the original error text was not retained.

The protected retirement intent expired at `07:05:39Z`, before execution
finished at `07:08:13Z`. The verification intent expired at `07:38:46Z`,
before execution finished at `07:41:56Z`. Both nonces were consumed before
expiry and bind their exact Plans. The installed validator checks expiry
before signature and nonce reuse; read-only validation of both stored intents
reproduces `legacy retirement intent is expired or has an invalid lifetime`.
Expiry is therefore the supported explanation for the message mismatch.
Do not report these two runs as live nonce-specific replay tests. The earlier
exercise did pass that exact diagnostic. Preserve the ten-minute approval
lifetime and single-use nonce rule; do not retry deletion to test reporting.

The remaining reporting improvement is tracked in
[the difficulty record](../../doc/todo.md#2026-09-14---successful-retirement-replay-reporting-and-evidence-freshness).
This acceptance update is documentation only. Do not deploy it. Keep engine
`85f351f` active. Plans v1–v8 and `ansible/inventory` remain explicit
compatibility, bootstrap, recovery, test, and historical inputs, never normal
fallback. Recovery-source activation, controller switching, runner retirement,
direct-IPv6 repair, and application-data deletion remain outside this milestone.
