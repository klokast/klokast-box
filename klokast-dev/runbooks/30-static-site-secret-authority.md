# Static Site Secret Authority Setup

Use this after the static-site GitHub App has been installed from
`klokast-dev/runbooks/25-github-app.md`.

The current static-site placement is `k001`, and the active controller is
`k002-ops`.

Do not paste Cloudflare tokens, GitHub keys, approval private keys, or signed
intents into chat.

# Touch ID Approval

Static-site actions use the dedicated `Klokast static-site approval` identity
in the Mac Secure Enclave. Read and complete
`klokast-dev/runbooks/15-touchid-secret-authority.md` first.

The static-site identity is separate from the private-instance identity. The
controller rejects the private-instance signer for static-site actions.

# 1. Install Approval Signer

Run from the MacBook:

```sh
cd path/to/klokast-box
git pull --ff-only

klokast-dev/bin/install-secret-authority-approval-signer \
  --controller k002-ops \
  --purpose static-site
```

The wrapper:

- creates or reuses one non-exportable, biometric-protected CryptoTokenKit
  identity;
- installs its public key into
  `/etc/klokast/secret-authority/allowed-signers-static-site` on `k002-ops`;
- runs a real signature verification round trip through the controller;
- requires Touch ID for the test signature.

Verify status:

```sh
tailscale ssh smith@k002-ops \
  'cd ~/src/klokast/klokast-box && ansible/bin/secret-authority static-site status --redacted'
```

Expected:

```text
allowed_signers_configured=true
```

# 2. Get Cloudflare Tunnel Token

In Cloudflare Zero Trust:

1. Go to `Networking` -> `Tunnels`.
2. Select the static-site tunnel, normally `klokast-static-k001`.
3. Select `Add a replica`.
4. Copy the `cloudflared` install command into a local text editor. Do not run
   the command.
5. Extract only the token string, normally the `eyJ...` value.

Cloudflare documents that anyone with this token can run the tunnel connector,
so handle it as a secret:

`https://developers.cloudflare.com/tunnel/advanced/tunnel-tokens/`

# 3. Review, Sign, And Ingest Cloudflare Token

Run from the MacBook:

```sh
klokast-dev/bin/ingest-static-site-cloudflare-token \
  --controller k002-ops \
  --box k001 \
  --domain www.klokast.ai
```

The wrapper:

- generates a short-lived `ingest-cloudflare-token` intent on the controller;
- shows a human-readable approval review before signing;
- requires typing an exact short approval phrase;
- signs the intent with the static-site Secure Enclave key after Touch ID;
- prompts for the Cloudflare tunnel token with echo disabled;
- sends the token only through stdin to the controller Secret Authority;
- verifies redacted status and prints a final JSON result.

Paste only the `eyJ...` token at the hidden prompt, not the full `cloudflared`
command.

The review card protects against confusion and accidental approval. It does not
protect against a compromised MacBook that lies about the intent it asks you to
sign. The Secure Enclave signs bytes, not the display text.

Verify:

```sh
tailscale ssh smith@k002-ops \
  'cd ~/src/klokast/klokast-box && ansible/bin/secret-authority static-site status --redacted'
```

Expected:

```text
allowed_signers_configured=true
cloudflare_token_configured=true
```

# Troubleshooting

- `Touch ID approval profile is missing`: complete
  `klokast-dev/runbooks/15-touchid-secret-authority.md`.
- `identity exists without Klokast profile metadata`: stop. Confirm the exact
  identity hash before you delete or replace anything.
- `approval intent is expired`: rerun the ingestion wrapper so it generates a
  fresh intent and signature.
- `approval intent nonce was already used`: rerun the ingestion wrapper so it
  generates a fresh intent and signature.
- `approval phrase mismatch`: rerun the wrapper and type the exact approval
  phrase shown in the prompt.
- `Cloudflare tunnel token is not raw base64 JSON`: paste only the token string,
  not the full `cloudflared` command.
- Touch ID does not appear: confirm that Touch ID is configured for the current
  Mac user and rerun the signer self-test.
