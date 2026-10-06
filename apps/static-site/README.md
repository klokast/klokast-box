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

Declare the application present in the private Instance. This fragment shows
its `apps` entry; keep the other required Instance fields:

```json
{
  "apps": {
    "static-site": {
      "desired-state": "present",
      "placement": {
        "mode": "single-box",
        "box": "boxa"
      }
    }
  }
}
```

Validate and publish desired-state changes through the
[Instance workflow](../../doc/klokast-instance-specification.md). The controller
tools consume the validated resource view derived from Instance.

The selected box must declare `edge-tunnel-ingress` in its Instance
`connectivity`. Static Site always requires the
Cloudflare Tunnel resource.

Apply platform resources from the controller as `smith`.

The target flow uses the controller credential broker for GitHub and Cloudflare
operations. See [Credential broker](../../doc/architecture.md#credential-broker)
for the trust boundary and
[Development controller operations](../../doc/platform-syscalls.md) for current
interfaces.

To create and seed the private website repo from the currently served site, use
the Secret Authority:

```sh
ansible/bin/secret-authority static-site bootstrap-repo \
  --box boxa \
  --domain www.klokast.ai
```

The GitHub App authority must be able to create a private repository in the
`klokast` organization and push the initial `main` branch. `bootstrap-repo`
refuses to seed an existing non-empty repository.

Then ingest the runtime Cloudflare secret from the MacBook. The wrapper
prompts for the token with echo disabled and sends it through stdin to the
active controller broker:

```sh
klokast-dev/bin/ingest-static-site-cloudflare-token \
  --controller boxb-ops \
  --box boxa \
  --domain www.klokast.ai
```

Then run install from `<box>-ops` as `smith`:

```sh
ansible/bin/secret-authority static-site install \
  --box boxa \
  --domain www.klokast.ai \
  --resources-registry ~/private/klokast/platform-resources.yml
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

## VM template compatibility

The Platform can test this app's unchanged pinned web image on synthetic data
inside a disposable networkless VM. See the
[VM update component test](../../doc/platform-updates.md#isolated-application-component-test).
This test does not run the publisher or Cloudflare Tunnel and does not qualify
a production VM for replacement.
