The public `klokast/klokast-instance` repository defines desired state only:
- the canonical example,
- a contract fixture,
- readable documentation,
- a source for integration tests.

# `klokast-instance` rules

- Do not edit a `klokast-box` checkout from this repository.
- Changes that are generic to Klokast belong in a separate upstream branch and pull request.
- Never commit secrets.
- Prefer changing declarative YAML over generated files.
- Validate before applying changes.
- `ops/deployment.yml` is the canonical instance topology.
- `ops/platform-resources.yml` is the canonical private platform-resource registry.
- Do not create parallel sources of truth in Ansible inventory files.
- Never write generated output into tracked paths.
- Never place secrets, credentials, private keys, or runtime state in this repository.

# Deployment

The `klokast` CLI is the only supported interpreter of a Klokast instance repository. Internally, klokast may call Ansible, image builders, Tailscale tooling, and app-specific controllers.
Users should create a private empty repository and run `klokast init`, rather than copying a static template and manually editing every file.

The deployment flow is:

```
klokast check
klokast doctor
klokast plan
klokast apply
klokast check --live
```

`klokast` CLI generates a complete, target-specific deployment scaffold and pins the engine version that generated it:

```
mkdir my-klokast
cd my-klokast

klokast init \
  --profile single-box \
  --box-id box-001 \
  --hostname-prefix k001
```

This generates:
```
klokast.yml
klokast.lock.yml
ops/deployment.yml
ops/platform-resources.yml
deployment.md
.codex/skills/deploy-klokast/
apps/
extensions/
```

# Deployment profiles

The deployment profile fed to `klokast init` (e.g. `--profile single-box` and `--profile two-boxes`) is one coherent unit.

For example, `klokast init --profile two-boxes` defines which box is active or passive for each application:
```
nextcloud:
  enabled: true
  resilience_mode: active_passive
  placement:
    active_master: box-001
    passive_backup: box-002
```

