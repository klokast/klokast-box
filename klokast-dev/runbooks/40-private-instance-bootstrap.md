# Private Instance Bootstrap: Human Procedure

Use this procedure to create the private Klokast Instance Specification v1 repository,
and give the active controller read-only access to it.

The Platform got one active controller `<box>-ops`.
The current approved engine is:

```text
commit: 400561fc625921e123252ea3b3f84a61e7109cca
build:  /var/lib/klokast/builds/klokast-cli/400561fc625921e123252ea3b3f84a61e7109cca/fc34f7dfcb84
```

Do not paste the GitHub App PEM, private deployment values, signed intents,
approval signatures, or private repository contents into chat. Do not run
this procedure from an airunner. Run the human steps from the trusted MacBook.

The human creates the exact empty private repository before App installation.
The temporary GitHub App has no Contents permission. It verifies the
repository and registers one read-only deploy key. The human, not the App or
an airunner, authors and pushes the first private commit.

## 0. Pre-requisites

Complete `klokast-dev/runbooks/15-touchid-secret-authority.md` before Step 1.
That migration installs the separate private-instance and static-site signers
and deploys their scope enforcement to the active controller.

## 1. Prepare the MacBook

Use the interactive helper from the `klokast-box` checkout on the MacBook:

```sh
cd /path/to/klokast-box
klokast-dev/bin/prepare-private-instance-bootstrap
```

Before it asks for settings, the helper requires a clean checkout and runs a
fast-forward Git pull. If the pull changes the helper, the running process
prints the old and new commits and restarts the updated helper. It does not
continue with shell functions loaded from the old commit.

The helper then lists the information, access, and hardware that you must
prepare. It explains each authority-changing action before it runs it. It:

- collects the non-secret bootstrap settings;
- confirms the updated public checkout commit;
- checks the MacBook tools and active controller;
- requires a clean controller checkout that contains the approved engine in
  its Git history, and verifies the exact sealed build receipt and binary;
- creates or reuses the private-instance Secure Enclave approval key and asks
  before it installs the public signer on the controller;
- saves an owner-only session file for the remaining steps.

The helper does not ask for a GitHub App PEM or private deployment values.
The public controller checkout can be newer than the approved engine. These
are separate pins: the current checkout supplies the bootstrap wrappers, and
the approved sealed engine supplies the generated Instance Specification.

An older helper that predates automatic restart cannot acquire this behavior
during its own process. For the first adoption from such a version, update the
checkout as a separate command before you run the helper:

```sh
git pull --ff-only
klokast-dev/bin/prepare-private-instance-bootstrap
```

When it completes, load the settings into the same terminal:

```sh
source "$HOME/.local/share/klokast/private-instance-bootstrap/session.sh"
```

The approval key is an Apple-native CryptoTokenKit identity protected by Touch
ID. The private key is not exportable. The local profile contains its public
key. A private, short-lived Apple `ssh-agent` obtains the matching Secure
Enclave identity only while a signature is made. An Apple passkey cannot
replace it because a passkey is bound to a compatible app or website and
cannot sign the bootstrap intent file.

See `klokast-dev/runbooks/15-touchid-secret-authority.md`
for the shared signer design and recovery procedure.

## 2. Create the family organization in GitHub

Create the `<family-org>` GitHub organization. The human must control this
organization.
The repository name is `klokast-instance`. The human will create the exact
empty private repository in Step 5.

Enable Deploy Keys: GitHub → <family-org> → Settings → Enabled

## 3. Create the temporary GitHub App

Open the organization settings in GitHub:

`Your organizations -> <family-org> -> Settings -> Developer settings -> GitHub Apps -> New GitHub App`

Use these settings:

- GitHub App name: a unique name such as
  `<family-org>-klokast-instance-bootstrap`;
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

## 4. Generate and protect the App private key

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

## 5. Create the exact empty private repository

Create the repository in the GitHub web interface before you install the App:

1. Open the `<family-org>` organization.
2. Select **New repository**.
3. Set **Repository name** to `klokast-instance`.
4. Set visibility to **Private**.
5. Do not select a repository template.
6. Do not add a README, `.gitignore`, or license.
7. Select **Create repository**.

The result must be the exact empty organization repository
`<family-org>/klokast-instance`. It must have no branch or commit. Do not
create it in a personal account. Do not import, fork, rename, or reuse an
existing repository.

This manual action does not give the controller or airrunner access to the
repository. The signed `register-repository` action in Step 8 will verify and
record its identity and empty state.

## 5.1 Install the App on the exact private repository

1. In the internet browser, open:
`https://github.com/organizations/<FAMILY-GITHHUB-ORGA>/settings/apps/<FAMILY>-klokast-instance-bootstrap>` , where for example:
  - `<FAMILY-GITHHUB-ORGA>` would be the `john-home` Github organization.
  - `<FAMILY>-klokast-instance-bootstrap>` would be the `john-klokast-instance-bootstrap` Github app.
2. In the left side panel, select **Install App**.
3. Select **Install** next to `INSTANCE_OWNER`.
4. Select **Only select repositories**. Don't select **All repositories** or an unrelated repository!
5. Select only `klokast-instance`.
6. Review the permissions and select **Install**.
7. Write down the "installation ID": the digits at the end of the URL. The App ID and installation ID are identifiers, they aren't secrets. Don't forget that the PEM is a secret.

## 6. Install the App credential on the controller

Set the two GitHub identifiers and the path to the (secret) PEM:

```sh
GITHUB_APP_ID="REPLACE_WITH_APP_ID"
GITHUB_APP_INSTALLATION_ID="REPLACE_WITH_INSTALLATION_ID"
PEM="$HOME/.config/klokast/secrets/private-instance-bootstrap.github-app.pem"
```

Then run the installer:

```sh
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
The exact empty private repository is INSTANCE_OWNER/klokast-instance.
```

Do not send the PEM. The agent can now prepare the controller read key and
check the redacted status.

## 7. Prepare the controller read key

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
fingerprint. The private deploy key stays root-only on `<box>-ops`.

## 8. Run a human-signed instance action

The GitHub App credential alone cannot authorize these changes. For each
high-authority change, the controller creates a single-use intent that expires
after 10 minutes. The intent names the exact action, repository, current clean
controller commit, approved engine commit, and key fingerprint when required.
In the intent, `repo_head` is the current controller commit and
`engine_commit` is the approved sealed engine from Step 1.

The wrapper does the mechanical work. Your responsibility is to read the
displayed intent, approve only the expected values, and use Touch ID. It does
not run a second action automatically.

### 8.1 Register the `<family-org>-klokast-instance` repository by the controller

From the trusted MacBook, run the wrapper:

```sh
klokast-dev/bin/run-private-instance-action register-repository
```

The wrapper displays the intent. Check these sensitive fields:

- action: `register-repository`;
- repository: `<family-org>/klokast-instance`;
- `repo_head`: the current clean controller checkout reported in Step 1;
- `engine_commit`: the `ENGINE_COMMIT` from Step 1;
- expiry: no more than 10 minutes in the future.

Answer `y` when all fields are correct.
Sign the Touch ID prompt as it pops up. The name of the popup, `ctcardtoken`, is the name of the macOS CryptoTokenKit process that manages the MacBook Secure Enclave key.
The controller will verify that the repository is private, empty, and owned by
the <family> organization.
It will record the repository ID. It won't change the repository contents.

### 8.2 Register the controller read-only key

After repository registration succeeds, run:

```sh
klokast-dev/bin/run-private-instance-action register-read-key
```

In the output, confirm these values:

- the same repository;
- the current clean controller commit in `repo_head`;
- the same approved engine commit;
- the same key fingerprint as in Step 7.

Answer `y`, then approve Touch ID. If you answer anything other than `y`, no signature is made and no action is run.
If an action fails or its intent expires, run the same wrapper command again to create a new intent. Do not reuse or edit an intent.

For each run, the wrapper adds one redacted record to
`$BOOTSTRAP_WORK/action-audit.jsonl`. The record has the action, result, failed
phase, exit code, controller commit, engine commit, repository hash, and intent
hash.
It does not contain the intent, signature, deploy key, GitHub token, or GitHub
response. The controller also records the approved action start, result, and
deploy-key cleanup result in `/var/log/klokast/secret-authority.jsonl`. The
wrapper prints both log locations when an action fails.

The controller asks GitHub to register that key as read-only.
It then uses the key to prove that the repository still has no Git refs. If this proof fails, the controller removes the new key and stops.

Confirm in GitHub that the deploy key is read-only. Then continue with Step 9.
Do not run `retire-bootstrap` now. You will run it in Step 12, after the first
private commit is pushed and App access is removed.

## 9. Create the staged Instance Specification v1 repository

Run the worktree helper from the trusted MacBook:

```sh
klokast-dev/bin/prepare-private-instance-worktree
```

The helper does these actions:

- checks the session, active controller, exact engine, sealed build operation,
  and fixed private paths;
- runs the guided values setup on the controller in the current terminal;
- reads the exact Tailnet DNS name from `tailscale status --json`;
- asks only for the Tailscale member login and checks it immediately;
- shows the DNS name, login, fixed `operator` and `family` roles, and reviewed
  two-box topology before it asks for confirmation;
- writes the complete JSON file atomically with mode `0600` and validates it
  with the sealed initializer;
- asks before it uses the sealed initializer to create the controller seed;
- asks before it streams the new seed to the MacBook;
- verifies that the worktree has the exact generated files, staged state,
  `main` branch, no commit, no remote, and exact engine lock.

The setup uses the Tailnet DNS name, not a machine MagicDNS name. It does not
add a timezone. Platform time is always `Etc/UTC`. The complete reviewed
topology is in [Klokast Instance Specification v1](../../doc/klokast-instance-specification.md).

If the values file does not exist, the setup creates it. If the existing file
is valid and has the reviewed topology, the setup shows it and can reuse it
without a write. If it is the known placeholder template, including a partial
edit of that template, the setup can repair it after confirmation. The setup
does not overwrite a malformed file, an unexpected file, a symbolic link, a
hard link, or a file with unsafe permissions.

An invalid login gives a specific correction and asks again. An invalid or
unavailable Tailscale status stops the operation. Cancellation, end of input,
or an interrupt cannot start seed creation. If the sealed initializer rejects
the completed values, the helper prints each rejected field before it stops.

The helper writes a redacted result to `$BOOTSTRAP_WORK/action-audit.jsonl`.
The record contains the phase, result, controller, repository hash, engine and
build pins, and whether it created, reused, or repaired each staged result. It
does not contain private values or generated Instance Specification content.

The controller seed and MacBook worktree must not already exist. The helper
does not overwrite or delete them. If the operation fails after it creates one
of them, read the final recovery message and ask the agent to inspect that
result before another attempt.

Run the normal helper again to repair the known placeholder values file. Do
not use `--archive-and-restart` for that recovery.

The old unreleased YAML seed is not compatible with this format. If a
controller seed or MacBook worktree comes from that earlier format, run:

```sh
klokast-dev/bin/prepare-private-instance-worktree --archive-and-restart
```

The helper checks that each existing repository is standalone, unborn, has no
remote, has a recognized seed file set, and contains no symbolic links. It
asks before it moves the controller seed, controller values file, and MacBook
worktree to owner-only timestamped archive paths. It does not delete them.
Then it creates a new JSON seed.

## 10. Review the private repository on the MacBook

After the helper succeeds, it prints the private worktree path. Set the same
shell variable for the remaining commands:

```sh
PRIVATE_PARENT="$HOME/src/private-klokast"
PRIVATE_WORKTREE="$PRIVATE_PARENT/klokast-instance"
```

The repository has two authoritative Instance Specification files:

```text
File                     Action in Step 10
klokast-instance.json    Review the complete private desired state.
klokast.lock.json        Review only. Do not edit.
```

Leave `.gitignore`, `AGENTS.md`, and `README.md` as generated. They are support
files, not Instance Specification inputs.

The helper already generated the complete instance from the controller values
file. No file requires an edit after a successful run. If the review finds an
incorrect private value, edit only `klokast-instance.json`, then run the
checks below. Never edit `klokast.lock.json`.

### 10.1 Review the engine and schema pins

Both `$schema` values and the lock commit must use the exact `ENGINE_COMMIT`
from Step 1:

```sh
python3 - "$PRIVATE_WORKTREE" "$ENGINE_COMMIT" <<'PY'
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
commit = sys.argv[2]
instance = json.loads((root / "klokast-instance.json").read_text())
lock = json.loads((root / "klokast.lock.json").read_text())
assert lock["engine"]["commit"] == commit
assert f"/{commit}/schemas/" in instance["$schema"]
assert f"/{commit}/schemas/" in lock["$schema"]
print("engine and schema pins ok")
PY
```

Stop if the lock needs a correction. Rebuild the seed. Do not repair the lock
by hand.

### 10.2 Review `klokast-instance.json`

Confirm these points locally:

- the private Tailnet DNS name and member login are exact;
- the member has both `operator` and `family` roles;
- `milla` is in `FR`, and `mingdu` is in `CN`;
- `k001` uses `local-ap-direct-egress` and `tailscale`;
- `k002` uses only `tailscale`;
- the active controller is `k002`, and the standby controller is `k001`;
- the airunner preference order is `k002-ops-airunner`,
  `k001-ops-airunner`, `vultr-ops`, then `hetzner-ops`;
- every listed airunner is desired and must be online with its required tag;
- Music is absent and its declared `library` data on `k002` has preservation
  intent;
- no other removed app is present;
- there is no timezone, runtime-status, secret, IP-address, or generated-state
  field.

See `doc/klokast-instance-specification.md` in the public repository for
placement, app, feature, data, and connectivity-profile rules.

### 10.3 Validate the review

Run:

```sh
cd "$PRIVATE_WORKTREE"
git diff --check
python3 -m json.tool klokast-instance.json >/dev/null
python3 -m json.tool klokast.lock.json >/dev/null
```

Run the sealed checker as described by the helper output or through the
controller workflow before deployment. Continue to Step 11 only when the two
JSON files contain the exact intended state.

## 11. Commit and push as the human

Run from the private worktree:

```sh
cd "$PRIVATE_WORKTREE"

test "$(git branch --show-current)" = main
git status --short
git diff --check
git diff --cached --check

git add klokast-instance.json klokast.lock.json README.md AGENTS.md .gitignore
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

## 12. Remove repository access from the temporary App

In GitHub:

1. Open the organization settings.
2. Select **GitHub Apps** or **Installed GitHub Apps**.
3. Select **Configure** for the temporary bootstrap App.
4. Keep **Only select repositories**.
5. Remove `klokast-instance` from the selection.
6. Save the change.

GitHub may refuse removal when this is the last selected repository. If it
does, stop. Do not select an unrelated repository, do not change to **All
repositories**, and do not uninstall the App yet. Tell the agent the exact
GitHub error without including private repository contents.

If removal succeeds, run the final human-approved action from the trusted
MacBook:

```sh
klokast-dev/bin/run-private-instance-action retire-bootstrap
```

Confirm the repository, engine commit, read-key fingerprint, and expiry in the
displayed intent. Answer `y`, then approve Touch ID.

The retirement action must confirm all of these conditions before it deletes
the controller copy of the App credential:

- the App installation can no longer list the private repository;
- anonymous Git access fails;
- the controller read-only deploy key can still fetch `main`.

Expected result includes:

```json
"retired": true
```

## 13. Delete the temporary App

Only after `retire-bootstrap` succeeds:

1. Uninstall the temporary App from the organization.
2. Delete the temporary GitHub App in its developer settings.
3. Delete the local PEM:

```sh
rm -i "$PEM"
unset PEM KEY_FINGERPRINT
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

- **The repository cannot be selected during App installation:** confirm that
  it exists in `INSTANCE_OWNER`, is private, and that you are an organization
  owner. Do not select an unrelated repository or **All repositories**.
- **The intent expired:** generate and sign a new intent.
- **The nonce was already used:** generate and sign a new intent.
- **The intent has another `repo_head`:** stop. The controller checkout changed
  after preflight. Rerun the wrapper and review the new current controller
  commit before approval.
- **The intent has another engine commit:** stop. Rerun
  `prepare-private-instance-bootstrap` and load its new session. A newer clean
  public controller checkout is permitted, but the intent must still contain
  the exact approved engine from Step 1.
- **The controller checkout is newer than the approved engine:** this is
  permitted when the approved engine is in the checkout history and the exact
  sealed build passes verification. Do not reset the controller checkout to
  the older engine commit.
- **The helper updates and then reports behavior from the old commit:** the
  running helper predates automatic restart. Run `git pull --ff-only` as a
  separate command, then run the helper again. Current helpers restart before
  any settings prompt or controller request.
- **The checkout changes again during automatic restart:** no bootstrap action
  ran. Run the helper again from the updated clean checkout.
- **An action fails after approval:** read the failed phase in the final
  wrapper summary. Review the last record in
  `$BOOTSTRAP_WORK/action-audit.jsonl`. Ask the agent to review the matching
  intent hash in the root-only controller audit before you retry.
- **macOS does not ask for Touch ID:** stop. Confirm that the session names the
  private-instance profile and that Touch ID is configured for the current Mac
  user. Rerun the signer self-test. Do not continue with an unapproved
  signature.
- **The Step 5 repository was not new and empty or contains a branch:** stop.
  Do not reuse, import, rename, empty, or delete it until the agent checks the
  state.
- **The staged destination exists:** stop. Do not overwrite it.
- **The worktree helper fails before seed creation:** correct the reported
  phase and run the helper again. It safely reuses a valid owner-only values
  file.
- **The worktree helper fails after seed creation:** do not delete or
  overwrite the controller seed or MacBook worktree. If no MacBook worktree
  was created, run the helper with `--resume-transfer`:

  ```sh
  klokast-dev/bin/prepare-private-instance-worktree --resume-transfer
  ```

  Recovery requires the matching owner-only audit record and rechecks the seed
  with the pinned sealed binary before transfer.
- **GitHub shows the deploy key as write-enabled:** stop and remove that key in
  the GitHub UI. Do not continue with a write-enabled controller key.
- **App removal fails because it is the last selected repository:** keep the
  App and its root-only controller credential in place, and ask the agent to
  review the retirement path.

## References

- [Private-instance Secret Authority](../../doc/secret-authority.md#private-instance-bootstrap)
- [Klokast Instance Specification v1](../../doc/klokast-instance-specification.md)
- [Apple `sc_auth` manual](https://keith.github.io/xcode-man-pages/sc_auth.8.html)
- [GitHub: install your own GitHub App](https://docs.github.com/en/apps/using-github-apps/installing-your-own-github-app)
- [GitHub: create an organization repository](https://docs.github.com/en/rest/repos/repos#create-an-organization-repository)
- [GitHub: review an installed GitHub App](https://docs.github.com/en/apps/using-github-apps/reviewing-and-modifying-installed-github-apps)
