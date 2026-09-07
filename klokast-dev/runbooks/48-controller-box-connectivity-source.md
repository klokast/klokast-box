# Controller Box Connectivity Source

This runbook implements the source-only decision in
[section 11.3 of the target architecture](../../doc/upstream-instance-target-architecture.md#113-controller-box-connectivity-source-migration).
Live acceptance completed on 2026-09-07. See the
[acceptance record](#acceptance-record-2026-09-07). The completed first-box
record remains unchanged. The adoption steps below describe the completed
procedure; do not repeat adoption. Later authorized checks use fresh
verification-only Plans and requests.

Run Platform commands as `smith` on the active controller, from
`~/src/klokast/klokast-box`. Run promotion and Touch ID approval on the trusted
MacBook. Keep all private inputs and acceptance evidence on the controller.

The new executor is `controller_box_connectivity_source_v1`. It permits
adoption and verification only. It never configures a router. Direct transport
is preferred; DERP is acceptable. A failed required check stops acceptance.
Do not run rollback, re-adoption, direct-IPv6 repair, or legacy-file removal.

## 1. Build and promote

Run the Python suite, the sealed Go suite/build, and the router and controller
Ansible syntax checks for the reviewed public implementation commit. Use the
existing `platform-builder build-klokast-cli --box BOX --approved-commit COMMIT`
workflow. Then use the
[MacBook engine promotion procedure](40-private-instance-bootstrap.md#14-promote-the-active-engine).
Do not edit the private lock directly.

The syntax-check playbooks are `ansible/playbooks/32-platform-box-access.yml`
and `ansible/playbooks/67-ops-controller-converge.yml`.

After promotion, install the matching tools and keep the returned Toolchain
v3 receipt path:

```sh
ansible/bin/converge-ops-controller --box BOX -- \
  --tags ops-controller-secret-authority-wrappers,ops-controller-apply
ansible/bin/controller-toolchain-receipt --build-dir "$BUILD_DIR"
```

Keep the controller checkout fixed at that engine through acceptance. Confirm
that `klokast-controller-guard --status --json --require-active` identifies the
same active controller as the private instance.

## 2. Keep the baselines

After promotion, create one owner-only evidence directory with `umask 077`.
Record the public and private commits and SHA-256 hashes of these files:

- private `klokast-instance.json` and `klokast.lock.json`;
- legacy `deployment.yml`, `platform-resources.yml`, and `controller-ha.yml`;
- the active source pointer and its immutable Authority State v2 document.

The instance must contain exactly two boxes. The controller box must use
`legacy_platform_resources`; the peer and Tailnet must use
`instance_specification_v1`. Read only the active source document. Do not
convert the state again or change a legacy file.

Use controller-generated Ansible inventory to record persistent router
configuration for both routers. Derive the box IDs from the keys of the
instance's `boxes` object. When combining the base and generated inventories,
set an explicit Ansible `--limit` for those routers. The base inventory also
contains example hosts. Record the SHA-256 hash, mode, owner, and link
target or absence for the network configuration, dhcpcd, dnsmasq, nftables,
sysctl, managed network helpers, and service runlevel links. Keep the exact
same file selection for the final comparison. Do not compare packet counters,
address lifetimes, latency, or generated leases as persistent configuration.
These measurements change during normal traffic.

## 3. Refresh evidence and select the controller box

Refresh evidence when the human is ready. Source receipts and observations
expire after 30 minutes; the approval request expires after ten minutes.
Use the existing [source synchronization and recovery steps](45-tailnet-authority-pilot.md#2-create-fresh-evidence-and-plan-v2),
then refresh the map and export a new owner-only Observation v1. Complete the
[read-only acceptance gate](../../doc/upstream-instance-target-architecture.md#completed-read-only-acceptance-alignment-design)
for the promoted engine. Keep each attempt in a separate evidence directory.
Never replace evidence that an earlier Plan or signature used.
Use the repository identity from the authenticated source receipt when
refreshing source evidence. Do not infer it from an assumed Git origin URL
format.

Resolve the active state path and create Plan v4:

```sh
AUTHORITY_STATE="/var/lib/klokast/authority-states/$(sed -n '1p' /var/lib/klokast/active-authority-state).json"
ansible/bin/platform-plan \
  --connectivity-target active-controller \
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

Require a deployable Plan with no refusal. Its selected box must host the
active controller. The selected group must contain exactly five scopes and
use `controller_box_connectivity_source_v1` with `no_mutation` as its rollback
type. The peer and Tailnet groups must report verification-only. Unselected
box actions have executor `none`.

## 4. Approve adoption, then verify

On the trusted MacBook, run unsigned preparation:

```sh
klokast-dev/bin/apply-platform-intent \
  --controller BOX-ops \
  --plan PLAN --authority-state AUTHORITY_STATE \
  --controller-toolchain-receipt TOOLCHAIN_RECEIPT \
  --source-recovery-receipt SOURCE_RECOVERY_RECEIPT \
  --instance-source-receipt SOURCE_RECEIPT \
  --observation OBSERVATION --build-dir BUILD_DIR --check
```

Preparation must compile equal old and effective inputs and verify the
controller-site router through the effective input. Require authenticated
access, configuration, service, route, firewall, and recognized reachability
checks. The request must have kind
`klokast.controller-box-connectivity-source-intent.v1`, action
`adopt_instance_specification`, and the expected controller identity.

Run the same helper without `--check`. Wait for the text prompt, type
`approve platform apply`, then approve Touch ID. Require a successful receipt
and one forward Authority State v2 record. Only the controller box source can
change. The desired-state and router baselines must remain equal.

Create fresh evidence and a new Plan with `--connectivity-target
active-controller`. The selected operation must now be
`verify_instance_authority`. Run the helper with `--check`, then with
`--prove-replay-refusal`. Require a `verified` receipt and the exact replay
refusal `Apply intent nonce was already used`.

Execution consumes its nonce before live verification. A failed check needs
a new preparation and signature. If publication succeeds but receipt storage
fails, the operation is incomplete. Inspect the active state and execution
evidence, then create a fresh verification Plan and request. Do not retry the
signed request, claim recovery, or change the network.

## 5. Complete the record

Store a final Plan with the same promoted engine and fresh evidence. Both
connectivity groups and Tailnet must be verification-only. Require unchanged
private desired-state files, legacy files, controller identity, and persistent
router configuration. The active source must remain the adoption result after
signed verification. No rollback exercise or waiting period is required.

Only then mark this milestone `live-verified`. Record the implementation
commit, sealed build operation, toolchain receipt, baseline directory,
adoption receipt and source transition, verification receipt, replay refusal,
and final Plan using controller-held references. Commit and push the final
documentation. Do not automatically deploy that documentation-only commit.

Read the final Plan's actions and continuing authorities. Report the remaining
legacy-owned setting groups, including legacy fields that have only an
unimplemented adoption action. Use that list for the next migration decision.
This procedure does not authorize another setting group or legacy removal.

## Acceptance record: 2026-09-07

Status: `live-verified`. The active controller remained `k002-ops`. The human
promoted and activated public engine
`cc68fc6c047c3ee8c40aebb63df2d3d40ac5e93c` with private commit
`70ab0b206deb89e6350df9d68d62e6b762ea2cd1`. Matching controller tools were
installed before the input and router baselines were taken.

All 507 Python tests passed. The sealed Go suite and binary build passed for
that exact implementation commit. Shell and both Ansible syntax checks
passed. Sealed build operation `a5661a97bc0a` completed cleanup with no
remaining resources. Its controller-held directory is:

```text
/var/lib/klokast/builds/klokast-cli/cc68fc6c047c3ee8c40aebb63df2d3d40ac5e93c/a5661a97bc0a
```

The activation and matching Controller Toolchain v3 receipts are:

```text
/var/lib/klokast/engine-activations/70ab0b206deb89e6350df9d68d62e6b762ea2cd1/4e3b5f9572f1b13903f59c7a5a3522663d7f8ec63f5cc6dfce27f13d67eecdcc.json
/var/lib/klokast/controller-toolchains/cc68fc6c047c3ee8c40aebb63df2d3d40ac5e93c/00170c0492c80796dd6f115ee6b3e9e59398ea2269c4d8c9e8315304f9cc0ca0.json
```

Unsigned preparation passed before each signed action. At
`2026-09-06T23:56:31Z`, adoption completed with result `success` and recovery
`not_needed`. One forward Authority State v2 record changed only
`box-connectivity-v1:k002` from `legacy_platform_resources` to
`instance_specification_v1`. The k001 and Tailnet sources and all group
scopes remained unchanged. The prior and resulting state documents are:

```text
/var/lib/klokast/authority-states/6cdc48ae5cf3027774b654d8dc1cc41f256172e70ceca882e9b529e608a27a23.json
/var/lib/klokast/authority-states/312370d38e672aea648fcc3d278ad41ce4383441d12fa5b66dad8420682d4aef.json
```

At `2026-09-07T00:00:02Z`, fresh signed verification completed with result
`verified` and recovery `not_needed`. The active source remained the adoption
result. The MacBook helper confirmed that the exact signed verification
request was refused with `Apply intent nonce was already used`. The adoption
and verification receipts, respectively, are:

```text
/var/lib/klokast/apply-executions/rFeyv1KT10EBKSr8_5cDWPOS/ef677410a37fcfdcf330660baac3addd613d6be8abbe9a05c953676b1c71170a.json
/var/lib/klokast/apply-executions/SgQVdI5pqrT9ihbEiOgQHBoE/8d25f505e65827814e04effb08f5931ee90a3bf1a87fdcc9a2a3d461a554dd23.json
```

Controller inspection verified receipt hashes, source linkage, protected
intent and input archives, and both consumed nonce bindings. The readable
execution and post-verification runtime copies were removed. Old and
effective compiler outputs and selected-router variables were equal under
the approved comparison rules. Authenticated router configuration, service,
route, firewall, and recognized reachability checks passed. The executor
issued verification commands only.

The final active-controller Plan is valid, compatible, healthy, deployable,
and authority-ready, with no refusals. Both box connectivity groups and
Tailnet are verification-only. A second final Plan checked the default
non-controller target with the same fresh evidence and the same group
dispositions. Adopted, unselected box actions have executor `none`. The Plans,
respectively, are:

```text
/var/lib/klokast/plans/70ab0b206deb89e6350df9d68d62e6b762ea2cd1/783d1dc6c5afe1f762faf8d419029fe159936289a09f05f4a9064606d28b4570.json
/var/lib/klokast/plans/70ab0b206deb89e6350df9d68d62e6b762ea2cd1/66d302a5c42477734c624c830e5d9df294ba533b4e154ac64c7e96522f3212a3.json
```

Private acceptance evidence remains on the controller under:

```text
/home/smith/private/klokast/controller-source-acceptance/cc68fc6c047c3ee8c40aebb63df2d3d40ac5e93c/a5661a97bc0a
```

Paths in this table are relative to that directory.

| Evidence | Contents |
| --- | --- |
| `acceptance-result.json` | Completed result, final Plans, source hash, and remaining legacy scope count. |
| `implementation-checks.json` and the syntax/build logs | Repository and sealed implementation validation. |
| `input-baseline.json` | Post-promotion commits and hashes of the five desired-state files, active pointer, and prior immutable state. |
| `baseline/`, `after-adoption/`, `final/` | Router snapshots and read-only Ansible results for each comparison phase. |
| `acceptance-programs.json` | Verified hashes of the controller-local evidence helper scripts. |
| `adoption-20260906T234901Z/` | Fresh source, recovery, observation, and Plan evidence for signed adoption. |
| `verification-20260906T235645Z/` | Fresh evidence for signed verification and replay refusal. |
| `signed-execution-verification.json` | Receipt and source checks, nonce bindings, archive protection, runtime cleanup, and the human's replay confirmation. |
| `final-20260907T000145Z/` | Final evidence, both planner targets, and `remaining-legacy-settings.json`. |

All five desired-state files and the prior immutable source document retained
their baseline hashes. All 138 persistent configuration entries on each
router matched across the baseline, after-adoption, and final snapshots.
Network values, router configuration, controller identity, applications, and
data were unchanged by this source-only operation. No router configuration,
service restart, or gateway repair was part of acceptance.

Live rollback, re-adoption, direct-IPv6 repair, and legacy-file removal remain
deferred. The acceptance documentation is committed and pushed separately;
the controller remains on the verified implementation commit. This
documentation update is not deployed.

### Remaining legacy-owned settings

The final Plan has 27 actions for scopes that remain legacy-owned, including
adoption actions whose executor is still `unimplemented_action`. It also
retains `legacy_engine_inventory`. The controller-held
`final-20260907T000145Z/remaining-legacy-settings.json` lists the exact scopes
and continuing authorities. The areas for the next migration decision are:

- Controller selection and HA settings: the selected controller, controller
  box and hostname entries, remote account, and repository path.
- Box settings: bridge ports, DHCP reservations, and shared guests.
- Application settings: configuration and enablement, data intent, device
  bindings, runtime state, resource requests, and placement.
- Legacy schema metadata and execution inventory.

These are review categories, not approved executor groups. The final Plan
sets `legacy_removal_ready` to `false`. A scope with no previous authority is
not a legacy-owned scope. The
[current work queue](../../doc/upstream-instance-target-architecture.md#current-work-queue)
owns the next decision; this acceptance grants no further migration or
removal approval.
