# Static Site Secret Authority Setup

Use this after the static-site GitHub App has been installed from
`klokast-dev/runbooks/25-github-app.md`.

The current static-site placement is `k001`, and the active controller is
`k002-ops`.

Do not paste Cloudflare tokens, GitHub keys, approval private keys, or signed
intents into chat.

# YubiKey Timing

The approval-signer wrapper prompts for the YubiKey and waits for USB detection
before local hardware-backed operations. Insert the YubiKey when prompted, or
leave it inserted before starting.

The YubiKey is needed at these moments:

1. Approval key generation, when the wrapper creates `~/.ssh/klokast-approval-sk`.
2. Approval signer self-test, when the wrapper signs a test message.
3. Every future high-authority intent signing, including Cloudflare token
   ingestion, static-site install, and bootstrap-repo approvals.

If the command appears to pause, check for a PIN prompt or touch request. The
wrapper labels these as `Enter the FIDO2 PIN of your Yubikey:` and
`Touch the Yubikey`.

# FIDO2 PIN And macOS OpenSSH

No Klokast-specific setup is required in the Yubico Authenticator app. The
approval key is created by OpenSSH. The local
`~/.ssh/klokast-approval-sk` file is a key handle, and the non-exportable key
material remains tied to the YubiKey.

The wrapper uses `-O verify-required`, so the YubiKey must have a FIDO2 PIN. If
the key is new, use Yubico Authenticator or YubiKey Manager on the MacBook to
set the FIDO2 PIN before running the wrapper.

On macOS, the system `ssh-keygen` may fail with:

```text
No FIDO SecurityKeyProvider specified
Key enrollment failed: invalid format
```

Install Homebrew OpenSSH and retry:

```sh
klokast-dev/bin/kk doctor --install

klokast-dev/bin/install-secret-authority-approval-signer \
  --controller k002-ops \
  --signer-id xiaoju-og \
  --key "$HOME/.ssh/klokast-approval-sk" \
  --ssh-keygen /opt/homebrew/bin/ssh-keygen
```

`kk doctor --install` also repairs the macOS `UseKeychain` SSH config
compatibility issue by adding `IgnoreUnknown UseKeychain` with a timestamped
backup when needed.

The wrapper prefers `/opt/homebrew/bin/ssh-keygen` or
`/usr/local/bin/ssh-keygen` automatically when present. If your OpenSSH build
requires an external FIDO provider, pass the exact OpenSSH
`sk-libfido2.dylib` provider path explicitly. Do not point this at Homebrew's
plain `libfido2.dylib`; it is not the OpenSSH provider.

```sh
klokast-dev/bin/install-secret-authority-approval-signer \
  --controller k002-ops \
  --signer-id xiaoju-og \
  --key "$HOME/.ssh/klokast-approval-sk" \
  --ssh-keygen /opt/homebrew/bin/ssh-keygen \
  --sk-provider /path/to/sk-libfido2.dylib
```

If an earlier failed enrollment left partial files, the wrapper will refuse to
reuse them. Move them aside and rerun:

```sh
mv "$HOME/.ssh/klokast-approval-sk" "$HOME/.ssh/klokast-approval-sk.failed"
mv "$HOME/.ssh/klokast-approval-sk.pub" "$HOME/.ssh/klokast-approval-sk.pub.failed"
```

# 1. Install Approval Signer

Run from the MacBook:

```sh
cd path/to/klokast-box
git pull --ff-only

klokast-dev/bin/install-secret-authority-approval-signer \
  --controller k002-ops \
  --signer-id xiaoju-og \
  --key "$HOME/.ssh/klokast-approval-sk"
```

The wrapper:

- creates a YubiKey/FIDO-backed SSH signing key if it does not exist;
- installs the public key into
  `/etc/klokast/secret-authority/allowed-signers` on `k002-ops`, restricted to
  the OpenSSH signature namespace `klokast-secret-authority`;
- preserves other existing signer lines;
- runs a real signature verification round trip through the controller;
- retries the local approval signature self-test if the FIDO2 PIN is typed
  incorrectly.

If `ed25519-sk` is not supported by the MacBook/YubiKey/OpenSSH stack, retry
with:

```sh
klokast-dev/bin/install-secret-authority-approval-signer \
  --controller k002-ops \
  --signer-id xiaoju-og \
  --key "$HOME/.ssh/klokast-approval-sk" \
  --key-type ecdsa-sk
```

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

Run from the MacBook with the YubiKey inserted:

```sh
klokast-dev/bin/ingest-static-site-cloudflare-token \
  --controller k002-ops \
  --box k001 \
  --domain www.klokast.ai \
  --signer-id xiaoju-og \
  --key "$HOME/.ssh/klokast-approval-sk"
```

The wrapper:

- generates a short-lived `ingest-cloudflare-token` intent on the controller;
- shows a human-readable approval review before signing;
- requires typing an exact short approval phrase;
- signs the intent with OpenSSH `ssh-keygen -Y sign` and the YubiKey-backed
  approval key;
- prompts for the Cloudflare tunnel token with echo disabled;
- sends the token only through stdin to the controller Secret Authority;
- verifies redacted status and prints a final JSON result.

Paste only the `eyJ...` token at the hidden prompt, not the full `cloudflared`
command.

The review card protects against confusion and accidental approval. It does not
protect against a compromised MacBook lying about the intent it asks you to
sign; the YubiKey signs bytes, not the display text.

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

- `No FIDO SecurityKeyProvider specified`: use Homebrew OpenSSH or pass
  `--sk-provider` as shown above.
- `approval key could not be read`: an earlier failed enrollment likely left a
  partial key handle; move the key files aside and rerun.
- `approval intent is expired`: rerun the ingestion wrapper so it generates a
  fresh intent and signature.
- `approval intent nonce was already used`: rerun the ingestion wrapper so it
  generates a fresh intent and signature.
- `approval phrase mismatch`: rerun the wrapper and type the exact approval
  phrase shown in the prompt.
- `Cloudflare tunnel token is not raw base64 JSON`: paste only the token string,
  not the full `cloudflared` command.
- `No FIDO authenticator found`: insert the YubiKey, unlock it if needed, and
  rerun the command.
