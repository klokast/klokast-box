# Provision Vultr Coder Guest

This provisions a separate coding account on `vultr-ops` for a trusted family
member. `vultr-ops` remains an authoritative AI runner, not the Platform
execution locus or credential custodian. Do not copy Platform private state,
controller credentials, Tailscale OAuth material, GitHub tokens, or another
user's Codex auth into the guest account.

## Current Mulan Defaults

- Unix account: `ml-agent`
- Host: `vultr-ops`
- GitHub repository: `klokast/mulan-projects`
- Repository visibility: private
- Codex authentication: per-user device login after provisioning

## MacBook Prerequisites

- Tailscale is logged into the operator tailnet identity.
- The tailnet policy allows operator SSH to `ml-agent@tag:infra`.
- `GITHUB_TOKEN` can create private repositories in `klokast` and manage deploy
  keys on `klokast/mulan-projects`.
- `curl`, `jq`, `tar`, and `tailscale` are available.

## Provision

From the MacBook checkout:

```sh
cd ~/src/klokast/klokast-box
git pull --ff-only
export GITHUB_TOKEN='...'

klokast-ops/bin/provision-vultr-coder-guest
```

The wrapper:

- creates private repo `klokast/mulan-projects` if it does not already exist;
- stages only checked-in non-secret Ansible files to a temporary directory on
  `vultr-ops`;
- creates locked user `ml-agent`;
- installs the same Codex CLI and config defaults used by the main `agent`
  account, scoped to `/home/ml-agent/src/klokast/mulan-projects`;
- generates `/home/ml-agent/.ssh/github-klokast-mulan-projects` on
  `vultr-ops`;
- registers only the public key as a write deploy key on
  `klokast/mulan-projects`;
- clones the repository as `ml-agent`.

## Verification

From the MacBook:

```sh
tailscale ssh ml-agent@vultr-ops 'id && codex --version'
tailscale ssh ml-agent@vultr-ops 'git -C ~/src/klokast/mulan-projects status --short --branch'
tailscale ssh ml-agent@vultr-ops 'test ! -e ~/.codex/auth.json'
tailscale ssh ml-agent@vultr-ops 'sudo -n true'
```

The final command must fail. `ml-agent` should not have broad sudo access.

Then authenticate Codex as Mulan:

```sh
tailscale ssh ml-agent@vultr-ops
codex login --device-auth
```

Complete the browser flow as Mulan. Do not copy `/home/agent/.codex/auth.json`
or OpenAI API keys into `/home/ml-agent`.

## Later Tailscale Access For Mulan

Mulan does not have a Tailscale login yet. Once she joins the tailnet, update
`~/private/klokast/tailscale-policy.hujson` from the active controller:

- add her exact login to a dedicated group such as `group:coder-guests`;
- grant `group:coder-guests` access to `tag:infra` on `tcp:22` and the
  mosh UDP range;
- add an SSH rule allowing that group to connect only as `ml-agent`;
- validate, commit, push, and apply the policy with the root-owned
  `ts-policy-*` wrappers.
