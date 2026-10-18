# Runner Git provisioning

The [credential authority model](../../doc/architecture.md#runner-git-credentials)
owns key custody, scope, replacement and revocation. This procedure gives the
Platform the one-time provider access needed to provision runner keys without
manual registration for each VM.

## Set up the controller

As a GitHub organization administrator, create a dedicated GitHub App named
`klokast-air-git` (or an available equivalent) for the `klokast` organization.
Set a homepage URL, disable webhooks and user authorization, and select only:

- Repository Administration: read and write.
- Repository Metadata: read-only (automatic).
- No other repository, organization or account permissions.

Install the App on **Only select repositories**, selecting **klokast-box** only.
Record its App ID and installation ID. Generate a PEM private key and keep it
in the human password manager. Do not paste the PEM in chat or commit it.
Repeat provider setup with an independent App credential for another controller.

On the active controller as `smith`, install the broker from the updated source:

```sh
cd ~/src/klokast/klokast-box
git pull --ff-only
ANSIBLE_CONFIG=ansible/ansible.cfg ansible-playbook -vv \
  -i ansible/execution-inventory/hosts \
  ansible/playbooks/69-air-user-bootstrap.yml --limit BOX-air --tags air-git-broker
```

From the admin laptop, send the downloaded PEM directly to the controller:

```sh
klokast-dev/bin/install-air-github-app \
  --controller BOX-ops --app-id APP_ID --installation-id INSTALLATION_ID \
  --pem "$HOME/.config/klokast/secrets/klokast-air-git.pem"
```

The installer sends credentials through SSH stdin. The broker verifies the
App's permissions, selected repository and deploy-key API access before it
atomically writes its root-only credential file. It does not use a runner's
GitHub CLI login, copy a private deploy key, or store a personal access token.
Ordinary controller convergence also installs the broker.

## Provision or repair a runner

New `ansible/bin/airunner provision --box BOX` operations include Git bootstrap
and push verification. To repair Git access on an existing declared runner,
run on the active controller:

```sh
ANSIBLE_CONFIG=ansible/ansible.cfg ansible-playbook -vv \
  -i ansible/execution-inventory/hosts \
  ansible/playbooks/69-air-user-bootstrap.yml --limit BOX-air
```

The role keeps the runner's existing generated key. It registers the public
key with title `Klokast BOX-air public source`, then selects that key for the
public checkout. A previously registered key must have that title and write
access; update the title in GitHub if it was registered manually. Existing
legacy keys with other filenames are left in place. After repair, review and
remove their old GitHub grants when no remaining client uses them.

The final `git push --dry-run --no-verify` checks receive access as `agent`
without creating a branch or running a local push hook. It does not assert
that every protected branch permits every proposed change. `airunner verify`
also checks the checkout's push access. No SSH agent is required.

## Revoke and recover

On the active controller, revoke a runner's managed key even if its VM is offline:

```sh
ansible/bin/secret-authority air-git revoke --box BOX
```

This deletes only keys with the exact managed runner title. Ordinary retirement
calls it after the VM is stopped. A failed revocation stops retirement before
it reports success; rerun after provider access is restored. Synthetic test
VM retirement does not require a GitHub credential.

For a compromised or lost key, revoke its GitHub grant, stop the runner, and
rebuild its identity. Do not replace a healthy runner's key during routine
convergence. If the controller is unavailable, use GitHub repository Settings
> Deploy keys to revoke the grant. Deleting the App or its credential does not
delete previously issued deploy keys.

Restore the App credential to a reconstructed controller with the laptop
installer. A promoted controller needs its own configured App credential.
Existing runner keys continue to work during a controller outage.

Broker events are recorded in `/var/log/klokast/secret-authority.jsonl` on the
controller. Missing configuration reports the laptop installer command;
provider errors report the HTTP status without exposing request data.

GitHub references: [deploy-key API and revocation behavior](https://docs.github.com/en/rest/deploy-keys/deploy-keys),
[GitHub App installation tokens](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-an-installation-access-token-for-a-github-app).
