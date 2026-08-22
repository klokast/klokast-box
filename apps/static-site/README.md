# Static Site

Small public static website for `https://www.klokast.ai`, served through one
Cloudflare Tunnel from the active DMZ VM.

## Publishing Model

Use the private GitHub repository `klokast/klokast-site` as the website source.
The DMZ publisher polls the repository over SSH and publishes the `www/`
subdirectory within about one minute.

Use standard static hosting directory indexes:

```text
www/index.html                         -> https://www.klokast.ai/
www/pollen/index.html                  -> https://www.klokast.ai/pollen/
www/nvidia-codesign/index.html         -> https://www.klokast.ai/nvidia-codesign/
www/nvidia-cyber/index.html            -> https://www.klokast.ai/nvidia-cyber/
www/assets/...                         -> shared assets
```

Slashless page URLs such as `/pollen` are handled by the web server's normal
directory redirect to `/pollen/`. The public web container does not mount the
Git checkout.

## Install

Enable `static-site` in the private platform-resource registry:

```yaml
apps:
  static-site:
    enabled: true
    placement:
      active_master: boxa
    resources: {}
```

Apply platform resources from the controller as `smith`.

The target flow uses the controller Secret Authority instead of exporting raw
GitHub and Cloudflare secrets into the shell. See `doc/secret-authority.md`.

To create and seed the private website repo from the currently served site, use
the Secret Authority:

```sh
ansible/bin/secret-authority intent static-site bootstrap-repo \
  --box boxa \
  --domain www.klokast.ai \
  > intent.json

ansible/bin/secret-authority static-site bootstrap-repo \
  --box boxa \
  --domain www.klokast.ai \
  --approval-intent intent.json \
  --approval-signature intent.json.sig \
  --signer-id human
```

The GitHub App authority must be able to create a private repository in the
`klokast` organization and push the initial `main` branch. `bootstrap-repo`
refuses to seed an existing non-empty repository.

Then ingest the runtime Cloudflare secret once from the MacBook. The wrapper
generates and signs the short-lived Secret Authority intent, prompts for the
token with echo disabled, sends the token only through stdin to `<box>-ops`,
and prints only redacted JSON:

```sh
klokast-dev/bin/ingest-static-site-cloudflare-token \
  --controller boxb-ops \
  --box boxa \
  --domain www.klokast.ai \
  --signer-id human \
  --key ~/.ssh/klokast-approval-sk
```

Then run install from `<box>-ops` as `smith`:

```sh
ansible/bin/secret-authority intent static-site install \
  --box boxa \
  --domain www.klokast.ai \
  --resources-registry ~/private/klokast/platform-resources.yml \
  > intent.json

ansible/bin/secret-authority static-site install \
  --box boxa \
  --domain www.klokast.ai \
  --resources-registry ~/private/klokast/platform-resources.yml \
  --approval-intent intent.json \
  --approval-signature intent.json.sig \
  --signer-id human
```

`install` generates an SSH deploy key on the DMZ VM and registers the public
key on `klokast/klokast-site` as read-only through the Secret Authority. If the
key is already registered, the minted GitHub token is not used by the app flow.

## Cloudflare

Create one Cloudflare Tunnel named `klokast-static-boxa`. Add a published
application route:

- hostname: `www.klokast.ai`
- path: leave empty
- service type: HTTP
- service URL: `http://127.0.0.1:18081`

Keep exactly one proxied CNAME for `www` pointing to the tunnel UUID target.
Do not add WAN port forwarding.

## Verify

```sh
apps/static-site/bin/static-sitectl verify \
  --box boxa \
  --domain www.klokast.ai \
  --resources-registry ~/private/klokast/platform-resources.yml

curl -I https://www.klokast.ai/pollen
curl -I https://www.klokast.ai/nvidia-codesign
curl -I https://www.klokast.ai/nvidia-cyber
curl -I https://www.klokast.ai/
```

Slashless page URLs should redirect to the trailing-slash directory URL, and
the trailing-slash URL should return `200` after the uploaded file is
available. The root path returns `200` only when `www/index.html` exists.
