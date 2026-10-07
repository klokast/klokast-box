# Shared helper contributor instructions

Before changing shared helpers, read the [catalog](README.md) and its linked
ownership contract. Follow the catalog's extraction and delivery rules.

- Maintain the catalog with each interface change. Keep consumer and test
  references current.
- Check all listed consumers before changing behavior. Add or update behavior
  tests when adding executable helpers, and run the affected consumer tests.
- Follow the [Ansible guide](../../ansible.md) for installation changes and the
  [shell rules](../../../doc/shell.md) for executable shell helpers.
- Keep planned interfaces distinct from implemented interfaces. Do not create
  placeholder modules or commands to populate the scaffold.
