# Nextcloud v2

Nextcloud v2 is the first target-local reconciler version of the app. It lives
beside the current `apps/nextcloud` implementation and does not replace it yet.

## Flow

```sh
apps/nextcloud-v2/bin/nextcloud-v2ctl build-images --builder boxb-ops

apps/nextcloud-v2/bin/nextcloud-v2ctl infra-prepare \
  --active-master boxa \
  --passive-backup boxb \
  --resources-registry ~/private/klokast/platform-resources.yml

apps/nextcloud-v2/bin/nextcloud-v2ctl install \
  --active-master boxa \
  --passive-backup boxb \
  --resource-grant /var/lib/klokast/approved-state/apps/nextcloud-v2/grant.json
```

`infra-prepare` runs `platform-resources apply --app nextcloud-v2` as
infrastructure authority and writes an app-scoped grant. App commands consume
only that grant.

## Operations

```sh
apps/nextcloud-v2/bin/nextcloud-v2ctl verify \
  --active-master boxa \
  --passive-backup boxb \
  --resource-grant /var/lib/klokast/approved-state/apps/nextcloud-v2/grant.json

apps/nextcloud-v2/bin/nextcloud-v2ctl backup-check \
  --active-master boxa \
  --passive-backup boxb \
  --resource-grant /var/lib/klokast/approved-state/apps/nextcloud-v2/grant.json

apps/nextcloud-v2/bin/nextcloud-v2ctl promote \
  --old-active boxa \
  --new-active boxb \
  --resource-grant /var/lib/klokast/approved-state/apps/nextcloud-v2/grant.json

apps/nextcloud-v2/bin/nextcloud-v2ctl remove \
  --box boxa \
  --resource-grant /var/lib/klokast/approved-state/apps/nextcloud-v2/grant.json
```

For routine lifecycle operations, prefer the common Platform wrapper:

```sh
ansible/bin/platform-app status nextcloud-v2
ansible/bin/platform-app stop nextcloud-v2
ansible/bin/platform-app start nextcloud-v2
ansible/bin/platform-app verify nextcloud-v2
```

From the MacBook, use the equivalent `kk app ...` commands. `stop` stops the
active runtime pods while preserving named Podman volumes.

Removal preserves named Podman volumes. Use `--wipe-data` only in an explicit
destructive test or decommission path.
