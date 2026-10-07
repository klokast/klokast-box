# Shared application helpers

This is the authoritative catalog for shared application deployment helpers.
The ownership and authority rules are in
[Shared application helpers](../../../doc/architecture.md#shared-application-helpers).

## Available interfaces

No shared library interfaces are available yet. This directory contains only
documentation. Existing application implementations still own their helper code.
See the [CLI index](../../../doc/cli-tools.md) for current Platform commands and
their execution locations.

Only library interfaces listed as available in this catalog are supported for
shared application use. Other modules in `ansible/lib/` are internal Platform
code.

## Source locations

| Location | Purpose |
| --- | --- |
| `ansible/lib/app_support/` | Shared Python helpers for application deployment tools. |
| `ansible/bin/` | Executable helpers used from shell, Python, or Ansible. |
| `ansible/tests/` | Shared helper behavior tests. |
| `apps/<app>/` | Application callers, configuration, policy, and lifecycle code. |

Paths in this table are relative to the repository root. Add executable helpers
to the existing CLI index and link their entries here. Keep each interface
description in one place.

## When to share code

Before adding a helper, check this catalog and the existing Platform commands.
Use existing interfaces when they provide the required behavior.

Extract shared code when at least two concrete consumers need the same behavior.
Similar source text alone is insufficient. Keep small copies when their intended
behavior differs.

Keep inputs explicit. Do not add application-name branches to a shared helper.
Leave application decisions with the caller. Prefer an executable interface
when callers use different languages; use a Python module when callers need
Python functions. Do not add a separate service or package registry for reuse.

## Catalog entry requirements

For each available helper, document:

- Purpose and canonical source path.
- Supported command or import interface, inputs, outputs, and failure behavior.
- Execution location, account, required authority, and dependencies.
- Side effects, network access, and temporary-file cleanup, where applicable.
- Installation or packaging method and source version identification.
- Current consumers, relevant behavior tests, and a short usage example.

Record planned helpers separately from available interfaces. Add a helper to
the available catalog only when its implementation and delivery are present.

## Delivery and changes

Controller helpers are delivered through the Platform controller toolchain.
Their installation is independent of application installation and removal.
Use the existing repository commit and Platform release process to identify
their source; see [Platform lifecycle](../../../doc/platform-lifecycle.md).
Update the owning Ansible installation tasks when adding executable helpers.

If a helper must run inside an application VM or container, package the required
code into that application's artifact from the canonical source. Record the
source version in the artifact's build metadata. The target must not import
code from another application's directory or a mutable controller checkout.
A helper fix requires rebuilding and delivering the affected artifacts.

For a helper change, check every listed consumer and run the relevant helper
and consumer tests. Test the intended independent use: an application must be
able to use the helper without installing another application. Update affected
callers and delivery tasks with interface changes. No separate SDK versioning
process is required.
