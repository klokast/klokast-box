# Nextcloud v2 App Instructions

`apps/nextcloud-v2` is the target-local reconciler implementation for
Nextcloud. Do not modify `apps/nextcloud` when working on v2.

## Architecture

- Controller work is thin: apply Platform resources, export a sanitized grant,
  copy desired JSON bundles to targets, and invoke `klokast-node`.
- Runtime work is target-local: `klokast-node` validates desired JSON, renders
  Podman kube YAML, calls handlers from
  `/usr/lib/klokast/apps/nextcloud-v2/`, and writes status JSON.
- Active/passive placement uses `<box>-bak` for backend containers and
  `<box>-dmz` for private ingress/proxy services.
- Backend and ingress state live in named Podman volumes. `/srv/nextcloud-v2/*`
  is legacy migration input only.
- Data is preserved by default during remove. `--wipe-data` deletes named
  volumes only.

## Trust Boundary

Network and Tailnet intent is declared in `platform-resources.yml`. Only
`smith` may apply those resources and export the grant. App operations
consume the grant only and must not read private registries, OAuth material, SSH
keys, or unrelated app state.

## Images

Build image archives on the active `<box>-ops` controller as `smith`:

```sh
apps/nextcloud-v2/bin/nextcloud-v2ctl build-images --builder k002-ops
```

OCI archives are operational artifacts under `.run/nextcloud-v2/oci/` and must
not be committed. Commit only `images.lock.yml` after a successful builder run.

## Controller Entry Point

Use `apps/nextcloud-v2/bin/nextcloud-v2ctl`. Pass box names, not VM hostnames.
The production app path is grant-based:

```sh
apps/nextcloud-v2/bin/nextcloud-v2ctl infra-prepare \
  --active-master k001 \
  --passive-backup k002 \
  --resources-registry ~/private/klokast/platform-resources.yml

apps/nextcloud-v2/bin/nextcloud-v2ctl install \
  --active-master k001 \
  --passive-backup k002 \
  --resource-grant /var/lib/klokast/approved-state/apps/nextcloud-v2/grant.json
```
