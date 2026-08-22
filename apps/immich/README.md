# Multisite Immich

This directory owns the Immich application automation for the Platform.

## Deployment Model

The deployment is active/passive:

- active master: writable Immich instance
- passive backup: restore-ready instance, stopped until promotion
- trusted-user ingress: Tailscale Serve at `https://photos.<tailnet>` from the
  active DMZ VM
- public ingress: not supported in v1
- administration and recovery: Tailscale to the Platform VMs

Do not serve normal authenticated traffic from the passive backup. It is a
recovery target, not a read replica.

## Install

Preferred operator flow runs on the active ops controller as `smith`.
When the operator starts on an infra-agent host such as `vultr-ops`, first enter
the controller:

```sh
tailscale ssh smith@boxb-ops
cd ~/src/klokast/klokast-box
apps/immich/bin/immich-install-from-controller \
  --active-master boxa \
  --passive-backup boxb
```

The controller installer generates/reuses a controller-local `0600` secrets
file, enables the private registry placement, applies app-scoped platform
resources, grants the app-approved state to `minion`, then runs the app
phase through `doas -u minion`.

The MacBook wrapper remains available when the admin laptop has direct Tailnet
SSH access to both controller users:

```sh
apps/immich/bin/immich-install-from-mac \
  --controller boxb-ops \
  --active-master boxa \
  --passive-backup boxb
```

Both installers validate the `tag:immich` auth-key wrapper configuration, but
they do not apply Tailnet policy.

### Controller Manual Path

Set required secrets in the controller environment:

```sh
export IMMICH_POSTGRES_PASSWORD='...'
export IMMICH_RESTIC_PASSWORD='...'
export IMMICH_RESTIC_REPOSITORY='sftp:neo@boxb-bak.example.ts.net:/srv/immich-restic/boxa'
```

The ops server also needs a root-owned auth-key wrapper for the private Immich
identity. The target is OAuth-backed one-off key minting from
`/etc/klokast/tailscale-policy.env`; legacy reusable auth-key files under
`/etc/tailscale-auth/` are transitional only.

Apply platform resources first. The standard repo ships only a disabled example
at `ops/platform-resources.example.yml`; real enabled placement should live in
deployment-specific private state.

```sh
ansible/bin/platform-resources \
  --registry path/to/platform-resources.yml \
  --approved-commit "$(git rev-parse HEAD)" \
  apply
```

Run preflight and install:

```sh
apps/immich/bin/immichctl preflight \
  --active-master boxa \
  --passive-backup boxb

apps/immich/bin/immichctl install \
  --active-master boxa \
  --passive-backup boxb \
  --resources-registry path/to/platform-resources.yml
```

The first admin user is created manually through the Immich web UI at
`https://photos.<tailnet>`.

## Verify

```sh
apps/immich/bin/immichctl verify \
  --active-master boxa \
  --passive-backup boxb \
  --resources-registry path/to/platform-resources.yml

apps/immich/bin/immichctl backup-check \
  --active-master boxa \
  --passive-backup boxb
```

The hourly backup hook briefly stops only `immich-server` while it writes a
PostgreSQL dump into the backup volume and snapshots the library/model-cache
volumes with restic. PostgreSQL, Valkey, and machine-learning stay up.

By default the active backend writes restic data over SFTP to the passive
backend at `/srv/immich-restic/<active-box>`. This SFTP target is transitional
host-service debt; app runtime state itself lives in Podman volumes.

## Promote

Promotion is manual to avoid split-brain.

```sh
apps/immich/bin/immichctl promote \
  --old-active boxa \
  --new-active boxb \
  --resources-registry path/to/platform-resources.yml
```

Before promotion, confirm the old active site is down or explicitly fenced and
restore the latest backup on the passive site.

## Remove

Remove services while preserving persistent data:

```sh
apps/immich/bin/immichctl remove --box boxa
```

Delete persistent data only with the explicit wipe flag:

```sh
apps/immich/bin/immichctl remove --box boxa --wipe-data
```

## Destroy

When Immich data is disposable and the next deployment should start clean, use
the controller-side destroy workflow. It removes both active and passive
runtime state, deletes the private ingress identity, disables Immich in the
private platform registry, applies app-scoped resource cleanup, and removes
controller-local Immich grants and secrets for that active/passive pair.

Preview first:

```sh
REG=~/private/klokast/platform-resources.yml
apps/immich/bin/immichctl destroy \
  --active-master boxa \
  --passive-backup boxb \
  --resources-registry "$REG" \
  --wipe-data \
  --yes \
  --dry-run-plan
```

Then run:

```sh
apps/immich/bin/immichctl destroy \
  --active-master boxa \
  --passive-backup boxb \
  --resources-registry "$REG" \
  --wipe-data \
  --yes
```
