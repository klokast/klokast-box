# upstream/instance architecture: target design

## Purpose

This document defines the target separation between:

1. the public Klokast implementation;
2. the private desired state of one Klokast deployment;
3. secrets;
4. generated and observed runtime state.

It is intended to guide implementation work in `klokast/klokast-box`.

The central objective is to let Klokast evolve upstream without forcing each user to maintain a private fork of the implementation, while preserving a clear, reviewable, reproducible, and secure definition of each deployed instance.

Every premises and statements in this document can and should be challenged.

---

## 1. Vision

### 1.1 Desired direction

Klokast should use a strict upstream/instance model:

- `klokast/klokast-box` is the public, generic, versioned implementation and the only source of standard platform behavior;
- each deployed Klokast platform has one separate private instance repository containing its desired state and local extensions;
- the private instance repository consumes a pinned version of `klokast/klokast-box`; it does not fork or copy the implementation;
- secrets never belong in either Git repository;
- generated, observed, cached, and mutable state lives on the active controller outside Git;
- the `klokast` CLI is the only supported interpreter of the instance repository.

The effective deployment is therefore:

```text
effective deployment
    = pinned klokast-box implementation
    + private instance desired state
    + runtime-injected secrets
```

Generated and observed state is an output of this combination, not another source of desired state.

### 1.2 Architectural goals

The new model should provide:

- **clean ownership boundaries** — generic behavior upstream, instance-specific intent downstream;
- **atomic upstream changes** — schemas, validators, templates, documentation, and tests evolve in one repository and one pull request;
- **safe upgrades** — the instance pins an immutable upstream revision and can roll back;
- **minimal private delta** — users maintain only what is unique to their deployment;
- **reproducibility** — every apply records both the engine revision and instance revision;
- **least privilege** — the airunner authors code but does not hold controller secrets or private runtime state;
- **fail-closed validation** — invalid or ambiguous desired state cannot be applied;
- **single source of truth** — topology and placement are declared once and compiled into generated inventories and policies;
- **portable instances** — the private repository describes one deployment without embedding mutable controller state.

---

## 2. Target repository model

### 2.1 Public upstream repository

Repository:

```text
github.com/klokast/klokast-box
```

Responsibilities:

- platform architecture;
- the `klokast` CLI;
- schemas and validators;
- compilers and reconcilers;
- Ansible roles and playbooks;
- standard box and VM definitions;
- standard application implementations and public app manifests;
- generic policy templates;
- migrations;
- tests and fixtures;
- canonical instance templates;
- release artifacts and their metadata.

It must not contain:

- real user identities;
- deployment-specific hostnames, IP addresses, MAC addresses, sites, or physical locations;
- private application configuration;
- raw secrets;
- live controller state;
- generated inventories from a real deployment;
- mutable application data.

### 2.2 Canonical public instance template

The canonical template should be integrated into `klokast-box`:

```text
klokast-box/
└── templates/
    └── instance/
```

This template is upstream product material. It defines the repository contract and the default starter configuration supported by the same revision of `klokast-box`.

It should not be maintained independently in a second public source repository. The existing public repository `github.com/klokast/klokast-instance` should eventually be deleted.

### 2.3 Private user instance repository

Example:

```text
github.com/xiaoju/klokast-instance
```

This repository is private and independently versioned.

It contains:

- instance topology and sites;
- stable logical box identities;
- runtime hostname prefixes;
- control-plane placement;
- private identities and group membership;
- deployment-approved access capabilities;
- application enablement and resource bindings;
- private application configuration;
- instance-specific extensions;
- an immutable upstream engine pin.

It does not contain:

- copies of upstream roles or standard application implementations;
- generated inventories or rendered policies;
- raw secrets;
- application databases and data;
- controller facts, plans, caches, logs, or apply receipts.

The private repository is the desired-state composition root for one Klokast deployment.

---

## 3. Target `klokast-box` structure

The exact implementation language may evolve, but the upstream repository should converge toward a structure similar to:

```text
klokast-box/
├── cmd/
│   └── klokast/
│
├── schemas/
│   ├── instance-contract-v1.json
│   ├── deployment-v1.json
│   └── platform-resources-v1.json
│
├── templates/
│   └── instance/
│       ├── README.md
│       ├── AGENTS.md
│       ├── .gitignore
│       ├── klokast.yml
│       ├── ops/
│       │   ├── deployment.yml
│       │   └── platform-resources.yml
│       ├── apps/
│       │   └── README.md
│       └── extensions/
│           └── README.md
│
├── tests/
│   └── fixtures/
│       ├── instance-single-box-valid/
│       ├── instance-two-box-valid/
│       └── instance-invalid/
│
├── apps/
├── ansible/
├── doc/
└── ...
```

The canonical source template should not contain a manually maintained `klokast.lock.yml` that attempts to pin the Git commit containing itself. The `klokast init` command must generate that lock dynamically from the installed or selected upstream release.

---

## 4. Target private instance structure

A generated private instance should initially remain small:

```text
klokast-instance/
├── README.md
├── AGENTS.md
├── .gitignore
├── klokast.yml
├── klokast.lock.yml
│
├── ops/
│   ├── deployment.yml
│   └── platform-resources.yml
│
├── apps/
│   └── README.md
│
└── extensions/
    └── README.md
```

Do not introduce empty interfaces merely because they might be useful later.

In particular, the initial contract should not require:

- `policy/access.yml`;
- a manually maintained Ansible `inventory/hosts.yml`;
- a `secrets/` directory inside the checkout;
- generated-state directories;
- a generic override directory that can shadow arbitrary upstream files.

Additional directories should be added only after their schema, ownership, merge behavior, and validation rules are implemented.

---

## 5. Instance repository contract

### 5.1 Root contract file

`klokast.yml` defines the version of the complete instance-directory contract and the locations of its authoritative inputs.

Recommended form:

```yaml
---
contract: 1

paths:
  deployment: ops/deployment.yml
  platform_resources: ops/platform-resources.yml
  app_configuration: apps
  extensions: extensions
```

Use:

- `contract` for the version of the repository/directory interface;
- `schema_version` inside individual YAML documents.

The root contract should not duplicate the upstream repository pin. That belongs only in `klokast.lock.yml`.

### 5.2 Engine lock

`klokast.lock.yml` is generated by `klokast init` or an explicit upgrade command.

Example:

```yaml
---
schema_version: 1

engine:
  repository: https://github.com/klokast/klokast-box
  ref: v0.1.0
  commit: 0123456789abcdef0123456789abcdef01234567
```

Semantics:

- `ref` is human-readable;
- `commit` is the authoritative immutable source revision;
- later versions may also pin image, ISO, VM-root, APK-repository, and container digests;
- validation rejects an empty, abbreviated, or unresolved commit;
- an upgrade modifies the lock explicitly and produces a reviewable plan.

### 5.3 Canonical desired-state files

`ops/deployment.yml` is authoritative for:

- instance identity;
- sites;
- logical boxes;
- runtime hostname prefixes;
- private group membership;
- active and standby controller placement;
- airunner placement.

`ops/platform-resources.yml` is authoritative for:

- per-box access capabilities;
- enabled and prohibited access transports;
- application enablement;
- application placement;
- optional resources and concrete deployment bindings.

Public app manifests in `klokast-box` remain authoritative for generic application requirements.

---

## 6. Stable box identity versus runtime naming

### 6.1 Required two-level model

A box must have:

1. a stable logical box ID used by all cross-file references;
2. a deployment-specific runtime hostname prefix used to generate machine names.

Example:

```yaml
boxes:
  box-001:
    hostname_prefix: k001
    site: site-001
```

Meaning:

```text
box-001       stable logical identity
k001          runtime hostname prefix
k001-dom0     generated host name
k001-router   generated host name
k001-bak      generated host name
k001-dmz      generated host name
k001-iot      generated host name
k001-ops      generated when the controller is placed there
k001-airunner generated when an airunner is placed there
```

All references in instance files must use `box-001`, not `k001`.

Example:

```yaml
control_plane:
  controller:
    active_box: box-001

  airunners:
    - box: box-001
```

```yaml
apps:
  nextcloud:
    placement:
      active_master: box-001
```

### 6.2 Identity rules

Logical box ID:

- immutable after the box is adopted into an instance;
- unique within the instance;
- used in Git history, resource bindings, plans, provenance, and controller state;
- never reused for a different physical box without an explicit replacement operation.

Runtime hostname prefix:

- unique within the instance;
- valid as a lowercase DNS label;
- used only to derive deployed host and VM names;
- changeable only through an explicit rename/migration workflow;
- must not be treated as the stable identity.

Changing a hostname prefix may still require operational migration of DNS, Tailnet identities, VMs, backup paths, and generated inventory. The logical-ID split limits desired-state edits; it does not make runtime renaming cost-free.

---

## 7. Standard box and control-plane model

### 7.1 Implicit mandatory box components

A box declaration means a standard Klokast box. Standard components must not be repeated as selectable roles in the private deployment file.

Each standard box implicitly includes the mandatory platform substrate defined upstream, including:

```text
<box>-dom0
<box>-router
<box>-bak
<box>-dmz
<box>-iot
```

The private instance must not declare arbitrary combinations such as a box with `dom0` but without its standard router or service-zone substrate unless a future upstream box profile explicitly supports that topology.

### 7.2 Explicit instance-level placements

Roles with instance-wide cardinality or authority rules must be declared explicitly:

- exactly one active controller;
- zero or one standby controller initially;
- at least one airunner;
- more than one airunner may be permitted, but the approved set should remain small;
- active and standby controllers must be on different boxes;
- colocating an airunner with the active controller is the default, not a hard requirement.

`<box>-ops` is derived from controller placement.

`<box>-airunner` is derived from airunner placement.

Do not create a generic free-form `roles` list on boxes. Controller and airunner placement have different semantics, lifecycle, cardinality, and trust implications.

---

## 8. Standard starter instance

### 8.1 Default topology

The default starter profile should be a coherent single-box deployment:

```yaml
---
schema_version: 1

instance:
  name: example-klokast

tailnet:
  magicdns_suffix: example.ts.net

  groups:
    operators:
      - admin@example.com

    family:
      - family@example.com

sites:
  site-001:
    country: XX
    timezone: Etc/UTC
    physical_location: Example location

boxes:
  box-001:
    hostname_prefix: k001
    site: site-001

control_plane:
  controller:
    active_box: box-001

  airunners:
    - box: box-001
```

This derives a standard first box containing the regular box substrate, the active controller, and one airunner.

### 8.2 Nextcloud as the standard first application

The intended product direction is for Nextcloud to be the standard staple application of the starter instance.

However, it must only be enabled by default when `klokast-box` genuinely supports a validated single-box Nextcloud mode.

Target form:

```yaml
apps:
  nextcloud:
    enabled: true
    resilience_mode: single_box
    placement:
      active_master: box-001
    ingress_mode: tailscale
```

Until that mode is implemented and tested upstream, the template should preselect `box-001` but leave Nextcloud disabled:

```yaml
apps:
  nextcloud:
    enabled: false
    placement:
      active_master: box-001
      passive_backup: ""
    ingress_mode: tailscale
```

An empty passive placement must not silently bypass an active/passive requirement. Reduced resilience must be explicit in the schema.

A future multi-box profile may generate:

```yaml
apps:
  nextcloud:
    enabled: true
    resilience_mode: active_passive
    placement:
      active_master: box-001
      passive_backup: box-002
```

---

## 9. Access-policy ownership

Do not add `policy/access.yml` to the initial instance contract.

Access authority is already divided into clearer sources:

```text
ops/deployment.yml
    private identities and group membership

ops/platform-resources.yml
    deployment-approved access capabilities and concrete bindings

public application manifests
    generic application access requirements

upstream policy templates
    standard infrastructure grants, tag ownership, SSH rules, and tests
```

A separate general-purpose access file would create another source of truth and unclear precedence.

A future local exception mechanism may be introduced only when it has:

- a narrow schema;
- explicit merge order;
- conflict detection;
- reserved-tag protection;
- required positive and negative policy tests;
- rationales and optional expiry;
- no ability to weaken upstream security invariants silently.

Such a file should be named to show that it is exceptional, for example:

```text
policy/local-access-extensions.yml
```

It should not be part of Contract v1.

---

## 10. Secrets, state, and filesystem boundaries

### 10.1 Private Git desired state

A private instance checkout contains only declared desired state and local source extensions.

Example controller checkout layout:

```text
/home/smith/src/
├── klokast-box/
└── klokast-instance/
```

The repositories should be siblings. Do not place a real private instance inside the upstream checkout.

### 10.2 Secrets

Secrets are not Git state.

Target system path:

```text
/etc/klokast/
```

Examples:

- GitHub deploy credentials;
- Tailscale credentials;
- controller signing or broker keys;
- provider API credentials;
- application passwords;
- encryption keys.

The active controller is the secret custodian.

The airunner must not hold:

- controller deploy credentials;
- provider authority;
- broker secrets;
- private runtime state.

The standby controller must not automatically receive active-controller secrets.

### 10.3 Persistent generated and observed state

Target system path:

```text
/var/lib/klokast/
```

Examples:

- rendered inventories;
- rendered firewall and Tailnet policies;
- discovered facts;
- plans;
- apply receipts;
- provenance;
- drift snapshots;
- controller locks;
- migration records.

### 10.4 Cache, temporary state, and logs

```text
/var/cache/klokast/   rebuildable downloads and artifacts
/run/klokast/         temporary process state and locks
journald or
/var/log/klokast/     logs
```

Do not use one catch-all `~/.klokast` directory on the system controller.

Application data belongs to its declared persistent storage substrate, not to the instance repository or controller state directory.

---

## 11. The `klokast` CLI as the only interpreter

The instance repository must not be interpreted independently by unrelated shell scripts, Ansible entry points, and application installers.

The supported interface should be:

```bash
klokast init
klokast check
klokast doctor
klokast plan
klokast apply
klokast check --live
```

Internal tools may still use Ansible, image builders, Tailscale wrappers, and application-specific controllers. They are implementation details invoked through the versioned CLI contract.

### 11.1 `klokast init`

Responsibilities:

- create a private instance directory from the canonical template;
- select a profile such as `single-box` or `two-box`;
- create stable logical IDs;
- set initial runtime hostname prefixes;
- generate `klokast.lock.yml`;
- generate only relevant configuration;
- validate the result before success.

Example:

```bash
klokast init \
  --profile single-box \
  --box-id box-001 \
  --hostname-prefix k001
```

### 11.2 `klokast check`

No network access and no mutation.

Validate:

- YAML syntax;
- supported contract and schema versions;
- full immutable engine commit;
- unique site, box, and hostname-prefix identifiers;
- all cross-file box references;
- controller cardinality;
- airunner cardinality;
- supported applications;
- valid placement and resilience modes;
- capability availability and prohibition rules;
- absence of obvious secret values in tracked files;
- absence of generated output in tracked paths.

### 11.3 `klokast doctor`

Read-only external verification.

Check:

- required credentials exist with correct ownership and permissions;
- engine and instance repositories are available;
- target boxes and Tailnet identities are reachable;
- selected hardware and storage satisfy prerequisites;
- required DNS and provider resources exist;
- external credentials work where a safe read-only check exists.

Never print secret values.

### 11.4 `klokast plan`

Produce a deterministic proposed change set:

- boxes and VMs to create, rename, modify, or remove;
- controller and airunner placement changes;
- policies to add or remove;
- applications to install, move, upgrade, or disable;
- image and artifact changes;
- missing required secrets;
- migrations and destructive actions;
- provenance inputs.

The plan must not mutate the platform.

### 11.5 `klokast apply`

Requirements:

- exact locked engine revision;
- valid instance;
- successful prerequisite checks;
- explicit active-controller authority;
- deterministic use of the reviewed plan;
- fail closed on ambiguity;
- no deployment from dirty repositories by default.

Every apply records:

```json
{
  "engine_commit": "...",
  "instance_commit": "...",
  "effective_config_sha256": "...",
  "controller": "k001-ops",
  "actor": "smith",
  "started_at": "...",
  "result": "success"
}
```

Dirty applies require an explicit break-glass option and must record the complete diff.

### 11.6 `klokast check --live`

Compare desired and observed state:

- missing or unexpected VMs;
- wrong hostname prefixes;
- wrong controller or airunner placement;
- image and artifact digest drift;
- Tailnet and firewall policy drift;
- application placement and health;
- resource-binding differences;
- backup and replication status.

---

## 12. Validation and completion criteria

A command exiting successfully is not sufficient proof that a deployment works.

The starter profile should eventually prove at least:

- dom0 boots from the expected immutable artifacts;
- standard router and zone VMs exist;
- only the active controller has mutation authority;
- the airunner is isolated from controller-private state;
- the controller reaches all managed nodes through the approved management plane;
- generated network policy passes positive and negative tests;
- enabled applications respond through their declared access path;
- prohibited ingress and egress paths are actually unavailable;
- backup succeeds;
- a restore probe succeeds;
- a second apply is idempotent;
- live checking reports no unexplained drift.

---

## 13. Required documentation changes

Update `doc/architecture.md` so the persistence and repository sections no longer describe the target model as:

- a private fork of `klokast-box` per deployment;
- a separate private state Git repository holding generated or mutable controller state.

Replace that model with:

```text
public klokast-box
    generic implementation, schemas, CLI, standard apps, canonical templates

private klokast-instance
    desired state, private identities, bindings, app configuration, extensions,
    immutable engine lock

/etc/klokast
    secrets and credentials

/var/lib/klokast
    generated and observed controller state

application storage
    persistent user-service data
```

Clarify that:

- generated inventories and rendered policies are controller outputs, not Git authority;
- app manifests are public intent;
- the private registry is the instance repository's desired-state binding;
- actual running/stopped facts are observed state;
- desired application lifecycle state may remain in the private registry;
- only the active controller mutates the platform.

---

## 14. Implementation sequence

### Phase 1 — document and validate the contract

1. Update `doc/architecture.md` to the new repository and state model.
2. Add `templates/instance/` to `klokast-box`.
3. Move the canonical template content there.
4. Define Contract v1 and the initial YAML schemas.
5. Add valid and invalid test fixtures.
6. Implement `klokast check --instance <path>`.
7. Validate the bundled template in upstream CI.

### Phase 2 — stable box identity

1. Add `hostname_prefix` to the deployment schema.
2. Change all cross-file references to stable logical IDs such as `box-001`.
3. Implement a resolver from logical box ID to generated runtime names.
4. Preserve both logical ID and runtime names in generated state and provenance.
5. Reject duplicate IDs and duplicate hostname prefixes.
6. Add an explicit future migration path for hostname-prefix changes.

### Phase 3 — initialization and locking

1. Implement `klokast init --profile single-box`.
2. Generate `klokast.lock.yml` from the selected engine release.
3. Generate coherent default controller and airunner placement.
4. Add a two-box profile later.
5. Stop asking users to manually copy and rename template files.

### Phase 4 — lifecycle commands

1. Implement `check`.
2. Implement `doctor`.
3. Implement deterministic `plan`.
4. Route mutation through `apply`.
5. Record provenance and apply receipts under `/var/lib/klokast`.
6. Implement `check --live`.

### Phase 5 — starter application

1. Define and implement an explicit Nextcloud `single_box` resilience mode.
2. Add validation, backup, recovery, and verification for that mode.
3. Enable Nextcloud by default in the single-box profile only after those guarantees exist.
4. Keep active/passive as the multi-box profile.

### Phase 6 — public template repository transition

Choose one:

- archive `klokast/klokast-instance` and direct users to `klokast init`; or
- publish it automatically from `klokast-box/templates/instance`.

Do not continue maintaining the same template manually in both repositories.

---

## 15. Non-goals and invariants

### Non-goals

Contract v1 does not need:

- arbitrary instance-level policy overrides;
- manually maintained Ansible host graphs;
- support for every possible topology;
- distributed multi-controller mutation authority;
- implicit merging of a private fork with upstream;
- secrets stored in Git, even encrypted by default;
- arbitrary shadowing of upstream implementation files.

### Invariants

The implementation must preserve these rules:

1. A real deployment never requires a private fork of `klokast-box`.
2. Generic behavior belongs upstream.
3. Deployment-specific desired state belongs in one private instance repository.
4. Secrets are outside Git.
5. Generated and observed state is outside Git.
6. All cross-file box references use stable logical IDs.
7. Runtime hostnames are derived deployment coordinates, not primary identity.
8. Standard box components are implicit upstream behavior.
9. Controller and airunner placement are explicit instance-level declarations.
10. Only the active controller may mutate the platform.
11. The airunner does not hold controller secrets or runtime state.
12. The `klokast` CLI is the only supported interpreter of the instance contract.
13. Invalid, ambiguous, unlocked, or unreviewed state fails closed.
14. Every apply records the exact engine and instance revisions.
15. The canonical template and engine evolve atomically in `klokast-box`.

---

## 16. Target architecture summary

```text
github.com/klokast/klokast-box
├── implementation
├── schemas
├── CLI
├── standard applications
├── policy templates
└── templates/instance
            │
            │ klokast init
            ▼
github.com/<owner>/klokast-instance
├── desired topology
├── stable box identities
├── runtime hostname prefixes
├── private identities
├── access capabilities
├── application bindings
├── local extensions
└── immutable engine lock
            │
            │ check / doctor / plan / apply
            ▼
active <box>-ops
├── /etc/klokast       secrets
├── /var/lib/klokast   generated and observed state
├── /var/cache/klokast rebuildable cache
└── /run/klokast       temporary state

application storage
└── persistent user-service data
```

The public implementation, private desired state, secrets, generated controller state, and application data are separate assets with separate ownership, lifecycles, and security boundaries.
