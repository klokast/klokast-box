# Upstream/instance target architecture

## Purpose

Klokast separates public implementation, private desired state, secrets,
generated controller state, and application data. Contract v1 establishes the
smallest useful desired-state interface, an offline generator and validator,
and a deterministic read-only Plan v1 artifact. It does not yet establish an
authorized apply or migration workflow.

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
The human-only authoring rule applies to this private repository. Approved
airunners remain commit and push authorities for reviewed changes to the public
`klokast/klokast-box` repository.

`templates/instance/` in `klokast-box` is the only public instance template.
Each real instance is a separate private repository. No other public template
repository is part of this architecture.

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
and `k001-ops`. A controller placed on the box uses `k001-ops`. A
controller-container airunner uses `k001-ops-airunner`. It runs as a root-managed
Podman container in the controller guest and has its own Tailnet identity. A
box-kind airunner uses the optional hardened `k001-airunner` Xen guest. An
external airunner keeps its declared single-label hostname. Logical IDs,
hostname prefixes, airunner IDs, and all resolved runtime names must be unique.
Prefixes must not collide with reserved role suffixes or produce names longer
than a DNS label.

Every standard box implicitly includes the upstream-defined standard substrate;
Contract v1 has no free-form role list. The control plane declares exactly one
active controller, at most one distinct standby controller, and at least one
airunner. Airunners have stable IDs and a closed variant:

```yaml
airunners:
  - id: airunner-001
    kind: controller_container
    box: box-001

  - id: airunner-hardened-001
    kind: box
    box: box-002

  - id: airunner-cloud-001
    kind: external
    hostname: vultr-ops-airunner
```

A runner must satisfy exactly one variant. A `controller_container` runner must
use the active or standby controller box. Box references and external runtime
identities must resolve without duplication or collision.

`<box>-ops-airunner` is a supported steady-state placement. It shares the
controller kernel and compromise domain, but it does not receive controller
private state, controller credentials, or the Podman control socket. A
`<box>-airunner` Xen VM is an optional stronger isolation boundary. It is not a
required migration target. `<box>-ops-airunner-candidate` is only an ephemeral
blue-green replacement identity. It is never declared as desired state.

## Canonical starter

The canonical template is a coherent single-box instance:

- stable box ID `box-001` with `hostname_prefix: k001`;
- one site;
- `box-001` as the explicit active controller;
- one controller-container airunner with stable ID `airunner-001`;
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
manifest resource IDs. Each Contract v1 resource binding is Boolean. A later
contract version must define a resource-specific schema before it accepts a
different value type. Contract v1 contains no private app configuration.

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
- controller and airunner hostnames from the runtime rules above.

The full declared capability set stays in the projection and provenance. The
adapter does not discard capabilities that are physically possible but
prohibited.

The expanded command is:

```text
klokast plan \
  --instance PATH \
  --compatibility-deployment FILE \
  --compatibility-registry FILE \
  --compatibility-controller-ha FILE \
  [--observation FILE --instance-source-receipt FILE] [--json]
```

The command is offline and read-only. It does not render inventory, contact a
host, or change an input. Each compatibility input must be one regular,
bounded, safe YAML document and must not contain an obvious raw secret. The
comparison covers the legacy deployment topology and Tailnet groups, the
controller set, the platform-resource registry, and engine-owned execution
inventory. It gives each transitional field one of these classes:

- `matched`: Contract intent equals the legacy value;
- `derived`: the deterministic adapter supplies the value;
- `compatibility_only`: Contract v1 cannot express the field, so a later
  migration must retain or replace it explicitly;
- `conflict`: Contract intent and legacy intent differ;
- `unsupported`: the adapter cannot safely interpret the field.

The result is compatible only when it has no conflict or unsupported field.
Every compatibility-only field names its continuing legacy authority. Thus
fields such as `dom0_bridge_ports`,
`dhcp_reservations`, `shared_guests`, app `runtime_state`, app VM definitions,
device bindings, ingress mode, and ephemeral approvals cannot disappear during
migration. They remain compatibility-only until a later schema or an explicit
replacement owns them.

An enabled app must use the placement mode that the current public manifest
supports. A disabled Contract app keeps its preselection in the projection;
the compatibility adapter does not turn that preselection into a legacy
cleanup target.

Without `--observation`, the command emits the expanded compatibility report.
With `--observation`, it emits a complete `klokast.plan.v1` artifact. The
observation must pass the same freshness, source-controller, generation-hash,
and `standard_substrate_v1` checks as `doctor`. The Plan v1 artifact records:

- the exact clean instance branch and commit;
- the fresh controller source-receipt hash, repository hash and numeric ID,
  remote ref, fetched commit, fetch time, and read-only deploy-key fingerprint;
- the exact builder-approved engine repository, ref, and commit;
- SHA-256 hashes of the four Contract inputs and all three compatibility
  inputs;
- the full deterministic projection and its hash;
- the observation source, timestamp, and generation hash;
- the explicit `standard_substrate_v1` health scope;
- all compatibility findings and named authority assignments;
- sorted proposed actions, retained legacy actions, rollback metadata, and
  refusal conditions;
- a canonical `plan_sha256` over every other artifact field.

The artifact never contains the local input paths or suspected secret values.
The supported `controller_container` runner becomes Contract-owned when its
`<box>-ops-airunner` Tailnet identity is online and has `tag:airunner`. It does
not retain a separate legacy runner authority.

Plan gates have distinct meanings:

- `compatible`: there is no conflict or unsupported field;
- `substrate_healthy`: `doctor` reports no drift in
  `standard_substrate_v1`;
- `deployable`: the instance is a clean commit, every finding has a proposed
  action or named continuing authority, its source receipt matches that commit,
  and there is no refusal;
- `authority_ready`: every scope has exactly one proposed or continuing
  authority;
- `legacy_removal_ready`: no field still uses a retained legacy authority.

Git state has separate gates:

- `check` accepts dirty tracked content;
- the compatibility report accepts a dirty or unborn repository, hashes the
  exact worktree inputs, and reports repository deployability separately;
- Plan v1 records a refusal unless the instance is clean and committed;
- a future apply will require the exact instance commit, engine commit, plan
  hash, and observed-state generation recorded by its plan.

The compatibility report exits `0` when its inputs are valid and compatible.
Plan v1 exits `0` only when it is deployable. Exit status `2` means invalid
input or a refusal. Exit status `1` means an operational failure.

On the active controller, `ansible/bin/platform-plan` verifies the selected
sealed-builder receipt, binary hash, and binary version. It runs Plan v1 as
`smith`. It also requires the instance source receipt to be a root-owned,
content-addressed file below `/var/lib/klokast/instance-sources/`. It verifies
`plan_sha256` and stores the canonical artifact without replacement at:

```text
/var/lib/klokast/plans/<instance-commit>/<plan-sha256>.json
```

This wrapper only creates controller evidence. It does not collect facts or
change Platform resources. It can store a valid refused plan for audit.

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
guests and to declared box-kind airunners. It checks controller-container and
external airunners only as Tailnet identities. The controller guest checks
already prove the host substrate for its airunner container. A
controller-container runner does not add an `airunner` Xen guest check.

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
klokast plan --instance PATH --compatibility-deployment FILE --compatibility-registry FILE --compatibility-controller-ha FILE [--observation FILE --instance-source-receipt FILE] [--json]
klokast doctor --instance PATH --observation FILE [--json]
```

`init` accepts one strict JSON values document. The input contains an instance
name, a `*.ts.net` MagicDNS suffix, the exact `operators` and `family` groups, a
two-letter country, an optional physical location, and one hostname prefix.
Both groups are non-empty, all members are valid logins, and at least one
operator is also a family member. The input has no timezone field. Platform
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
      "family": ["admin@example.com", "family@example.com"]
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

/etc/klokast/private-instance
    root-owned private-instance read-only deploy key and pinned Git host keys

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
Standby controllers do not automatically receive active secrets. The human
authors and pushes private instance changes from a trusted workstation. This
restriction does not apply to the public implementation repository. The active
controller has a clean deployment checkout with a disabled push URL and a
root-held read-only deploy key. Airunners can author and push reviewed public
implementation changes, but they do not clone the private instance repository,
hold private registries or controller credentials, or receive the controller
Podman socket. A future automated private-instance edit must be a narrow,
validated proposal action on the controller. Airunners do not produce
deployable binaries locally.

## Private repository bootstrap and source custody

`ansible/bin/platform-instance` and the installed root wrapper `ksa-instance`
implement the private-source custody boundary. The exact repository name is
`<family>/klokast-instance`. The name keeps `klokast` and `klokast-box`
available for possible upstream forks.

The human first creates the exact empty private organization repository in the
GitHub web interface. The workflow then uses one dedicated, temporary GitHub
App. The App is installed only on that repository. It is never installed on
all organization repositories or on an unrelated carrier repository. The App
has Repository Administration write and Metadata read only. It has no Contents
permission. Three controller changes require a fresh, human-signed intent that
is bound to the reviewed public engine commit:

- verify and register the exact private, empty, non-fork repository;
- register the controller public key as a read-only deploy key, then use that
  key to prove that the repository has no Git refs;
- retire the temporary App credential after the human removes that repository
  from the App installation.

The MacBook wrapper `klokast-dev/bin/run-private-instance-action` validates and
displays one short-lived intent, asks the human for an explicit decision, and
uses Touch ID before it transfers and runs only that action. It does not chain
these authority changes.

The human initializes the instance with a sealed-builder `klokast` binary,
transfers the staged repository to the trusted workstation, reviews it, commits
it, and pushes it with a human-owned private-repository identity. The GitHub App
does not push content. The controller deploy key cannot push content. The
airunner does not receive the repository or any bootstrap credential.

After the initial `main` push, the human removes the repository from the App
installation. The retirement action confirms that the App can no longer list
the repository and that the read-only deploy key can still fetch `main`. It then
removes the temporary App ID, installation ID, and private key from the
controller. The human can delete the dedicated App later in the GitHub web
interface.

`platform-instance sync` authenticates with the root-held deploy key, refuses
an anonymously readable repository, accepts only a fast-forward of `main`, and
keeps the checkout push-disabled. It writes an immutable, root-owned Instance
Source Receipt v1 below:

```text
/var/lib/klokast/instance-sources/<commit>/<receipt-sha256>.json
```

The receipt records the private repository name only in root-controlled
controller evidence. Plan v1 copies only its repository hash and numeric ID,
not the private repository name or local path. A receipt is invalid after 30
minutes. Plan v1 refuses a receipt that does not match the checked instance
branch and commit.

## Deferred milestones

After the private-source and Plan v1 milestone, separate reviewed milestones
may implement, in this order:

1. a separately authorized `apply` with fencing, revalidation, rollback, and
   live verification;
2. private-instance migration while all compatibility-only fields remain under
   their current authority;
3. removal of legacy inventory and registry authority only after parity,
   rollback tests, and an explicit observation period succeed;
4. application-specific configuration schemas;
5. site executors under a later deployment schema version.

The current milestone does not install `klokast` as an ambient controller
command, author or push a private commit, migrate an instance, alter existing
deployment inventory or platform-resource inputs, or apply a plan. Repository
creation is an explicit human GitHub action. Controller registration and
deploy-key registration occur only when the human supplies the separate signed
intents described above.
