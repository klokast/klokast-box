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
  --box boxa \
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
  --box boxa \
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
  --controller boxb-ops \
  --box boxa \
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

Run one wrapper command for each high-authority action from the trusted
MacBook. First verify and register the human-created repository. Then register
the controller read-only key:

```sh
klokast-dev/bin/run-private-instance-action register-repository
klokast-dev/bin/run-private-instance-action register-read-key
```

The wrapper gets one 10-minute intent from the controller and displays it. The
human checks the exact action, repository, current controller commit, approved
engine commit, key fingerprint when present, and expiry before approval. The
wrapper asks before it calls the dedicated Apple CryptoTokenKit identity.
Touch ID protects the non-exportable Secure Enclave key. The wrapper transfers
only the intent and signature and runs only the approved action. It never
copies the local approval profile to the controller or an airunner.

The approved engine and the current public controller checkout are separate
pins. The controller checkout can be newer. It must be clean, and the approved
engine must be in its Git history. Before an approval or seed operation, the
MacBook wrappers also verify the exact sealed build directory, builder receipt,
binary hash, and binary version with `platform-instance verify-engine`.
The closed approval intent schema v2 uses `repo_head` for the current
controller commit and `engine_commit` for the approved sealed engine. This
intent version is separate from Instance Specification v1.

`register-read-key` keeps the GitHub key only when GitHub confirms
`read_only: true` and an authenticated Git query proves that the repository has
no refs.

Use the MacBook helper to run the guided controller-private values setup,
generate the initial files with one verified sealed-builder output, transfer
the seed, and verify its initial Git state:

```sh
klokast-dev/bin/prepare-private-instance-worktree
```

The helper shows the detected DNS name, member login, roles, and reviewed
topology only in the trusted terminal. It does not copy those values into
arguments or journals. It does not commit, add a remote, or push. Its
underlying controller operations use these fixed `platform-instance`
interfaces. The values file and destination use fixed paths below the
controller private root:

```sh
ansible/bin/platform-instance configure-values \
  --engine-commit ENGINE-COMMIT \
  --build-dir /var/lib/klokast/builds/klokast-cli/ENGINE-COMMIT/OPERATION

ansible/bin/platform-instance seed \
  --build-dir /var/lib/klokast/builds/klokast-cli/ENGINE-COMMIT/OPERATION \
  --values /home/smith/private/klokast/init-values.json \
  --destination /home/smith/private/klokast/instance-seed

ansible/bin/platform-instance validate-candidate \
  --engine-commit ENGINE-COMMIT \
  --build-dir /var/lib/klokast/builds/klokast-cli/ENGINE-COMMIT/OPERATION \
  <klokast-instance.json
```

`validate-candidate` accepts at most one 64 KiB instance document through
standard input. It copies the owner-only unborn seed to a temporary private
directory, replaces only `klokast-instance.json`, checks it with the sealed
binary, returns the checked Git tree, and removes the temporary directory.
It does not change the seed or values file.

Review and publish the transferred worktree with the MacBook helper:

```sh
klokast-dev/bin/publish-private-instance
```

The helper commits and pushes `main` with the human private-repository
identity. It can also publish a later staged `klokast-instance.json` update
when remote `main` still equals the local base commit. The sealed seed remains
the validation base after the temporary GitHub App is retired and after the
read-only deployment checkout exists. Neither controller state grants Git
write authority. Do not commit or push from an airunner. Do not use the
temporary GitHub App to push content.

After the first push, use the GitHub web interface to remove this repository
from the temporary App installation. GitHub does not permit an installed App
to have zero selected repositories. If `klokast-instance` is the only
repository in the organization, uninstall the organization App installation
instead. Keep the App definition and credentials until the signed retirement
action succeeds. Then run:

```sh
klokast-dev/bin/run-private-instance-action retire-bootstrap
```

Retirement fails unless the App can no longer list the repository or the App
identity proves that the saved installation was uninstalled. It also requires
the dedicated App to have no other installation, the anonymous Git read to
fail, and the deploy key to read `refs/heads/main`. On success, it deletes the
temporary App PEM and IDs from the controller. The human can then delete the
dedicated GitHub App through the GitHub web interface.

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

Run the same synchronization after each human-published instance update. The
controller remains a read-only consumer of the private repository.
