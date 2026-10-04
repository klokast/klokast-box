# Threat model

Human-approved Platform code defines privileged mechanisms.
Authorized policy defines their permitted scope.
The admin-agent decides when to invoke them.

Klokast separates **Platform infrastructure** from **user workloads**.

The Platform infrastructure includes the components that enforce isolation, networking, identity, storage, updates, and orchestration.
User workloads include applications, containers, and user-agents running inside the environments provided by the Platform.

The primary security goal is:

> Compromise or malfunction of a user workload, external dependency, or AI agent must not silently redefine the Platform's security boundaries.

Klokast favors a small number of strong trust boundaries over approval ceremonies for routine operations.

## Integrity-TCB

The **integrity Trusted Computing Base (integrity-TCB)** is the minimum set of components whose compromise can redefine or bypass Platform security policy.

It includes the infrastructure enforcement layer and the approved privileged automation that controls it.

Typical members include:

- `<box>-dom0`;
- `<box>-router`;
- the privileged parts of `<box>-ops`;
- infrastructure credentials and authorization mechanisms;
- Platform policy, validation, compiler, and executor code;
- approved Ansible playbooks and other privileged automation;
- the mechanism that admits Platform software into production.

The integrity-TCB protects the integrity of Platform boundaries.
It is not intended to guarantee uninterrupted availability.

The integrity-TCB should remain as small and deterministic as practical.

## Admin-agent

The **admin-agent** is the autonomous AI administrator of the Platform.

Its trust depends on the Platform lifecycle mode.

### Production

In production, the admin-agent is an operator of the Platform, not an authority that can redefine it.

It may:

- inspect Platform and application state;
- diagnose problems;
- choose among existing Platform operations;
- invoke approved privileged automation;
- propose configuration changes;
- write and test proposed new Platform automation.

It must not:

- modify the admitted Platform implementation;
- make newly written privileged code executable;
- bypass Platform policy through an unrestricted privileged programming interface.

A compromised admin-agent may misuse operations already available to it, but must not be able to create new privileged mechanisms or expand its own execution authority.

### Development

In development, the admin-agent may modify, deploy, and execute Platform code and privileged automation as part of normal development.

A development deployment therefore does not provide the production code-integrity guarantee.

The difference between development and production is a lifecycle property of the Platform, not a different AI-agent role.

## User workloads

Applications, application containers, user-agents, and application-specific automation are outside the integrity-TCB.

They may request capabilities but must not directly control:

- Xen or `dom0`;
- router or infrastructure firewall policy;
- Platform identities or infrastructure credentials;
- infrastructure networking;
- privileged Platform code.

Compromise of one user workload should be contained to the capabilities explicitly granted to that workload.

## Human authority

The human is the root authority for durable changes to Platform trust or deployment authority.

There are two distinct admission decisions.

### Code admission

Code admission decides which privileged Platform implementation is trusted.

In production, admitting a new Platform release is a human-authorized change to the integrity-TCB.

Once admitted, the Platform and admin-agent may use the mechanisms provided by that release without further human approval for each execution.

### Capability admission

Capability admission decides what a deployment, application, workload, or identity is allowed to do.

Human authorization is required when a change expands durable authority beyond what is already authorized.

Examples include installing an application with new capabilities, adding infrastructure identity, or granting access across an existing security boundary.

The human should authorize semantic capabilities that can be understood, not generated implementation details such as nftables rules or Ansible tasks.

## Autonomous operation

Operations within already-approved code and already-authorized policy may execute autonomously.

Examples include:

- applying an approved firewall policy;
- restarting or replacing a failed service;
- A/B updating a container;
- renewing certificates;
- running backups;
- applying routine package updates;
- restoring desired state after drift.

Automation should not require human approval merely because its implementation performs privileged actions.

## Desired state and observation

The private Instance repository describes durable **desired state** for one Platform deployment.

Observed runtime state describes what currently exists.

Observation is evidence, not authority.
It must not silently become desired state.

The Platform reconciles observed state toward authorized desired state.

## Public Platform code and private Instance state

The public `klokast/klokast-box` repository contains reusable Platform implementation.

Each deployment has a private Instance repository containing deployment-specific desired state.

Code in the public repository does not gain production authority merely because it exists on a branch.
Production authority comes from Platform code admission.

A modification to the private Instance repository does not by itself authorize an expansion of deployment authority.
Authority-expanding changes must enter through the applicable human authorization path.

## External software and services

Klokast necessarily trusts external software and service providers within the scope where their software or service has authority.

Examples include:

- operating-system repositories;
- application developers;
- container registries;
- Git hosting;
- Tailscale;
- other integrated infrastructure providers.

Human confirmation of every upstream update does not materially improve security when the human cannot audit the update.

Protection should instead come from authenticated release channels, isolation, least privilege, small TCBs, backups, rollback, and recovery procedures.

## Accepted residual risks

The model does not claim to survive compromise of every integrity-TCB component.

In particular:

- compromise of `dom0` may compromise its guests;
- compromise of privileged Platform executors may bypass Platform policy;
- a malicious update from a trusted software supplier may compromise the scope in which that software runs;
- compromise of the human authorization mechanism may authorize malicious changes.

These risks are mitigated primarily by reducing the integrity-TCB, isolating roles, controlling code and capability admission, and maintaining recovery paths.

## Design principle

> Human-approved code defines privileged mechanisms; authorized policy defines their permitted scope; the admin-agent chooses when to invoke them.

Klokast should add security complexity only when it preserves a concrete trust boundary.
