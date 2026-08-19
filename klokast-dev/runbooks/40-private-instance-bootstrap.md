# Private Instance Bootstrap: Human Procedure

Use this procedure to create the private Contract v1 repository,
and give the active controller read-only access to it.

The Platform got one active controller `<box>-ops`.
The current approved engine is:

```text
commit: ce0c769a6357b4aedfdefbf3e5eaa77d7543c9ee
build:  /var/lib/klokast/builds/klokast-cli/ce0c769a6357b4aedfdefbf3e5eaa77d7543c9ee/9a4ebc19b032
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
after 10 minutes. The intent names the exact action, repository, controller
engine commit, and key fingerprint when required.

The wrapper does the mechanical work. Your responsibility is to read the
displayed intent, approve only the expected values, and use Touch ID. It does
not run a second action automatically.

### 8.1 Register the `<family-org>-klokast-instance` repository by the controller

From the trusted MacBook, run the wrapper:

```sh
klokast-dev/bin/run-private-instance-action register-repository
```

The wrappers displays the intent. The following fields are specially sensitive:

- action: `register-repository`;
- repository: `<family-org>/klokast-instance`;
- engine commit: the `ENGINE_COMMIT` from Step 1;
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

In the output, confirm these :
- same repository
- same engine commit
- same key fingerprint as in Step 7.

Answer `y`, then approve Touch ID. If you answer anything other than `y`, no signature is made and no action is run.
If an action fails or its intent expires, run the same wrapper command again to create a new intent. Do not reuse or edit an intent.

For each run, the wrapper adds one redacted record to
`$BOOTSTRAP_WORK/action-audit.jsonl`. The record has the action, result, failed
phase, exit code, controller, engine commit, repository hash, and intent hash.
It does not contain the intent, signature, deploy key, GitHub token, or GitHub
response. The controller also records the approved action start, result, and
deploy-key cleanup result in `/var/log/klokast/secret-authority.jsonl`. The
wrapper prints both log locations when an action fails.

The controller asks GitHub to register that key as read-only.
It then uses the key to prove that the repository still has no Git refs. If this proof fails, the controller removes the new key and stops.

Confirm in GitHub that the deploy key is read-only. Then continue with Step 9.
Do not run `retire-bootstrap` now. You will run it in Step 12, after the first
private commit is pushed and App access is removed.

## 9. Create the staged Contract v1 repository

Run the worktree helper from the trusted MacBook:

```sh
klokast-dev/bin/prepare-private-instance-worktree
```

The helper does these actions:

- checks the session, active controller, exact engine, sealed build operation,
  and fixed private paths;
- creates or reuses the owner-only controller values file and opens it in
  `vi` with an interactive terminal;
- asks before it runs the sealed initializer;
- asks before it streams the new seed to the MacBook;
- verifies that the worktree has the exact generated files, staged state,
  `main` branch, no commit, no remote, and exact engine lock.

It does not display or copy the private values file. Do not enter its values in
chat. The template has this JSON shape:

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

The helper writes a redacted result to `$BOOTSTRAP_WORK/action-audit.jsonl`.
The record contains the phase, result, controller, repository hash, engine and
build pins, and whether it created or reused each staged result. It does not
contain private values or generated Contract content.

The controller seed and MacBook worktree must not already exist. The helper
does not overwrite or delete them. If the operation fails after it creates one
of them, read the final recovery message and ask the agent to inspect that
result before another attempt.

## 10. Edit and review the private repository on the MacBook

After the helper succeeds, it prints the private worktree path. Set the same
shell variable for the remaining commands:

```sh
PRIVATE_PARENT="$HOME/src/private-klokast"
PRIVATE_WORKTREE="$PRIVATE_PARENT/klokast-instance"
```

The repository has four authoritative Contract files, but you must edit only
the two files under `ops/` in this step:

```text
File                          Action in Step 10
klokast.yml                   Review only. Do not edit.
klokast.lock.yml              Review only. Do not edit.
ops/deployment.yml            Edit topology and control-plane intent.
ops/platform-resources.yml    Edit access and application intent.
```

Leave `.gitignore`, `AGENTS.md`, and `README.md` as generated. They are support
files, not Contract inputs.

### 10.1 Review the generated control files

`klokast.yml` must continue to point to these two paths:

```yaml
paths:
  deployment: ops/deployment.yml
  platform_resources: ops/platform-resources.yml
```

`klokast.lock.yml` must continue to name the canonical public engine, `main`,
and the exact `ENGINE_COMMIT` from Step 1. These commands must produce no diff
and must print `engine lock ok`:

```sh
git -C "$PRIVATE_WORKTREE" diff -- klokast.yml klokast.lock.yml
grep -Eq "^[[:space:]]+commit: $ENGINE_COMMIT$" \
  "$PRIVATE_WORKTREE/klokast.lock.yml" && echo "engine lock ok"
```

Stop if either generated control file needs a manual correction. Do not repair
a generated control file by hand.

### 10.2 Edit `ops/deployment.yml`

Open `$PRIVATE_WORKTREE/ops/deployment.yml` in your local editor. Make only
these changes:

- keep `schema_version`, `instance`, and all `tailnet` values exactly as the
  initializer generated them;
- keep `site-001` and `box-001` exactly as generated;
- add `site-002` for the physical site that contains `k002`;
- set `site-002.country` to its two-letter uppercase country code;
- set `site-002.timezone` to `Etc/UTC`;
- set `site-002.physical_location` to the private physical-location text for
  `k002`; omit this optional field only when no such text is recorded;
- add `box-002` with `hostname_prefix: k002` and `site: site-002`;
- replace `control_plane` with the exact block below.

```yaml
control_plane:
  controller:
    active_box: box-002
    standby_box: box-001
  airunners:
    - id: airunner-001
      kind: controller_container
      box: box-002
```

This airunner resolves to `k002-ops-airunner`. Do not add current runtime
health, IP addresses, credentials, or generated inventory to this file.

Before you continue, confirm that an authoritative private source records the
country and physical location for `k002`. Stop if it does not. Do not guess a
private site value and do not copy it into chat.

### 10.3 Edit `ops/platform-resources.yml`

Open `$PRIVATE_WORKTREE/ops/platform-resources.yml` in your local editor.
Replace the single-box starter intent with the current two-box intent.

Under `boxes`, use the stable logical IDs `box-001` for `k001` and `box-002`
for `k002`. Each box must contain all four access fields:

```text
declared_capabilities     legacy available capabilities plus legacy prohibited capabilities
enabled_capabilities      exact effective legacy enabled capabilities
prohibited_capabilities   exact effective legacy prohibited capabilities
policy                    exact effective legacy policy
```

Do not copy a missing legacy field as an empty value. Use the effective value
after legacy defaults are applied. The enabled and prohibited sets must not
overlap. Each non-`none` policy value must be enabled on that box.

Under `apps`, represent every enabled legacy application that has an embedded
public manifest in the approved engine. Use these placement conversions:

```text
Legacy placement                         Contract v1 placement
active_master only                       mode: single_box; box
active_master plus passive_backup        mode: active_passive; active_master; passive_backup
boxes                                    mode: multi_box; boxes
```

Convert each runtime box name to its logical ID: `k001` becomes `box-001` and
`k002` becomes `box-002`. Copy only Boolean entries from the legacy app
`resources` map. Use `{}` when that map is empty.

An enabled application must have an `apps/<app>/platform-resources.yml`
manifest in the public engine at `ENGINE_COMMIT`. Stop if a manifest is
missing. Do not omit the enabled application, create a private manifest, or
disable the application only to make the Contract pass.

Do not copy these compatibility-only values into Contract v1:

- runtime state or application-private configuration;
- users, devices, bridge ports, DHCP reservations, or shared-guest settings;
- application VM details, ingress modes, secrets, or generated files.

### 10.4 Review the two edits

Run:

```sh
cd "$PRIVATE_WORKTREE"
git diff --check
git diff -- ops/deployment.yml ops/platform-resources.yml
```

Review every private value locally. Confirm again that `klokast.yml`,
`klokast.lock.yml`, `.gitignore`, `AGENTS.md`, and `README.md` have no unstaged
changes. Continue to Step 11 only when every enabled application is represented
and both private files contain the exact intended state.

## 11. Commit and push as the human

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
- **The intent has another engine commit:** stop. Pulling or changing the
  public controller checkout during bootstrap changes the approval boundary.
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
- [Upstream/instance target architecture](../../doc/upstream-instance-target-architecture.md)
- [Apple `sc_auth` manual](https://keith.github.io/xcode-man-pages/sc_auth.8.html)
- [GitHub: install your own GitHub App](https://docs.github.com/en/apps/using-github-apps/installing-your-own-github-app)
- [GitHub: create an organization repository](https://docs.github.com/en/rest/repos/repos#create-an-organization-repository)
- [GitHub: review an installed GitHub App](https://docs.github.com/en/apps/using-github-apps/reviewing-and-modifying-installed-github-apps)
