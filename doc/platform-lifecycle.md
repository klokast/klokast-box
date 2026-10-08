Platform lifecycle determines which privileged implementation is allowed to run.

The Platform lifecycle modes are:

1. **development**: easy, may change the Platform freely, Platform code is allowed to perform privileged operations.
2. **production**: restricted, may execute only an admitted Platform release.

## Development and production are deployment properties

Development and production describe the trust model of a running Klokast deployment. They are not stages that a deployment transitions between during normal operation.

A **development deployment** runs mutable Platform code under developer authority. Privileged code may be edited, rebuilt, and redeployed directly.

A **production deployment** runs only an admitted Platform release. Its privileged Platform implementation is protected from modification by autonomous agents and ordinary runtime processes.

Code moves from development into production through a release boundary:

development work -> testing -> Platform release -> human admission -> production deployment

This is not a transition of the development deployment itself into production. The release produced and tested during development is admitted into a separate production trust environment.

Likewise, a production deployment must not expose a normal operation that turns it into an unrestricted development deployment. Disabling production code protections is a recovery or reprovisioning action outside the normal Platform lifecycle.

The essential distinction is:

> Development determines which code may become a Platform release.
> Production determines which released code is allowed to execute with privileged authority.

## Development

Development mode is optimized for rapid Platform iteration:
- there is no Integrity-TCB guarantee
- code can be tested and iterated freely without code-admission authorizations

The developper and the development-agent are the root authority. They can:

- inspect the Platform
- diagnose missing functionality
- propose or write new Platform automation
- introduce arbitrary Platform code changes
- modify Platform source code
- edit, validate, commit and push private Instance desired state through ordinary Git on the active controller
- add or change privileged automation
- modify Ansible playbooks, compilers, executors, schemas, and syscalls
- deploy directly from a mutable working tree
- repeatedly rebuild and restart Platform components

The focus of development is to gradually convert operational intelligence into deterministic automation.

## Production

Production is optimized for controlled Platform integrity.
Production can identify which Platform implementation was admitted
and prevent unauthorized replacement of it.

The production Platform implementation is not writable by:

- developer-agent
- admin-agent
- user-agent
- application workloads
- ordinary unprivileged Platform processes

The AI may invoke the syscalls and automation provided by the installed release,
but it cannot make newly generated privileged code executable.

In production mode, code has no privileged authority until it is included in a Platform release that passes the production admission boundary.

## Application installation

Applications must be installable from authorized Instance declarations in both
development and production. Installation must not require development mode.
The Instance selects applications, placement, and permitted capabilities; see
[Instance desired state](klokast-instance-specification.md).

In production, admitted Platform mechanisms supply resources and app-scoped
inputs. Application-owned code configures and runs the application within its
granted environment. Adding an application that uses existing supported
resource types does not by itself require a new Platform release. Capability
expansion follows [the threat model](threat-model.md#capability-admission).

A new privileged host hook, resource type, broker action, or other Platform
mechanism requires Platform release admission. Moving code into `apps/` does
not change its execution authority. Application code must not receive a
general controller shell or infrastructure credentials to install itself.

The current controller application tools still use development-only resource
interfaces. Automatic installation from Instance, production application
delivery, and dependency validation remain implementation work. This contract
does not permit bypassing their current lifecycle guards.

## Platform release

A Platform release is the unit of production code admission.

It contains the coherent Platform implementation required by the deployment, including:
- privileged executors
- compilers
- schemas
- playbooks
- automation

It specifies:
- version
- source identity
- artifact identity
- privileged Platform code
- approved automation

A production deployment runs a specific approved Platform release.

Compromise of a development environment should not provide the ability to push a production release.

## Production upgrade

A production Platform upgrade changes the Integrity-TCB.
It requires human authorization.
The human can only authorize the new Platform release as a whole.

- In development: development and testing produce a release artifact, which may then be admitted into a production deployment.
- In production mode: admin-agent detects a new release in Klokast upstream code repository -> human approve platform upgrade -> admin AI applies the upgrade
