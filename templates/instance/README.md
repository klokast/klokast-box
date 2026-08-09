# Klokast instance Contract v1 template

This directory is the canonical single-box desired-state template bundled with
the matching `klokast-box` engine revision. It contains no secrets, generated
state, private application configuration, or local extension interface.

Contract v1 has four authoritative files:

- `klokast.yml`: contract version and the two desired-state paths;
- `klokast.lock.yml`: generated engine repository, ref, and full commit lock;
- `ops/deployment.yml`: topology, identities, and controller/airunner placement;
- `ops/platform-resources.yml`: access capabilities, app enablement, placement,
  and public-manifest resource bindings.

The source template intentionally has no `klokast.lock.yml`: a generated
instance must receive a lock for the exact engine commit used by its
builder-approved `klokast` binary. Instance generation is a later milestone.

For now, validate an existing standalone private instance repository with:

```sh
klokast check --instance /path/to/klokast-instance
```

The check is offline and non-mutating. Only `check` and `version --json` are
implemented in this milestone.
