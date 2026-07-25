# Secret Authority

Klokast's target secret model is an action broker, not a chat-visible vault.
The AI/operator asks for a bounded action; deterministic controller code verifies
that request, uses root-held credentials internally, and runs the approved
workflow without printing raw secret values.

## Static-Site Pilot

The v0 pilot is `apps/static-site`.

- Durable GitHub authority is a GitHub App private key on the active
  `<box>-ops` controller under `/etc/klokast/secret-authority/`.
- `smith` may call only the checked root wrapper
  `/usr/local/sbin/ksa-static-site` through sudo.
- High-authority actions require a signed canonical intent verified with
  `ssh-keygen -Y verify` and the root-owned `allowed-signers` file.
- Cloudflare tunnel tokens are ingested once from stdin and stored root-only as
  app state. The token is released only into the child install process.
- GitHub installation tokens are minted only inside the wrapper and are passed
  only to the child `static-sitectl` process environment.

Root-side GitHub App configuration:

```sh
sudo install -d -m 0700 -o root -g root /etc/klokast/secret-authority
sudo install -m 0600 -o root -g root github-app.pem \
  /etc/klokast/secret-authority/github-app.pem
sudo tee /etc/klokast/secret-authority/github-app.env >/dev/null <<'EOF'
GITHUB_APP_ID=123456
GITHUB_APP_INSTALLATION_ID=12345678
EOF
sudo chmod 0600 /etc/klokast/secret-authority/github-app.env
```

The GitHub App must be installed on the `klokast` organization with only the
permissions needed by the static-site pilot: repository administration write for
repo creation/deploy keys and contents write for the initial `bootstrap-repo`
push.

Approval signers use OpenSSH allowed-signers format:

```text
human sk-ssh-ed25519@openssh.com AAAA...
```

Install the file as:

```sh
sudo install -m 0600 -o root -g root allowed-signers \
  /etc/klokast/secret-authority/allowed-signers
```

Generate an approval intent from the controller checkout:

```sh
ansible/bin/secret-authority intent static-site install \
  --box k001 \
  --domain www.klokast.ai \
  --resources-registry ~/private/klokast/platform-resources.yml \
  > intent.json
```

The human signs `intent.json` from a trusted client with a YubiKey-backed
SSH/FIDO signing key:

```sh
ssh-keygen -Y sign -f ~/.ssh/klokast-approval-sk \
  -n klokast-secret-authority intent.json
```

Run the approved action from `<box>-ops` as `smith`:

```sh
ansible/bin/secret-authority static-site install \
  --box k001 \
  --domain www.klokast.ai \
  --resources-registry ~/private/klokast/platform-resources.yml \
  --approval-intent intent.json \
  --approval-signature intent.json.sig \
  --signer-id human
```

To ingest the static-site tunnel token, use the MacBook-side wrapper. It
generates the controller intent, displays the approval review, signs with the
YubiKey-backed OpenSSH key, prompts for the token with echo disabled, sends the
token only through stdin, and verifies redacted status:

```sh
klokast-dev/bin/ingest-static-site-cloudflare-token \
  --controller k002-ops \
  --box k001 \
  --domain www.klokast.ai \
  --signer-id human \
  --key ~/.ssh/klokast-approval-sk
```

Redacted status is safe to inspect:

```sh
ansible/bin/secret-authority static-site status --redacted
```

Do not store root provider credentials or app tunnel tokens on `vultr-ops`.
That host is an authoritative AI runner, not the Platform credential custodian.

## Cloudflare Guardian Target

The Cloudflare target is stronger than the current manual static-site token
ingestion path. The active controller remains `k002-ops`, while the hardware
guardian is `k001-dom0` because the YubiKey is physically visible there.

The long-lived Cloudflare authority token must not be stored plaintext on
`k002-ops`, app VMs, `vultr-ops`, in chat, or in Git. It is installed as an
encrypted blob under `/etc/klokast/cloudflare-guardian/` on `k001-dom0`. A
root-owned external unseal command, also on `k001-dom0`, must perform the
YubiKey-backed decrypt/unseal operation. The repo deliberately does not
implement custom cryptography.

The controller dispatcher is:

```sh
ansible/bin/secret-authority intent cloudflare static-site-tunnel \
  --guardian k001-dom0 \
  --box k001 \
  --domain www.klokast.ai \
  --cloudflare-account-id ACCOUNT \
  --cloudflare-zone-id ZONE \
  > intent.json

ansible/bin/secret-authority cloudflare static-site-tunnel \
  --guardian k001-dom0 \
  --box k001 \
  --domain www.klokast.ai \
  --cloudflare-account-id ACCOUNT \
  --cloudflare-zone-id ZONE \
  --approval-intent intent.json \
  --approval-signature intent.json.sig \
  --signer-id xiaoju-og
```

The dom0 guardian verifies the same human signature and its own replay log
before unsealing. It reconciles only the policy-pinned static-site Cloudflare
tunnel and DNS record, then returns the tunnel connector token to the
controller Secret Authority for static-site install use.

Converge the guardian policy and executable from the controller:

```sh
ansible/bin/converge-cloudflare-guardian \
  --box k001 \
  --cloudflare-account-id ACCOUNT \
  --cloudflare-zone-id ZONE
```

Install encrypted authority material from the MacBook:

```sh
klokast-dev/bin/install-cloudflare-authority \
  --guardian k001-dom0 \
  --ciphertext cloudflare-authority.enc \
  --policy policy.json \
  --allowed-signers allowed-signers \
  --unseal-command unseal-cloudflare-authority
```

`install-cloudflare-authority` refuses plaintext tokens. The unseal command is
deployment-private and must require the physical YubiKey touch/PIN.
