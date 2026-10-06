# Immich App Instructions

Immich is deployed as an app-local automation package. Keep all Immich-specific
playbooks, roles, scripts, templates, image lock files, and runbooks under
`apps/immich/`.

Use neutral box names in this app. Test examples use `boxa` as the
active master and `boxb` as the passive backup. The Platform backend VM suffix
is still `-bak`.

## Architecture

- Active master:
  - `<box>-bak`: Immich backend containers.
  - `<box>-dmz`: private Tailscale ingress.
- Passive backup:
  - `<box>-bak`: same backend stack installed, but stopped until promotion.
  - `<box>-dmz`: same private ingress installed, but stopped until promotion.

Only one site is writable at a time. The passive site is a recovery target, not
a read replica.

## Required Runtime

Backend VM, rootless Podman under `neo`:

- `immich-server`: pinned upstream Immich server image.
- `immich-machine-learning`: pinned upstream Immich machine-learning image.
- `immich-postgres`: pinned upstream Immich PostgreSQL image.
- `immich-valkey`: pinned Valkey image, no host port.
- Named Podman volumes hold library, database, cache, backup, and restore
  state.
- Backups run through a short-lived pinned Alpine container with restic
  installed at runtime.

DMZ VM, rootless Podman under `neo`:

- `immich-private-ingress` pod with proxy and userspace Tailscale sidecar.
- Tailscale identity: `photos` with `tag:immich`.
- Tailscale state is a named Podman volume.

Backend containers inherit the VM Tailscale identity. The private Immich
frontend is the app-specific exception because family access needs an ACL
boundary narrower than `tag:dmz`.

Network access and Tailnet ingress intent are declared in
`apps/immich/platform-resources.yml` and applied by the platform-owned
`ansible/bin/platform-resources` workflow. The Immich app roles must not mutate
router, Podman VM firewall, Tailnet policy, or privileged infrastructure
directly.

## Image Sources

Canonical image references are pinned in `images.lock.yml`. Target VMs run
`immich-image-source-preflight` before install to verify the selected digest
refs and prove they are pullable.

Current source policy:

- `ghcr.io` images use direct GHCR pulls only.
- `docker.io` images may use the same Docker Hub mirror profiles as Nextcloud.

Do not use unpinned `latest`, `release`, or `v2` tags for deployment.

## Automation Entry Point

Use `apps/immich/bin/immichctl` on the active development controller. Declare
Immich present and its placement in Instance before installation. Pass box names,
not VM hostnames:

```sh
apps/immich/bin/immichctl install \
  --active-master boxa \
  --passive-backup boxb \
  --resources-registry path/to/platform-resources.yml
```

Required install environment:

- `IMMICH_POSTGRES_PASSWORD`
- `IMMICH_RESTIC_PASSWORD`
- `IMMICH_RESTIC_REPOSITORY`

The deployment server must also expose `/usr/local/sbin/ts-authkey-immich` for
`tag:immich`. It uses OAuth-backed one-use key minting from
`/etc/klokast/tailscale-policy.env`.

Secrets must not be committed.
