# Registry Migration Compatibility Checkpoint

This is the first implementation step in the
[remaining registry and inventory batch](../../doc/upstream-instance-target-architecture.md#115-remaining-registry-and-inventory-migration-batch).
It prepares a rollback checker that understands the later private inputs.
It does not adopt another setting group. The completed connectivity and
controller identity acceptance records remain in force.

## Build and inspect

Run repository tests, then use the active controller's sealed builder for the
exact pushed commit. Use an isolated public checkout on the controller with
`main` tracking `origin/main`. Keep the canonical controller checkout at the
active engine until the human starts promotion. This keeps normal controller
resolution available during the build and review.

Run the syntax check and existing Ansible builder from that checkout:

```sh
ANSIBLE_CONFIG="$PWD/ansible/ansible.cfg" ansible-playbook -vv \
  --syntax-check -i ansible/inventory/hosts.yml \
  ansible/playbooks/73-platform-builder.yml
ansible/bin/platform-builder build-klokast-cli \
  --box "$ACTIVE_BOX" --approved-commit "$ENGINE_COMMIT"
```

Require successful sealed Go tests/build, a verified receipt and binary, and
successful builder cleanup. The public schema and checker accept optional
substrate and inactive-app settings. All existing planner targets must
refuse those extensions with `registry.checkpoint-only`. A schema-valid
extension must never produce a deployable Plan from this engine.

On the controller, use an owner-only temporary instance copy to test the
current settings against the checkpoint. Keep the real private checkout
unchanged. Carry every supported legacy field into the typed candidate;
reject unknown fields rather than omit them. Preserve explicit empty values,
saved expiry values, cleanup placement, and the existing retained-data
declarations. Keep candidate and diagnostic files on the controller. Record
only paths, hashes, counts, and test outcomes in public acceptance notes.

Check the unchanged current instance against the checkpoint too. After
repinning only its engine metadata in a temporary copy, compatibility must
pass. The detailed legacy field list will be longer because the old omitted
app findings hid nested configuration. Extra findings do not mean that new
settings were introduced.

## Promote

Record the active source hash, canonical engine, private commit, and private
desired-state file hashes before promotion. Use the exact sealed engine and
operation from the verified build. When the human is ready, update the
canonical public checkout to that exact commit through a fast-forward pull,
then use the existing
[MacBook promotion helper](40-private-instance-bootstrap.md#14-promote-the-active-engine)
with an explicit active controller. Review the metadata-only diff and approve
Touch ID. Do not add the new instance fields in this promotion.

After activation, verify the active engine and generate its matching
controller toolchain receipt. Require the same source record, connectivity,
controller pair, and legacy desired-state bytes. Only the private schema URLs
and lock engine metadata can change. Keep the promotion and activation
receipts and a fresh compatibility report on the controller.

## Continue the grouped adoption

With the checkpoint active, continue the
[registry source runbook](51-registry-source-adoption.md). Its consumer engine
keeps both sealed-engine checks for private publication. Keep the source
executor, consumer, inventory, and recovery gates in the target document.
They are not supplied by this checkpoint.

Do not remove legacy files or restart applications. Adoption and subsequent
verification require fresh signed actions. Keep data retention and the
deferred IPv6 and rollback interfaces unchanged.

## Acceptance record: 2026-09-07

Status: `live-verified`. Active engine:
`e12b42620a31e2c708ac5fdfdefcb693f3dc6f75`. Private commit:
`de8e252cca751319582b877ab6805d0387c90ee4`.
Sealed build operation: `8797cc91f31b`.

The controller-held evidence directory is:
`/home/smith/private/klokast/registry-checkpoint-acceptance/e12b42620a31e2c708ac5fdfdefcb693f3dc6f75`.
Its `checkpoint-acceptance-result.json` records activation and toolchain
receipt references. `candidate-verification-result.json` records lossless
conversion of both boxes and all eight disabled apps, equal full and box-only
compiler outputs, equal router variables, and explicit deployment refusal.
The current active instance passes sealed validation and compatibility.

The only private changes were the two schema URLs and lock engine commit.
All other private file bytes, modes, and owners remained unchanged. Authority
State hash `0b34c848ef2008f74259593ce7c869473a16cfa1e4f130b47eb49b556c0d8005`
and the active/standby pair remained unchanged. Normal controller resolution
passed after activation. Toolchain receipt hash:
`19f78b17666b80cbfea8a5f528cfbd562433079d5fcfbf15f1b1e5a5e1e66d0a`.

Activation receipt:
`/var/lib/klokast/engine-activations/de8e252cca751319582b877ab6805d0387c90ee4/63114ac1603816a5a0bd7e510f0f5980536addd0207e5c017953611b2ff6d0fe.json`.
Continue the registry source-adoption decision in the target document.
