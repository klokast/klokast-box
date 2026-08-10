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

The source template intentionally has no `klokast.lock.yml`. Generate a new
standalone instance with the builder-approved binary:

```sh
klokast init \
  --instance /path/to/new-klokast-instance \
  --profile single-box \
  --values /path/to/init-values.json
```

The strict JSON values file supplies the private instance name, Tailnet groups,
site country, optional physical location, and hostname prefix. It has no
timezone field. Platform time is always `Etc/UTC` (GMT).

`init` writes the lock for the exact engine commit, creates a local Git
repository on branch `main`, and stages the generated inputs. It does not make
a commit, add a remote, or copy the values file. The destination must not exist
and must not be inside another Git worktree.

Validate a standalone private instance repository with:

```sh
klokast check --instance /path/to/klokast-instance
```

Both commands are offline. `check` is non-mutating.

Before a later migration, compare the instance with a transitional private
platform-resources registry:

```sh
klokast plan \
  --instance /path/to/klokast-instance \
  --compatibility-registry /path/to/platform-resources.yml
```

This command is also offline and read-only. It classifies fields that Contract
v1 cannot yet own. It does not write or apply a deployable plan.
