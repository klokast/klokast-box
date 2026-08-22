# Static Site App Instructions

This app serves one public static website at `https://www.klokast.ai`.
Keep all app-specific automation under `apps/static-site/`.

## Architecture

- Placement: one active box selected by private desired state.
- Runtime host: `<box>-dmz`.
- Public ingress: Cloudflare Tunnel from the DMZ VM.
- Source path: the private GitHub repository `klokast/klokast-site` stores a
  standard static website tree under `www/`; the DMZ publisher fetches `main`
  over SSH and publishes `www/` to the static web root.

Do not mount or expose the Git checkout into the public web container. The
public container only mounts the atomically published static web root.

## Required Runtime

DMZ VM, rootless Podman under `neo`:

- `static-site-web`: pinned `static-web-server:2-alpine` image, bound only to
  `127.0.0.1:18081`.

DMZ VM OpenRC services:

- `static-site-web`: starts or refreshes the rootless Podman container.
- `static-site-publisher`: unprivileged polling publisher for the Git `www/`
  source tree, using a read-only GitHub deploy key generated on the DMZ VM.
- `static-site-cloudflared`: outbound-only Cloudflare Tunnel connector using
  the pinned Alpine `cloudflared` package.

Network access is declared in `apps/static-site/platform-resources.yml` and
applied by the platform-owned `ansible/bin/platform-resources` workflow. App
roles must not mutate router, Podman VM firewall, Tailnet policy, or dom0
state directly.

## Automation Entry Point

Use `apps/static-site/bin/static-sitectl` from the controller. Pass box names,
not VM hostnames:

```sh
apps/static-site/bin/static-sitectl install \
  --box boxa \
  --domain www.klokast.ai \
  --resources-registry path/to/platform-resources.yml
```

Required install environment:

- `STATIC_SITE_CLOUDFLARED_TOKEN`, only when bypassing the Secret Authority for
  debugging.

`GITHUB_TOKEN` is also needed when bootstrapping `klokast/klokast-site` or
registering the DMZ deploy public key if bypassing the Secret Authority. Normal
operator flow uses `ansible/bin/secret-authority`, which keeps the GitHub App
root credential and Cloudflare tunnel token root-only on `<box>-ops`.
