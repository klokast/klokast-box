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

With the checkpoint active, implement and promote the source-aware consumer
engine. The publication helper can then validate new fields with both sealed
engines. Keep the source executor, consumer, inventory, and recovery gates in
the target document. They are not supplied by this checkpoint.

Do not remove legacy files or restart applications. Adoption and subsequent
verification require fresh signed actions. Keep data retention and the
deferred IPv6 and rollback interfaces unchanged.
