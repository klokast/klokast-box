# Nextcloud Automation Overview

This overview is the first file to read before changing the multisite
Nextcloud automation. Nextcloud app code stays under `apps/nextcloud/`, while
router and Podman VM firewall policy is owned by the platform-resource layer.

## Entry Point

Use `apps/nextcloud/bin/nextcloudctl`, not `ansible-playbook` directly, unless
you are deliberately debugging one playbook. The wrapper:

- accepts box names such as `boxa`, not VM hostnames such as `boxa-bak`.
- renders temporary per-box inventories with `ansible/bin/render-node-inventory`.
- combines the app role path with the shared role path:
  `apps/nextcloud/ansible/roles:${REPO_ROOT}/ansible/roles`.
- requires a deployment platform-resource registry for `install`, `verify`,
  `promote`, and `failback` through `--resources-registry` or
  `KLOKAST_PLATFORM_RESOURCES_REGISTRY`.
- runs `ansible/bin/platform-resources --registry ... --app nextcloud verify`
  before app install and app verify. It does not apply router or VM firewall
  policy.

`nextcloudctl install` runs:

1. platform-resource verification
2. `00-preflight.yml`
3. `20-install.yml`

`nextcloudctl verify` runs:

1. platform-resource verification
2. `40-verify.yml`

`nextcloudctl failback` is an alias for `install` with the active/passive
direction passed by the operator. There is no separate failback playbook.

## Deployment Model

Nextcloud is active/passive across two boxes:

- Active `<box>-bak`: rootless Podman pod with Nextcloud FPM, nginx,
  PostgreSQL, Redis, cron, and backup helper containers.
- Passive `<box>-bak`: the same backend stack is installed but stopped.
- Active `<box>-dmz`: private Tailscale ingress identity `next` with
  `tag:nextcloud`, serving `https://next.<tailnet>` to trusted users.
- Passive `<box>-dmz`: the same private ingress is installed but stopped.
- Optional public ingress: Cloudflare Tunnel connector service on the active
  DMZ only when the deployment registry enables the
  `cloudflare-tunnel-egress` resource.

Only one site should be writable or serving authenticated traffic. The passive
site is a recovery target, not a read replica.

## Important Invariants

- The router must not DNAT WAN `80` or `443` to Nextcloud.
- Containers inherit the VM Tailscale identity. The app-specific Tailscale
  identity for private Nextcloud ingress is the explicit exception, because
  family members should reach only `tag:nextcloud:443`, not the DMZ VM or the
  backend VM directly.
- Backend exposure is narrow: the pod binds only the backend zone VM address
  on port `8080`. The platform-resource layer permits only the DMZ zone VM
  source to that backend port at the router and backend VM firewall.
- PostgreSQL and Redis have no host ports. They live inside the backend pod.
- Cloudflare Tunnel egress TCP/UDP `7844` is disabled unless the deployment
  registry enables `cloudflare-tunnel-egress`.
- Image references are locked in `apps/nextcloud/images.lock.yml`. The
  `nextcloud-image-preflight` role verifies manifest digests and writes the
  selected target-local pull map to
  `/etc/klokast/nextcloud/image-sources.json`.
- Secrets come from controller environment variables and are written with
  `no_log` into target-side secret files and Podman secrets. Never commit
  secrets or add them to inventory.
- The active site enables the `nextcloud` service, the cron container, and the
  hourly backup hook. The passive site keeps those stopped and removes the
  hourly backup hook.
- Promotion is manual to avoid split-brain. `60-promote.yml` stops old active
  services when reachable, deletes the offline exact `next` Tailscale identity,
  and starts the promoted backend/private ingress services; it does not restore
  data, move Cloudflare routes, or prove that the old active is fenced.

## Wrapper Commands

| Command | Playbooks | Main purpose |
| --- | --- | --- |
| `preflight` | `00-preflight.yml` | Validate selected boxes, domain, and Podman on `-bak`/`-dmz`. |
| `preflight-images` | `05-preflight-images.yml` | Verify and pull locked image digests from each target VM. |
| `remove-legacy` | `10-remove-legacy.yml` | Remove the old root-level private-only proof of concept. |
| `install` | platform resources verify, `00`, `20` | Full convergence for active/passive backend and optional DMZ services. |
| `verify` | platform resources verify, `40` | Verify platform-resource policy, backend runtime, and optional DMZ tunnel state. |
| `backup-check` | `50-backup-check.yml` | Confirm the active backend backup marker is recent. |
| `promote` | `60-promote.yml` | Stop old active services, move the stable private ingress identity, and start promoted backend/DMZ services. |
| `failback` | same as `install` | Reinstall/converge with the desired active/passive direction. |
| `remove` | `90-remove.yml` | Stop and remove managed app services, preserving data by default. |

## Role Map

App-local roles:

- `nextcloud-image-preflight`: validates `images.lock.yml`, selects a
  reachable image source profile, verifies locked digests, pulls images as
  `neo`, and writes `/etc/klokast/nextcloud/image-sources.json`.
- `nextcloud-backend`: builds local app and backup images from locked base
  refs, renders env files and secrets, deploys the rootless backend pod,
  installs OpenRC services, and controls active/passive service and backup-hook
  state.
- `nextcloud-dmz`: when enabled, installs the pinned Alpine `cloudflared`
  package, stores the correct active/passive tunnel token, renders the deploy
  helper and OpenRC service, and keeps the tunnel running only on the active
  DMZ. When disabled, it removes the app-owned connector service/helper.
- `nextcloud-private-ingress`: installs and controls an app-specific
  `tailscaled` instance on DMZ VMs. The active DMZ enrolls as hostname `next`
  with `tag:nextcloud` and serves HTTPS 443 to a local DMZ proxy that forwards
  to the backend upstream; the passive DMZ keeps the service stopped.
- `nextcloud-verification`: checks active backend HTTP/config state, passive
  backend stopped state, active-only private ingress state, and optional
  active-only Cloudflare connector state.
- `nextcloud-backup-check`: checks the active backend backup success marker.
- `nextcloud-legacy-cleanup`: removes the pre-app root-level proof of concept.
- `nextcloud-remove`: removes the app-local services, helpers, config roots,
  containers, and optionally persistent data.

Platform-resource components:

- `apps/nextcloud/platform-resources.yml`: declares required compute, network,
  Tailnet, and artifact resources.
- `ansible/bin/platform-resources`: compiles the deployment registry and app
  manifests, then applies or verifies platform-owned nftables include files and
  records apply provenance.
- `ansible/playbooks/80-platform-resources.yml`: applies compiled router and Podman
  VM platform-resource rules.
- `ansible/playbooks/81-platform-resources-verify.yml`: verifies compiled rules.

## Phase 1: Preflight

### `00-preflight.yml`

Purpose: validate the selected active/passive pair before making changes.

- Loads `../../images.lock.yml` so image lock structure is available.
- Asserts `nextcloud_active_master`, `nextcloud_passive_backup`, and
  `nextcloud_domain` were passed by the wrapper and have valid forms.
- Rejects using the same box for active and passive.
- Asserts each target is one of the selected boxes and is a backend or DMZ VM.
- Checks `/usr/bin/podman` exists, failing with a reminder to run the Platform
  Podman VM phases first.

### `05-preflight-images.yml`

Purpose: independently verify image availability from each target VM.

Role: `nextcloud-image-preflight`

- Requires `apps/nextcloud/images.lock.yml`.
- Creates `/etc/klokast/nextcloud` and installs the helper
  `/usr/local/sbin/nextcloud-image-source-preflight`.
- Tries the selected image source profile, or `auto` by default.
- Verifies each mirror returns the locked digest, proves the digest refs are
  pullable by `neo`, and writes the selected pull refs to
  `/etc/klokast/nextcloud/image-sources.json`.

## Phase 2: Platform Resources

Before `install` or `verify`, the wrapper checks that:

- `nextcloud` is enabled in the deployment platform-resource registry.
- registry boxes match `--active-master` and `--passive-backup`.
- router and backend VM nftables include files contain the expected compiled
  rules.

The platform-resource layer is applied separately:

```sh
ansible/bin/platform-resources --registry path/to/platform-resources.yml --approved-commit "$(git rev-parse HEAD)" apply
```

Run `apply` against the full registry. Use `--app nextcloud` only for
Nextcloud-scoped `show` and `verify`.

## Phase 3: Legacy Cleanup

### `10-remove-legacy.yml`

Purpose: remove the older root-level private-only proof of concept before the
app-local layout is installed. This is an explicit one-time migration command,
not part of normal `install` convergence after the app-local layout exists.

Role: `nextcloud-legacy-cleanup`

- Stops the legacy `nextcloud` OpenRC service if present.
- Removes `/etc/init.d/nextcloud`,
  `/usr/local/sbin/nextcloud-stack-deploy`, and the legacy
  `/usr/local/sbin/nextcloud-occ`.
- Removes the legacy `nextcloud` pod on backend VMs.
- Removes `/etc/klokast/nextcloud` by default.
- Wipes `/srv/nextcloud` only when `nextcloud_wipe_legacy_data=true`.

## Phase 4: Install

### `20-install.yml`

Purpose: install or refresh the active/passive backend, private DMZ Tailscale
ingress, and optional DMZ Cloudflare connector.

Roles:

- `nextcloud-image-preflight`
- `nextcloud-backend` on backend VMs
- `nextcloud-dmz` on DMZ VMs
- `nextcloud-private-ingress` on DMZ VMs

Backend role details:

- Reads required environment secrets:
  `NEXTCLOUD_ADMIN_PASSWORD`, `NEXTCLOUD_POSTGRES_PASSWORD`,
  `NEXTCLOUD_RESTIC_PASSWORD`, and `NEXTCLOUD_RESTIC_REPOSITORY`.
- Deploys the rootless Podman pod through
  `/usr/local/sbin/nextcloud-backend-deploy`.
- Builds `localhost/klokast-nextcloud-app:33.0.3` and
  `localhost/klokast-nextcloud-backup:1` from locked base images.
- Creates containers for PostgreSQL, Redis, Nextcloud FPM, nginx, and active
  site cron. The passive backend pod is stopped after deployment.
- Enables and starts `nextcloud` only on the active backend.
- Keeps both the public hostname and `next.<tailnet>` in Nextcloud trusted
  domains, and removes `overwritehost` so both ingress paths work.
- Installs `/etc/periodic/hourly/nextcloud-backup` only on the active backend.

Private ingress role details:

- Requires `/usr/local/sbin/ts-authkey-nextcloud` on the deployment server.
  Target auth-key issuance is OAuth-backed one-off minting from
  `/etc/klokast/tailscale-policy.env`; legacy reusable key files under
  `/etc/tailscale-auth/` are transitional only.
- Installs the Alpine `tailscale` package on DMZ VMs.
- Renders `/etc/init.d/nextcloud-private-ingress` and
  `/usr/local/sbin/nextcloud-private-ingress-converge`.
- Runs only on the active DMZ as hostname `next` with `tag:nextcloud`, using
  Tailscale Serve HTTPS 443 to proxy to the backend zone VM on port `8080`.

Cloudflare DMZ role details:

- Does nothing network-facing unless `nextcloud_cloudflare_tunnel_enabled=true`.
- When enabled, requires `NEXTCLOUD_CLOUDFLARED_TOKEN_ACTIVE` and
  `NEXTCLOUD_CLOUDFLARED_TOKEN_PASSIVE`.
- Installs pinned Alpine package `cloudflared=2026.3.0-r1` from edge/testing,
  renders `/usr/local/sbin/nextcloud-cloudflared-deploy` and
  `/etc/init.d/nextcloud-cloudflared`, and keeps the connector running only on
  the active DMZ.

## Phase 5: Verify

### `40-verify.yml`

Purpose: verify the multisite runtime state on backend and DMZ VMs.

- Backend checks confirm the pod state, the single backend zone VM `8080/tcp`
  host exposure, active HTTP status, active Nextcloud config, and passive
  stopped state.
- DMZ checks confirm private Tailscale ingress runs only on the active DMZ,
  enrolls as `next` with `tag:nextcloud`, and serves the private URL.
- DMZ checks confirm Cloudflare Tunnel is stopped when disabled, or running
  only on the active DMZ when enabled.

### `50-backup-check.yml`

Purpose: verify recent active-backend backup success.

- Runs only on the selected active backend VM.
- Checks `/srv/nextcloud/backup/last-success.epoch`.
- Fails when the marker is missing or older than 3900 seconds.

## Phase 6: Promote, Fail Back, Remove

### `60-promote.yml`

Purpose: manually promote the passive site after the operator has fenced or
accepted loss of the old active site.

- Stops the old active backend service if reachable.
- Stops the old active private ingress and optional DMZ tunnel service if
  reachable.
- Clears the old private ingress state directory, waits briefly, and deletes
  the stale offline exact Tailscale machine for hostname `next` and
  `tag:nextcloud`.
- Starts and enables the promoted backend service.
- Starts and enables the promoted private ingress service.
- Starts and enables the promoted DMZ tunnel service when Cloudflare is
  enabled.

Promotion does not restore data, prove backup freshness, modify Cloudflare
routing, or update any durable active/passive inventory.

### `90-remove.yml`

Purpose: remove the app-local deployment from a selected box.

- Stops and disables `nextcloud-private-ingress`, `nextcloud-cloudflared`,
  `nextcloud`, and legacy `nextcloud-firewall` when present.
- Removes the backend `nextcloud` pod.
- Removes stale DMZ `nextcloud-cloudflared` containers.
- Removes managed OpenRC service files, helper scripts, image preflight helper,
  and hourly backup hook.
- Removes `/etc/klokast/nextcloud`, `/etc/klokast/nextcloud-cloudflared`,
  and `/etc/klokast/nextcloud-private-ingress`.
- Deletes the offline exact `next` Tailscale machine for `tag:nextcloud` and
  clears the private ingress Tailscale state.
- Wipes `/srv/nextcloud` only when `nextcloud_wipe_data=true`.
