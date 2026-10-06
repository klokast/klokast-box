# Documentation index

Start here to find documentation in this repository. General Platform documents
are in `doc/`. Application and implementation instructions are beside their code.
See the [documentation ownership rules](../README.md#documentation-ownership)
for the relationship between these documents.

## Read first

Read these documents in order:

1. [Glossary](glossary.md): terms used in the project.
2. [Threat model](threat-model.md): attackers, trust boundaries, and authority.
3. [Platform lifecycle](platform-lifecycle.md): development, production, and releases.
4. [Architecture](architecture.md): system layers, services, networks, and storage.

## Platform contracts

- [Instance desired state](klokast-instance-specification.md): the private Instance specification.
- [Controller operations](platform-syscalls.md): supported development controller operations.
- [Apply specification](apply-specification.md): inputs, authorization, and execution rules.
- [Resource compiler](architecture.md#resource-compiler): resource requests and Platform grants.
- [Platform map](platform-map.md): observed state, runtime files, and findings.

## Operation and mechanisms

- [Platform deployment](platform-deploy.md): provisioning and controller recovery.
- [VM inspection and tests](platform-updates.md): explicit inspection, template tests, and boot recovery.
- [CLI tools and wrappers](cli-tools.md): command reference.
- [Secure CLI builder](secure-builder.md): the build procedure for the deployable Klokast CLI.
- [Cloudflare setup](cloudflare.md): tunnel and public ingress procedures.
- [Power off](power-off.md): draft shutdown and restart instructions.
- [Operations journal](operations-journal.md): how to keep private case notes on the active controller. The notes themselves are not in this repository.

## Contributor rules and background

- [Git rules](git.md): required commit and push workflow.
- [Shell automation](shell.md): shell implementation rules.
- [LLM-assisted operations](llm-assisted-secure-private-cloud-operations.md): an explanation of design decisions, trade-offs, and failure modes.

## Documentation outside this directory

| Start here | Contents |
| --- | --- |
| [Application catalog](../apps/README.md) | Supported applications and deployment strategy. Continue to the selected application's README and local instructions. |
| [Ansible guide](../ansible/ansible.md) | Automation rules, directory structure, and links to playbook descriptions. |
| [Playbook descriptions](../ansible/overview-playbooks/) | Purpose and stages of the Platform playbooks. |
| [Developer guide](../klokast-dev/README.md) and [developer runbooks](../klokast-dev/runbooks/) | MacBook setup and developer procedures. |
| [Operator runbooks](../klokast-ops/runbooks/) | Operator tooling, remote access, cloud runner setup, and recovery. |
| [NanoKVM recovery](../klokast-ops/runbooks/60-nanokvm-recovery-skill.md) | Console recovery when normal remote access is unavailable. |
| [Infrastructure runbooks](../runbooks/) | Manual network setup and console recovery procedures. |
| [Operations reference](../ops/ops.md) | Box hardware and remote service restart guidance. |
| [Instance template](../templates/instance/README.md) | Starting point for a private Instance repository. |
| [Resource ownership test plan](../test-plan.md) | Destructive tests for resource ownership and firewall rules. |
| [Agent instructions](../AGENTS.md) | Task-specific reading requirements and execution rules. Subdirectories can have additional `AGENTS.md` files. |
