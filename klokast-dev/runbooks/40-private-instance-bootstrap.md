# Private Instance Bootstrap: Human Procedure

Use this procedure to create the private Contract v1 repository and give the
active controller read-only access to it.

The current active controller is `k002-ops`. The current approved engine is:

```text
commit: e87c9b0e0641c4318e8e501c37f3a1fa4ab865e6
build:  /var/lib/klokast/builds/klokast-cli/e87c9b0e0641c4318e8e501c37f3a1fa4ab865e6/2658c86fa5ec
```

Do not paste the GitHub App PEM, private deployment values, signed intents,
approval signatures, or private repository contents into chat. Do not run
this procedure from an airunner. Run the human steps from the trusted MacBook.

The temporary GitHub App has no Contents permission. It creates the empty
repository and registers one read-only deploy key. The human, not the App or
an airunner, authors and pushes the first private commit.

Complete `klokast-dev/runbooks/15-touchid-secret-authority.md` before Step 1.
That migration installs the separate private-instance and static-site signers
and deploys their scope enforcement to the active controller.

## 1. Prepare the MacBook

Use the interactive helper from the `klokast-box` checkout on the MacBook:

```sh
cd /path/to/klokast-box
klokast-dev/bin/prepare-private-instance-bootstrap
```

The helper first lists the information, access, and hardware that you must
prepare. It explains each action before it makes a change. It then:

- collects the non-secret bootstrap settings;
- checks and updates the public checkout;
- checks the MacBook tools and active controller;
- creates or reuses the private-instance Secure Enclave approval key and asks
  before it installs the public signer on the controller;
- saves an owner-only session file for the remaining steps.

The helper does not ask for a GitHub App PEM or private deployment values.

When it completes, load the settings into the same terminal:

```sh
source "$HOME/.local/share/klokast/private-instance-bootstrap/session.sh"
```

`INSTANCE_OWNER` is an existing GitHub organization that the human controls.
Keep the repository name as `klokast` unless the architecture changes.

The approval key is an Apple-native CryptoTokenKit identity protected by Touch
ID. The private key is not exportable. The local profile contains its public
key. A private, short-lived Apple `ssh-agent` obtains the matching Secure
Enclave identity only while a signature is made. An Apple passkey cannot
replace it because a passkey is bound to a compatible app or website and
cannot sign the bootstrap intent file. See
`klokast-dev/runbooks/15-touchid-secret-authority.md` for the shared signer
design and recovery procedure.

## 2. Create the temporary GitHub App

Open the organization settings in GitHub:

```text
Your organizations -> INSTANCE_OWNER -> Settings -> Developer settings
-> GitHub Apps -> New GitHub App
```

Use these settings:

- GitHub App name: a unique name such as
  `INSTANCE_OWNER-klokast-instance-bootstrap`;
- Homepage URL: `https://github.com/klokast/klokast-box`;
- Callback URL: blank;
- Request user authorization during installation: disabled;
- Device Flow: disabled;
- Setup URL: blank;
- Webhook Active: disabled;
- Repository Administration: **Read and write**;
- Repository Contents: **No access**;
- Repository Metadata: **Read-only**;
- all organization and account permissions: **No access**;
- subscribed events: none;
- installation scope: **Only on this account**.

Create the App. Review the permissions again before you continue. Do not add
Contents access.

## 3. Generate and protect the App private key

On the App settings page:

1. Record the integer **App ID**. Do not use the Client ID.
2. Select **Generate a private key**.
3. Confirm that GitHub downloaded one `.pem` file.

Move the PEM out of `Downloads`:

```sh
PEM_DOWNLOAD="$HOME/Downloads/REPLACE_WITH_DOWNLOADED_FILE.pem"
PEM="$HOME/.config/klokast/secrets/private-instance-bootstrap.github-app.pem"

test -r "$PEM_DOWNLOAD" || {
  echo "missing PEM: $PEM_DOWNLOAD" >&2
  exit 1
}
install -d -m 0700 "$HOME/.config/klokast/secrets"
install -m 0600 "$PEM_DOWNLOAD" "$PEM"
rm -i "$PEM_DOWNLOAD"
```

Do not paste or display the PEM.

## 4. Install the App on the private organization

On the GitHub App page (`https://github.com/organizations/<githhub-organization>/settings/apps/<family>-klokast-instance-bootstrap/installations`):

1. In the left side panel, select **Install App**.
2. Select **Install** next to `INSTANCE_OWNER`.
3. Select **Only select repositories**.
4. Leave the repository selection empty because the target repository does
   not exist yet.
5. Review the permissions and select **Install**.
   [ #BUG: GitHub does not allow an empty selected-repository installation. ]

6. Record the integer installation ID from the final browser URL.

GitHub grants an App access to repositories that the App creates. The App ID and installation ID are identifiers, not secrets. The PEM is a secret.

## 5. Install the App credential on the controller

Set the two GitHub identifiers, then use the checked-in installer:

```sh
GITHUB_APP_ID="REPLACE_WITH_APP_ID"
GITHUB_APP_INSTALLATION_ID="REPLACE_WITH_INSTALLATION_ID"

klokast-dev/bin/install-instance-github-app \
  --controller "$CONTROLLER" \
  --pem "$PEM" \
  --app-id "$GITHUB_APP_ID" \
  --installation-id "$GITHUB_APP_INSTALLATION_ID"
```

Expected output includes:

```text
bounded instance bootstrap token mint ok
```

The redacted JSON status must contain:

```json
"github_app_configured": true
```

Clear the identifier variables:

```sh
unset GITHUB_APP_ID GITHUB_APP_INSTALLATION_ID
```

### Stop point 1

Tell the agent:

```text
The temporary private-instance GitHub App is installed.
The private organization is INSTANCE_OWNER.
The repository name is klokast.
```

Do not send the PEM. The agent can now prepare the controller read key and
check the redacted status.

## 6. Prepare the controller read key

The human can run this step directly, or ask the agent to run it:

```sh
PREPARE_RESULT="$BOOTSTRAP_WORK/prepare.json"

tailscale ssh "$SSH_TARGET" sh -s -- \
  "$INSTANCE_OWNER" "$INSTANCE_REPO" >"$PREPARE_RESULT" <<'SH'
set -eu
owner=$1
repo=$2
cd "$HOME/src/klokast/klokast-box"
ansible/bin/platform-instance prepare \
  --repo-owner "$owner" \
  --repo-name "$repo"
SH

python3 -m json.tool "$PREPARE_RESULT"
KEY_FINGERPRINT="$(python3 - "$PREPARE_RESULT" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    value = json.load(handle)
fingerprint = value.get("key_fingerprint", "")
if not fingerprint.startswith("SHA256:"):
    raise SystemExit("controller did not return a key fingerprint")
print(fingerprint)
PY
)"
```

The result is redacted. It contains a repository hash and the public-key
fingerprint. The private deploy key stays root-only on `k002-ops`.

## 7. Run a human-signed instance action

Use this section three times, in this order:

1. `create-repository`;
2. `register-read-key`;
3. `retire-bootstrap`, after the first private commit is pushed and the
   repository is removed from the App installation.

Each intent expires after 10 minutes. Complete the review, signature, upload,
and action before it expires.

Set the action. Use `none` only for `create-repository`:

```sh
ACTION="create-repository"
ACTION_FINGERPRINT="none"
```

For the other two actions, use:

```sh
ACTION="register-read-key"  # or: retire-bootstrap
ACTION_FINGERPRINT="$KEY_FINGERPRINT"
```

Generate the canonical intent on the active controller:

```sh
case "$ACTION" in
  create-repository|register-read-key|retire-bootstrap) ;;
  *) echo "unsupported action: $ACTION" >&2; exit 1 ;;
esac

LOCAL_INTENT="$BOOTSTRAP_WORK/$ACTION.intent.json"
rm -f "$LOCAL_INTENT" "$LOCAL_INTENT.sig"

tailscale ssh "$SSH_TARGET" sh -s -- \
  "$INSTANCE_OWNER" "$INSTANCE_REPO" "$ACTION" \
  "$ACTION_FINGERPRINT" >"$LOCAL_INTENT" <<'SH'
set -eu
owner=$1
repo=$2
action=$3
fingerprint=$4
cd "$HOME/src/klokast/klokast-box"
case "$action" in
  create-repository)
    ansible/bin/secret-authority intent instance create-repository \
      --repo-owner "$owner" --repo-name "$repo" --ttl-seconds 600
    ;;
  register-read-key|retire-bootstrap)
    ansible/bin/secret-authority intent instance "$action" \
      --repo-owner "$owner" --repo-name "$repo" \
      --key-fingerprint "$fingerprint" --ttl-seconds 600
    ;;
  *) exit 2 ;;
esac
SH
```

Validate and display the intent before signing:

```sh
python3 - "$LOCAL_INTENT" "$ACTION" "$INSTANCE_OWNER" \
  "$INSTANCE_REPO" "$ENGINE_COMMIT" "$ACTION_FINGERPRINT" <<'PY'
import json
import sys

path, action, owner, repo, engine_commit, fingerprint = sys.argv[1:]
with open(path, encoding="utf-8") as handle:
    value = json.load(handle)
expected = {
    "schema_version": 1,
    "authority": "klokast-secret-authority",
    "app": "instance",
    "action": action,
    "repo_owner": owner,
    "repo_name": repo,
    "repo_head": engine_commit,
}
for key, wanted in expected.items():
    if value.get(key) != wanted:
        raise SystemExit(f"intent mismatch for {key}")
if action == "create-repository":
    if "key_fingerprint" in value:
        raise SystemExit("create intent contains an unexpected fingerprint")
elif value.get("key_fingerprint") != fingerprint:
    raise SystemExit("intent fingerprint mismatch")
print(json.dumps(value, indent=2, sort_keys=True))
PY
```

Confirm all fields on the screen. In particular, check the action, owner,
repository, key fingerprint when present, engine commit, and expiry time.

Sign with the hardware-backed key from the `klokast-box` checkout:

```sh
klokast-dev/bin/sign-secret-authority-intent \
  --purpose private-instance \
  --intent "$LOCAL_INTENT"
```

Use Touch ID when macOS asks you to approve the signature. Confirm that the
prompt is for the dedicated private-instance approval identity.

Upload only the intent and signature to the controller:

```sh
REMOTE_APPROVAL_DIR="/home/smith/private/klokast/bootstrap-approvals"
REMOTE_INTENT="$REMOTE_APPROVAL_DIR/$ACTION.intent.json"
REMOTE_SIGNATURE="$REMOTE_INTENT.sig"

tailscale ssh "$SSH_TARGET" sh -s -- "$REMOTE_APPROVAL_DIR" <<'SH'
set -eu
directory=$1
case "$directory" in
  /home/smith/private/klokast/*) ;;
  *) exit 2 ;;
esac
install -d -m 0700 "$directory"
SH

tailscale ssh "$SSH_TARGET" \
  "umask 077; cat > '$REMOTE_INTENT'" <"$LOCAL_INTENT"
tailscale ssh "$SSH_TARGET" \
  "umask 077; cat > '$REMOTE_SIGNATURE'" <"$LOCAL_INTENT.sig"
```

Run the exact signed action:

```sh
tailscale ssh "$SSH_TARGET" sh -s -- \
  "$INSTANCE_OWNER" "$INSTANCE_REPO" "$ACTION" \
  "$ACTION_FINGERPRINT" "$SIGNER_ID" \
  "$REMOTE_INTENT" "$REMOTE_SIGNATURE" <<'SH'
set -eu
owner=$1
repo=$2
action=$3
fingerprint=$4
signer=$5
intent=$6
signature=$7
cd "$HOME/src/klokast/klokast-box"
case "$action" in
  create-repository)
    ansible/bin/secret-authority instance create-repository \
      --repo-owner "$owner" --repo-name "$repo" \
      --approval-intent "$intent" --approval-signature "$signature" \
      --signer-id "$signer"
    ;;
  register-read-key|retire-bootstrap)
    ansible/bin/secret-authority instance "$action" \
      --repo-owner "$owner" --repo-name "$repo" \
      --key-fingerprint "$fingerprint" \
      --approval-intent "$intent" --approval-signature "$signature" \
      --signer-id "$signer"
    ;;
  *) exit 2 ;;
esac
rm -f "$intent" "$signature"
SH
```

Do not reuse an intent. If it expires or fails after its nonce is consumed,
generate and sign a new intent.

Run this section first with `create-repository`, then with
`register-read-key`. GitHub must show the controller deploy key as read-only.

## 8. Create the staged Contract v1 repository

Create the private init values directly on the controller. Do not enter these
values in chat:

```sh
tailscale ssh "$SSH_TARGET"
cd ~/src/klokast/klokast-box
umask 077
vi ~/private/klokast/init-values.json
```

Use this JSON shape with the real private values:

```json
{
  "schema_version": 1,
  "instance": {"name": "REPLACE_WITH_PRIVATE_INSTANCE_NAME"},
  "tailnet": {
    "magicdns_suffix": "REPLACE.ts.net",
    "groups": {
      "operators": ["REPLACE_WITH_OPERATOR_LOGIN"],
      "family": ["REPLACE_WITH_OPERATOR_LOGIN", "REPLACE_WITH_FAMILY_LOGIN"]
    }
  },
  "site": {
    "country": "REPLACE_WITH_TWO_LETTER_COUNTRY",
    "physical_location": "REPLACE_WITH_K001_LOCATION"
  },
  "box": {"hostname_prefix": "k001"}
}
```

One operator must also be in the family group. Do not add a timezone; Platform
time is always `Etc/UTC`.

Exit the controller shell, then seed with the exact sealed build:

```sh
tailscale ssh "$SSH_TARGET" sh -s -- \
  "$ENGINE_COMMIT" "$BUILD_OPERATION" <<'SH'
set -eu
commit=$1
operation=$2
cd "$HOME/src/klokast/klokast-box"
ansible/bin/platform-instance seed \
  --build-dir "/var/lib/klokast/builds/klokast-cli/$commit/$operation" \
  --values "/home/smith/private/klokast/init-values.json" \
  --destination "/home/smith/private/klokast/instance-seed"
SH
```

The destination must not already exist. If it exists, stop and ask the agent
to inspect it. Do not overwrite it.

## 9. Transfer and edit the private repository on the MacBook

Transfer the staged repository directly from the controller to the MacBook:

```sh
SEED_ARCHIVE="$BOOTSTRAP_WORK/instance-seed.tar"
PRIVATE_PARENT="$HOME/src/private-klokast"
PRIVATE_WORKTREE="$PRIVATE_PARENT/klokast"

test ! -e "$PRIVATE_WORKTREE" || {
  echo "destination already exists: $PRIVATE_WORKTREE" >&2
  exit 1
}
install -d -m 0700 "$PRIVATE_PARENT"

tailscale ssh "$SSH_TARGET" sh -s -- >"$SEED_ARCHIVE" <<'SH'
set -eu
cd /home/smith/private/klokast
test -d instance-seed/.git
tar -cf - instance-seed
SH

tar -xf "$SEED_ARCHIVE" -C "$PRIVATE_PARENT"
mv "$PRIVATE_PARENT/instance-seed" "$PRIVATE_WORKTREE"
rm -f "$SEED_ARCHIVE"
chmod -R go-rwx "$PRIVATE_WORKTREE"
```

The generated starter has one `k001` box. Edit it to describe the current
two-box deployment:

- keep stable `box-001` for `k001`;
- add `box-002` for `k002`;
- set `box-002` as the active controller;
- set `box-001` as the standby controller;
- declare one `controller_container` airunner on `box-002`, which resolves to
  `k002-ops-airunner`;
- copy the exact private Tailnet groups, sites, country values, and physical
  locations from the current deployment;
- for each box, set `declared_capabilities` to the union of the current
  available and prohibited capabilities;
- keep enabled capabilities, prohibited capabilities, and access policy equal
  to the current registry;
- represent every enabled legacy app, its logical box placement, and its
  Boolean public-manifest resource bindings;
- do not copy runtime state, device bindings, bridge ports, DHCP data, ingress
  details, secrets, generated files, or compatibility-only fields into
  Contract v1.

Only these four files are authoritative:

```text
klokast.yml
klokast.lock.yml
ops/deployment.yml
ops/platform-resources.yml
```

Confirm that `klokast.lock.yml` contains the exact `ENGINE_COMMIT`. Review all
private values before the commit.

## 10. Commit and push as the human

Run from the private worktree:

```sh
cd "$PRIVATE_WORKTREE"

test "$(git branch --show-current)" = main
git status --short
git diff --check
git diff --cached --check

git add klokast.yml klokast.lock.yml ops/deployment.yml \
  ops/platform-resources.yml README.md AGENTS.md .gitignore
git diff --cached --check
git status --short
git commit -m "Initialize private Klokast instance"

git remote add origin "git@github.com:$INSTANCE_OWNER/$INSTANCE_REPO.git"
git push -u origin main
```

Use the human's private-repository GitHub identity. Do not use the temporary
App, a controller key, or an airunner key to push.

Confirm in the GitHub web UI that the repository is private and that `main`
contains the expected initial commit.

### Stop point 2

Tell the agent that the private `main` branch is pushed. Do not paste the
repository contents or private commit diff.

## 11. Remove repository access from the temporary App

In GitHub:

1. Open the organization settings.
2. Select **GitHub Apps** or **Installed GitHub Apps**.
3. Select **Configure** for the temporary bootstrap App.
4. Keep **Only select repositories**.
5. Remove the new `klokast` repository from the selection.
6. Save the change.

GitHub may refuse removal when this is the last selected repository. If it
does, stop. Do not select an unrelated repository, do not change to **All
repositories**, and do not uninstall the App yet. Tell the agent the exact
GitHub error without including private repository contents.

If removal succeeds, run section 7 with:

```sh
ACTION="retire-bootstrap"
ACTION_FINGERPRINT="$KEY_FINGERPRINT"
```

The retirement action must confirm all of these conditions before it deletes
the controller copy of the App credential:

- the App installation can no longer list the private repository;
- anonymous Git access fails;
- the controller read-only deploy key can still fetch `main`.

Expected result includes:

```json
"retired": true
```

## 12. Delete the temporary App

Only after `retire-bootstrap` succeeds:

1. Uninstall the temporary App from the organization.
2. Delete the temporary GitHub App in its developer settings.
3. Delete the local PEM:

```sh
rm -i "$PEM"
unset PEM KEY_FINGERPRINT ACTION ACTION_FINGERPRINT
```

Do not delete the controller deploy key. It is the active controller's
read-only source credential.

### Stop point 3

Tell the agent:

```text
The first private main commit is pushed.
The temporary App bootstrap authority is retired.
The temporary App is deleted.
```

The agent can then run `platform-instance sync`, create a fresh Observation
v1 file, and generate the live read-only Plan v1 artifact. Legacy deployment
and registry authority stays active. No apply action is part of this runbook.

## Troubleshooting

- **The GitHub installation requires one selected repository:** stop. Do not
  grant the App access to an unrelated repository.
- **The intent expired:** generate and sign a new intent.
- **The nonce was already used:** generate and sign a new intent.
- **The intent has another engine commit:** stop. Pulling or changing the
  public controller checkout during bootstrap changes the approval boundary.
- **macOS does not ask for Touch ID:** stop. Confirm that the session names the
  private-instance profile and that Touch ID is configured for the current Mac
  user. Rerun the signer self-test. Do not continue with an unapproved
  signature.
- **The repository already exists:** stop. Do not reuse, import, rename, or
  delete a repository until the agent checks the state.
- **The staged destination exists:** stop. Do not overwrite it.
- **GitHub shows the deploy key as write-enabled:** stop and remove that key in
  the GitHub UI. Do not continue with a write-enabled controller key.
- **App removal fails because it is the last selected repository:** keep the
  App and its root-only controller credential in place, and ask the agent to
  review the retirement path.

## References

- [Private-instance Secret Authority](../../doc/secret-authority.md#private-instance-bootstrap)
- [Upstream/instance target architecture](../../doc/upstream-instance-target-architecture.md)
- [Apple `sc_auth` manual](https://keith.github.io/xcode-man-pages/sc_auth.8.html)
- [GitHub: install your own GitHub App](https://docs.github.com/en/apps/using-github-apps/installing-your-own-github-app)
- [GitHub: create an organization repository](https://docs.github.com/en/rest/repos/repos#create-an-organization-repository)
- [GitHub: review an installed GitHub App](https://docs.github.com/en/apps/using-github-apps/reviewing-and-modifying-installed-github-apps)
