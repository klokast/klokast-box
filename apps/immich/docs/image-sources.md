# Image Sources

Container image source selection is target-local. The app keeps canonical
upstream image names, human-readable tags, and authoritative manifest digests
in `images.lock.yml`, then each target VM verifies and pulls selected digest
refs before install.

## Profiles

- `direct`: GHCR direct and Docker Hub direct.
- `china-1ms`: GHCR direct and Docker Hub mirror at `docker.1ms.run`.
- `china-daocloud`: GHCR direct and Docker Hub mirror at
  `docker.m.daocloud.io`.
- `china-daocloud-prefix`: GHCR direct and Docker Hub mirror at
  `m.daocloud.io/docker.io`.

GHCR images are not mirrored in v1. If GHCR is unreachable from a target VM,
preflight fails closed.

## Security Rules

- Use pinned manifest digests as the source of truth.
- Treat pinned tags as operator-facing labels only; upstream tags can move.
- Do not use `latest`, `release`, or `v2` as deployment refs.
- Treat mirrors as transport only.
- Fail closed when a registry or mirror cannot serve the locked digest, returns
  a different digest, omits the digest header, or cannot provide the blobs
  needed by `podman pull`.
