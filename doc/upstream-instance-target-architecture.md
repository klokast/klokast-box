# Upstream/instance target architecture

## 1. Purpose and non-negotiable invariants

This document is the living design authority for the transition from legacy
deployment state to private-instance authority. It records the current
implementation, the target transition design, and the gates for later
milestones. It does not authorize an apply or remove a legacy authority.

The related documents have separate ownership:

- [Architecture](architecture.md) owns stable trust boundaries, execution
  loci, and Trusted Computing Base (TCB) rules.
- [Klokast Instance Specification v1](klokast-instance-specification.md) owns
  the normative JSON contract and `klokast` CLI behavior.
- This document owns transition status, Plan semantics, future apply,
  migration, and acceptance gates.
- [Private Instance Bootstrap](../klokast-dev/runbooks/40-private-instance-bootstrap.md),
  [Secret Authority](secret-authority.md), [Platform Map](platform-map.md), and
  other runbooks own exact human and controller procedures.

These invariants apply to all transition milestones:

- The public implementation and private desired state are separate Git
  authorities.
- Generated and observed state is output. It is never another Git authority.
- The active controller is the only Platform mutation locus and the only
  Platform secret custodian.
- The airunner can author public implementation changes. It cannot hold the
  private instance repository, controller credentials, or Platform private
  state.
- The human authors and pushes private desired-state changes from a trusted
  workstation.
- A deployable binary comes only from the active controller's sealed,
  networkless builder workflow.
- Read-only evidence does not authorize execution.
- A legacy-only field keeps its current authority until a later schema or an
  explicit replacement owns it.
- A later apply must fail closed on incomplete authority, stale evidence, or
  changed inputs.
- Legacy authority removal requires separate, explicit human approval after
  migration, rollback, idempotence, and observation gates pass.

The authority model is:

```text
effective desired state
    = one builder-approved klokast/klokast-box commit
    + one private klokast-instance.json

runtime authority
    = effective desired state
    + controller-held secrets
```

The engine lock selects the exact public commit that interprets the instance.
It does not make generated state authoritative.

## 2. State ownership

Klokast separates five state classes.

| State class | Owner and location | Required boundary |
| --- | --- | --- |
| Public implementation | `klokast/klokast-box` | Generic architecture, schemas, CLI code, app manifests, automation, tests, and the canonical instance template. It contains no private deployment values or user data. |
| Private desired state | One private `<family>/klokast-instance` repository | The two authoritative JSON files declare instance intent and select one immutable engine commit. The repository is not a fork of the public implementation. |
| Secrets | `/etc/klokast` on the active controller | Root-controlled credentials and broker state stay outside Git. An airunner and standby controller do not receive them by default. |
| Generated and observed state | `/var/lib/klokast` on the active controller | Inventories, facts, observations, plans, provenance, source receipts, execution receipts, and verified build outputs are evidence or output. They are not desired-state inputs. |
| Application data | Application storage | Persistent user-service data has its own retention, backup, restore, and deletion lifecycle. Omission from desired state does not authorize deletion. |

Rebuildable downloads and caches can use `/var/cache/klokast`. Temporary
process state can use `/run/klokast`. Neither location is an authority.

## 3. Current two-file JSON instance model

Instance Specification v1 has two authoritative files:

- `klokast-instance.json` declares topology, Tailscale membership,
  connectivity, controllers, an ordered list of exact airunner runtime identities,
  typed app features, placement, and retained-data intent.
- `klokast.lock.json` selects the canonical engine repository, the readable
  branch name, and the exact full engine commit.

The support files `.gitignore`, `AGENTS.md`, and `README.md` are tracked but
are not additional desired-state documents. The repository has no private app
manifest, inventory, generated-state directory, or site-executor interface.

The normative schemas, field rules, examples, and CLI exit behavior are in the
[Klokast Instance Specification v1](klokast-instance-specification.md). This
document does not duplicate those schemas. The old unreleased YAML instance
files are invalid inputs.

Version 1 has no top-level site catalog. Each box declares its stable `site`
label, two-letter `country`, and `description` directly. When more than one box
uses the same site label, all such boxes must declare the same country and
description. The resolver derives its normalized site projection from these
box-owned values.

The document has no instance ID. The private repository identity and the
source receipt identify the desired-state source. A second free-form ID would
not add authority. The root Tailscale object is `tailscale`, and each box uses
`connectivity` for its set of connectivity-profile names.

Public code, documents, examples, and test fixtures use neutral box, site, and
country values. A public bootstrap template can describe a two-box shape, but
it cannot supply deployment identities or locations. The human must author
those values in the private repository before publication.

Version 1 fixes the engine repository to
`https://github.com/klokast/klokast-box`. Custom engine repositories and
public upstream forks are outside version 1.

## 4. Controller, airunner, and build authority

Only the active `<box>-ops` controller can inspect controller-private inputs,
hold secrets, create authoritative evidence, or mutate the Platform. A
standby controller is not a second active authority. Promotion must first
fence the old active controller.

Instance Specification v1 stores `airunners` as a non-empty, duplicate-free,
ordered array of exact runtime identities. The first identity has the highest
preference. The order does not start, stop, select, or fail over a runner.
Every listed identity remains desired and must be online.

The supported current runtime form is `<box>-ops-airunner`. It is a
root-managed container in `<box>-ops`, has `tag:airunner`, and shares the
controller VM kernel and compromise domain. Its exact private identity comes
only from `klokast-instance.json`. It must not receive controller-private
mounts, the Podman control socket, controller credentials, or private instance
state.

An exact `<cloud>-ops` identity is valid only when the private desired state
declares it as an airunner. It then has `tag:infra` and remains online. A cloud
host otherwise belongs only to bootstrap history. There is no current
requirement for a cloud runner, and the current design does not depend on one.

An optional `<box>-airunner` Xen VM could provide a stronger isolation
boundary. Version 1 does not declare that runtime form. Its schema, lifecycle,
network policy, migration, and recovery design are deferred. It is not a
migration requirement.

The active controller creates deployable `klokast` binaries through
`platform-builder build-klokast-cli`. The sealed builder uses a short-lived,
zero-VIF Xen snapshot, injected approved source, vendored modules, and a
digest-pinned Go image. The controller verifies the source repository, ref,
commit, test result, binary digest, embedded engine identity, and receipt.
Neither the airunner nor the credential-bearing controller directly builds a
deployable binary.

## 5. Deterministic projection and legacy compatibility

The resolver is offline and deterministic. It uses only the two checked JSON
documents and the embedded public manifests and connectivity profiles. It has
no host discovery, network access, environment-dependent defaults, or current
runtime input.

The projection contains the selected engine, Tailscale groups, sites derived
from box metadata, boxes, derived runtime names, UTC
timezone, controller placement, the exact ordered airrunner identities, app
placement, typed features, and retained-data intent. It sorts maps and
unordered sets. It preserves airrunner order. The projection hash changes when
any authoritative input or ordered runner priority changes.

The compatibility planner compares the projection with three current legacy
authorities:

- the private deployment document;
- the private platform-resource registry;
- the controller HA registry.

The engine also supplies its checked execution inventory. The adapter can
derive legacy runtime names, Tailnet groups, connectivity capability fields,
controller placement, app placement, app feature selections, and data intent
where the version 1 contract has an exact representation.

The adapter does not silently discard fields that version 1 cannot express.
Examples include legacy bridge ports, DHCP reservations, shared-guest
settings, app runtime state, app VM definitions, managed-device bindings,
ingress mode, and ephemeral approvals. Each such field stays under its named
legacy authority until a later design replaces or adopts it.

Compatibility is read-only. It does not render inventory, contact hosts,
change a legacy file, or make the private instance authoritative.

## 6. Observation v1 and `standard_substrate_v1`

`platform-map export-observation` converts one existing mapper snapshot into a
bounded, redacted Observation v1 document. Export does not collect facts. The
observation contains only its schema and generation hashes, UTC observation
time, source controller, normalized Tailnet machine presence, online state and
tags, and the box-level dom0 and Xen guest state needed by the named health
scope.

`klokast doctor` is offline and read-only. It verifies the observation hash,
freshness, active-controller source, projection hash, required Tailnet
identities and tags, dom0 reachability, Xen availability, and the running,
configured, and autostart state of the standard router, `bak`, `dmz`, `iot`,
and controller guests. It checks each declared airrunner identity for
presence, online state, and its required tag.

The health scope is exactly `standard_substrate_v1`. It does not claim any of
these health properties:

- application process, container, feature, or application-data health;
- backup or restore health;
- retained-data integrity or recoverability;
- managed-device health;
- app-specific network-flow health;
- compatibility-only field or resource health.

Extra legacy resources do not become desired state and do not make this
limited health result false. Their legacy authority remains active.

## 7. Instance source custody and publication

The private repository bootstrap is implemented. The human creates the exact
empty private organization repository `<family>/klokast-instance`. A
dedicated, temporary GitHub App can verify that repository and register one
read-only controller deploy key. The App has repository Administration write,
Metadata read, and no Contents permission. It cannot push instance content.

The MacBook helpers separate public engine review from private content review:

- `prepare-private-instance-bootstrap` verifies the current public helper
  checkout, the approved sealed engine, controller access, and the dedicated
  Touch ID approval signer.
- `prepare-private-instance-worktree` collects private values in the trusted
  terminal, creates a staged repository through the sealed engine, and
  transfers it to the MacBook.
- `publish-private-instance` accepts the initial seed or a later staged change
  to `klokast-instance.json`. It sends the candidate document through standard
  input for a sealed controller check, compares the checked tree with the
  staged MacBook tree, displays the private diff only on the MacBook, and
  commits and pushes only after human approval.

The update helper refuses a changed remote base, unstaged changes, changes to
support files, or a direct human edit to the lock. It does not merge or
overwrite a changed remote branch.

After the first human push, the human removes repository access from the
temporary App. The controller retirement action confirms that the App no
longer has repository authority. For a single-repository organization, the
human can uninstall the App installation and keep the App identity until the
controller proves that the installation no longer exists and that the
dedicated App has no other installation. Retirement also confirms that
anonymous reads fail and the root-held deploy key can still fetch `main`. It
then deletes the temporary App credential.

`platform-instance sync` maintains a clean controller deployment checkout
with a disabled push URL. Each authenticated fast-forward fetch creates a
fresh, content-addressed Instance Source Receipt v1. The receipt records the
private repository identity only in root-controlled controller evidence. Plan
v1 copies the repository hash and numeric ID, not its name or local path.

The exact bootstrap, publication, update, retirement, and recovery procedures
are in [Private Instance Bootstrap](../klokast-dev/runbooks/40-private-instance-bootstrap.md).

## 8. Plan v1 evidence and gates

Plan v1 is a deterministic, read-only evidence artifact. Its exact inputs are:

1. A checked Instance Specification v1 checkout with
   `klokast-instance.json` and `klokast.lock.json`.
2. The exact builder-approved engine repository, ref, commit, binary, and
   sealed-builder receipt.
3. The legacy deployment document.
4. The legacy platform-resource registry.
5. The legacy controller HA registry.
6. A fresh Observation v1 document from the declared active controller.
7. A fresh, root-owned Instance Source Receipt v1 that matches the checked
   instance branch and commit.

The compatibility planner assigns each compared scope one finding class:

- `matched`: instance intent equals the legacy value.
- `derived`: the deterministic adapter supplies the value.
- `compatibility_only`: version 1 cannot express the field. The finding must
  name one continuing legacy authority.
- `conflict`: instance intent and legacy intent differ.
- `unsupported`: the adapter cannot safely interpret the input.

The artifact converts findings into sorted, closed action descriptions:

- `adopt_instance_specification` proposes that a future authorized apply move
  a matched or derived scope from its current authority to
  `instance_specification_v1`.
- `retain_legacy` keeps a compatibility-only scope under its named legacy
  authority and performs no mutation.
- `verify_substrate` records the read-only `standard_substrate_v1` check.

These are proposed actions. The current `future_authorized_apply` executor
name is a marker, not an implemented executor.

A valid Plan v1 records refusals for:

- each `conflict` or `unsupported` compatibility finding;
- a compatibility-only finding without a known continuing authority;
- an unknown finding class;
- each `doctor` drift finding;
- a dirty or unborn instance repository;
- an instance source receipt that does not match the checked branch and
  commit.

Malformed, unsafe, stale, or internally inconsistent inputs produce validation
diagnostics or an operational failure instead of executable evidence. The
planner re-reads the instance and compatibility inputs and refuses a coherent
artifact if they change during planning.

Plan provenance contains:

- the exact instance branch and commit;
- SHA-256 hashes of both authoritative JSON files;
- the source-receipt hash, private-repository hash and numeric ID, remote ref,
  fetched commit and time, and read-only deploy-key fingerprint;
- the exact engine repository, ref, and commit;
- hashes of all three legacy compatibility inputs;
- the full deterministic projection and its hash;
- the observation source, time, and generation hash;
- the explicit `standard_substrate_v1` health scope;
- all findings, authority assignments, actions, rollback metadata, refusals,
  and diagnostics;
- `plan_sha256`, which covers every other artifact field.

It does not contain local input paths, the private repository name, or
suspected secret values. `platform-plan` verifies the active-controller guard,
sealed builder receipt, binary digest and version, root-owned source receipt,
observation source, and canonical plan hash. It stores the result without
replacement below `/var/lib/klokast/plans/`. A valid refused plan can be kept
for audit. The Plan root and each content-addressed commit directory are
root-owned, group-readable and group-traversable by the controller account,
and mode `0750`. Each immutable Plan is root-owned, group-readable, and mode
`0440`. This access lets the controller account review stored evidence without
granting it permission to replace that evidence.

The gates have distinct meanings:

- `compatible` means that there is no `conflict` or `unsupported` finding.
- `substrate_healthy` means that `doctor` found no drift in its exact
  `standard_substrate_v1` scope.
- `deployable` means that the instance is a clean commit, the source receipt
  matches it, the compatibility and limited health evidence is complete,
  every finding has a proposed action or named continuing authority, and no
  refusal exists. It does not authorize execution.
- `authority_ready` means that every compatibility scope has exactly one
  proposed or continuing authority.
- `legacy_removal_ready` means that no field retains legacy authority.

The current Plan v1 implementation is read-only and implements the evidence
artifact. Its `authority_ready` calculation does not yet prove exact action and
authority coverage for each compatibility scope. Until that check is hardened,
a human must confirm that every `compatibility_only` finding names one
continuing authority. A current `legacy_removal_ready: false` result is
expected.

## 9. Engine promotion target design

Version 1 fixes the engine repository to the canonical
`klokast/klokast-box` upstream and pins one full commit. Initialization and
publication protect the existing lock, but no controlled workflow can yet
promote it to a later canonical commit.

The next design loop must specify one closed promotion from the current locked
commit to one reviewed canonical commit. Before promotion can move to
`decided`, the design must define:

- who proposes, reviews, and authorizes the new commit;
- how the controller proves canonical upstream ancestry and the selected ref;
- how the sealed builder tests and builds the exact candidate;
- which old and new source, schema, binary, and receipt hashes are inputs;
- how the private lock update is delivered to the trusted MacBook without
  giving the controller or airunner private-repository push authority;
- all refusal cases for divergence, downgrade, stale evidence, or mismatched
  engine identity;
- rollback to the last accepted engine and recovery when either engine cannot
  read the current instance;
- deterministic tests and a live acceptance procedure.

Custom engine repositories remain outside version 1. Promotion must not add a
generic repository selector or make the airunner an engine-approval authority.

## 10. Authorized apply target design

Authorized apply is not implemented. A later apply must consume one immutable,
deployable Plan artifact. It must not recompute an open-ended operation from
ambient controller state.

The apply boundary requires all of these controls:

- active-controller fencing before any mutation;
- exact revalidation of the sealed engine binary and receipt, private instance
  commit and input hashes, Plan hash, source receipt, and Observation v1
  generation;
- a closed set of typed executors and inputs, with no generic shell or
  arbitrary playbook escape;
- one action-specific rollback preparation before each mutation;
- separate, explicit human authorization bound to the exact Plan and action
  set;
- immutable execution receipts that record the authorization, executor,
  before and after authorities, input hashes, result, and rollback material;
- live post-apply observation and verification against the declared action
  result.

Apply must refuse stale observation, changed desired state, changed legacy
inputs, incomplete authority coverage, unavailable rollback, inactive
controller status, unknown executor or action type, and any scope not present
in the authorized Plan. A failed post-apply verification must stop later
actions and use the prepared action-specific recovery path.

The first pilot must use one narrow, real Plan scope that is not
compatibility-only. It must not delete application data, remove a legacy
authority, add site executors, or expand the application schema.

## 11. Staged migration and legacy removal

Migration must use this order:

1. Complete read-only evidence.
2. Design and prove engine promotion.
3. Harden Plan authority coverage.
4. Implement one narrow authorized apply pilot.
5. Migrate scopes while legacy-only fields keep their current authority.
6. Test rollback and idempotence.
7. Observe the result.
8. Remove legacy authority only after explicit approval.

Each migrated scope must have one authority before, during, and after the
change. A scope cannot be controlled by both the instance and a legacy input.
A compatibility-only field cannot disappear because a new schema omits it.
The migration design must name its replacement, preserve its existing
authority, or refuse the transition.

Legacy removal is a separate milestone. It requires complete parity, no
retained legacy action, successful rollback and idempotence tests, a defined
observation period, recovery documentation, and explicit human approval.
Application-data deletion always remains a separate, typed, human-authorized
operation.

## 12. Implementation status and design work queue

Design loops use only these state labels:

- `proposed`: the problem and constraints are recorded, but design gates are
  incomplete.
- `decided`: authority, inputs, outputs, refusal cases, rollback, tests, and
  recovery are complete in this document.
- `implemented`: the decided design exists in checked-in deterministic code
  and has repository-level verification.
- `live-verified`: the implementation passed its approved real-environment
  acceptance without weakening a boundary.

A later milestone must update this document before code work starts. A
milestone can move to `decided` only when every required design item above is
complete.

| Milestone | State | Current status and next gate |
| --- | --- | --- |
| JSON contract | `implemented` | The two-file Instance Specification v1 is implemented and remains unreleased. Its current shape uses `tailscale`, box `connectivity`, and no instance ID. Public fixtures contain no deployment-specific values. |
| `klokast init` | `implemented` | Deterministic offline repository creation is implemented. |
| `klokast check` | `implemented` | Closed JSON and repository validation is implemented. |
| Compatibility planner | `implemented` | The deterministic comparison with all three legacy inputs is implemented. |
| `klokast doctor` and Observation v1 | `implemented` | The limited `standard_substrate_v1` read-only health check is implemented. |
| Sealed builder | `implemented` | The networkless controller-managed CLI build and receipt path is implemented. |
| Private source custody | `implemented` | Private repository bootstrap, read-only deployment checkout, and source receipts are implemented. Live instance state is not recorded in the public repository. |
| MacBook bootstrap, publication, and updates | `implemented` | Helpers and deterministic tests are implemented. Real macOS and Touch ID acceptance remains an external verification item. |
| Plan v1 | `implemented` | The hashed artifact is implemented as read-only evidence. Exact per-scope authority-coverage hardening remains. |
| Engine promotion | `proposed` | It is not implemented. Complete the canonical engine-commit promotion design next. |
| Authorized apply | `proposed` | It is not implemented. Design closed executors and rollback types before the pilot. |
| Migration and legacy removal | `proposed` | Work has not started. It follows promotion, authority hardening, and the apply pilot. |

The first four items in the ordered design work queue remain gated by a
successful read-only acceptance Plan. A contract-valid private instance and a
successful source synchronization do not satisfy that gate. Before a retry,
the human must review private desired state against each exact current legacy
authority. The process must not rewrite desired state from an observation or
weaken a `conflict` finding to make the Plan pass.

The exact current legacy controller HA desired-state document needs a defined
controller-private custody and provenance path. A checked-in neutral example
is not a valid live compatibility input. This design item is `proposed`; decide
its authority, creation, update, rollback, test, and recovery behavior before
the next acceptance attempt.

The ordered design work queue is:

1. Specify canonical engine-commit promotion. Keep custom engine repositories
   outside version 1.
2. Correct `authority_ready` so that it proves one action and one authority for
   every compatibility scope.
3. Define closed executor and rollback types, including refusal, test, audit,
   and recovery behavior.
4. Design one narrow apply pilot from a real Plan scope that is not
   legacy-only.
5. Use successful pilot evidence to design staged scope migration and the
   later legacy-removal gate.

After this documentation milestone is merged, the active controller must run
the existing private-instance read-only acceptance flow. The flow must verify
redacted bootstrap retirement and source status, stop for human completion if
the temporary App still has authority, synchronize the read-only checkout,
refresh the Platform map, export a fresh owner-only Observation v1, and store a
Plan v1 made with the currently locked sealed engine. The result must have no
`conflict` or `unsupported` finding. A human must confirm one continuing
authority for each `compatibility_only` finding.
`legacy_removal_ready: false` is expected. The controller keeps the Plan and
private findings. Only generic design defects can return to this public
document. Runtime drift in the limited health scope is a refusal. The
read-only acceptance flow must report it and must not repair, start, stop, or
reconfigure a Platform host.

Application schema expansion, application-data deletion, site executors,
custom engine repositories, and isolated airunner VMs remain deferred.
