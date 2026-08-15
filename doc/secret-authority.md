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
  `ssh-keygen -Y verify` and the root-owned, scope-specific signer file.
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

The MacBook uses two separate Apple-native Secure Enclave identities. The
static-site signer file contains only:

```text
human-static-site namespaces="klokast-secret-authority" sk-ecdsa-sha2-nistp256@openssh.com AAAA...
```

The private-instance signer is stored separately as `human-private-instance`.
Use `klokast-dev/runbooks/15-touchid-secret-authority.md` to create and install
both signers. Do not write either signer file by hand.

The controller paths are:

```text
/etc/klokast/secret-authority/allowed-signers-static-site
/etc/klokast/secret-authority/allowed-signers-private-instance
```

Generate an approval intent from the controller checkout:

```sh
ansible/bin/secret-authority intent static-site install \
  --box k001 \
  --domain www.klokast.ai \
  --resources-registry ~/private/klokast/platform-resources.yml \
  > intent.json
```

The human signs `intent.json` on the trusted MacBook with the static-site
Secure Enclave key:

```sh
klokast-dev/bin/sign-secret-authority-intent \
  --purpose static-site \
  --intent intent.json
```

Run the approved action from `<box>-ops` as `smith`:

```sh
ansible/bin/secret-authority static-site install \
  --box k001 \
  --domain www.klokast.ai \
  --resources-registry ~/private/klokast/platform-resources.yml \
  --approval-intent intent.json \
  --approval-signature intent.json.sig \
  --signer-id human-static-site
```

To ingest the static-site tunnel token, use the MacBook-side wrapper. It
generates the controller intent, displays the approval review, signs with the
static-site Secure Enclave key after Touch ID, prompts for the token with echo
disabled, sends the token only through stdin, and verifies redacted status:

```sh
klokast-dev/bin/ingest-static-site-cloudflare-token \
  --controller k002-ops \
  --box k001 \
  --domain www.klokast.ai
```

Redacted status is safe to inspect:

```sh
ansible/bin/secret-authority static-site status --redacted
```

Do not store root provider credentials or app tunnel tokens on `vultr-ops`.
That host is an authoritative AI runner, not the Platform credential custodian.

## Private Instance Bootstrap

Private-instance bootstrap uses a separate, temporary GitHub App. Do not reuse
the static-site App. Configure the temporary App with these repository
permissions only:

- Administration: read and write;
- Metadata: read;
- Contents: no access.

The human first creates the exact empty private organization repository
`FAMILY/klokast-instance` in the GitHub web interface. Do not add a README,
license, `.gitignore`, template, branch, or commit. Install the App on only
that repository. Never grant it access to all organization repositories or an
unrelated repository. From the trusted workstation, install its credential on
the active controller:

```sh
klokast-dev/bin/install-instance-github-app \
  --pem /path/to/private-key.pem \
  --app-id 123456 \
  --installation-id 12345678 \
  --controller auto
```

The installer sends the PEM through standard input. It stores the credential
under `/etc/klokast/secret-authority/instance-bootstrap/`. It does not put a
token or PEM on the command line.

Prepare the controller read key as `smith` on the active controller:

```sh
ansible/bin/platform-instance prepare \
  --repo-owner FAMILY \
  --repo-name klokast-instance
```

The command prints a redacted repository hash and the public-key fingerprint.
It keeps the private key root-only under `/etc/klokast/private-instance/`.

Create and sign one intent for each high-authority action. This example
verifies and registers the exact empty private repository that the human
created:

```sh
ansible/bin/secret-authority intent instance register-repository \
  --repo-owner FAMILY \
  --repo-name klokast-instance \
  > register-repository.intent.json

klokast-dev/bin/sign-secret-authority-intent \
  --purpose private-instance \
  --intent register-repository.intent.json

ansible/bin/secret-authority instance register-repository \
  --repo-owner FAMILY \
  --repo-name klokast-instance \
  --approval-intent register-repository.intent.json \
  --approval-signature register-repository.intent.json.sig \
  --signer-id human-private-instance
```

For private-instance actions, use the dedicated Apple CryptoTokenKit identity
with a non-exportable Secure Enclave key and biometric protection. A passkey
cannot sign an OpenSSH intent file. Sign on the trusted workstation. Move the
intent and signature through the approved controller terminal path. Do not
copy the local approval profile to the controller or an airunner. The signing
helper uses a private Apple agent only for the duration of one operation.

Use `register-read-key` with the fingerprint returned by `prepare`. The action
registers a GitHub deploy key only when GitHub confirms `read_only: true`:

```sh
ansible/bin/secret-authority intent instance register-read-key \
  --repo-owner FAMILY \
  --repo-name klokast-instance \
  --key-fingerprint SHA256:FINGERPRINT \
  > register-read-key.intent.json

ansible/bin/secret-authority instance register-read-key \
  --repo-owner FAMILY \
  --repo-name klokast-instance \
  --key-fingerprint SHA256:FINGERPRINT \
  --approval-intent register-read-key.intent.json \
  --approval-signature register-read-key.intent.json.sig \
  --signer-id human-private-instance
```

Generate the initial files on the controller with one verified sealed-builder
output. The values file and destination must be below the controller private
root. The command makes a staged Git repository but does not commit or push:

```sh
ansible/bin/platform-instance seed \
  --build-dir /var/lib/klokast/builds/klokast-cli/ENGINE-COMMIT/OPERATION \
  --values /home/smith/private/klokast/init-values.json \
  --destination /home/smith/private/klokast/instance-seed
```

Transfer `instance-seed` to the trusted workstation. Review it, commit it, add
the private remote, and push `main` with the human private-repository identity.
Do not commit or push it from an airunner. Do not use the temporary GitHub App
to push content.

After the first push, use the GitHub web interface to remove this repository
from the temporary App installation. Then sign and run `retire-bootstrap` with
the same repository and key fingerprint. Retirement fails unless the App can
no longer list the repository, the anonymous Git read fails, and the deploy key
can still read `refs/heads/main`. On success, it deletes the temporary App PEM
and IDs from the controller. The human can delete the dedicated GitHub App
later through the GitHub web interface.

Synchronize the deployment checkout and create a fresh source receipt:

```sh
ansible/bin/platform-instance sync \
  --repo-owner FAMILY \
  --repo-name klokast-instance
```

The checkout is `/home/smith/private/klokast/instance`. Its push URL is
disabled. The receipt is below `/var/lib/klokast/instance-sources/` and is valid
for 30 minutes. Pass its exact path to `ansible/bin/platform-plan` with
`--instance-source-receipt`. Redacted status is:

```sh
ansible/bin/platform-instance status
```
