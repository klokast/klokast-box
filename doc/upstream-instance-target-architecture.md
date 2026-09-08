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
`connectivity` for its set of provider-neutral capabilities. Every box
includes `overlay`.

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
documents and the embedded public manifests and connectivity capabilities. It has
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
identities and tags, dom0 reachability, and Xen availability. The router and
controller guests must be present, online, configured, running, and set to
start automatically. The shared `bak`, `dmz`, and `iot` guests must have the
expected Tailnet identity and tag and a Xen configuration. They can be
intentionally stopped, offline, and not set to start automatically. Each
declared airunner identity must be present, online, and have its required tag.

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
5. The exact private legacy controller HA registry from the controller's
   private state.
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
- `refuse` binds a conflict or unsupported finding to a non-executable action.
- `verify_substrate` records the read-only `standard_substrate_v1` check.

These Plan v1 actions are proposed. Its `future_authorized_apply` executor
name remains a marker and is not accepted by the implemented Tailnet executor.

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
- `authority_ready` means that every finding has one canonical ID and exactly
  one action. Each compatibility-only finding also has exactly one continuing
  authority assignment with the matching source digest.
- `legacy_removal_ready` means that no field retains legacy authority.

The current Plan v1 implementation is read-only. It rejects duplicate,
missing, mismatched, and extra finding coverage. A human still reviews each
compatibility-only finding and confirms its continuing authority.
`legacy_removal_ready: false` is expected while any such authority remains.

## 9. Engine promotion decision

Status: `live-verified`. The real MacBook Touch ID promotion and controller
activation passed on 2026-08-24.

Version 1 promotes only on canonical
`https://github.com/klokast/klokast-box` `main`. It supports metadata-only
promotion and closed, reversible Instance v1 transitions from the legacy
top-level site form to the current inline box-metadata form. The airunner authors and
pushes public implementation code. The active controller tests and builds the
exact public commit. The human selects the engine, approves it with the
existing `human-private-instance` Touch ID signer, and pushes the private
commit. The private Git commit is the engine-selection authority. Promotion
and activation receipts are immutable evidence. They do not select an engine.

Metadata-only promotion can change only these values:

- `klokast-instance.json` `$schema` commit;
- `klokast.lock.json` `$schema` commit;
- `klokast.lock.json` `engine.commit`.

The legacy Instance v1 transition additionally renames `tailnet` to
`tailscale`, renames each box `connectivity-profiles` field to `connectivity`,
copies each referenced site's country and description into its boxes, and
removes the redundant instance ID and top-level site map. The controller
reconstructs the candidate independently, requires every legacy site to be
used, and proves the inverse transform equals the exact base document. No
private value can change. The connectivity transition maps `tailscale` to
`overlay` and maps `local-ap-direct-egress` to the adjacent
`local-ap-uplink`, `direct-wan-egress` pair. Its inverse accepts only
`overlay` and that complete pair. It rejects partial pairs, unknown values,
edge ingress, and direct ingress. Other schema migrations, custom repositories, and
an arbitrary downgrade are outside this milestone.

The trusted MacBook command is
`klokast-dev/bin/promote-private-instance-engine`. It requires clean and
synchronized public and private `main` branches. It reads the old engine only
from the private lock. A normal promotion requires the new commit to be the
exact public and controller `main` commit and a strict descendant of the old
commit. The helper builds the candidate in an owner-only temporary directory,
shows the complete private diff only on the MacBook, and sends one bounded
candidate envelope through standard input. `--check` never edits the real
private worktree.

The active controller exposes `promotion-preflight`, `promotion-approve`,
`promotion-activate`, and `engine-status` through `platform-instance`. These
operations are evidence and validation interfaces. They are not ambient
engine selectors. Preflight requires the active `smith` controller, canonical
public `main`, exact ancestry, exactly identified sealed builds, successful
builder tests, matching embedded engine identities, a fresh private source
receipt, and an exact MacBook, GitHub, and controller private base. The target
binary must accept the candidate. The current binary must accept the prepared
rollback form.

The canonical 10-minute signed intent binds both engine commits, both build
operations, both binary and builder-receipt hashes, the controller public
commit, private repository hash and numeric ID, private base commit and tree,
candidate and rollback trees, the exact schema-transition identifier,
source-receipt hash, signer, nonce, issue time, and expiry. The installed
`ksa-instance` wrapper verifies the scoped signer,
consumes the nonce, audits the action, and stores an immutable promotion
receipt. Promotion and activation directories are root-owned, readable and
traversable by `smith`, and mode `0750`. Receipts are root-owned,
`smith`-readable, and mode `0440`:

```text
/var/lib/klokast/engine-promotions/<engine-commit>/<receipt-sha256>.json
/var/lib/klokast/engine-activations/<private-commit>/<receipt-sha256>.json
```

These receipts contain hashes and numeric repository IDs. They do not contain
the private repository name, a private path, or private JSON. Activation
fetches private `main` with the read-only deploy key. It requires the fetched
commit tree to equal the approved candidate tree and binds that private
commit, its fresh source receipt, the promotion receipt, the sealed build, and
the selected engine.

After the first private commit, candidate publication uses the exact clean
deployment checkout, not the unborn seed. It requires a fresh source receipt
and an exact MacBook, GitHub, and controller base. It resolves the engine from
the private lock and its activation receipt. The bootstrap session and its
hard-coded build remain valid only for repository creation and initial
publication. A later normal update can change only `klokast-instance.json`.
The controller uses the recorded inverse schema transition to reconstruct its
rollback form, which must also pass the recorded rollback engine.

Rollback is a new forward private commit. `--rollback` can select only the
previous engine in the active activation receipt. It reconstructs the current
semantic instance with the previous schema URLs and lock, verifies the result
with the previous sealed binary, obtains a new Touch ID approval, pushes
without force, synchronizes the controller, and creates a new activation
receipt. It cannot hide Platform drift or a private-intent conflict. If a
candidate fails before publication, no private file changes. If publication
succeeds and activation fails, the helper stops and reports the checked
rollback command.

The workflow refuses dirty, divergent, unborn, unrelated, equal, stale,
tampered, unsupported, or non-canonical inputs; unexpected private changes;
missing or ambiguous sealed evidence; changed source bases; expired intents;
wrong signers; changed signed bytes; nonce reuse; and a candidate or activated
tree mismatch. Live acceptance must test interruption and resume boundaries,
forward rollback, later publication without bootstrap pins, receipt modes and
hashes, focused contracts, and the exact controller-sealed build.

## 10. Authorized apply decision

Status: `live-verified`. On 2026-08-25, the byte-preserving Tailnet authority
pilot passed adoption, verification-only planning, forward rollback,
re-adoption, final verification-only planning, and exact signed-intent replay
refusal. The final authority is Instance Specification v1 for all three
scopes. The live policy bytes stayed unchanged.

Apply consumes one immutable, deployable Plan v2 artifact. It does not
recompute an open-ended operation from ambient controller state. Plan v1 stays
valid as read-only historical evidence, but Apply always refuses Plan v1 and a
Plan without the two v2 bindings below.

### 10.1 Authority state and Plan v2

`klokast.authority-state.v1` is immutable, canonical JSON. Its hash is the
SHA-256 digest of its canonical bytes. States are stored without replacement
at `/var/lib/klokast/authority-states/<sha256>.json`. One root-owned pointer is
replaced atomically and contains only the active hash and a newline.

The initial state has no migrated scopes. Each later state records the prior
state hash, the exact transitioned scopes, the resulting authority for each
scope, the signed Apply intent hash, and a unique transition identifier. A
rollback creates a new forward state that assigns the scopes to the legacy
deployment authority. It never edits or deletes earlier evidence. A state is
invalid if its schema, hash, prior link, scopes, authorities, intent hash, or
transition identifier is absent, duplicated, unknown, or inconsistent.

Plan v2 adds the following required inputs and bindings to Plan v1:

- `--authority-state FILE`, bound by its canonical content hash and active
  state hash;
- `--controller-toolchain-receipt FILE`, bound by its canonical content hash;
- one atomic action group containing exactly
  `deployment.tailnet.magicdns_suffix`,
  `deployment.tailnet.groups.operators`, and
  `deployment.tailnet.groups.family`.

Each scope must have a `matched` compatibility finding and the expected legacy
deployment source digest. An unmigrated scope has the action
`adopt_instance_specification`. A migrated scope has
`verify_instance_authority`; it does not propose adoption again. Mixed
authority across the three scopes, a partial group, another action, or another
executor is a refusal. Plan v2 remains read-only evidence.

`klokast.controller-toolchain.v1` proves that the controller has a clean public
checkout at the selected engine commit. It binds the exact checked-in and
installed SHA-256 hashes for the sealed engine, `ksa-instance`, `ksa-apply`,
the active-controller guard, policy renderer, fixed policy template, and the
internal Tailnet policy mutation helper. Missing, extra, dirty, mismatched, or
non-regular inputs are refusals. `platform-instance` and installed root
wrappers also compare their own installed bytes with the checked-in source.
They refuse drift and print the exact tagged `converge-ops-controller` command
that repairs the selected controller.

### 10.2 Source recovery and authorization

`platform-instance source-recovery-check` uses the existing controller-held
read-only deploy credential. It fetches the active private branch and exact
commit into a fresh owner-only temporary directory. It verifies the fetched
tree, engine lock, source receipt, and sealed engine. It does not change the
canonical checkout, Git remote, credential, or private desired state. It
removes the temporary checkout and stores a redacted, immutable
`klokast.private-source-recovery.v1` receipt.

Apply uses a separate Touch ID purpose. The principal is
`human-platform-apply`, and the controller allowed-signers file is
`/etc/klokast/secret-authority/allowed-signers-platform-apply`. The signer is
installed, checked, used, and synchronized as a separate controller-HA item.
The broader private-instance signer cannot authorize Apply.

Preflight creates canonical `klokast.apply-intent.v1` bytes. The intent is
single-use and expires ten minutes after issue. It binds the Plan, authority
state, source-recovery receipt, source receipt, Observation, private commit,
legacy hashes, sealed engine, toolchain receipt, exact three-scope action set,
executor, rollback type, live policy body hash, live ETag, candidate hash,
nonce, issue time, and expiry. The MacBook helper displays those exact bytes.
Its `--check` mode is non-mutating. Normal mode signs only those bytes through
the `human-platform-apply` Touch ID identity and transfers only the intent and
signature.

### 10.3 Closed executor and preflight

The only Apply executor is `tailnet_policy_inputs_v1`. It accepts exactly the
three Tailnet scopes as one atomic action set. Its only rollback type is
`tailnet_policy_preimage_v1`. It has no generic command, path, playbook, scope,
or executor input and no extension field that can create such an escape.

Preflight revalidates the active controller, Plan v2, authority state, private
source-recovery receipt, Observation freshness and source, private commit,
legacy hashes, sealed engine, source receipt, and controller toolchain receipt.
For adoption, all three findings must be `matched`, unmigrated, and controlled
by the expected legacy deployment source. For rollback, all three must be
migrated together and controlled by Instance Specification v1.

The fixed public template is rendered independently with the private-instance
inputs and with the unchanged legacy deployment shadow. The renderer accepts
exactly one of those input modes. Preflight gets the live policy and ETag and
requires byte equality of instance-rendered, legacy-rendered, and live policy.
It validates the candidate through the Tailnet policy validation endpoint.
Any mismatch refuses the pilot; there is no normalization or weaker semantic
comparison.

### 10.4 Execution, rollback, and recovery

The root `/usr/local/sbin/ksa-apply` boundary exposes only `preflight`,
`execute`, and `rollback`. Execution rejects an inactive controller, a stale or
replayed nonce, an expired or incorrectly signed intent, a partial or unknown
action, a wrong executor or rollback type, and every changed bound input.

Immediately before mutation, execution gets the live policy again and requires
the signed body hash and ETag. It saves the exact body and ETag as immutable
rollback material. It posts the exact candidate with `If-Match` set to that
ETag. An HTTP 412 or any ETag conflict fails closed, as required by the
Tailscale policy API concurrency contract. It then gets the policy and verifies
the exact candidate bytes before it creates and activates the new forward
authority state.

Execution stores immutable intent, signature, execution, rollback-material,
audit, and authority-transition evidence. Receipts and logs contain hashes and
redacted identifiers, not private policy values. Exact policy preimages stay
only in root-only rollback storage because recovery needs them. Nonce
consumption is atomic and occurs before the first mutation attempt, so an
uncertain result cannot be replayed.

If verification fails after the post, the executor gets the new ETag, restores
the exact saved preimage with `If-Match`, and gets and verifies the restored
bytes. A verified restoration records a failed execution with restored runtime
and unchanged authority. If restoration cannot be verified, the result is
`recovery_required`, the authority state does not advance, later actions stop,
and a human must inspect the live policy from the active controller. Recovery
uses the immutable preimage and the same closed rollback operation; it does not
permit a supplied policy body or generic API call.

A human-authorized rollback is also a new forward transition. It applies the
stored exact preimage, verifies it, assigns all three scopes to the legacy
deployment authority, and keeps the unchanged legacy values as the active
source. Re-adoption requires a new Plan and new approval.

### 10.5 Privilege, refusal, and acceptance

The infrastructure account keeps read-only policy pull and validation access.
It has no direct passwordless access to `ts-policy-apply` or another policy
mutation helper. Policy mutation is reachable only through the authorized
root Apply boundary and its internal fixed helper.

Apply refuses stale observation or recovery evidence, changed private or
legacy input, a wrong or inactive authority state, unknown or partial scope,
wrong action, executor, or rollback, expired or replayed intent, wrong signer,
mismatched toolchain, changed live body, changed ETag, HTTP 412, and failed
post-write verification. Deterministic tests must cover each refusal, canonical
hashing, state transitions, forward rollback, wrapper installation, signer
separation and HA preservation, privilege removal, and temporary recovery
without canonical-checkout mutation or private-value logging.

Live acceptance is complete only after adoption, a fresh verification-only
Plan, one real forward rollback, re-adoption, final idempotence, and replay
refusal all pass with immutable evidence. The final authority is Instance
Specification v1 for all three scopes. The legacy values remain unchanged as a
dormant rollback and drift-detection shadow. A later shadow difference is a
refusal, not a second authority. Application deployment, data operations,
schema expansion, site executors, and legacy removal are outside this pilot.

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

## 11.1 First box connectivity source migration

Status: `live-verified` on 2026-09-06. The
[controller preflight directory-mode correction](todo.md#2026-09-05---box-preflight-work-directory-loses-group-access-under-umask-077)
was implemented and activated. Signed execution then exposed a separate
[protected archive path error](todo.md#2026-09-06---signed-box-verification-reads-the-protected-preflight-archive).
The human promoted the combined correction as engine `86b8c7e`, and the
controller verified its sealed build and matching installed tools. The
controller stored verified execution receipt `edf3c7e` and final
verification-only Plan `9dc2cf9`. The MacBook helper confirmed refusal of
the exact repeated signed request because its nonce was already used. All
seven setting-source and desired-state hashes remained unchanged. This change
moves one box connectivity group from the old private Platform resource
registry to `klokast-instance.json`. It does not
change the declared values. It does not move the box that hosts the active
controller. The human must make a later explicit decision before the second
box can move. There is no elapsed-time condition.

The root Apply program must not import or execute Python from the
`smith`-owned public checkout. It contains the sealed-build checks that it
needs. It runs the sealed, read-only Plan command as `smith`. Apply rejects
duplicate JSON keys, trailing JSON, unknown fields, and non-canonical bytes in
stored JSON evidence.

`klokast.controller-toolchain.v3` binds the installed root Apply program, the
active-controller guard, the fixed Tailscale helpers, the policy renderer and
template, `platform-resources`, and the sealed engine. Apply checks the public
checkout commit and clean state again immediately before an approved action.

`klokast.authority-state.v2` is a new immutable chain. Each state links to the
prior Authority State v1 or v2 hash. It contains only approved setting groups
and one source for every complete group. The allowed sources are the old
private file and `klokast-instance.json`. Validation rejects unknown groups,
changed or incomplete scope lists, duplicate scopes, and mixed group
ownership. One separately approved, non-network conversion creates the first
v2 state from the active v1 state. It preserves the current Tailnet source and
sets each box connectivity group to the old registry. It does not change the
v1 evidence chain or network state.

`klokast.plan.v3` has a sorted `action_groups` list. The completed Tailnet
group stays verification-only. Each box has one `box_connectivity_v1` group
with exactly these five findings:

- the box mapping that the resource compiler uses;
- the connectivity methods declared by the instance;
- the available connection methods;
- the enabled connection methods;
- the forbidden connection methods.

All other findings keep the unimplemented-action marker. Plans v1 and v2
cannot authorize a box connectivity action. Plan selects the unique instance
box that does not host the active controller. Planning refuses an absent or
ambiguous selection.

Before approval, Apply copies the old registry to root-owned temporary
storage. It replaces only the selected box access lists with values derived
from the instance. It preserves bridge ports, DHCP reservations, shared-guest
state, applications, and all other old fields. It compiles the old and
effective registries. The comparison removes only `registry_path` and
`registry_sha256`. Every other compiled field and the selected router variable
document must be equal. The signed request binds both input hashes.

The only new executor is `box_connectivity_v1`. It accepts one exact Plan
group and the automatically selected box. It has no command, playbook, path,
application, second-box, or arbitrary-scope input. The existing
`human-platform-apply` Touch ID signer authorizes the action. The
`platform-resources` Apply and verification operations target the selected
router only. They do not operate on applications, shared guests, other boxes,
Tailnet policy, or application data.

Execution revalidates the active controller, Plan v3, Authority State v2,
private commit, old files, observation, source-recovery receipt, sealed engine,
toolchain, public checkout, and both compiler results. It stores rollback
material before the source transition. The immutable rollback copies stay
root-only. Apply makes exact, read-only copies in its short-lived work
directory for the `smith`-owned compiler process, and removes those copies
when execution ends. It creates a new v2 state that assigns the selected group
to the instance. It then applies and verifies the selected router from the
effective input. It verifies controller access and the declared network paths.

Box source adoption, verification, and rollback accept direct or DERP
transport. Authenticated Ansible access, the router hostname, configuration,
services, routes, and firewall checks remain required. The controller runs
one bounded Tailscale probe with `--until-direct=false`; it must return one
recognized reply from the selected router. A timeout, command failure,
wrong peer, or unknown output still stops the operation. Transport is observed
state, not a signed source input or an authorization condition.

When a successful check observes DERP, print one informational notice for
that check. The root wrapper forwards only fixed notice text to stderr so the
Mac terminal shows it without changing JSON evidence on stdout. Its audit
record names the box, operation, check-mode flag, and observed transport; it
does not include the peer endpoint or private configuration. Direct replies
need no notice. This rule also applies to failure restoration and explicit
rollback. The separate direct-IPv6 repair retains its direct-path success
criteria and is not a prerequisite for box source acceptance.

On failure, Apply creates another forward v2 state that restores the old
registry as the selected group source. It runs the same one-router operation
with the old input and verifies the restored state. It reports
`recovery_required` if it cannot prove restoration. The old network values
stay unchanged as a rollback copy. A later difference between the instance
and that copy stops normal Apply until a separate content-change design is
approved.

Repository acceptance must cover Authority State v2 and v1 conversion, Plan
v3, complete closed groups, strict canonical JSON, effective-registry parity,
unique non-controller selection, narrow router commands, stale or changed
inputs, wrong box or signer, replay, and failed restoration.

Development acceptance verifies the existing instance-owned first-box group.
Do not repeat Authority State conversion or initial adoption. Reuse the
selected engine's successful build and activation. If acceptance exposes a
code defect and the human authorizes repair, complete the existing sealed
build and signed promotion workflow before starting a new acceptance attempt.
Install the wrappers that match the selected, activated
engine and verify every required component in a Controller Toolchain v3
receipt. Keep the public controller checkout clean and fixed at that engine
throughout acceptance.

Create fresh source, source-recovery, observation, and Plan v3 evidence. The
selected first-box and Tailnet groups must be verification-only; the
controller box must retain the old registry. Require exact parity between the
instance-derived and old network values. Run the MacBook helper with
`--check`, then approve one `verify_instance_authority` request with
`--prove-replay-refusal`. Require a `verified` execution receipt and refusal
of the repeated signed request because its nonce was already used. All
authenticated router, configuration, service, route, and firewall checks must
pass. Direct transport is preferred; DERP is acceptable and informational.
Confirm that the active setting-source record and desired-state files have
unchanged hashes. Finish with a fresh verification-only Plan v3. These checks
are the agreed criteria for the first-box milestone to become `live-verified`.
There is no fixed observation or waiting period.

Live rollback and re-adoption remain unverified and deferred. Preserve their
code, saved inputs, signatures, receipts, and recovery evidence. They and the
separate direct-IPv6 repair do not block continued development. This narrower
development acceptance does not satisfy the rollback gates for later legacy
removal. A failed required check stops acceptance: record the failure without
changing network settings, restarting Tailscale, or starting IPv6 repair.
Controller identity, applications, the second box, old-file removal, and
application data remain outside this change.

## 11.2 Ops-only overlay IPv6 repair

Status: `implemented`; live acceptance is pending. This action repairs the
existing overlay capability. It does not change Instance Specification,
Authority State, Tailnet policy, source ownership, applications, or data.

`klokast.overlay-direct-repair-intent.v1` is one closed action. It uses the
existing `human-platform-apply` signer. A verification-only Plan v3 selects
the active controller box and its unique peer. The intent binds the engine and
private commits, Plan, Authority State, observation, source receipts, sealed
engine, Controller Toolchain v3 receipt, Freebox gateway hash and API version,
delegation slot and `/64`, stable router link-local next hop, both box IDs,
Huawei prerequisite, and exact router and ops preimage hashes. It accepts no
command, path, playbook, gateway, prefix, zone, or second action from the
human.

Live revalidation compares configuration, not changing measurements. The
original evidence bytes must still match their signed hashes. Only these
differences between the original and fresh evidence are permitted:

- Anonymous nftables `counter packets N bytes N` measurements may increase.
  Rule text, order, quoted strings, named counters, quotas, and limits stay
  exact. Counter resets require fresh approval.
- IPv6 address lifetimes may decrease by at most 600 seconds. Both finite
  lifetimes must remain at least 60 seconds, and preferred lifetime must not
  exceed valid lifetime. Address identity, interface, flags, and infinite
  lifetimes stay exact. Tentative, duplicate, deprecated, expired, renewed
  beyond the signed lifetime, or unrecognized address output is refused.
- The latency in the single successful direct ping line may change. The
  router identity, Tailnet address, global IPv6 endpoint, and UDP port stay
  exact. The endpoint must equal the recorded peer global address; DERP,
  extra lines, and unknown output are refused.

Files, modes, sysctl values, Freebox state, and all other fields stay exact.
This comparison is part of the engine-bound root Apply program, not a helper
loaded from the writable checkout. It validates both snapshots before
approval and execution. It does not rewrite historical evidence or rollback
material. Tests must cover normal countdown and traffic during review,
configuration drift, altered stored bytes, wrong identities, and replay.

The root-owned Freebox broker has four operations: `inspect`,
`configure-ops-delegation`, `verify`, and `restore`. A separate physical
authorization installer stores the application token root-only. The broker
uses the fixed local gateway endpoint and the documented Freebox challenge
authentication. It rejects redirects, gateway or API drift, unknown response
shapes, a delegation array other than eight `/64` slots, the first slot,
collisions, prefix drift, and a next hop that is not link-local. It keeps the
exact old delegation document only in root-only rollback storage. Logs and
receipts use a gateway identity hash.

IPv6 stays disabled by default. The repair installs one stable checked WAN
link-local address on the active router, routes the selected `/64` only to its
ops interface, sets WAN `accept_ra=2`, enables IPv6 forwarding, and advertises
only that prefix. The ops VM adds persistent SLAAC without replacing or
restarting IPv4. Router rules permit required ICMPv6, ops Tailscale UDP source
port `41641`, STUN UDP `3478`, direct Tailscale UDP `41641` input, and
established return traffic. Other zones remain IPv4-only.

The Huawei gateway stays manual. A checked command prints the current peer
router global IPv6 address and the exact UDP `41641` pinhole replacement. Apply
requires the active router to reach the peer router directly through an IPv6
endpoint before mutation. It then prepares the router, configures the Freebox,
enables the ops prefix, refreshes only the active ops and peer router Tailscale
endpoints, and requires `netcheck` IPv6 and a direct IPv6 ping.

Apply stores exact Freebox, router, ops, sysctl, address, and live firewall
preimages. Failure restores every preimage and verifies IPv4 controller and
DERP-capable Tailnet recovery. An unproved restoration records
`recovery_required`. The nonce is consumed before final live revalidation, so
a failed, stale, or successful signed action cannot be replayed.

## 11.3 Controller box connectivity source migration

Status: `live-verified` on 2026-09-07. The human approved source-only adoption
for k002, which hosts the active controller, then approved fresh signed
verification and confirmed exact replay refusal. Engine `cc68fc6` changed
only the k002 connectivity source. Final Plan `783d1dc` reports both box
connectivity groups and Tailnet as verification-only, with no refusals. All
five private desired-state files and all 138 saved persistent configuration
entries on each router remained unchanged. The
[controller-box acceptance record](../klokast-dev/runbooks/48-controller-box-connectivity-source.md#acceptance-record-2026-09-07)
contains the controller-held evidence references.

This completes connectivity source adoption for the existing two-box
deployment. It does not complete the private-instance migration. Section
11.1 remains the completed first-box acceptance record. The contract below
records the implemented and verified decision.

Deployment Plans use `klokast.plan.v4`. The CLI and controller wrapper accept
`--connectivity-target non-controller|active-controller`, with
`non-controller` as the default. The selected box comes only from the private
instance. Planning and execution must compare it with the active-controller
identity. Exactly two boxes are required. Controller-box selection requires
the peer and Tailnet groups to use the instance. Each connectivity group has
its existing five scopes. Every adopted, unselected group reports
verification-only, with no executable action for that unselected group.

The new `controller_box_connectivity_source_v1` executor accepts only
`adopt_instance_specification` and `verify_instance_authority`. Its closed
`klokast.controller-box-connectivity-source-intent.v1` request uses the
existing Touch ID signer and helper. It binds the selected box, controller,
action, Plan, active source record, engine, toolchain, private inputs, and
compiler comparison. It accepts no router apply, rollback, command, or extra
scope. Instance Specification v1, Authority State v2, and Controller Toolchain
v3 remain unchanged. Historical evidence is immutable. Older Plans cannot
authorize this executor. First-box and deferred IPv6 interfaces remain.

Preparation and execution build an effective registry from instance-derived
connectivity values for the selected and already adopted boxes. All unrelated
legacy fields remain. Old and effective compiler outputs must be equal after
removing only their registry path and hash. Selected-router variables must be
equal. Authenticated router access, configuration, services, routes, firewall,
and a recognized direct or DERP reply must pass through the effective input.
Direct transport is preferred; DERP is acceptable.

Signed execution consumes the nonce before live verification. After that
verification succeeds, it rechecks the controller and all bound inputs, then
appends one Authority State v2 record that changes only the selected box's
source. The shared source writer uses a short-lived local file lock and an
exact prior-state check at publication. It refuses concurrent source changes.
This executor never calls `apply-box-access`, router configuration handlers,
service restarts, or gateway repair. Its authority is limited to controller
source publication and evidence storage; compromise remains in the existing
controller TCB. No new account, credential, service, or network flow is needed.

Failure before publication leaves the active source unchanged. Publication
followed by a receipt-storage failure is an incomplete operation. Inspect the
active source and evidence, then use fresh verification and approval. Do not
retry the signed request, run automatic repair, or claim recovery. Live
rollback, re-adoption, direct-IPv6 repair, and legacy-file removal are deferred.

Repository acceptance must cover both targets, adopted unselected groups,
older-Plan refusal, controller identity, exact scopes and input binding,
expired signatures, nonce reuse, and concurrent publication. Signed-path tests
use real temporary files under umask 077 and check runtime readability,
protected archives, receipts, and cleanup. Command tests require verification
only and cover direct, DERP, wrong peer, unknown reply, verification failure,
publication failure, and receipt failure. Run the Python suite, sealed Go
suite/build, and relevant Ansible syntax checks before promotion.

Live acceptance promotes one reviewed implementation commit with the existing
sealed-build and human approval workflows and installs matching tools. Take
input and persistent-router baselines after promotion. Refresh source,
recovery, observation, and Plan evidence when the human is ready. Run unsigned
preparation, then approve adoption with Touch ID. Require a source transition
that affects only k002. Create a fresh verification-only Plan, approve
verification, and prove exact replay refusal through the existing helper.
Store a final Plan with both connectivity groups and Tailnet verification-only.
All desired-state files and persistent router configuration must be unchanged.
No rollback exercise or waiting period is required.

Only these completed checks permit `live-verified`. Record the result and
controller-held evidence in the controller-box runbook and this document.
Commit and push the acceptance documentation without automatically deploying
that documentation-only commit. List the remaining legacy-owned setting
groups from the final Plan for the next human migration decision. This action
does not authorize another migration or legacy removal.

## 11.4 Controller identity source migration

Status: `live-verified` on 2026-09-07. Signed adoption, fresh signed
verification, replay refusal, unchanged-setting checks, and controller and
MacBook resolver checks passed. See the
[acceptance record](../klokast-dev/runbooks/49-controller-identity-source.md#acceptance-record-2026-09-07).
The human authorized this migration and
continued work across the remaining migration scopes without a separate
scope decision each time. Record each bounded design before implementation.
The existing human signing steps still authorize exact engine promotions and
live source transitions. The completed acceptance records remain immutable.
This authorization does not request data deletion, live controller
switchover, or the deferred IPv6 repair and rollback exercises.

### Selection from the final Plan

The review used final Plan `783d1dc` and its exact remaining-scope list in the
[controller-box acceptance record](../klokast-dev/runbooks/48-controller-box-connectivity-source.md#remaining-legacy-owned-settings).
The controller still had the accepted source record during this read-only
review. The saved Plan is historical selection evidence. Implementation and
live approval must use fresh evidence for their selected engine.

| Candidate | Finding and next dependency |
| --- | --- |
| Controller identities | All four HA box/hostname fields match the instance projection. Controller placement is already derived from the instance. Adoption needs an actual source-aware resolver and a closed signed action. This is the recommended next scope. |
| Box bridge ports, DHCP reservations, and shared guests | These remain compatibility-only. Instance Specification v1 has no exact representation for them. They need a separate schema or replacement design before source adoption. |
| Application declarations and data intent | Some fields match or derive from the instance, but omitted/absent apps, cleanup placement, devices, and retained data have different authority and lifecycle rules. This needs an app-specific scope and verification design. |
| Legacy schema metadata and execution inventory | A matched schema version is a format check. It does not establish an instance-owned setting or remove an execution-inventory dependency. These belong to a later replacement/removal decision. |

### Scope, authority, and versioned evidence

Adopt the existing active/standby pair as one `controller-identity-v1` group
with exactly these five scopes:

```text
controller_ha.controllers[0].box
controller_ha.controllers[0].hostname
controller_ha.controllers[1].box
controller_ha.controllers[1].hostname
deployment.control_plane.controller
```

The first four scopes have prior source `legacy_controller_ha`. The last
scope is a derived placement finding, not a stored legacy deployment value;
its prior source is `controller_ha_markers`. Existing HA markers remain the
live permission boundary after desired identity adoption. No caller chooses
another pair or a partial scope set. Exactly two instance boxes and two
controllers are required, with both connectivity groups and Tailnet already
adopted. Identities, live roles, accounts, credentials, applications, data,
network settings, and legacy files stay unchanged.

Instance Specification v1 remains unchanged. Plan v5 adds
`migration_target`, selected by `--migration-target connectivity|controller-identity`.
The default preserves the existing connectivity selector and executors.
Explicit controller-identity selection enables only the new
`controller_identity_source_v1` executor. Already adopted unselected groups
report verification with executor `none` for their unselected box or identity
groups. The existing Tailnet verification group remains available.

Authority State v3 preserves the existing v2 fields and connectivity groups,
adds the closed five-scope identity group with source
`instance_specification_v1`, and adds `controller_identity_adoption` with
`nonce`, `intent_sha256`, and `plan_sha256`. One signed adoption changes v2
to v3 atomically. Later v3 source records preserve this adoption reference.
No separate unsigned conversion publishes state. Older states and Plans
remain immutable and cannot authorize this executor. Existing connectivity
and deferred IPv6 consumers accept the new version while preserving the
identity group and its adoption reference during any separately authorized
operation. Controller Toolchain v4 adds the installed `controller_ha`
resolver to the existing closed component set; historical v3 receipts remain
readable but cannot authorize the new identity executor.

### Resolution and permission

`ksa-apply controller-identity-status` is a fixed read-only controller action.
It emits `klokast.controller-identity-status.v1`: schema version, source,
current source hash, engine commit, and the active/standby box and hostname
pairs. It does not synchronize private input, mint credentials, or approve a
new deployment. Before adoption it validates the existing controller set and
live roles. After adoption it validates the v3 adoption reference, protected
intent and Plan, clean current private and public checkouts, the current
instance controller fields, and exact equality with the adopted pair. It
rechecks source and inputs before returning. A changed desired pair requires
a future explicit identity-change action; this source-only action cannot
move the controller.

The installed `ops-controller-ha` and its MacBook copy use that status action
for normal resolution and dispatch. Local legacy configuration supplies the
fixed account and repository path and contact hints only. Exactly one
reachable contact must return a valid active-controller result; unavailable
or stale standby evidence cannot supply a fallback active authority. An
explicit dispatch hostname must equal the verified active hostname.
`kk` uses the same check for automatic and explicit controller selection.
MacBook promotion helpers retain their explicit bootstrap contact path:
engine promotion and activation still perform their own root controller and
private-source validation. No airunner receives private instance files.

Live checks independently obtain the actual runtime hostname and configured
HA marker from both controllers through the controller's authenticated
transport. Require exactly one active role, one standby role, expected box
and hostname fields, and the same `active_box` in both markers. Missing or
malformed markers, the guard's legacy-unconfigured fallback, unreachable
peers, unknown replies, and conflicting roles are refusals. The new executor
never writes HA markers, promotes, fences, synchronizes private state,
reseeds credentials, runs router configuration, or dispatches an arbitrary
command. It runs in the existing controller TCB; no new account, daemon,
credential, or network permission is introduced.

### Signed execution and failure handling

The closed `klokast.controller-identity-source-intent.v1` binds action,
Plan, current source, controller pair, exact five scopes, private commit and
input hashes, engine/build/toolchain, source/recovery/observation receipts,
and equal old/effective controller configuration hashes plus stable marker
evidence. Permit only adoption and verification. Unknown fields, extra
scopes, rollback requests, changed inputs, expired signatures, and old Plans
are refused. Reuse the existing Touch ID signer and helper.

Preparation stages equal old/effective controller configurations in a
controller-readable temporary directory and saves exact copies in the
root-only preflight archive. The effective configuration derives identities
from the sealed projection and preserves the legacy account and repository
path. Execution verifies the signature and protected preparation, consumes
the nonce, reconstructs and compares every bound input, and checks both live
roles. It repeats input and role checks before publication. Publish one
forward source record using the existing short-lived local lock and exact
prior-state comparison, then write the receipt. Verification writes a receipt
without another source transition. Remove readable runtime copies on every
exit. Keep protected archives and nonce evidence.

Failure before publication leaves the source unchanged. A publication with
no completed receipt is incomplete: inspect the current source and archives,
then use fresh signed verification. Never retry a used request, silently
restore legacy ownership, or repair the network. The adoption reference
permits inspection even if receipt storage failed.

### Recovery and installation order

Keep the human's legacy contact configuration and immutable source,
approval, and build evidence. `ops-controller-ha --legacy-recovery` permits
only the existing status, bootstrap, synchronization, fencing/promotion,
demotion, sanitization, and reseeding procedures. It cannot resolve a normal
active dispatch or run a workload command. This explicit recovery mode uses
the saved contacts when source custody is unavailable and retains the
existing fencing prerequisite for promotion. It is not invoked by migration.
Restore controller source custody and reconcile private desired identity
through the human workflow before normal dispatch resumes. Do not repoint
the active source to an old record. A source rollback or identity change
requires a separately designed signed action; neither is part of this
executor.

The existing non-provider state synchronization includes the immutable Plans
and protected preflight archives as well as source states and receipts. The
v3 identity reference needs its original Plan and intent after reconstruction.
Preserve their root ownership and permissions. Synchronization still excludes
provider credentials and the controller-private instance deploy key.

Promote one reviewed sealed engine, then install matching active-controller
and MacBook tools before adoption. The standby needs its existing guard;
source adoption does not give it private credentials or change its installed
engine. Verify the installed resolver in Toolchain v4. Before promotion,
exercise missing-source recovery and reconstruction from saved controller
contacts and immutable evidence in isolated tests. Historical interfaces
remain available with explicit recovery intent where required.

### Acceptance gates

Tests cover both planner targets, exact scope coverage and source assignment,
v2-to-v3 publication, v3 preservation, and older-plan/toolchain refusal. Test
both resolvers, explicit dispatch, wrong contact hints, changed identities,
missing source/markers, two active roles, unreachable standby, partial or
unknown replies, expired signatures, consumed nonces, concurrent publication,
and failure before publication or receipt storage. Use real temporary files
under umask 077 for signed execution, archive modes, readable runtime copies,
receipts, and cleanup. Command assertions permit verification only. Run the
Python suite, sealed Go tests/build, shell checks, and controller Ansible
syntax checks for the exact implementation commit.

After promotion, save private-file, both HA-marker, and persistent-router
baselines. Use fresh unsigned preparation, signed adoption, fresh signed
verification, and exact replay refusal. Verify automatic and explicit
resolution from the controller and MacBook. Require one identity-source
transition, unchanged identities and roles, unchanged settings, and final
Plans with the identity group, both connectivity groups, and Tailnet
verification-only. Save controller-held evidence, commit and push acceptance
documentation, and continue the remaining migration queue. Do not deploy a
final documentation-only commit automatically.

### Recorded execution result

Engine `c8f863b` and private commit `d43aaab` completed the exact identity
source transition. Authority State v3 `0b34c84` adds the identity group and
preserves both box groups and Tailnet. Final Plans for identity and both
connectivity targets report all four groups as verification-only. The
original input hashes, ownership and modes, both persistent controller
markers, and 138 persistent configuration entries on each router remained
unchanged. Controller automatic and explicit resolution passed, with standby
dispatch refused. The human also verified MacBook automatic and explicit
resolution and its dispatch dry run. All selected the unchanged active
controller. The final acceptance result is `live-verified`.

The runbook holds full controller evidence references and the
[remaining legacy list](../klokast-dev/runbooks/49-controller-identity-source.md#remaining-legacy-owned-settings).
Do not repeat adoption or automatically deploy acceptance documentation.

## 11.5 Remaining registry and inventory migration batch

Registry status: `live-verified`. Registry schema, consumers, and source
adoption are complete. Execution inventory and runner identity adoption are
`decided`; continued implementation is already authorized. The offline
renderer and comparison checkpoint precede the new source executor.

The batch started from identity Plan `9866249`, which contained 22 legacy-source
actions and continuing execution inventory. Final registry Plan `2d2bfbe5`
now has no retained legacy setting actions. It keeps `execution_inventory`
under `legacy_engine_inventory` and one pending runner adoption action with
prior authority `none`. The
[registry acceptance record](../klokast-dev/runbooks/51-registry-source-adoption.md#remaining-authority-and-action)
holds the exact remaining list.

The batch addresses these connected dependencies:

- Give box bridge ports, DHCP reservations, and shared-guest runtime intent
  explicit typed instance representations. Preserve their effective values,
  including existing compiler defaults for omitted fields.
- Define application absence, disabled preselection, cleanup placement,
  managed-device and VM bindings, and retained data before adopting app
  groups. An omitted app can still have legacy cleanup or binding fields.
  Neither absence nor equal active network output permits data deletion.
- Make normal compiler, guest-control, mapping, and application entry points
  consume the selected source. A source record and an equal migration-time
  rendering alone do not replace a later direct read or write of the legacy
  registry. Explicit compatibility reads must not bypass normal source
  ownership for mutation.
- Classify fixed controller account/path rules and schema metadata as engine
  policy or format evidence where appropriate. Preserve explicit controller
  recovery contact inputs. Do not add a source-adoption action merely to
  relabel a version field.
- Replace the continuing execution inventory with instance-derived host
  selection and upstream topology. Audit explicit limits and recovery
  inventory before removing that dependency.

Private-value preparation and publication must remain on the controller and
trusted MacBook. The runner must not receive the private checkout or raw
private configuration. Use exact engine promotion and signed source actions,
compiler and target-variable equality, protected evidence, unchanged-setting
baselines, and checked publication. Keep old files and historical execution
evidence until their separate removal and recovery gates are complete.

### Compatibility checkpoint

Status: `live-verified`. This prerequisite comes before the source executor.
The publication helper checks each candidate with both the active engine and
its previous sealed engine. The current previous engine rejects new fields.
Do not remove that check or use a lossy reverse schema conversion. First
promote an engine that accepts the following optional, closed v1 extensions
without changing private values. The later consumer engine can then use this
checkpoint as its rollback checker. Both promotions use the existing exact
metadata-only workflow. Schema acceptance is not source adoption.

- `boxes.<box>.substrate` contains `bridge-ports`, `dhcp-reservations`, and
  `shared-guests`. Each field is optional so omission stays distinct from an
  explicit empty value. Reservations contain only hostname, IPv4 address,
  and MAC address. Guest roles are `bak`, `dmz`, and `iot`; runtime intent is
  `running` or `stopped`.
- `inactive-apps` stores disabled application configuration, including apps
  whose public manifest was removed. It cannot enable an app. Each entry has
  typed optional placement, Boolean resource selections, device and VM
  bindings, runtime state, ingress mode, isolation, user bindings, and
  ephemeral controls. Placement distinguishes primary, secondary, builder,
  and multi-box targets. An empty primary, secondary, or builder string is a
  saved unselected target; keep it empty. This preserves cleanup targets and preselection.
  An `apps` entry can separately retain data with `desired-state: absent`.
  A present application cannot also have an inactive entry.
- Nonempty references must name instance boxes. Reject unknown fields, nulls, invalid
  addresses, duplicate identities or addresses within a box, unsafe interface
  names, conflicting placements, and malformed ephemeral controls. No raw
  credentials, executable strings, provider keys, or arbitrary command fields
  are accepted. Text labels are data and never execution instructions.

The checker runs without credentials or network access. Its worst-case
compromise is untrusted validation output; it has no publication or execution
authority. Deployment still requires the controller-held sealed binary and
existing root validation. The checkpoint planner must explicitly refuse
deployment when any new extension is present. It must not silently ignore
accepted fields or authorize a new source action. Existing instance inputs,
Plan v5, Authority State v3, Toolchain v4, and signed executors keep their
current behavior. Historical artifacts remain immutable.

Expand omitted-app compatibility findings to expose retained nested settings.
Do not report a disabled app's complete legacy entry as replaceable merely
because its enabled flag is false. This corrects the scope inventory; it does
not adopt those fields or remove the old files.

Verify the old fixtures and planner targets, typed extension acceptance,
closed-schema and semantic refusals, retained-data coexistence, explicit
checkpoint deployment refusal, and deterministic detailed legacy coverage.
Run the Python suite and sealed Go tests/build before checkpoint promotion.
Build from an isolated controller checkout so the active resolver retains its
matching canonical engine until the human promotion starts. The checkpoint
does not install services, apply configuration, publish instance values, or
change source records. Compare those inputs before and after promotion.

The later grouped adoption must still implement normal source-aware consumers,
stale legacy-writer refusal, compiler and inventory equality, protected source
publication, and signed live acceptance. Complete that executor design here
before its implementation. Keep the overall batch `proposed` until those
remaining design gates are complete.

The [checkpoint runbook](../klokast-dev/runbooks/50-registry-migration-checkpoint.md)
owns sealed build, private candidate checks, and metadata-only promotion.
Repository verification passed the Go suite and all 512 Python tests. The
corrected engine `e12b426` passed its sealed build, exact private round-trip
and compiler checks, and metadata-only human promotion. Private commit
`de8e252` is active. The source record and controller pair stayed unchanged.
The checkpoint runbook holds the acceptance references.

### Registry source adoption

Status: `live-verified` on 2026-09-08. Signed adoption, fresh signed verification,
exact replay refusal, final Plan, and unchanged-setting checks passed. Adopt
the remaining registry settings as one atomic group. The
[registry source runbook](../klokast-dev/runbooks/51-registry-source-adoption.md)
contains the implementation and acceptance workflow.
This step uses the existing two-box arrangement, a configured controller pair,
and disabled applications only. It does not enable an application or change
retained data. Execution-inventory replacement remains a separate dependency
in this batch; keep its continuing authority visible until its consumers and
recovery inputs are replaced. Continued work on it needs no new scope decision.

Use the checkpoint's Instance Specification v1 without another schema change.
Each box must have a `substrate` object and the instance must have an
`inactive-apps` map. Keep every legacy app entry, including entries without a
current manifest. Preserve omitted fields, explicit empty targets, false
resource selections, saved expiry values, and retained-data declarations.
The complete old and rendered registry objects must be equal before adoption.
Unrepresented fields or present apps are refusals, not permission to omit data.

The sealed `klokast registry` renderer emits the effective registry, its exact
input hashes, and a sorted scope list. The group owns the three substrate
scopes for each box, every represented inactive-app field including its false
enabled flag, and declared retained-data scopes. Omitted substrate fields
still have explicit source ownership and retain existing compiler defaults.
The private instance determines boxes, apps, and scopes; callers cannot supply
them. Preserve detailed compatibility findings. A complete renderer replaces
the checkpoint's deployment refusal only when all fields compare equally.

Plan v6 adds the explicit `--migration-target registry` selector. The default
remains connectivity. The sole new executor is `registry_source_v1`, with a
closed `klokast.registry-source-intent.v1` approval. It permits source adoption
and verification only. All unselected adopted groups report verification
with executor `none`. The intent binds the exact Plan, current source record,
controller pair, engine/build, toolchain, private/source/recovery/observation
inputs, registry scope set, and compiler and target-variable comparisons.
Older Plans cannot authorize this executor.

Authority State v4 adds the registry group and its original signed adoption
reference. Preserve the identity adoption reference and all existing groups.
Validate the closed registry field vocabulary, complete box scope set, and
app enabled scopes. The planner requires the exact private-instance and source
scope sets. The root reader and executor also check the original protected
adoption Plan. Source publication uses the shared local lock and exact prior-state
check. Keep v2 and v3 readers closed to new fields, including null fields.

Normal reads use the installed `platform-registry` client and the root
`ksa-apply registry-source-status` action. The latter verifies the active
controller, adopted-source evidence, clean matching public/private checkouts,
sealed renderer, and unchanged inputs before returning effective values.
It accepts no caller path or executable. It does not write source records or
run configuration commands. The client supplies legacy values before adoption
and instance values afterward. Failure to verify the selected source must not
fall back to the old file. Noncanonical registry overrides cannot select
another source after adoption.

The compiler, mapper, lifecycle reader, and direct app-registry readers use
this client. Legacy writer entry points call its source guard before any
write or dependent runtime operation. After adoption they refuse with an
instruction to publish instance intent through the existing human workflow.
The source-only migration does not grant app launch, removal, or data-deletion
authority. An explicit compiler compatibility mode remains available for
read-only comparison and verification. It must reject apply, grant, and
other mutations. Historical box mutation/rollback paths remain available for
their prior source versions; a registry-adopted source requires a new recovery
design before those paths can change its ownership. Keep the deferred IPv6
interface and historical artifacts intact.

Classify the three legacy schema-version fields as format evidence and the
fixed controller account/path as engine policy. Check their actual values.
Do not emit source-adoption actions for them. The old documents stay as
explicit comparison and recovery inputs. Toolchain v5 adds the installed
registry client; it also binds the unchanged existing component set.

Preparation and signed execution use protected archives and readable runtime
copies. Compare the full compiler output and box-only output, ignoring only
registry path/hash provenance, and require equal router variables for both
boxes. Verify both routers with the effective registry and check the
controller pair. Consume the nonce before execution-time live verification.
Revalidate all inputs after verification and before publishing one v4 record.
Never apply router configuration, restart services, repair a gateway, or
automatically roll back on a verification error. If publication succeeds but
receipt storage fails, report an incomplete operation; require inspection and
fresh signed verification. Do not retry the signed request.

Before promotion, test renderer round trips, missing/extra fields, empty
targets, defaults, retained data, detailed scope coverage, all planner targets,
old Plan refusal, wrong controllers, changed inputs, expired signatures,
nonce reuse, concurrent source changes, publication/receipt failures, and
verification-only command sets. Exercise real temporary files under umask
077, readable runtime copies, protected archives, receipts, and cleanup.
Test normal reads and writer refusals after adoption, including registry
overrides, missing helpers, and corrupt source evidence. Run the Python suite,
sealed Go tests/build, and affected shell/Ansible syntax checks.

Promote the reviewed consumer engine through the existing Touch ID workflow.
Then prepare the typed private candidate on the controller and publish it
through the trusted MacBook helper, retaining both engine checks. Refresh
source/recovery/observation evidence when the human is ready. Require signed
adoption, fresh signed verification, exact replay refusal, unchanged source
groups outside this batch, unchanged private semantic settings, and unchanged
persistent router configuration. Store a final Plan and the remaining
execution-inventory authority. Update acceptance documentation without
automatically deploying its final documentation commit.

### Recorded registry execution result

Engine `db61bc7` and private commit `366efc19` completed the exact registry-only
Authority State v4 transition. Fresh signed verification and exact replay
refusal passed with nonce `byL1lnwV2C2G5cLwE54NiOut`. Final Plan `2d2bfbe5`
reports all five adopted groups as verification-only. The original desired
state, both controller markers, and 138 persistent configuration entries on
each router remained unchanged. The normal registry reader uses the instance;
the legacy writer guard refuses. Recognized DERP on k001 passed access checks.
Both signed archives, receipts, consumed nonces, and runtime cleanup passed
inspection. The
[acceptance record](../klokast-dev/runbooks/51-registry-source-adoption.md#acceptance-record-2026-09-08)
holds the full controller evidence references.

### Remaining inventory and runner dependencies

Status: `decided`; implementation scope is already authorized. Final Plan
`2d2bfbe5` retains only `execution_inventory` as a legacy authority. Its one
pending adoption action is the declared controller-container runner identity;
the action has prior authority `none`, not a retained legacy registry field.

The consumer audit found that `converge-ops-controller` combines the static
base inventory with `render-node-inventory` output. Ansible still loads a
box-specific host-variable file that enables the runner, while the generic
group default disables it. The current generator accepts a caller-supplied
box and suffix. Complete the next design around instance-derived membership,
runner selection, and upstream role topology. Compare effective host groups
and variables, preserve exact target limits and recovery inventory, and audit
all normal consumers before changing the Plan's continuing-authority report.
The existing private `airunners` list supplies intent; this work does not
authorize runner retirement, controller changes, or removal of recovery files.

#### Inventory and runner source adoption design

Adopt execution host selection and the complete ordered runner identity list
as one `execution-inventory-v1` group. Its scopes are `execution_inventory`
and one `deployment.control_plane.airunners.<identity>` scope per declared
runner. Derive membership and scope order from the checked private instance;
accept no caller-supplied host set. Limit this source-only milestone to the
existing two-box arrangement, the configured controller pair, and already
adopted registry, identity, and connectivity groups. Keep all effective host
variables, role memberships, router settings, controller roles, runner runtime
state, applications, and data unchanged. No runner is enrolled or retired.

The sealed `klokast inventory --instance PATH --json` command renders only
instance-derived box hosts, role groups, MagicDNS suffix, and controller-runner
selection. It includes no credentials, observations, arbitrary variable input,
or legacy host file. Upstream generic group variables remain engine policy.
Dynamic application inventories remain compiler outputs from the adopted
registry. Keep bootstrap and recovery target selection explicit.

Normal Platform inventory consumers must select a controller-local reader.
Before adoption it supplies the existing inventory. After adoption it supplies
the sealed instance projection and upstream group policy from an inventory
directory that does not load legacy host variables. Unknown source versions,
missing installed helpers, modified inputs, and incomplete source evidence
must refuse. Retained legacy inventories are explicit comparison and recovery
inputs; they must not provide an automatic fallback after adoption. Existing
one-box limits stay explicit. Audit shell wrappers, Python entry points,
application launchers, builder paths, and default Ansible configuration.

The reader has read authority over private topology and protected evidence.
The root source executor can append one source record after verification; it
cannot run arbitrary commands, configure hosts, restart services, launch or
retire runners, or repair networking. The offline renderer has no credentials
or network authority. The unprivileged comparison helper writes protected
controller-private evidence and uses only inventory parsing and sealed tools.

Plan v7 adds explicit `--migration-target inventory`. Other targets retain
their existing interfaces and default selection. The new executor is
`inventory_source_v1`, with a closed versioned intent permitting adoption and
verification only. Bind the exact Plan, controller pair, source record,
private files and commit, engine/build, Toolchain v6, source/recovery/observation
evidence, ordered runner list, policy-file hashes, and old/effective inventory
comparison hashes. Older Plans cannot authorize this executor. Instance
Specification v1 remains unchanged.

Authority State v5 adds the complete inventory group and its original signed
adoption anchor. Preserve all five prior groups and their original anchors.
After adoption, all unselected groups report verification-only; the inventory
authority becomes instance-derived and the pending runner action is covered
by the new group. Keep legacy removal unready until its separate recovery
and removal gates are complete. Historical Plans, source records, and receipts
stay immutable. Historical schemas must reject new fields, including nulls.

Preparation compares Ansible's effective variables and group memberships for
every instance-derived host using the existing base plus generated inventory
and the new independent inventory. Ignore only Ansible inventory-path
provenance, never target addresses, users, connection settings, or role policy.
Require complete host coverage. Legacy sample hosts outside the instance are
reported as excluded from normal selection and are never contacted. Preserve
the full registry/compiler and both router-variable comparisons. Verify the
controller pair, both routers, and fresh online/tag evidence for every declared
runner. Recognized direct or DERP transport is acceptable.

Signed execution consumes the nonce before live verification, then rechecks
all bound inputs and the controller before publication. Use the shared short
local lock and exact prior-state check. A failure before publication leaves
the source unchanged. Publication followed by receipt failure is incomplete
and requires inspection followed by fresh signed verification. Never retry a
used request or repair the network automatically. Live rollback, re-adoption,
legacy-file removal, and a recovery-source switch are deferred; preserve their
inputs and refuse unsupported ownership changes.

Test deterministic rendering, complete host and variable coverage, unknown
boxes and runners, scope changes, all Plan targets, old-version rejection,
changed policy/private inputs, wrong controllers and peers, expired requests,
nonce reuse, concurrent publication, and receipt failure. Exercise real files
under umask 077, protected archives, readable runtime copies, receipt creation,
and cleanup. Assert verification-only command sets and no legacy fallback.
Run the Python suite, sealed Go tests/build, and affected Ansible/shell checks.
Use a sealed private comparison before promotion to detect inventory merge
differences; keep the canonical controller at its active engine during this
review. Promote one reviewed implementation through the existing human
workflow, install matching tools, then obtain signed adoption and fresh signed
verification with exact replay refusal. Preserve baseline files, source-only
transition checks, and final Plan/evidence before marking this step live-verified.

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
| JSON contract | `implemented` | The two-file Instance Specification v1 is implemented and remains unreleased. Its current shape uses the top-level `tailscale` object as the v1 overlay-provider selection, elementary box connectivity capabilities, and no instance ID. Public fixtures contain no deployment-specific values. |
| `klokast init` | `implemented` | Deterministic offline repository creation is implemented. |
| `klokast check` | `implemented` | Closed JSON and repository validation is implemented. |
| Compatibility planner | `implemented` | The deterministic comparison with all three legacy inputs is implemented. |
| `klokast doctor` and Observation v1 | `implemented` | The limited `standard_substrate_v1` read-only health check is implemented. |
| Sealed builder | `implemented` | The networkless controller-managed CLI build and receipt path is implemented. |
| Private source custody | `implemented` | Private repository bootstrap, read-only deployment checkout, and source receipts are implemented. Live instance state is not recorded in the public repository. |
| MacBook bootstrap, publication, and updates | `implemented` | Helpers and deterministic tests are implemented. The real MacBook publication and Touch ID engine-promotion paths are verified. Initial-bootstrap recovery remains an external verification item. |
| Plan v1 | `implemented` | The hashed artifact is read-only evidence with exact finding, action, and continuing-authority coverage. |
| Plan v2 | `historical` | Plan v2 binds the completed atomic Tailnet action group. Its artifacts stay immutable. It cannot authorize box connectivity. |
| Authority State v2 and Plan v3 | `implemented` | Authority State v2 closes source ownership by setting group. Plan v3 selects only the unique box that does not host the active controller and authorizes its exact five-scope connectivity group. |
| Read-only acceptance alignment | `live-verified` | On 2026-08-24, the active controller verified source and activation custody, refreshed observations without repair, and stored an immutable deployable Plan with exact finding, action, and continuing-authority coverage. The human confirmed every compatibility-only continuing authority. Legacy removal stays blocked. |
| Engine promotion | `live-verified` | On 2026-08-24, the real MacBook Touch ID workflow completed the reversible connectivity transition and a later metadata-only promotion. The active controller verified the exact sealed builds and immutable promotion and activation evidence. |
| Elementary connectivity capabilities | `live-verified` | On 2026-08-24, controlled promotion, private-state alignment, exact sealed validation, read-only acceptance, and human continuing-authority review passed. No Platform resource apply or application runtime operation was part of acceptance. |
| Authorized apply | `live-verified` | On 2026-08-25, the dedicated Touch ID signer and closed root executor completed byte-preserving adoption, a verification-only Plan, forward rollback, re-adoption, a final verification-only Plan, and replay refusal. The final authority is Instance Specification v1 for the exact three-scope Tailnet group. The live policy bytes remained unchanged, and immutable evidence was retained. |
| First box connectivity migration | `live-verified` | On 2026-09-06, the human promoted the corrected sealed engine and approved one verification-only request with Touch ID. Authenticated k001 router configuration, service, route, firewall, and reachability checks passed. The controller stored a `verified` execution receipt, and the MacBook helper confirmed refusal of the exact replay because its nonce was already used. A final Plan kept k001 and Tailnet verification-only and k002 on the old registry. All setting-source and desired-state hashes remained unchanged. Live rollback and re-adoption remain unverified and deferred. |
| Ops-only overlay IPv6 repair | `implemented` | The Freebox broker, physical credential installer, manual Huawei prerequisite, ops-only network path, signed action, rollback, and repository tests are implemented. Signed live acceptance is deferred under the current work queue. |
| Controller box connectivity source migration and Plan v4 | `live-verified` | On 2026-09-07, source-only adoption, fresh signed verification, and exact replay refusal passed with engine `cc68fc6`. Both planner targets report both box groups and Tailnet as verification-only. Only the k002 source changed; private desired-state files and persistent router configuration remained unchanged. Section 11.3 links the acceptance evidence. |
| Controller identity sources | `live-verified` | Signed source adoption, fresh signed verification, exact replay refusal, unchanged-setting checks, controller and MacBook resolution, and all three final Plan selections passed for engine `c8f863b`. Section 11.4 links the completed acceptance evidence. |
| Registry compatibility checkpoint | `live-verified` | Engine `e12b426` passed sealed tests/build, private round-trip checks, and metadata-only promotion to private commit `de8e252`. Settings, source ownership, and controller identity stayed unchanged. |
| Registry source adoption | `live-verified` | Engine `db61bc7` and private commit `366efc19` passed signed adoption, fresh signed verification, exact replay refusal, and final unchanged-setting checks on 2026-09-08. Final Plan `2d2bfbe5` reports all five source groups as verification-only. Runbook 51 holds the completed evidence. |
| Remaining inventory and runner adoption | `decided` | Final Plan `2d2bfbe5` retains execution inventory and one pending runner identity action. Section 11.5 closes the source-only design. The offline renderer and inventory comparison checkpoint are implemented; source execution and normal-consumer replacement remain pending. |
| Legacy removal | `proposed` | Keep old files and recovery evidence until the exact removal and recovery gates are complete. No data deletion is inferred from migration authorization. |

### Current work queue

1. Complete the bounded design and implementation of the
   [remaining inventory and runner dependencies](#remaining-inventory-and-runner-dependencies)
   from final registry Plan `2d2bfbe5`. The human authorized continued scope
   development without another scope-selection question. Record bounded
   designs, preserve data and recovery, and use the existing exact signed
   promotion and live-action workflows.
2. Keep legacy removal `proposed` until its recovery, rollback, observation,
   and exact live-action gates are complete. Continued migration work does
   not by itself authorize deletion of retained data or recovery evidence.

Deferred work: direct-IPv6 repair and live first-box rollback and re-adoption
testing. Neither blocks continued development. Keep their recovery code,
saved inputs, and evidence. This is the single current queue; runbooks and
failure notes link here rather than define another work order.

### Completed read-only acceptance alignment design

Apply, migration, and legacy-removal work remains gated by a successful
read-only acceptance Plan. The current gate is `live-verified`. A
contract-valid private instance and a successful source synchronization do not
satisfy this gate by themselves. The following `decided` design records the
completed acceptance and remains the baseline for future revalidation.

The baseline acceptance attempt used the currently locked sealed engine and
identified generic planner and Doctor defects. The corrected behavior is part
of a later public engine commit. The fixed-engine contract prevents that
binary from interpreting an instance whose lock selects the earlier commit.
Therefore controlled canonical engine promotion is a prerequisite for live
verification of this alignment milestone. Do not replace the lock directly,
run an unbound binary, or weaken the engine check to avoid that dependency.
The refused baseline Plan is valid input evidence for the engine-promotion
design loop.

Authority and custody are as follows:

- The existing legacy deployment document remains authoritative for its
  represented legacy scopes.
- The existing private platform-resource registry remains authoritative for
  its represented legacy scopes.
- The human owns the transitional legacy controller HA registry on the
  trusted MacBook at `~/.config/klokast/controller-ha.yml`. The active
  controller holds the operational copy at
  `~/private/klokast/controller-ha.yml`. Existing private-state
  synchronization can copy it to a standby controller.
- The two-file private instance repository does not contain the transitional
  HA registry. The airunner does not hold it.
- `ops/controller-ha.example.yml` is only a neutral example. It is never live
  compatibility evidence and there is no fallback from a missing private
  registry to that example.

The HA registry has a closed root and closed controller entries. It declares
one or two unique controllers. Each controller name has the exact
`<box>-ops` form, uses account `smith`, and uses the fixed public-engine
checkout path. It does not declare a preferred active controller. Resolution
uses an explicit command option first, then
`KLOKAST_CONTROLLER_HA_CONFIG`, then the controller-private or MacBook path
for the local execution locus. It fails when no valid registry is available.
An airunner command must name the controller explicitly because the airunner
has no private HA input.

A development-only MacBook installer creates or replaces the MacBook registry
and copies it to an explicitly named active controller. Its inputs are the
candidate file and exact active-controller identity. Before installation, it
shows the target and requires an interactive terminal user to enter the exact
phrase `install transitional controller HA config`. This development
deployment does not require a cryptographic Touch ID signature for this
transitional file. The installer validates the closed schema, writes mode
`0600` atomically, archives the prior file, and validates the active-controller
markers after transfer. A validation failure restores the prior file. The
recovery path is a reinstall from the human-held MacBook copy through an
explicitly named controller. Mature deployments start with private-instance
authority and do not use this migration-only registry.

Compatibility input and output rules are as follows:

- A legacy deployment `boxes` map with no entries claims no box authority.
  It does not conflict with boxes in the private instance. A non-empty partial
  map continues to produce a conflict for each missing projected box.
- Controller candidate validation can require compatibility. It uses the
  exact sealed engine and the controller-private deployment, resource, and HA
  files. It returns checked compatibility evidence and refuses malformed
  input or any `conflict` or `unsupported` finding. It does not consume an
  Observation, create a Plan, or change any state.
- `publish-private-instance --check` sends the staged private JSON to that
  validation path and reports whether it is compatible. It does not commit,
  push, publish, rewrite desired state, or disclose private findings to the
  public repository. Publication uses the same required compatibility check.
- The human explicitly edits only private `klokast-instance.json` to mirror
  the current enabled applications, placement, typed features, retained-data
  intent, and connectivity intent. No tool derives or rewrites that intent
  from an observation.

The shared-guest health rule in section 6 is part of this decision. Doctor
does not read the legacy resource registry to decide whether a shared guest
must run. This keeps the health scope independent of compatibility-only
runtime intent and does not change the Plan schema.

The implementation must refuse an absent or invalid private HA file, an
ambiguous controller registry, an inactive transfer target, changed or
unapproved sealed engine evidence, incompatible private intent, a changed
publication base, or any non-interactive attempt to install the transitional
HA file. It must not repair a Platform resource or relax a compatibility
finding.

Repository tests must cover HA resolution and strict validation, installer
archive and rollback behavior, empty and partial legacy box maps, a stopped
shared guest, candidate compatibility refusal, and publication check mode.
The focused contract, Plan, source-custody, MacBook-helper, Touch ID, and
cloud-runner tests must pass. The active controller's sealed builder must test
and build the exact committed engine.

Rollback is file-local. Restore the archived private HA registry if its
installation fails. Do not publish a failed candidate instance update. If a
published private update does not pass the later read-only Plan gate, publish
a human-reviewed corrective private commit or revert it through the same
preflight. No rollback action changes Platform runtime or application data.

Live verification must install and synchronize the private HA registry, run
the MacBook check and publication flow, synchronize the controller checkout,
refresh the Platform map, export a fresh Observation v1, and store a Plan v2.
The Plan must be valid, compatible, substrate healthy, deployable, and
authority ready. It must have no refusal and no `conflict` or `unsupported`
finding. Each `compatibility_only` finding must have exactly one continuing
authority. `legacy_removal_ready: false` remains expected. The controller
keeps all private inputs, findings, observations, and Plan artifacts.

After each future engine promotion, the active controller must rerun the
private-instance read-only acceptance flow before apply, migration, or
legacy-removal work continues. The flow must verify
redacted bootstrap retirement and source status, stop for human completion if
the temporary App still has authority, synchronize the read-only checkout,
refresh the Platform map, export a fresh owner-only Observation v1, and store a
Plan v2 made with the currently locked sealed engine and the active authority
state and controller toolchain receipts. The result must have no
`conflict` or `unsupported` finding. A human must confirm one continuing
authority for each `compatibility_only` finding.
`legacy_removal_ready: false` is expected. The controller keeps the Plan and
private findings. Only generic design defects can return to this public
document. Runtime drift in the limited health scope is a refusal. The
read-only acceptance flow must report it and must not repair, start, stop, or
reconfigure a Platform host.

Application schema expansion, application-data deletion, site executors,
custom engine repositories, and isolated airunner VMs remain deferred.
