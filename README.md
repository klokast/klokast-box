# Klokast documentation

Use the [documentation index](doc/README.md) to find Platform documents,
application instructions, automation guides, and runbooks.

Read these first:

1. [Glossary](doc/glossary.md)
2. [Threat model](doc/threat-model.md)
3. [Platform lifecycle](doc/platform-lifecycle.md)
4. [Architecture](doc/architecture.md)

Platform contracts:

- [Instance desired state](doc/klokast-instance-specification.md)
- [Controller operations](doc/platform-syscalls.md)
- [Apply specification](doc/apply-specification.md)
- [Resource compiler](doc/architecture.md#resource-compiler)
- [Platform map](doc/platform-map.md)

## Documentation ownership

Subsystem documents describe one mechanism and must not redefine
concepts owned by foundational or contract documents.

Each concept has one normative owner. References should link to that
owner rather than duplicate its rules.

Operational history, deployment-specific evidence, and migration notes
are not normative Platform design.
