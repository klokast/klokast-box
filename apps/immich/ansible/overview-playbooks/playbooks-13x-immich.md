# Immich Automation Overview

This overview is the first file to read before changing the multisite Immich
automation. Immich app code stays under `apps/immich/`, while router and Podman
VM firewall policy is owned by the platform-resource layer.

## Entry Point

Use `apps/immich/bin/immichctl`, not `ansible-playbook` directly, unless
deliberately debugging one playbook. The wrapper:

- accepts box names such as `k001`, not VM hostnames such as `k001-bak`.
- renders temporary per-box inventories with `ansible/bin/render-node-inventory`.
- combines app-local roles with shared roles.
- requires a deployment platform-resource registry for `install`, `verify`,
  `promote`, and `failback`.
- runs `ansible/bin/platform-resources --registry ... --app immich verify`
  before app install and app verify.
- exposes `destroy --wipe-data --yes` for intentional clean-slate removal of
  Immich runtime state, backup remnants, app grants, secrets, and resource
  ownership.

## Deployment Model

Immich is active/passive across two boxes:

- Active `<box>-bak`: rootless Podman pod with Immich server,
  machine-learning, PostgreSQL, and Valkey.
- Passive `<box>-bak`: same backend stack installed but stopped.
- Active `<box>-dmz`: private Tailscale ingress identity `photos` with
  `tag:immich`, serving `https://photos.<tailnet>`.
- Passive `<box>-dmz`: same private ingress installed but stopped.

## Important Invariants

- The router must not DNAT WAN `80` or `443` to Immich.
- Backend exposure is narrow: the pod binds only the backend zone VM address on
  TCP `2283`.
- PostgreSQL, Valkey, and machine-learning have no host ports.
- Images are locked in `apps/immich/images.lock.yml`.
- Secrets come from controller environment variables and are written with
  `no_log` into target-side secret files. Never commit secrets.
- Promotion is manual to avoid split-brain.
