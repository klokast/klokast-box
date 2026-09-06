# Controller Box Connectivity Source

This runbook implements the source-only decision in
[section 11.3 of the target architecture](../../doc/upstream-instance-target-architecture.md#113-controller-box-connectivity-source-migration).
Live acceptance is pending. Keep the completed first-box acceptance record.

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
configuration for both routers. Record the SHA-256 hash, mode, owner, and link
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
