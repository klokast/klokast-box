# Public GitHub metadata

The [upstream metadata authority model](../../doc/architecture.md#public-upstream-metadata-credentials)
owns token scope, custody and recovery. This procedure installs the existing
controller capability. It does not change VPN routing or grant private Git access.

## Install the command

Run on the active controller as `smith`, from the clean, current public checkout:

```sh
cd ~/src/klokast/klokast-box
ANSIBLE_CONFIG=ansible/ansible.cfg ansible-playbook -vv \
  -i ansible/execution-inventory/hosts \
  ansible/playbooks/67-ops-github-metadata.yml --limit BOX-ops
```

Normal controller tool convergence also installs the capability. Each target
must have a clean development checkout and an existing controller identity.

## Create and install one token per controller

In your human GitHub account, open **Settings → Developer settings → Personal
access tokens → Fine-grained tokens**. Create a token named for the controller,
for example `Klokast k001 public metadata`. Choose an expiration, select
**Public repositories (read-only)**, and grant no additional repository,
organization or account permissions. Keep it in the human password manager.
The broker checks API usability; it cannot inspect the complete permission
selection of a fine-grained token.

On the Mac, run the installer from the updated checkout. Enter the token at its
hidden prompt. Do not put the token in chat, shell arguments or an environment file.

```sh
klokast-dev/bin/install-github-metadata-token --controller k001-ops
```

Repeat with a different token for `k002-ops` when needed. Distinct tokens permit
separate revocation; tokens for one user still share that user's API allowance.
The installer checks the fixed release endpoint before replacing the previous
credential. It sends the token through SSH stdin and saves it only on its owning
controller. An optional `--token-file PATH` reads a private local file instead
of the prompt; keep that file outside Git with mode `0600`.

On the owning controller, inspect configuration without a network request:

```sh
ansible/bin/secret-authority github-metadata status
```

Fetch the current release through the fixed source catalog:

```sh
ansible/bin/secret-authority github-metadata fetch --source codex-stable
```

Codex image preparation and the final no-downgrade check use this same path.
They stop with an installation message when the token is absent. They do not
fall back to an anonymous request or to an unverified saved release.

For an already-declared optional VPN download client, the public Python client
can run within `platform-vpn-egress with-proxy` on the same box. The client
passes only that temporary loopback proxy port to the broker. The token remains
inside the broker. No proxy setting is installed globally.

## Rate limits and recovery

On a rate-limit response, use the UTC retry time in the error message. Calls
before that time do not contact GitHub. Restarting a caller does not clear the
recorded wait. After the wait, unchanged authenticated responses reuse the
saved metadata. Network failure does not permit use of an old release.

Events are in `/var/log/klokast/secret-authority.jsonl`. The root-only cache and
operation lock are in `/var/lib/klokast/github-metadata`. Logs contain the source,
HTTP outcome and retry time, not the token or provider response bodies.

For expiry or compromise, revoke the affected token in GitHub and run the
installer with a new dedicated token. A failed token check preserves the old
credential. Same-controller replacement preserves the token with the existing
credential allowlist. Reconstruct each controller independently; never copy a
token or cache from another controller.

GitHub references: [token creation and permissions](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens),
[release endpoint](https://docs.github.com/en/rest/releases/releases#get-the-latest-release),
and [retry and conditional request rules](https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api).
