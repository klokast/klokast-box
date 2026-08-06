# Local extensions

Create subdirectories only when needed:

- `apps/` — private applications not suitable for upstream
- `ansible/` — instance-specific roles or playbooks
- `hooks/` — lifecycle hooks exposed by the upstream platform

An extension must use a documented upstream interface. Do not override
or shadow arbitrary upstream implementation files.

