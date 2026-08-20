# Klokast Instance Specification v1

This private repository declares the desired state of one Klokast instance.
It has two authoritative files:

- `klokast-instance.json`: private topology, membership, connectivity-profile,
  controller, airunner, application, and retained-data intent;
- `klokast.lock.json`: the exact public engine repository, branch, commit, and
  commit-pinned schema.

`.gitignore`, `AGENTS.md`, and this file are support files. The repository must
not contain secrets, generated state, observed runtime status, or user data.

Create a new standalone repository with the builder-approved binary:

```sh
klokast init \
  --instance /path/to/new-klokast-instance \
  --values /path/to/complete-klokast-instance.json
```

The values file is the complete instance document. `init` writes deterministic
JSON, writes the engine lock, creates branch `main`, and stages the files. It
does not create a commit or remote and does not copy the values file under
another name.

Validate local edits offline:

```sh
klokast check --instance /path/to/klokast-instance
```

Compare the instance with the transitional controller inputs:

```sh
klokast plan \
  --instance /path/to/klokast-instance \
  --compatibility-deployment /path/to/deployment.yml \
  --compatibility-registry /path/to/platform-resources.yml \
  --compatibility-controller-ha /path/to/controller-ha.yml
```

These commands do not apply Platform changes. See the public engine document
`doc/klokast-instance-specification.md` at the locked commit for the complete
rules.
