# Klokast Instance

This private repository contains desired state in `klokast-instance.json`.
Do not commit secrets, runtime observations, generated output, or user data.

Create it with:

```sh
klokast init --instance PATH --values FILE
```

Validate and preview changes with:

```sh
klokast check --instance PATH
klokast plan --instance PATH --json
```

Use ordinary Git to commit and push desired state. These commands do not apply
Platform changes. See the public `doc/klokast-instance-specification.md` and
`doc/platform-syscalls.md` for the source and execution contracts.
