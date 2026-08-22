# Image Sources

Container image source selection is target-local. The app keeps canonical
upstream backend image names, human-readable tags, and authoritative manifest
digests in `images.lock.yml`, then each target VM checks reachable sources and
pulls the selected digest refs before install.

## Profiles

- `direct`: Docker Hub through `registry-1.docker.io`.
- `china-1ms`: Docker Hub mirror at `docker.1ms.run`.
- `china-daocloud`: Docker Hub mirror at `docker.m.daocloud.io`.
- `china-daocloud-prefix`: Docker Hub mirror at `m.daocloud.io/docker.io`.

Tests from a deployment with restricted international registry access showed
direct Docker Hub `/v2/` timing out. `docker.m.daocloud.io` served manifests but redirected
blob downloads to `production.cloudflare.docker.com`, which timed out. The
`china-1ms` profile worked for the locked backend image pulls.

After moving a box to a site with different network paths, rerun:

```sh
apps/nextcloud/bin/nextcloudctl preflight-images --boxes boxa,boxb
```

The direct profile should be selected if canonical Docker Hub is reachable and
serves the locked digests.

## Security Rules

- Use pinned manifest digests as the source of truth.
- Treat pinned tags as operator-facing labels only; upstream tags can move.
- Do not use `latest`.
- Treat mirrors as transport only.
- Fail closed when a registry or mirror cannot serve the locked digest, returns
  a different digest, omits the digest header, or cannot provide the blobs
  needed by `podman pull`.
- Keep secrets out of image references and build args.

## Cloudflared

The Cloudflare Tunnel connector does not use the `cloudflare/cloudflared`
container image. Some Docker Hub mirrors can serve that image's manifest but
still redirect layer/config blob downloads to `production.cloudflare.docker.com`,
which is not a reliable path from the current sites.

The DMZ role installs the pinned Alpine `cloudflared` package and runs it as
the `nextcloud-cloudflared` OpenRC service under `neo`.
