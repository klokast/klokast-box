# Static Site Cloudflare token setup

First install the static-site GitHub App credential with the
[GitHub App procedure](25-github-app.md). Instance must declare the static-site
application present on the selected box. Use the configured active controller.

Get the raw token for the selected Cloudflare Tunnel. Keep the token private.
Do not paste a token or a private key into chat.

From the operator MacBook, run:

```sh
klokast-dev/bin/ingest-static-site-cloudflare-token \
  --controller boxb-ops \
  --box boxa \
  --domain www.klokast.ai
```

The helper prompts with echo disabled and sends the token through standard
input to the installed controller credential broker. Paste the raw token,
not a full connector installation command. The broker checks Instance placement
before it writes the root-only token file.

Verify redacted broker status as `smith` on the active controller:

```sh
ansible/bin/secret-authority static-site status --redacted
```

`cloudflare_token_configured` must be `true`. Continue with the owning
[application install procedure](../../apps/static-site/README.md#install).
See [Credential broker](../../doc/architecture.md#credential-broker) for authority.
