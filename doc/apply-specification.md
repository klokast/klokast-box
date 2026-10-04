# Klokast Apply Specification

## Purpose

`Apply` reconciles the running Platform with already-authorized desired state using the currently approved Platform implementation.

Apply is an **execution mechanism**, not an authorization ceremony.

It must not require a human signature for every reconciliation, package update, firewall compilation, container replacement, or other routine operation.

## Inputs

Apply operates from three conceptual inputs:

1. **Platform implementation** — the code and automation allowed to perform privileged operations;
2. **Instance desired state** — the deployment-specific state and capability grants that are authoritative for this Klokast;
3. **Observed runtime state** — current facts used to determine what work is necessary.

Observed state is evidence only. It must not redefine desired state.

## Authorization

Human authorization occurs when durable authority is created or expanded, not when Apply later implements that authority.

Examples:

- the human authorizes installation of an application and its capability envelope;
- authorized desired state records that decision;
- Apply may then create containers, allocate addresses, compile firewall rules, restart services, and perform other implementation work without further human approval.

If Apply discovers that the requested result requires authority not present in desired state or policy, it must stop rather than invent or silently grant that authority.

## Execution model

Apply may be invoked by:

- the autonomous AI administrator;
- scheduled automation;
- the Klokast application;
- recovery or reconciliation jobs;
- other approved Platform workflows.

Apply executes only approved Platform code.

Untrusted callers may choose among exposed Platform syscalls and supply validated semantic parameters. They must not supply executable privileged code.

Conceptually:

```text
authorized desired state
        +
observed state
        +
approved Platform implementation
        |
        v
determine required operations
        |
        v
validate operation scope
        |
        v
execute approved automation
        |
        v
record result
```

## Scope

Apply may operate on the whole Platform or on a bounded target such as:

```text
platform
network
box
app
service
```

Scoped Apply is an optimization and isolation mechanism. It does not create a different authorization model.

## Required properties

### Deterministic privilege boundary

All privileged effects must be implemented by approved Platform code.

No Instance field, AI response, application manifest, or runtime observation may be interpreted as arbitrary privileged shell or equivalent executable code.

### Idempotence

Re-running Apply should be safe where practical.

Automation should converge toward desired state rather than depend on one-shot command sequences.

### Validation

Before executing a privileged operation, Apply validates that:

- the operation is supported by the current Platform implementation;
- its parameters are valid;
- the target exists or is expected to exist;
- the operation is permitted by current Instance policy;
- required capabilities have already been authorized.

### No authority invention

Apply must never create a new capability merely because it appears operationally convenient.

When new authority is required, Apply reports the missing authorization to the caller.

### No desired-state mutation from observation

Apply may update operational records and audit data, but it must not rewrite desired state merely to match observed reality.

### Auditability

Apply records enough information to answer:

- what operation was requested;
- who or what requested it;
- which Platform version executed it;
- which target was affected;
- whether it succeeded or failed.

This is an audit requirement, not a cryptographic proof chain for every intermediate artifact.

### Failure and recovery

A failed Apply must fail visibly.

Where an operation cannot be atomic, its automation should be restartable or reconciliatory so that a later Apply can complete or repair the transition.

Rollback may be used where appropriate, but the Platform does not require every operation to be a transaction.

## Development mode

In development, Apply may execute directly from the developer's mutable working tree.

The developer may add or modify privileged automation without production code-admission ceremony.

Development mode therefore does not provide the production code-integrity guarantee.

## Production mode

In production, Apply executes only Platform implementation admitted under the production lifecycle policy.

The autonomous AI may invoke that implementation but must not modify it or substitute newly generated privileged code.

A production Platform software update changes the code that Apply is allowed to execute and therefore follows the production Platform-update authorization path.

## Non-goals

Apply does not need:

- an immutable signed Plan for every routine reconciliation;
- separate receipts for every trusted component on every run;
- a fresh human approval for operations already covered by authorized desired state;
- cryptographic attestation between components that are already inside the same Integrity TCB merely to prove normal internal data flow.

Such mechanisms may be added later only where they protect a specific trust boundary.

## Summary rule

> Apply implements authorized state with approved code. It does not create authority and it does not decide what code is trusted.
