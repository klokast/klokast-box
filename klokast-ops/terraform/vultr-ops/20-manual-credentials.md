# Manual Credentials For `vultr-ops`

These credentials are controller-side inputs for
`klokast-ops/bin/provision-vultr-ops`. Keep them out of git, shell history,
Terraform variables, cloud-init, and copied logs.

## Set The Variables In The Current Shell

Use `read -s` so the tokens are typed into the terminal, not included in the
command line that the shell records in history.

```sh
read -r -s -p 'VULTR_API_KEY: ' VULTR_API_KEY; printf '\n'; export VULTR_API_KEY
read -r -s -p 'GITHUB_TOKEN: ' GITHUB_TOKEN; printf '\n'; export GITHUB_TOKEN
export VULTR_OPS_BOOTSTRAP_SSH_KEY_FILE="$HOME/.ssh/klokast-infra-vultr-ops"
```

Verify without printing secrets:

```sh
test -n "${VULTR_API_KEY:-}" && printf 'VULTR_API_KEY is set\n'
test -n "${GITHUB_TOKEN:-}" && printf 'GITHUB_TOKEN is set\n'
test -r "$VULTR_OPS_BOOTSTRAP_SSH_KEY_FILE" && printf 'bootstrap SSH key is readable\n'
```

Do not run these commands with `set -x`, and do not use
`export VULTR_API_KEY=...` or `export GITHUB_TOKEN=...` with literal token
values on the command line.

Unset them after provisioning:

```sh
unset VULTR_API_KEY GITHUB_TOKEN VULTR_OPS_BOOTSTRAP_SSH_KEY_FILE
```

## Generate `VULTR_API_KEY`

Use a dedicated Vultr user or sub-account for this automation when practical.
The Vultr API key can create and manage cloud resources, and Vultr documents
that API keys should be copied at creation time and stored securely.

Console path:

1. Log in to the Vultr Console.
2. Open `Dashboard`, then `Vultr API` under `Orchestration`.
3. In `User Access Tokens`, enter a name such as `klokast-vultr-ops`.
4. Choose an expiry. Prefer the shortest expiry that covers this provisioning
   window.
5. Click `Add Key`.
6. Copy the token once, then paste it at the `VULTR_API_KEY:` prompt above.

If Vultr API IP allowlisting is enabled, add the controller's current public
IP before running the wrapper.

## Generate `GITHUB_TOKEN`

The wrapper uses this token only on the controller to add the public deploy key
generated on `vultr-ops` to `klokast/klokast-box`.

Create a fine-grained personal access token:

1. In GitHub, open `Settings` > `Developer settings`.
2. Open `Personal access tokens` > `Fine-grained tokens`.
3. Click `Generate new token`.
4. Set the resource owner to `klokast`.
5. Restrict repository access to only `klokast-box`.
6. Set repository permission `Administration` to `Read and write`.
7. Set a short expiration.
8. Generate the token and paste it at the `GITHUB_TOKEN:` prompt above.

This permission is needed because GitHub's deploy-key create endpoint requires
repository `Administration` write access. If the organization requires token
approval, wait until the token is approved before provisioning.

## Generate `VULTR_OPS_BOOTSTRAP_SSH_KEY_FILE`

This key is the initial public-SSH bootstrap key injected into the Vultr
instance. It is not the GitHub deploy key; the deploy key is generated later
on `vultr-ops`.

Generate it on the controller:

```sh
mkdir -p "$HOME/.ssh"
chmod 700 "$HOME/.ssh"

ssh-keygen \
  -t ed25519 \
  -a 100 \
  -f "$HOME/.ssh/klokast-infra-vultr-ops" \
  -C "klokast-infra-vultr-ops bootstrap"

chmod 600 "$HOME/.ssh/klokast-infra-vultr-ops"
chmod 644 "$HOME/.ssh/klokast-infra-vultr-ops.pub"
```

If you protect the key with a passphrase, load it into `ssh-agent` before
running the provisioning wrapper:

```sh
eval "$(ssh-agent -s)"
ssh-add "$HOME/.ssh/klokast-infra-vultr-ops"
```

Then set:

```sh
export VULTR_OPS_BOOTSTRAP_SSH_KEY_FILE="$HOME/.ssh/klokast-infra-vultr-ops"
```

The wrapper derives the public key from this file, creates or imports the
Vultr SSH key named `klokast-infra-vultr-ops`, and uses the private key only
for the initial Ansible SSH connection.

## References

- Vultr API key creation: `https://docs.vultr.com/platform/other/api/current-user/new-api-key`
- Vultr API access controls: `https://docs.vultr.com/platform/other/users/manage-users/api-access`
- GitHub personal access tokens: `https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens`
- GitHub deploy-key API permissions: `https://docs.github.com/en/rest/deploy-keys/deploy-keys`
