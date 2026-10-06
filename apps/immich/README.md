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

Declare Immich present and select its active/passive placement in Instance.
Run the manual application workflow as `smith` on the active development
controller. Automatic installation from Instance is not implemented; see
[Platform lifecycle](../../doc/platform-lifecycle.md#application-installation).

Set required secrets in the controller environment:

```sh
export IMMICH_POSTGRES_PASSWORD='...'
export IMMICH_RESTIC_PASSWORD='...'
export IMMICH_RESTIC_REPOSITORY='sftp:neo@boxb-bak.example.ts.net:/srv/immich-restic/boxa'
```

The ops server also needs a root-owned auth-key wrapper for the private Immich
identity. It uses OAuth-backed one-use key minting from
`/etc/klokast/tailscale-policy.env`.

Apply Platform resources from the validated Instance first. Declare the
application present with its active/passive placement in Instance; see
[Instance desired state](../../doc/klokast-instance-specification.md).

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
