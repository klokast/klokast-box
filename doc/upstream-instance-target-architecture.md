# Upstream/instance target architecture

## Purpose

Klokast separates public implementation, private desired state, secrets,
generated controller state, and application data. Contract v1 establishes the
smallest useful desired-state interface and an offline validator. It does not
yet establish deployment or migration workflows.

```text
effective desired state
    = builder-approved klokast-box commit
    + private instance Contract v1 inputs

runtime authority
    = effective desired state
    + secrets injected by the active controller
```

Generated and observed data is output, never another Git authority.

## Repository and build ownership

`github.com/klokast/klokast-box` owns generic behavior:

- architecture, schemas, `klokast`, automation, and tests;
- standard box and VM definitions;
- public application manifests and implementations;
- generic policy templates;
- the canonical instance template.

It must not contain real identities, deployment coordinates, private app
configuration, raw secrets, mutable controller state, or user data.

Each deployment will have one independent private instance repository. It does
not fork or shadow `klokast-box`. Contract v1 contains topology, private group
membership, stable identities, controller/airunner placement, approved access
capabilities, app placement/resource bindings, and an immutable engine lock.

The existing public `klokast/klokast-instance` repository is transitional. The
canonical source is `templates/instance/` in `klokast-box`; the separate public
template repository will eventually be retired. It must not become a second
synchronized source of truth. This milestone does not modify or delete it.

Deployable Go binaries must be produced by the active controller through
`platform-builder build-klokast-cli`. The authoritative build runs in a
short-lived, zero-VIF Xen snapshot using the sealed Alpine 3.23 template and a
digest-pinned Go image. The controller verifies stopped-guest outputs. Neither
the airunner nor the credential-bearing controller runs unreviewed Go code to
produce a deployable binary.

## Contract v1 layout

The canonical source template is:

```text
templates/instance/
├── README.md
├── AGENTS.md
├── .gitignore
├── klokast.yml
└── ops/
    ├── deployment.yml
    └── platform-resources.yml
```

The source template has no self-referential lock. `klokast init` adds:

```text
klokast.lock.yml
```

A complete private Contract v1 repository therefore has four authoritative
inputs:

```text
klokast.yml
klokast.lock.yml
ops/deployment.yml
ops/platform-resources.yml
```

Contract v1 has no private `apps/`, `extensions/`, access-policy override,
secrets, generated-state, inventory, or site-executor interface. Site-local
executors require a later deployment schema version after the security gates in
`doc/site-executor.md` are implemented and validated.

## Root contract and engine lock

`klokast.yml` identifies only the contract and the two desired-state paths:

```yaml
---
contract: 1
paths:
  deployment: ops/deployment.yml
  platform_resources: ops/platform-resources.yml
```

It does not duplicate the engine repository or expose speculative directory
interfaces.

Generated instances require this lock shape:

```yaml
---
schema_version: 1
engine:
  repository: https://github.com/klokast/klokast-box
  ref: main
  commit: 0123456789abcdef0123456789abcdef01234567
```

`ref` is human-readable. The builder verifies the canonical repository and
the synchronized upstream branch before it injects the repository, ref, and
commit into `klokast`. The full 40-hex `commit` is authoritative. All three
lock values must match the running builder-approved binary.

## Stable identity and control plane

Cross-file references use stable logical IDs. Runtime names derive from a
separate prefix:

```yaml
boxes:
  box-001:
    hostname_prefix: k001
    site: site-001
```

This derives `k001-dom0`, `k001-router`, `k001-bak`, `k001-dmz`, `k001-iot`,
and placed control-plane names. Logical IDs and hostname prefixes must be
unique. Prefixes must not collide with reserved role suffixes or produce names
longer than a DNS label.

Every standard box implicitly includes the upstream-defined standard substrate;
Contract v1 has no free-form role list. The control plane declares exactly one
active controller, at most one distinct standby controller, and at least one
airunner. Airunners have stable IDs and a closed variant:

```yaml
airunners:
  - id: airunner-001
    kind: box
    box: box-001

  - id: airunner-cloud-001
    kind: external
    hostname: vultr-ops-airunner
```

A runner must satisfy exactly one variant. Box references and external runtime
identities must resolve without duplication or collision.

## Canonical starter

The canonical template is a coherent single-box instance:

- stable box ID `box-001` with `hostname_prefix: k001`;
- one site;
- `box-001` as the explicit active controller;
- one box-kind airunner with stable ID `airunner-001`;
- Nextcloud disabled but preselected on `box-001` in `single_box` placement;
- no empty passive placement.

Nextcloud may be enabled by a later milestone only after the single-box mode,
backup, recovery, and verification guarantees are implemented. A future
two-box profile may select explicit, distinct active and passive boxes.

## Capabilities and applications

`ops/platform-resources.yml` binds deployment intent to public manifests.
Contract v1 uses `declared_capabilities`, never `available_capabilities`:

- every enabled capability must be declared;
- enabled and prohibited sets must be disjoint;
- declared and prohibited sets may overlap, representing a physically possible
  but forbidden transport;
- every policy value must be an enabled capability or `none`.

Applications must exist in the public manifests embedded in `klokast`. Their
placement must use a closed `single_box`, `active_passive`, or `multi_box`
variant and reference declared boxes. App resource bindings must name public
manifest resource IDs. Contract v1 contains no private app configuration.

## Schemas and offline checking

The engine embeds Draft 2020-12 schemas for:

- the single-box init values;
- the root contract;
- the engine lock;
- deployment;
- platform resources.

Schemas reject unknown fields. `klokast` also embeds the canonical template and
public app manifests, so generation and checking require no network access.

Implemented commands are:

```text
klokast version --json
klokast init --instance PATH --profile single-box --values FILE [--json]
klokast check --instance PATH [--json]
```

`init` accepts one strict JSON values document. The input contains an instance
name, Tailnet suffix and groups, a two-letter country, an optional physical
location, and one hostname prefix. The input has no timezone field. Platform
time is always `Etc/UTC` (GMT), and the generator writes that value to the
deployment document.

```json
{
  "schema_version": 1,
  "instance": {"name": "family-klokast"},
  "tailnet": {
    "magicdns_suffix": "example.ts.net",
    "groups": {
      "operators": ["admin@example.com"],
      "family": ["family@example.com"]
    }
  },
  "site": {"country": "FR", "physical_location": "Example home"},
  "box": {"hostname_prefix": "k001"}
}
```

The generator requires a builder-approved engine identity. It copies the
embedded template to an owner-only sibling staging directory, writes the exact
engine lock, creates an independent local Git repository on branch `main`, and
stages all inputs. It does not make a commit, add a remote, use the network, or
copy the values document into the instance. It runs `check` before an atomic
no-replace publication. Failure removes the staging directory. The destination
must not exist and must not be inside another Git worktree.

`check` is read-only and validates:

- a standalone Git repository and tracked authoritative inputs;
- safe relative paths with no traversal or symlink escape;
- one safe YAML document per file, with no duplicate keys or custom tags;
- schemas and supported versions;
- the full lock commit against the running engine commit;
- logical IDs, runtime prefixes, references, and cardinality;
- runner and placement variants;
- capability and public-manifest resource rules;
- forbidden tracked generated/secret paths and obvious raw-secret patterns.

Diagnostics never print suspected values. A dirty instance worktree is allowed;
authoritative inputs still must be tracked. Exit status is `0` for valid, `2`
for validation failure, and `1` for operational failure.

## Filesystem and authority boundaries

```text
private instance Git repository
    declared Contract v1 desired state only

/etc/klokast
    controller-owned secrets and credentials

/var/lib/klokast
    generated inventories, facts, plans, receipts, provenance, and build outputs

/var/cache/klokast
    rebuildable downloads and artifacts

/run/klokast
    temporary process state and locks

application storage
    persistent user-service data
```

The active controller is the credential custodian and only mutation authority.
Standby controllers do not automatically receive active secrets. Airunners may
author reviewed Git changes but do not hold Platform private state or produce
deployable binaries locally.

## Deferred milestones

After local Contract v1 generation, separate reviewed milestones may implement:

1. private repository creation and remote registration;
2. a compatibility resolver and logical-to-runtime compiler projection;
3. provenance-aware `doctor`, `plan`, `apply`, and live checking;
4. private-instance migration and removal of legacy inventory authority;
5. application-specific configuration schemas;
6. site executors under a later deployment schema version;
7. retirement of the separate public template repository.

This milestone does not install `klokast` on the controller, migrate a private
instance, alter existing deployment inventory/platform-resource inputs, add
lifecycle commands, or modify external repositories.
