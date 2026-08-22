# Immich App Instructions

Immich is deployed as an app-local automation package. Keep all Immich-specific
playbooks, roles, scripts, templates, image lock files, and runbooks under
`apps/immich/`.

Do not use historical box names in this app. Test examples use `boxa` as the
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
  state. `/srv/immich/*` is legacy migration input only.
- Backups run through a short-lived pinned Alpine container with restic
  installed at runtime.

DMZ VM, rootless Podman under `neo`:

- `immich-private-ingress` pod with proxy and userspace Tailscale sidecar.
- Tailscale identity: `photos` with `tag:immich`.
- Tailscale state is a named Podman volume. Host `tailscaled`/`nginx` services
  are legacy only.

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

Preferred operator entry point from the admin MacBook is
`apps/immich/bin/immich-install-from-mac`. It generates or reuses the local
Immich secrets file, runs the controller-side infra grant as `smith`,
including stale private-ingress identity cleanup, then runs the app install as
`minion`.

Use `apps/immich/bin/immichctl` for controller-side debugging. Pass box names,
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
`tag:immich`. The target is OAuth-backed one-off key minting from
`/etc/klokast/tailscale-policy.env`; legacy reusable key files under
`/etc/tailscale-auth/` are transitional only.

Secrets must not be committed.

Use `apps/immich/bin/immichctl destroy --wipe-data --yes` only when Immich data
is intentionally disposable. That path is allowed to remove runtime volumes,
the transitional Restic SFTP repository, controller-local Immich secrets, and
approved Immich grants.
