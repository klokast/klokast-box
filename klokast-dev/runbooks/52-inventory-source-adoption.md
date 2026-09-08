# Inventory and runner source adoption

Status: `decided`. The offline renderer and comparison checkpoint are
implemented. Source adoption and normal-consumer replacement are pending.
The [bounded design](../../doc/upstream-instance-target-architecture.md#inventory-and-runner-source-adoption-design)
owns scope, authority, recovery, and acceptance requirements.

Keep the canonical controller checkout at the active engine during review.
Build the clean reviewed commit through the sealed builder from an isolated
public checkout on the active controller as `smith`. Then run:

```sh
klokast-dev/bin/prepare-inventory-source-candidate --build-dir BUILD_DIR
```

The helper verifies the sealed build and clean public/private checkouts. It
creates a temporary controller-private instance fixture with only engine and
schema-reference metadata adjusted for the reviewed binary. It never publishes
that fixture or changes the active source. The sealed `klokast inventory`
renderer derives host selection and runner enablement without legacy input.

`compare-instance-inventory` runs Ansible inventory parsing for the existing
base plus generated box files and the independent instance graph plus generic
upstream group policy. It compares all selected host variables and complete
group memberships. Only `inventory_file` and `inventory_dir` provenance fields
are excluded. Host addresses, connection users, runner enablement, and role
policy must be equal. Unknown or incomplete graphs refuse. These commands
perform no host contact, configuration, or service operation.

Evidence stays below
`~/private/klokast/inventory-source-candidates/ENGINE/UTC_TIMESTAMP/`.
The safe result contains counts, hashes, and paths. Raw Ansible output and
private projections remain in that directory. The temporary private Git
fixture and temporary inventory copies are removed after comparison. Inputs,
checkout commits, and the source pointer are rechecked before success.

Complete Plan v7, Authority State v5, Toolchain v6, the closed executor,
normal inventory readers, and consumer tests before promotion. This offline
checkpoint does not adopt any scope or remove the continuing execution
inventory authority. Use the existing human promotion and exact signed
adoption/verification workflows for the final implementation. Keep the
[registry acceptance record](51-registry-source-adoption.md#acceptance-record-2026-09-08)
and all recovery files.
