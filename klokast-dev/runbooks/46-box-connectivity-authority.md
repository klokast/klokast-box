# First Box Connectivity Authority

Use this runbook for development acceptance of the first `box_connectivity_v1`
source change. Verify the existing instance-owned settings. The action
selects the unique box that does not host the active controller. It
must not operate on the controller box, applications, shared guests, or
Tailnet policy.

Run Platform commands as `smith` on the active `<box>-ops` controller from
`~/src/klokast/klokast-box`. Run approval commands on the trusted MacBook.
Do not run Platform commands on an infra-agent.

Direct and DERP connections are both permitted for this source-change
workflow, including rollback. Successful checks print an informational
notice when the controller-to-router probe uses DERP. The notice does not
mean that subsequent traffic must use the same route. Authenticated Ansible
access and all router configuration checks remain required. A timeout or
failed verification still stops the operation. Do not run the separate
direct-IPv6 repair just to satisfy this source-change workflow.

## 1. Prepare The Controller

Check the active controller, clean public checkout, private engine lock, and
engine activation with the existing controller status workflows. Use the
already built and activated engine. If the human authorizes repair of a code
defect found during acceptance, first use the existing
[engine promotion workflow](40-private-instance-bootstrap.md#14-promote-the-active-engine)
for the corrected commit. Then start a new acceptance attempt. Keep the
controller checkout and selected engine fixed until the final Plan is stored.

Install the matching wrappers and create a Toolchain v3 receipt:

```sh
ansible/bin/converge-ops-controller --box BOX -- \
  --tags ops-controller-secret-authority-wrappers
ansible/bin/controller-toolchain-receipt --build-dir "$BUILD_DIR"
```

Use the selected engine's existing sealed build directory as `BUILD_DIR`.
Keep the reported path as `TOOLCHAIN_RECEIPT`. The receipt must verify every
required installed component against that engine. Stop on any unexplained
mismatch.

On the controller, run the Ansible syntax check before you create evidence.
Set the repository-local configuration and role path explicitly:

```sh
ANSIBLE_CONFIG="$PWD/ansible/ansible.cfg" \
ANSIBLE_ROLES_PATH="$PWD/ansible/roles" \
  ansible-playbook --syntax-check ansible/playbooks/32-platform-box-access.yml
```

Run the existing Python test suite on the public implementation checkout:

```sh
python3 -m unittest discover -s ansible/tests -p 'test_*.py'
```

Reuse the successful sealed build receipt and log for the unchanged engine.
That build runs `go test -mod=vendor -buildvcs=false ./...` inside the pinned
builder image. The controller does not need a separate Go installation.

## 2. Check The Existing Setting Sources

Read the active state path on the controller:

```sh
AUTHORITY_STATE="/var/lib/klokast/authority-states/$(sed -n '1p' /var/lib/klokast/active-authority-state).json"
```

Require Authority State v2. The selected box and Tailnet must use
`instance_specification_v1`; the controller box must use
`legacy_platform_resources`. Do not repeat the completed authority conversion
or initial adoption.

Keep owner-only baseline hashes on the controller for the active-state marker,
its immutable state file, both private instance JSON files, and the legacy
deployment, resource, and controller HA files. Record the public and private
checkout commits. Compare them again after signed verification. Keep the
existing rollback inputs and all prior evidence.

## 3. Create Plan V3

Prepare this evidence when the human is ready to run the MacBook approval.
The source receipt and observation each expire after 30 minutes. A successful
earlier preflight does not extend that time. For a delayed handoff, run the
existing controller preparation commands from the MacBook remote terminal
immediately before `--check` and `--prove-replay-refusal`. Do not hand over
fixed evidence paths for an approval at an unknown later time.

Use section 2 of [the Tailnet pilot](45-tailnet-authority-pilot.md) to
synchronize the private source, prove source recovery, refresh the Platform
map, and export a fresh observation. Then run `platform-plan` with the active
Authority State v2 and
Toolchain v3 receipt:

```sh
ansible/bin/platform-plan \
  --build-dir "$BUILD_DIR" \
  --instance ~/private/klokast/instance \
  --compatibility-deployment ~/private/klokast/deployment.yml \
  --compatibility-registry ~/private/klokast/platform-resources.yml \
  --compatibility-controller-ha ~/private/klokast/controller-ha.yml \
  --observation "$OBSERVATION" \
  --instance-source-receipt SOURCE_RECEIPT_PATH \
  --authority-state "$AUTHORITY_STATE" \
  --controller-toolchain-receipt TOOLCHAIN_RECEIPT
```

Keep each attempt in a separate private evidence directory. Keep its source
receipt, recovery receipt, observation, Plan, and approval arguments together.
If evidence expires, create a new set with the same engine and unchanged
baseline. Do not alter timestamps or replace an observation used by an old
Plan. Both MacBook calls must use the new set. The Apply intent also has its
own ten-minute approval limit.

The Plan must be valid, deployable `klokast.plan.v3`, with no refusal. It must
select the unique box that does not host the active controller. Its action
group must contain exactly five scopes, use `box_connectivity_v1`, and select
`verify_instance_authority`. The Tailnet group must also be verification-only.
The controller box must retain the old registry. All unrelated changes must use
`unimplemented_action`.

## 4. Check And Verify One Box

On the trusted MacBook, run:

```sh
klokast-dev/bin/apply-platform-intent \
  --controller BOX-ops \
  --plan PLAN --authority-state AUTHORITY_STATE \
  --controller-toolchain-receipt TOOLCHAIN_RECEIPT \
  --source-recovery-receipt SOURCE_RECOVERY_RECEIPT \
  --instance-source-receipt SOURCE_RECEIPT_PATH \
  --observation OBSERVATION --build-dir BUILD_DIR --check
```

The preflight compiles the unchanged registry and the instance-derived
effective registry. It ignores only `registry_path` and `registry_sha256` in
the comparison. It also runs the selected router action in Ansible check mode.
It does not sign or change the source record.

Run the same command with `--prove-replay-refusal` in place of `--check`.
Review the selected box, five scopes, `verify_instance_authority` action, and
all input hashes. Approve this one request with Touch ID. Require a `verified`
execution receipt, followed by refusal of the exact repeated signed request
with `Apply intent nonce was already used`.

The verification action checks only `<selected-box>-router`. Require
authenticated access and the existing hostname, configuration, service,
route, and firewall checks. Direct traffic is preferred. A DERP notice is
information and does not block acceptance.

Compare all baseline hashes and checkout commits. The setting-source record
and desired-state files must remain unchanged. Create a fresh observation
and final Plan v3 with the same engine. The selected box and Tailnet must
remain verification-only; the controller box must retain the old registry.
There is no fixed observation or waiting period.

If a required check fails, stop acceptance and record the failed check in
[`doc/todo.md`](../../doc/todo.md). Do not change network settings, restart
Tailscale, or run IPv6 repair to force success.

Only after all checks pass, mark the first-box milestone `live-verified` in
the [target architecture](../../doc/upstream-instance-target-architecture.md#111-first-box-connectivity-source-migration).
State explicitly: live rollback and re-adoption remain unverified and
deferred. Commit and push the documentation changes. Do not automatically
deploy that documentation-only commit. Report readiness for the next
migration decision; the controller box requires a separate explicit decision.

## 5. Deferred Rollback And Re-adoption

This section is retained for a later, separately authorized test. Do not run
it as part of the current development acceptance. Live rollback and
re-adoption remain unverified and deferred. They and direct-IPv6 repair do
not block continued development. The [current work queue](../../doc/upstream-instance-target-architecture.md#current-work-queue)
owns the next work order.

Use the fresh verification Plan and the successful adoption receipt:

```sh
klokast-dev/bin/apply-platform-intent \
  --controller BOX-ops \
  --plan PLAN --authority-state AUTHORITY_STATE \
  --controller-toolchain-receipt TOOLCHAIN_RECEIPT \
  --source-recovery-receipt SOURCE_RECOVERY_RECEIPT \
  --instance-source-receipt SOURCE_RECEIPT_PATH \
  --observation OBSERVATION --build-dir BUILD_DIR \
  --rollback-execution-receipt ADOPTION_EXECUTION_RECEIPT
```

Approve Touch ID. The rollback creates a new forward Authority State v2
record, applies the saved old input to the same router, and verifies it.

Create a fresh adoption Plan v3. Reapply with
`--prove-replay-refusal`. The helper must complete the source change and then
prove that the controller refuses the same signed request. Create one final
Plan v3. The selected group must be verification-only.

Keep all old registries, authority states, Plans, intents, execution receipts,
rollback material, Toolchain receipts, and audit records. Do not authorize the
controller box. That box needs a separate human decision.
