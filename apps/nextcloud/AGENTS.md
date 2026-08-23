# Nextcloud App Instructions

Nextcloud is deployed as an app-local automation package. Keep all
Nextcloud-specific playbooks, roles, scripts, templates, image build contexts,
and runbooks under `apps/nextcloud/`.

Do not use historical box names in this app. Test examples use `boxa` as the
active master and `boxb` as the passive backup.
The Platform backend VM suffix is still `-bak`.

## Architecture

- Active master:
  - `<box>-bak`: Nextcloud backend containers.
  - `<box>-dmz`: private Tailscale ingress and optional Cloudflare Tunnel
    connector. Instance Specification selects it with the semantic
    `public-ingress: cloudflare-tunnel` feature.
- Passive backup:
  - `<box>-bak`: same backend stack installed, but stopped until promotion.
  - `<box>-dmz`: same private ingress and optional tunnel connector installed,
    but stopped until promotion.

Only one site is writable at a time. Do not serve authenticated reads from the
passive backup; hourly replication can leave it stale relative to permissions,
shares, versions, previews, sessions, and file locks.

## Required Runtime

Backend VM, rootless Podman under `neo`:

- `nextcloud-fpm`: custom image based on pinned `nextcloud:*fpm-alpine`.
- `nextcloud-web`: pinned `nginx:*alpine`.
- `nextcloud-postgres`: pinned `postgres:*alpine`, no host port.
- `nextcloud-redis`: pinned `redis:*alpine`, no host port.
- `nextcloud-cron`: same app image running `/cron.sh`, active site only.
- `nextcloud-backup`: custom Alpine image with restic and PostgreSQL client.

DMZ VM:

- `nextcloud-private-ingress`: OpenRC-managed app-specific Tailscale identity
  `next` with `tag:nextcloud`, serving `https://next.<tailnet>` to the backend
  upstream.
- `nextcloud-cloudflared`: OpenRC service running the pinned Alpine
  `cloudflared` package as `neo`, outbound-only, only when the deployment
  registry enables `cloudflare-tunnel-egress`.

Backend containers inherit the VM Tailscale identity. The private Nextcloud
frontend is the app-specific exception because family access needs an ACL
boundary narrower than `tag:dmz`.

Network access and Tailnet ingress intent are declared in
`apps/nextcloud/platform-resources.yml` and applied by the platform-owned
`ansible/bin/platform-resources` workflow. The Nextcloud app roles must not
mutate router, Podman VM firewall, Tailnet policy, or privileged infrastructure
directly.

## Image Sources

Canonical backend image references stay under `docker.io` and are pinned in
`images.lock.yml`. Target VMs run `nextcloud-image-source-preflight` before
install to pick a reachable source profile, verify that mirror digests match the
lock file, and prove the selected digest refs are pullable.

Current profiles:

- `direct`: canonical Docker Hub.
- `china-1ms`: `docker.1ms.run`.
- `china-daocloud`: `docker.m.daocloud.io`.
- `china-daocloud-prefix`: `m.daocloud.io/docker.io`.

Use `controller-oci` only after adding explicit implementation support and
digest verification for transferred archives.

## Automation Entry Point

Use `apps/nextcloud/bin/nextcloudctl`. Pass box names, not VM hostnames:

```sh
apps/nextcloud/bin/nextcloudctl install \
  --active-master boxa \
  --passive-backup boxb \
  --domain cloud.example.tld \
  --resources-registry path/to/platform-resources.yml
```

Required install environment:

- `NEXTCLOUD_ADMIN_PASSWORD`
- `NEXTCLOUD_POSTGRES_PASSWORD`
- `NEXTCLOUD_RESTIC_PASSWORD`
- `NEXTCLOUD_RESTIC_REPOSITORY`

Required only when the semantic public-ingress feature enables
`cloudflare-tunnel-egress` and both placement boxes enable `edge-ingress`:

- `NEXTCLOUD_CLOUDFLARED_TOKEN_ACTIVE`
- `NEXTCLOUD_CLOUDFLARED_TOKEN_PASSIVE`

The deployment server must also expose `/usr/local/sbin/ts-authkey-nextcloud`
for `tag:nextcloud`. The target is OAuth-backed one-off key minting from
`/etc/klokast/tailscale-policy.env`; legacy reusable key files under
`/etc/tailscale-auth/` are transitional only.

Secrets must not be committed.
