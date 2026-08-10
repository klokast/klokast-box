# Upstream/instance target architecture

## Purpose

Klokast separates public implementation, private desired state, secrets,
generated controller state, and application data. Contract v1 establishes the
smallest useful desired-state interface, an offline generator and validator,
and a deterministic read-only compatibility plan. It does not yet establish a
deployment or migration workflow.

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
and `k001-ops`. A controller placed on the box uses `k001-ops`. A box-kind
airunner uses the dedicated `k001-airunner` guest and its own Tailnet identity;
it does not run in the controller guest. An external airunner keeps its
declared hostname. Logical IDs, hostname prefixes, airunner IDs, and all
resolved runtime names must be unique. Prefixes must not collide with reserved
role suffixes or produce names longer than a DNS label.

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

The target box runner identity is always `<box>-airunner`. Transitional
`<box>-ops-airunner` and `<box>-ops-airunner-candidate` identities are legacy
resources. They are not equivalent to the target identity.

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

## Deterministic projection and compatibility planning

The internal Contract v1 resolver has no host discovery, network access,
environment-dependent defaults, or current-state input. It sorts unordered
maps and sets before it emits the projection. The same checked input bytes and
engine identity therefore produce the same projection hash.

The projection contains stable logical IDs and the exact runtime mapping for
sites, boxes, controllers, airunners, access, app placement, and public
resource bindings. For the transitional compiler, it derives:

- legacy box keys and placement targets from `hostname_prefix`;
- `available_capabilities` as `declared_capabilities` minus
  `prohibited_capabilities`;
- controller and box-kind airunner hostnames from the runtime rules above.

The full declared capability set stays in the projection and provenance. The
adapter does not discard capabilities that are physically possible but
prohibited.

`klokast plan --instance PATH --compatibility-registry FILE [--json]` is an
offline, read-only parity check. It does not render inventory, write a plan,
contact a host, or change the compatibility registry. The registry must be one
regular, bounded, safe YAML document and must not contain an obvious raw
secret. The report gives each transitional field one of these classes:

- `matched`: Contract intent equals the legacy value;
- `derived`: the deterministic adapter supplies the value;
- `compatibility_only`: Contract v1 cannot express the field, so a later
  migration must retain or replace it explicitly;
- `conflict`: Contract intent and legacy intent differ;
- `unsupported`: the adapter cannot safely interpret the field.

The plan is compatible only when it has no conflict or unsupported field. It
is authority-ready only when it is also deployable and has no
compatibility-only field. Thus fields such as `dom0_bridge_ports`,
`dhcp_reservations`, `shared_guests`, app `runtime_state`, app VM definitions,
device bindings, ingress mode, and ephemeral approvals cannot disappear during
migration. They remain compatibility-only until a later schema or an explicit
replacement owns them.

An enabled app must use the placement mode that the current public manifest
supports. A disabled Contract app keeps its preselection in the projection;
the compatibility adapter does not turn that preselection into a legacy
cleanup target.

The JSON report has `schema_version: 1`. It records the engine identity, exact
SHA-256 hashes of the four authoritative input files, the projection hash, the
compatibility-registry hash, repository state, and redacted findings. It does
not print suspected secret values.

Git state has separate gates:

- `check` accepts dirty tracked content;
- read-only `plan` accepts a dirty or unborn repository, hashes the exact
  worktree inputs, and reports `deployable: false`;
- a future deployable plan will require a clean committed instance;
- a future apply will require the exact instance commit, engine commit, plan
  hash, and observed-state generation recorded by its plan.

Exit status is `0` when the inputs are valid and compatible, including a
read-only non-deployable report. It is `2` for invalid input, a conflict, or an
unsupported field, and `1` for an operational failure.

## Observation v1 and offline doctor

`platform-map export-observation --file PATH` reads one existing mapper
snapshot. It does not collect facts. It writes Observation v1 JSON to standard
output and does not change Platform state.

Observation v1 has this closed data model:

- `schema_version`, `observed_at`, `source_controller`, `source_map_sha256`,
  and `generation_sha256`;
- `tailnet_machines`, with only a normalized hostname, online state, and
  sorted tags for each machine;
- `boxes`, with only the hostname prefix, dom0 reachability, Xen availability,
  and separate sorted sets of running, configured, and autostart guests.

The source map hash covers the exact mapper snapshot bytes. The generation
hash covers canonical JSON for all Observation v1 fields except the generation
hash itself. The exporter rejects malformed or ambiguous source identities.
It excludes addresses, users, locations, provider facts, private desired
state, containers, volumes, logs, mapper findings, and all other mapper data.
All timestamps are UTC and use `Z`. Observation v1 has no timezone input.

`klokast doctor --instance PATH --observation FILE [--json]` is offline,
read-only, and non-mutating. It uses the same deterministic Contract resolver
as `plan`. It does not invoke `platform-map`, use the network, or read the
compatibility registry. It accepts only a bounded regular non-symlink
Observation v1 file, rejects unknown fields and duplicate identities, and
verifies the generation hash. An observation is invalid when it is more than
30 minutes old or more than five minutes in the future.

`doctor` requires the observation source to equal the declared active
controller. For each Contract box, it checks the dom0, router, bak, dmz, and
iot Tailnet identities and their role tags. It also checks dom0 reachability,
Xen availability, and the running, configured, and autostart state of each
standard guest. It applies the same checks to active and standby controller
guests and to declared box-kind airunners. It checks external airunners only
as Tailnet identities. The expected box-kind guest and Tailnet identity are
`<box>-airunner`.

The standard box checks also prove the shared-zone substrate for enabled app
placement boxes. `doctor` does not claim app-container, managed-device, or
compatibility-only resource health. Extra legacy guests and Tailnet machines
do not influence the authority decision. Legacy authority remains active.

The JSON result has `schema_version`, `valid`, `healthy`, engine and exact
input provenance, the projection hash, the observation generation hash, a
redacted finding summary, findings, and diagnostics. Exit status is `0` when
the inputs are valid and observed state is healthy, `2` for invalid input or
observed drift, and `1` for an operational failure.

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
klokast plan --instance PATH --compatibility-registry FILE [--json]
klokast doctor --instance PATH --observation FILE [--json]
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
no-replace publication. Failure removes the staging directory or reports its
exact remaining path if removal fails. The destination must not exist and must
not be inside another Git worktree.

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

The current generator uses a Linux `renameat2` no-replace publication and the
builder currently produces Linux/amd64 binaries. Other operating systems and
architectures are not supported by this implementation milestone.

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

After the read-only doctor milestone, separate reviewed milestones may
implement, in this order:

1. a provenance-aware, deployable `plan` that records the exact clean instance
   commit, engine commit, content hashes, and observed-state generation;
2. private repository creation and remote registration;
3. a separately authorized `apply` with fencing, revalidation, rollback, and
   live verification;
4. private-instance migration while all compatibility-only fields remain under
   their current authority;
5. removal of legacy inventory and registry authority only after parity,
   rollback tests, and an explicit observation period succeed;
6. application-specific configuration schemas;
7. site executors under a later deployment schema version;
8. retirement of the separate public template repository.

The current milestone does not install `klokast` on the controller, create or
modify a private repository, migrate an instance, alter existing deployment
inventory or platform-resource inputs, apply a plan, or modify an external
repository.
