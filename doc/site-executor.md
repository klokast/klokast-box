# Platform Controller And Site Executors

Status: proposed target architecture; not yet implemented.

This document specifies how to separate Platform-wide decision authority from
site-local execution. Until every mandatory deployment and security gate in
this document is implemented and validated, the current rule remains in force:
only the active controller may inspect or mutate Platform targets through
Platform workflows.

Related documents:

- `doc/architecture.md`: current Platform roles and trust boundaries.
- `doc/platform-resource-control-plane.md`: compilation of app intent into
  approved deployment state.
- `apps/ops/README.md`: current active/standby controller operation.
- `doc/platform-deploy.md`: provisioning and recovery model.
- `doc/secret-authority.md`: signed intents and secret-backed action brokers.
- `doc/platform-map.md`: current read-only discovery and verification path.

## 1. Vision

The Platform spans one to five residential sites. Cross-site round-trip time
can be hundreds of milliseconds. Ansible normally turns a logical convergence
into many remote module invocations, so cross-site latency is multiplied by
task count even when the target does little work.

The Platform should send one bounded operation across the wide-area link, do
the detailed work close to the target, and return a structured result. It
should achieve this without creating several independent control planes,
copying global credentials to every site, or allowing a disconnected site to
make new infrastructure decisions.

The target model therefore separates:

- **authority**: one active controller decides and authorizes desired state for
  the whole Platform;
- **execution**: one executor at each site applies an authorized, site-scoped
  job using local connections;
- **recovery**: the controller, operator workstation, and NanoKVM retain paths
  that do not depend on the executor running.

The intended result is lower convergence latency, resumable operations, and a
smaller compromise radius for routine site work. The design is not intended to
provide autonomous multi-controller operation or automatic split-brain
failover.

## 2. Design Decision

There is exactly one active controller per Platform deployment. A site may
have one executor. An executor is an actuator, not a controller replica.

The target invariant is:

> Only the active controller may choose or authorize a Platform mutation. A
> site executor may apply an unexpired, replay-resistant, site-scoped job that
> the active controller authorized.

The controller remains responsible for global serialization and conflict
detection. Executors must not coordinate mutations with each other, reconcile
from private desired state autonomously, or infer a new desired state from
local observations.

One `<box>-ops` VM may host both roles when it belongs to the active
controller's site. A non-active `<box>-ops` may host the executor role without
becoming an active or standby mutation authority. Controller HA and executor
placement are separate concepts.

### 2.1 Expected benefits and costs

Expected benefits:

- one wide-area dispatch replaces many latency-bound task sessions;
- accepted work can finish and be queried after a transient disconnect;
- site-local credentials and reachability can have a smaller blast radius than
  controller-wide management access;
- site jobs provide a stable boundary behind which Ansible and Go primitives
  can evolve independently;
- local artifact caches and parallel execution reduce cross-site transfer and
  total convergence time;
- explicit jobs and receipts improve provenance over an unstructured remote
  shell session.

Expected costs:

- every executor becomes a privileged component in its site's TCB;
- promotion must fence controller signing authority at every executor;
- the Platform gains job schemas, replay state, locks, receipts, upgrades, and
  recovery procedures;
- a compromised executor can falsify its own results;
- the controller must handle ambiguous outcomes after connection loss;
- code and policy versions can drift between controller and executor;
- a self-hosted executor is unavailable during failure of its own dom0 or
  storage substrate;
- the additional machinery may exceed its value for an infrequently managed
  site unless it is introduced incrementally.

The split is justified only when it delivers both meaningful latency reduction
and useful authority compartmentalization. Task batching alone is preferable
when it meets the operational target without adding distributed authorization.

### 2.2 Alternatives considered

#### Keep direct cross-site Ansible and batch tasks

This is the lowest-complexity improvement and should continue regardless of
the executor decision. It does not provide resumable site jobs or a site-level
authority boundary, and some workflows will remain coupled to WAN latency.

#### Re-enable SSH multiplexing

Connection reuse must be tested, but it is not the architecture. Existing
measurements showed that reuse did not remove the dominant per-command cost on
the current Tailscale SSH path. It also does not address interrupted jobs,
site-scoped authority, or local artifact distribution.

#### Replace Tailscale with another overlay

An overlay change cannot remove geographic round-trip time. It also introduces
a new identity, certificate, discovery, firewall, and recovery system. Test an
alternative SSH server over the existing overlay before attributing the issue
to the overlay itself. A network migration requires independent security and
operational justification.

#### Make every site an active controller

This is rejected. Independent controllers would need distributed locking,
consistent desired state, conflict resolution, credential replication, and
reliable fencing across residential partitions. The risk of split-brain is
greater than the latency benefit.

#### Replace Ansible with privileged APIs

This is premature. A permanent API expands the attack and operations surface
before the job contract is understood. An on-demand CLI over the existing
management plane provides a smaller first implementation.

#### Rewrite all deployment logic in Go

This creates migration risk and discards useful Ansible modules before the
required local primitives are known. The preferred strangler approach keeps
Ansible as the bootstrap and integration layer and extracts only workflows
that benefit from stricter transactional behavior.

## 3. Goals

- Reduce a cross-site workflow to a small, documented number of WAN sessions.
- Preserve one Platform-wide desired-state and authorization authority.
- Restrict an executor to named actions and resources at its assigned site.
- Keep provider, Tailnet, signing, broker, and global registry authority on the
  active controller.
- Run existing Ansible locally before requiring a rewrite of deployment logic.
- Permit progressive replacement of suitable Ansible roles with deterministic
  Go CLIs.
- Make job acceptance, progress, completion, retry, and failure auditable.
- Make a lost response safe: the controller can query an operation without
  blindly applying it again.
- Preserve manual fencing, offline recovery, and reconstruction.
- Fail closed on stale code, ambiguous authority, invalid input, expired jobs,
  replay, scope expansion, or missing prerequisites.

## 4. Non-goals

- Multiple active controllers.
- Executor-to-executor consensus or coordination.
- Automatic controller election or promotion.
- A general remote shell, generic job scheduler, or arbitrary-code API.
- Autonomous site reconciliation while disconnected from the controller.
- Replacing the controller, bootstrap workflows, or NanoKVM recovery.
- Moving Platform private state or credentials to the infra-agent or airunner.
- Rewriting all Ansible automation before obtaining operational benefit.
- Introducing a long-running privileged HTTP service for the first version.
- Solving application data replication or active/passive promotion semantics.

## 5. Terminology

### Platform controller

The single active `<box>-ops` control-plane role that owns the private instance
registry, compiles desired state, serializes global operations, holds global
credentials, authorizes jobs, and aggregates results.

### Site executor

A site-scoped execution role, initially hosted in the site's `<box>-ops` VM.
It validates and applies fixed job types against resources in exactly one
site. It has no authority to select placement or authorize additional work.

### Job

An immutable, canonical authorization envelope for one declared operation.
Its identity includes the action, target scope, desired-state digest, engine
digest, controller epoch, expiry, and unique operation ID.

### Plan

Controller-compiled, non-secret or narrowly secret-bearing input consumed by a
job. A plan contains concrete execution data for one site but not unrelated
private-registry content.

### Receipt

The executor's durable record of job acceptance, progress, result, input
digests, timestamps, and exit status. A receipt is an observation, not proof
that a compromised executor behaved honestly.

### Controller epoch

A monotonically increasing authority generation used to fence jobs issued by
an old active controller. Promotion changes the epoch and executor trust state
through an explicit recovery procedure.

## 6. Authority And Responsibility

| Concern | Active controller | Site executor |
| --- | --- | --- |
| Own Platform desired state | Yes | No |
| Read full private registry | Yes | No |
| Choose placement | Yes | No |
| Compile site plans | Yes | No |
| Detect cross-site conflicts | Yes | No |
| Authorize and sign jobs | Yes | No |
| Apply an accepted local job | Optional fallback | Yes |
| Hold provider or Tailnet OAuth | Yes | No |
| Mint general identities | Brokered on controller | No |
| Apply global Tailnet policy | Yes | No |
| Run local app convergence | May dispatch | Yes |
| Collect local facts | May use fallback | Yes |
| Aggregate Platform state | Yes | No |
| Promote controller or app site | Yes, with gates | No |
| Recover blank or failed box | Yes | No |

The executor is in the local site TCB. Compromise of an executor must be
assumed to permit falsification of its results and compromise of every local
resource it can mutate. The design goal is to prevent that compromise from
granting authority over another site or global external services.

## 7. Trust And Network Boundaries

Each executor must have a distinct site identity and network policy. A generic
`tag:ops` identity with Platform-wide access is insufficient for the target
boundary.

The policy model should distinguish:

- controller-to-executor job dispatch;
- executor-to-local-dom0 management;
- executor-to-local-router management;
- executor-to-local-VM management;
- controller-direct recovery access;
- forbidden executor-to-other-site access.

An executor for `k001` must not reach `k002` management endpoints. Targets
must reject executor identities belonging to other sites. The controller keeps
a separate direct management path for recovery and independent verification.

The first implementation may continue using Tailscale SSH as the authenticated
transport. The signed job is still required before state-changing rollout: it
binds the action and scope, supports replay protection, and survives future
transport changes. Transport authentication alone does not define what a
privileged executor may do.

The executor must not accept arbitrary shell text, playbook paths, inventory
paths, environment variables, `--extra-vars`, URLs, file destinations, Unix
users, or privilege flags from a job unless the selected job schema explicitly
defines and validates them.

## 8. Execution Locus And Recovery Boundary

A site executor hosted in `<box>-ops` depends on its box's dom0, storage,
network, and Xen runtime. It cannot be the only management path for those
dependencies.

The initial executor scope should include:

- read-only site facts and health probes;
- application install, lifecycle, and verification through sanitized grants;
- guest-local configuration;
- artifact verification and loading where the capability is site-scoped.

The controller must initially retain direct responsibility for:

- blank-box bootstrap and reinstall;
- dom0 storage destruction or reconstruction;
- creation and recovery of the executor VM;
- controller promotion, demotion, and fencing;
- Tailnet and provider policy;
- global identity issuance;
- cross-site application promotion;
- operations whose recovery requires the executor to remain available after
  its own host is stopped.

Dom0, router, and VM lifecycle actions may move into executor scope only after
each has an independent rollback or recovery path. For example, a router
firewall apply requires syntax validation, a management-flow invariant, a
rollback watchdog, and controller-direct or console recovery.

## 9. Executor Form

Version 1 should be an on-demand CLI reached over Tailscale SSH, not an HTTP
API or permanent job daemon.

Illustrative interface:

```text
platform-site-run submit < job-envelope.json
platform-site-run status OPERATION_ID
platform-site-run result OPERATION_ID
platform-site-run capabilities
platform-site-run health
```

`submit` remains attached while practical and journals before applying. If the
connection is lost, the accepted operation may complete locally; the
controller later queries it by operation ID. A temporary child process is
permitted for an accepted job, but it must have a bounded lifetime and must not
become a persistent scheduler.

The installed executable and its privileged helpers are root-owned and
versioned. A dedicated unprivileged account should receive job requests. It
may invoke only fixed root wrappers through narrowly scoped `doas` policy.
Using `smith` as the steady-state executor account would collapse the intended
boundary.

## 10. Job Contract

Jobs must use a versioned canonical encoding suitable for an established
signature tool. Do not implement custom cryptography. The initial signature
mechanism should reuse an approved operating-system facility such as
`ssh-keygen -Y sign` and `ssh-keygen -Y verify`, following the existing Secret
Authority pattern.

An illustrative envelope is:

```json
{
  "schema_version": 1,
  "operation_id": "0198cafe-0000-7000-8000-000000000001",
  "controller_epoch": 4,
  "site": "site-001",
  "box": "k001",
  "action": "app.apply",
  "resource": "torrent",
  "approved_engine_commit": "0123456789abcdef",
  "executor_digest": "sha256:...",
  "plan_digest": "sha256:...",
  "issued_at": "2026-08-08T12:00:00Z",
  "expires_at": "2026-08-08T12:10:00Z",
  "requested_by": "smith@k002-ops"
}
```

The signed envelope references a canonical plan by digest. The controller may
send the plan in the same stdin stream or make a content-addressed artifact
available through an approved store. The executor must hash it before reading
semantic fields.

The signature must cover every authority-bearing field. Unsigned metadata may
not change execution behavior.

### Required validation order

Before creating an accepted receipt, the executor must:

1. Decode with strict schema validation and reject unknown fields.
2. Verify the controller signature against the locally trusted active signer.
3. Verify the controller epoch.
4. Verify `issued_at`, `expires_at`, and permitted clock skew.
5. Validate the site and box against immutable executor configuration.
6. Validate the action and resource against the local capability allowlist.
7. Verify engine, executor, plan, and artifact digests.
8. Check the operation ID and replay ledger.
9. Check that no conflicting local operation holds the required lock.
10. Validate action-specific preconditions.
11. Persist an `accepted` receipt atomically.
12. Begin execution.

No action may begin before the durable acceptance record exists.

## 11. Job State Machine And Idempotency

The minimum state machine is:

```text
received
   │
   ├─ rejected
   │
   └─ accepted → applying → verifying → complete
                    │             │
                    └─────────────┴─→ failed
```

Receipts must record at least:

- operation ID;
- envelope, plan, engine, and executor digests;
- controller epoch;
- action, site, box, and resource;
- accepted, started, updated, and completed UTC timestamps;
- current state and bounded progress detail;
- changed state;
- exit classification;
- redacted error summary;
- references to local detailed logs.

Submitting the same operation ID and identical digests must return the existing
state or result. Reusing an operation ID with different content must fail.
Automatic retry may query and resume only when the action explicitly supports
resume. It must never reinterpret `unknown result` as `safe to apply again`.

Each action must declare its lock scope, such as:

- whole site;
- one box;
- dom0;
- router;
- one VM;
- one application resource.

Cross-site locks remain controller-owned. Executors must not implement a
distributed lock protocol.

## 12. Plans And Data Minimization

The controller compiles a plan from the private instance registry, public app
manifests, live or cached facts, and the approved engine revision. The executor
receives only the fields required for its action.

A site plan must not contain:

- other sites' topology or private bindings;
- provider credentials;
- Tailscale OAuth material;
- controller deploy or signing private keys;
- broker internals;
- unrelated application secrets or grants;
- arbitrary executable content not bound to an approved engine digest.

For app work, the existing compiler-produced grant is the starting point. The
executor must not receive the full private registry merely because the current
standby synchronization process already copies it.

Generated plans and receipts are runtime state under `/var/lib/klokast/`, not
Git authority. Temporary payloads belong under `/run/klokast/` with restrictive
permissions. Logs must be redacted and bounded.

## 13. Credentials And Secret-Backed Capabilities

Global credentials remain on the active controller. The preferred order for a
site action is:

1. no secret required;
2. controller performs the external secret-backed step and sends a
   non-secret result;
3. controller mints a short-lived, single-purpose credential;
4. a site-local root broker uses a site-scoped credential without revealing
   it to the executor process;
5. persist a site secret only when the workload or offline recovery model
   intrinsically requires it.

Raw secrets must be passed through stdin or a protected file descriptor, never
argv. They must not appear in the signed public envelope, receipts, logs,
Ansible verbosity, process listings, or error output.

A site-local broker expands the local TCB. Its credential must be unable to
affect another site or global policy. Broker actions require the same job scope
and active-controller checks as the executor.

## 14. Controller Failover And Fencing

Executor trust must not be inferred only from reachability. During controller
promotion:

1. Fence the old active controller using the existing human-approved process.
2. Determine the next controller epoch.
3. Establish the new active controller signer through an operator-controlled
   recovery action.
4. Update executor trust with an authenticated, auditable operation.
5. Invalidate the previous signer or epoch on every reachable executor.
6. Reconcile outstanding operations before authorizing conflicting work.
7. Reseed provider and API authority on the new active controller.
8. Verify exactly one controller can issue newly accepted jobs.

Executors that did not receive the new epoch must fail closed for new mutation
jobs. They may continue to expose redacted health and existing receipts through
a separately authorized read-only path.

Job expiry limits the usefulness of jobs signed before fencing. Expiry is not
a substitute for revoking the old signer. Clock synchronization is therefore
a security prerequisite, and clock-health checks must precede mutation.

The initial implementation should prefer short-lived controller signing keys
or operator-mediated signer rotation rather than inventing a distributed key
service. Signer recovery material must remain outside Git.

## 15. Failure Model

### Connection lost before acceptance

No durable receipt exists. The controller may resubmit the identical operation
ID and payload.

### Connection lost after acceptance

The executor continues only if the action was defined as disconnect-tolerant.
The controller queries the same operation ID. It must not generate a new ID
until the previous outcome is known or a documented recovery action resolves
it.

### Executor crashes during apply

On restart or explicit recovery, the executor reports `failed` or `recovery
required` unless the action has a tested resume procedure. A new apply must not
silently overwrite the journal of an interrupted action.

### Controller crashes

An accepted site job may finish. The reconstructed or promoted controller
collects receipts before issuing conflicting work.

### Site partition

The executor may finish an accepted, unexpired job. It must not pull new
desired state, promote resources, or schedule reconciliation while isolated.

### Stale executor code

The job fails before acceptance when the required executor or engine digest is
not installed. Updating the executor is a separate, explicitly authorized
operation with rollback.

### Compromised executor

Assume all local results and resources are compromised. Revoke its Tailnet
identity, fence the site execution path, inspect through the controller or
NanoKVM, rebuild the executor, rotate site credentials, and independently
verify targets. Other sites and global credentials must remain outside its
reach by construction.

### Compromised controller

The controller can authorize all Platform mutations and remains a Platform-wide
TCB component. Human approval, Git provenance, narrow brokers, fencing, and
offline recovery remain necessary. Executors do not protect the Platform from
a valid but malicious active-controller signer.

### False success

Receipts are not remote attestation. The controller performs periodic direct
sampling and compares provider-, Tailnet-, target-, and executor-visible facts.
High-risk operations require independent verification or human confirmation.

## 16. Audit And Observability

The controller records:

- the approved engine and instance revisions;
- compiled plan digest;
- signed job envelope and signature;
- dispatch attempts and transport result;
- received executor receipt and result digest;
- controller-side final decision;
- any direct verification result.

The executor records:

- validation outcome and stable rejection code;
- job state transitions;
- invoked fixed action and local target;
- redacted command results;
- changed state and verification summary;
- local recovery requirements.

Logs must correlate by operation ID. Human output should be concise; detailed
logs remain controller- or executor-local. Secret values, raw private plans,
tokens, and private keys are forbidden in audit output.

The controller's Platform map should eventually display executor identity,
version, trusted epoch, reachability, last completed job, outstanding recovery
state, and drift between receipt and independent facts.

## 17. Implementation Strategy

### Ansible first

The first executor should run existing playbooks from the site-local
`<box>-ops`. Inventory must be generated from the validated site plan and must
not allow hosts outside the executor's assigned box or site.

Ansible should use local or site-local connections. It must not receive the
controller's general SSH key, raw private registry, provider credentials, or
unfiltered extra arguments. Task batching remains desirable for audit clarity
and recovery even after network latency is removed.

### Progressive Go primitives

Move a workflow from Ansible to Go when it benefits from transactional apply,
strict schemas, atomic file changes, resumable state, or many local probes.
Each primitive should offer fixed operations such as:

```text
validate
plan
apply
verify
status
```

Go primitives consume the same controller-compiled plan and produce the same
receipt model. They must not add placement logic or private-state authority to
the executor. Ansible may initially call one Go primitive per logical
transaction; the executor may later call it directly.

### APIs later, only if justified

Do not introduce a privileged HTTP API merely to avoid SSH startup time. An API
would require a daemon, authentication, authorization, replay protection,
version negotiation, request persistence, cancellation, TLS lifecycle, and a
new exposed attack surface. Consider it only after the CLI job protocol is
stable and measurements show transport setup remains material.

## 18. Deployment Plan

Every phase must be independently deployable and reversible. Promotion to the
next phase requires its completion gates.

### Phase 0: baseline and threat model

1. Record cross-site RTT, transport type, workflow duration, task count, SSH
   invocation count, and target execution time for representative checks and
   app convergence.
2. Classify every proposed executor action by authority, input trust, target,
   credentials, network access, and worst-case compromise.
3. Define stable site and executor identities in the private instance schema.
4. Specify controller and executor Tailnet flows, including negative flows.
5. Select the standard signing tool, canonical encoding, clock-skew limit,
   expiry policy, and controller epoch recovery procedure.
6. Define retained controller-direct recovery paths.

Completion gates:

- reviewed threat model;
- reproducible baseline measurements;
- no ambiguous site ownership;
- documented fencing and signer-recovery procedure;
- positive and negative network-policy tests designed.

### Phase 1: read-only prototype

1. Create a site-executor Unix account on one non-active `<box>-ops`.
2. Install a root-owned executor CLI with only `health`, `capabilities`,
   `submit`, `status`, and `result` entry points.
3. Implement strict envelopes, operation IDs, local locks, receipts, bounded
   logs, and redaction.
4. Add one read-only site-health job using existing Platform check logic.
5. Dispatch it from the active controller using one Tailscale SSH session.
6. Compare executor output with a direct controller check.
7. Test connection loss before and after acceptance.

No state-changing privileges or secrets are installed in this phase.

Completion gates:

- repeated submissions are idempotent;
- altered or unknown fields fail closed;
- wrong-site jobs fail;
- interrupted results are queryable;
- direct and executor facts agree within documented tolerances;
- measured WAN session count and duration improve materially.

### Phase 2: signed authorization and fencing

1. Install the active controller's job signer through a root-owned broker.
2. Install only its public trust material and current epoch on the prototype
   executor.
3. Require signatures and expiry for every job.
4. Implement replay records and conflicting-operation locks.
5. Exercise planned controller switchover and executor trust rotation.
6. Exercise emergency promotion with the old active fenced.
7. Verify old-epoch and old-signer jobs are rejected.

Completion gates:

- signature, digest, expiry, replay, epoch, and scope negative tests pass;
- signer private material never reaches an executor or logs;
- promotion establishes exactly one accepted issuer;
- an unreachable executor fails closed after promotion;
- recovery is documented and repeatable from the operator workstation.

### Phase 3: application-scoped mutation

1. Select a reversible, non-critical app workflow.
2. Compile an app- and site-scoped plan from existing approved grants.
3. Give the executor account only the required `minion`-level capabilities.
4. Run the existing app playbook locally without a full private registry.
5. Record changed state and perform local verification.
6. Independently verify the result from the controller.
7. Test apply, second-apply idempotence, stop/start, invalid plan, rollback,
   interrupted execution, and executor rebuild.

Completion gates:

- no infrastructure or other-app authority is reachable;
- no secret appears in the plan, receipt, logs, or process argv;
- second apply is idempotent;
- recovery from lost response and interrupted apply is deterministic;
- direct fallback remains functional.

### Phase 4: site rollout

1. Provision an executor identity and account for each site.
2. Apply site-specific Tailnet grants and SSH rules.
3. Install executor trust, action policy, CLI, journals, and log policy.
4. Validate cross-site denials from every executor.
5. Enable read-only jobs, then approved app actions site by site.
6. Add executor status to Platform mapping and health checks.
7. Retain the controller-direct path during an extended observation period.

Completion gates:

- every executor can reach only its declared local targets;
- each site can be independently fenced;
- controller aggregation detects missing or stale executors;
- a complete executor rebuild is tested;
- no standby controller gains global mutation authority.

### Phase 5: selected infrastructure actions

Evaluate actions individually. Candidate order:

1. guest-local baseline convergence;
2. content-addressed artifact load;
3. named VM lifecycle;
4. precompiled VM firewall fragment;
5. precompiled router fragment;
6. routine dom0 convergence.

Before enabling an action, document its local capability, rollback, recovery
path, lock scope, secret use, and independent verification. Destructive
storage, controller lifecycle, global policy, and controller promotion remain
controller-only unless a later architecture decision explicitly changes them.

### Phase 6: Go extraction

1. Choose one Ansible role with high task count and a narrow local state model.
2. Define its versioned plan and result schema.
3. Implement a deterministic Go CLI without a daemon or embedded authority.
4. Run it through Ansible first and compare results.
5. Add failure injection, idempotence, rollback, and reconstruction tests.
6. Allow the executor to invoke it directly after parity is demonstrated.
7. Repeat only where the reduction in complexity or failure risk is positive.

Ansible remains the bootstrap and integration layer unless evidence supports a
later change.

## 19. Test Matrix

At minimum, test:

### Functional

- valid read-only, apply, verify, and status jobs;
- unchanged second apply;
- action-specific rollback or recovery;
- controller-direct fallback;
- executor rebuild from Git, public artifacts, and external private state.

### Authorization

- invalid signature;
- unknown signer;
- old controller epoch;
- expired and not-yet-valid job;
- wrong site, box, app, VM, action, or artifact;
- unknown schema field;
- modified plan after signing;
- reused operation ID with identical and different content;
- executor attempt to reach another site;
- unprivileged account attempt to call an undeclared root action.

### Failure and reconnect

- WAN loss before acceptance;
- WAN loss during apply and during result delivery;
- controller process restart;
- executor process restart;
- target reboot;
- Tailscale daemon restart;
- direct-to-relay and relay-to-direct path change;
- stale lock and incomplete receipt;
- full executor VM loss;
- controller switchover and emergency promotion.

### Security and data handling

- no secrets in argv, environment dumps, verbosity, receipts, or logs;
- restrictive owner and mode checks for trust, journal, and temporary files;
- Tailnet positive and negative policy tests;
- signer rotation and revocation;
- audit correlation by operation ID;
- malicious target output cannot alter controller decisions or terminal
  control state;
- oversized input and output are bounded;
- symlink, traversal, and unsafe destination inputs are rejected.

### Performance

- WAN sessions per job;
- dispatch-to-accept latency;
- apply and verification duration;
- bytes transferred;
- local target execution duration;
- comparison with direct cross-site Ansible;
- behavior under representative RTT and relay conditions.

## 20. Rollback

The controller-direct execution path remains available throughout rollout.
Disabling an executor consists of:

1. Stop dispatching new jobs.
2. Inspect and resolve accepted operations.
3. Revoke the executor's Tailnet identity or grants.
4. Remove its privileged `doas` capabilities.
5. Preserve receipts and audit logs on the controller.
6. Return affected workflows to direct controller execution.

Rollback must not require deleting desired state, rotating unrelated
credentials, or promoting another controller. A compromised executor follows
the stronger rebuild and credential-rotation procedure in the failure model.

## 21. Completion Criteria

The architecture is ready for routine state-changing use only when:

- exactly one controller signer can issue newly accepted jobs;
- every executor is cryptographically and logically bound to one site;
- executors cannot read the full private registry or global credentials;
- jobs are strict, signed, expiring, replay-resistant, and idempotent;
- lost responses and interrupted applies have tested outcomes;
- controller promotion fences the previous issuer at executors;
- executor compromise has a documented site-scoped rebuild procedure;
- controller-direct and NanoKVM recovery remain tested;
- direct verification can detect materially false executor reports;
- representative workflows show a meaningful latency reduction;
- app and infrastructure authority remain separated;
- all positive and negative network-policy tests pass;
- exact engine, plan, and result provenance is recorded.

## 22. Consequences

The split adds a privileged TCB component at each site and makes fencing more
complex. It is justified only if the executor remains narrowly scoped and the
Platform obtains both performance and compartmentalization benefits.

The design is a regression if executors become synchronized controllers that
hold private registries, general SSH authority, provider credentials, or
autonomous reconciliation logic. In that model, the Platform would gain
latent multi-controller authority without consensus or reliable fencing.

The intended long-term architecture is instead:

```text
one Platform authority
    → compiled and signed site jobs
        → constrained site-local executors
            → local Ansible and deterministic Go primitives
                → structured receipts and independent verification
```
