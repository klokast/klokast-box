# GitHub App for Klokast Static Site

Create a GitHub App for the `klokast` organization when the Secret Authority
needs brokered GitHub access for the static website.

Do not create or paste a long-lived personal access token for this flow. The
target pattern is a GitHub App private key stored root-only on `<box>-ops`; the
Secret Authority mints short-lived installation tokens only inside checked
wrappers.

# 1. Create the empty private repository

Use the GitHub web UI from the human admin account.

1. Open `https://github.com/organizations/klokast/repositories/new`.
2. Set Owner to `klokast`.
3. Set Repository name to `klokast-site`.
4. Set Visibility to `Private`.
5. Do not add a README, `.gitignore`, or license.
6. Create the repository.

Creating the repository first lets the app be installed only on
`klokast/klokast-site`, instead of across the whole organization.

# 2. Create the GitHub App

1. Open `https://github.com/organizations/klokast/settings/apps/new`.
2. If that URL does not work, navigate through:
   profile photo -> Your organizations -> `klokast` -> Settings ->
   Developer settings -> GitHub Apps -> New GitHub App.
3. Fill in:
   - GitHub App name: `klokast-static-broker`
   - Description: `Secret Authority broker for the Klokast static website`
   - Homepage URL: `https://www.klokast.ai/`
   - Callback URL: leave blank
   - Expire user authorization tokens: leave enabled
   - Request user authorization during installation: disabled
   - Enable Device Flow: disabled
   - Setup URL: leave blank
   - Webhook Active: disabled
4. Set repository permissions:
   - Administration: Read and write
   - Contents: Read and write
   - Metadata: read-only is automatic
5. Set Organization permissions to no access.
6. Set Account permissions to no access.
7. Subscribe to no events.
8. Under "Where can this GitHub App be installed?", choose
   `Only on this account`.
9. Click `Create GitHub App`.

# 3. Generate and keep the private key

1. On the app settings page, record the integer `App ID`.
   Do not confuse it with the Client ID.
2. Scroll to `Private keys`.
3. Click `Generate a private key`.
4. Confirm that a `.pem` file downloaded to the local machine.

GitHub only downloads the private key at generation time. It does not show it
again later; it stores only the public part.

If GitHub reports that the key was generated but no PEM file is available:

1. Check the browser downloads shelf and the local `Downloads` directory for a
   recently downloaded `.pem` file.
2. If the PEM is still missing, treat that key as unusable.
3. Generate a new private key.
4. Confirm the new PEM file exists before continuing.
5. Delete the orphaned unusable key from the GitHub App private-key list. If
   GitHub refuses to delete the only key, generate and confirm the replacement
   key first, then delete the orphaned key.

Do not paste the PEM into chat. It must be transferred into root-only Secret
Authority storage on `<box>-ops`.

Before transfer, move the downloaded key out of `Downloads` on the MacBook.
This is not an SSH identity, so prefer a Klokast-specific secret directory over
`~/.ssh`:

```sh
PEM_DOWNLOAD="$HOME/Downloads/YOUR_DOWNLOADED_GITHUB_APP_KEY.pem"
PEM="$HOME/.config/klokast/secrets/klokast-static-broker.github-app.pem"

test -r "$PEM_DOWNLOAD" || { echo "missing PEM: $PEM_DOWNLOAD" >&2; exit 1; }
install -d -m 0700 "$HOME/.config/klokast/secrets"
install -m 0600 "$PEM_DOWNLOAD" "$PEM"
rm -i "$PEM_DOWNLOAD"
ls -l "$PEM"
```

# 4. Install the app on the repository

1. In the app sidebar, click `Install App`.
2. Click `Install` next to `klokast`.
3. Choose `Only select repositories`.
4. Select only `klokast-site`.
5. Click `Install`.
6. Record the installation ID from the browser URL. It looks like:
   `https://github.com/organizations/klokast/settings/installations/12345678`
   The trailing number is `GITHUB_APP_INSTALLATION_ID`.

At the end of this setup, the human admin should have:

```text
GITHUB_APP_ID=<integer from app settings>
GITHUB_APP_INSTALLATION_ID=<integer from installation URL>
github-app.pem=<downloaded private key file>
```

Next, install those values into the root-only Secret Authority configuration on
the active `<box>-ops` controller from the MacBook:

```sh
cd path/to/klokast-box
git pull --ff-only

GITHUB_APP_ID="123456"
GITHUB_APP_INSTALLATION_ID="12345678"
PEM="$HOME/.config/klokast/secrets/klokast-static-broker.github-app.pem"

klokast-dev/bin/install-static-site-github-app \
  --controller boxb-ops \
  --pem "$PEM" \
  --app-id "$GITHUB_APP_ID" \
  --installation-id "$GITHUB_APP_INSTALLATION_ID"

unset GITHUB_APP_ID GITHUB_APP_INSTALLATION_ID PEM
```

The wrapper writes:

- `/etc/klokast/secret-authority/github-app.env`
- `/etc/klokast/secret-authority/github-app.pem`

Both files are root-owned on `<box>-ops`; the wrapper then checks redacted
Secret Authority status and mints a test installation token without printing
the token.

# References

- `https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/registering-a-github-app`
- `https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/managing-private-keys-for-github-apps`
- `https://docs.github.com/en/apps/using-github-apps/installing-your-own-github-app`
- `https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/choosing-permissions-for-a-github-app`
