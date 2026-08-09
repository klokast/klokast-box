# Vultr Ops Infra-Agent Specification

`vultr-ops` is a cloud AI runner and persistent Control TCB authority. It is
not the Ansible execution locus or Platform credential custodian. It runs
OpenAI Codex CLI as Unix user `agent` and reaches the controller over Tailscale:

```sh
tailscale ssh smith@<box>-ops
```

`<box>-ops` keeps Platform private state, executes Terraform/Ansible workflows,
and holds Tailscale OAuth material. `vultr-ops` must not store Platform
infrastructure-provider API credentials, controller credentials, or private
deployment state. Its own Git write key and AI authentication remain
runner-scoped credentials and make it authoritative. Custody separation reduces
secret exposure but does not make the runner non-authoritative.

## Instance

- provider: Vultr
- region: Seoul, KR (`icn`)
- plan: shared CPU `vc2-1c-1gb`
- OS: `Ubuntu 26.04 LTS x64`, Vultr OS ID `2760`
- automatic backups: disabled
- hostname: `vultr-ops`
- label: `vultr-ops`
- connectivity: public IPv4 and public IPv6
- Tailscale tag: `tag:infra`
- Vultr tags: `klokast`, `infra-agent`
- bootstrap SSH key: `klokast-infra-vultr-ops`

Terraform stores only non-secret infrastructure state. The Ubuntu OS ID is
checked into the Terraform defaults so the provisioning wrapper does not depend
on a live OS-name lookup.

## Secrets And API Keys

The provisioning wrapper is `klokast-ops/bin/provision-vultr-ops`.

Required controller-side environment:

```sh
export VULTR_API_KEY='...'
export GITHUB_TOKEN='...'
export VULTR_OPS_BOOTSTRAP_SSH_KEY_FILE="$HOME/.ssh/klokast-infra-vultr-ops"
```

Optional:

```sh
export VULTR_OPS_BOOTSTRAP_SSH_PUBLIC_KEY_FILE="$VULTR_OPS_BOOTSTRAP_SSH_KEY_FILE.pub"
export VULTR_OPS_BOOTSTRAP_SSH_KEY_NAME='klokast-infra-vultr-ops'
export VULTR_OPS_BOOTSTRAP_SSH_IPV4_CIDRS='203.0.113.10/32'
export VULTR_OPS_BOOTSTRAP_SSH_IPV6_CIDRS='2001:db8::10/128'
export VULTR_OPS_OS_ID='2760'
export VULTR_OPS_OS_NAME='Ubuntu 26.04 LTS x64'
```

Credential handling:

- `VULTR_API_KEY` is read by Terraform from the controller environment only.
- `GITHUB_TOKEN` is read by Ansible on the controller only to register the
  public deploy key generated on `vultr-ops`.
- Tailscale enrollment uses the existing controller wrapper:
  `/usr/local/sbin/ts-authkey-infra --hostname vultr-ops --tags tag:infra`.
- Before minting the auth key, the provisioning wrapper checks local
  `tailscale status --json` for an existing exact `vultr-ops` MagicDNS owner
  with a stale or incompatible identity. It fails before enrollment instead of
  accepting a suffixed name such as `vultr-ops-1`.
- If a Vultr SSH key named `klokast-infra-vultr-ops` already exists, the
  wrapper imports it into Terraform state before planning.
- The Tailscale auth key is short-lived, single-use, written only to a
  temporary Ansible extra-vars file, and deleted by the wrapper on exit.
- No API key, auth key, OAuth secret, or GitHub token is passed through
  Terraform variables, cloud-init, checked-in inventory, or git.

## Users And Access

- `root`, `neo`, and `agent` passwords are locked.
- `neo` can use passwordless sudo for break-glass maintenance.
- `agent` runs Codex CLI and may use only narrow root-owned wrappers such as
  `codex-apt-update`, `codex-apt-upgrade`, `codex-tailscale-status`, and
  `codex-tailscale-prefs`.
- Operator access is Tailscale SSH, then `mosh` and `tmux`.
- Public SSH exists only as the bootstrap path and is restricted by the Vultr
  firewall to the configured source CIDRs.

## Tooling

Installed packages include:

- `ansible`
- `bubblewrap`
- `build-essential`
- `ca-certificates`
- `curl`
- `git`
- `jq`
- `mosh`
- `nodejs`
- `npm`
- `shellcheck`
- `tailscale`
- `tmux`
- `vim`

`golang-go` and `nano` are removed. Deployable Go builds run in the
controller-owned ephemeral Xen builder. A checksum-pinned temporary Go
toolchain may be used only for dependency maintenance and is removed after the
task. `nvm`, current Node.js LTS, and `@openai/codex` are installed under
`/home/agent`.

For both `neo` and `agent`:

- `EDITOR=vi`, `VISUAL=vi`, and `GIT_EDITOR=vi`
- interactive bash attaches to tmux session `main`
- tmux uses mouse mode, a large history limit, vi copy mode, and clipboard
  integration

## GitHub

The playbook generates the deploy key on `vultr-ops`:

```text
/home/agent/.ssh/github-klokast-box
```

Only the public key is registered in GitHub. The controller-side `GITHUB_TOKEN`
needs permission to create a deploy key with write access on
`klokast/klokast-box`. The repository is cloned to:

```text
/home/agent/src/klokast/klokast-box
```

Codex configuration is written to:

```text
/home/agent/.codex/config.toml
```

## Tests

Before provisioning:

```sh
cd klokast-ops/terraform/vultr-ops
terraform fmt -check -recursive
terraform init -backend=false -input=false
terraform validate
terraform test

cd ../../ansible
ansible-playbook --syntax-check playbooks/01-vultr-ops-infra-agent.yml \
  -e tailscale_auth_key=dummy

cd ../..
shellcheck klokast-ops/bin/provision-vultr-ops
```

After provisioning:

```sh
tailscale ssh neo@vultr-ops 'hostnamectl --static && sudo -l'
tailscale ssh agent@vultr-ops 'id && codex --version'
tailscale ssh agent@vultr-ops 'test ! -e /usr/bin/go && test ! -e /usr/bin/gofmt'
tailscale ssh agent@vultr-ops 'git -C ~/src/klokast/klokast-box status --short --branch'
tailscale ssh agent@vultr-ops 'test ! -f /etc/klokast/tailscale-policy.env'
```

Then rerun `klokast-ops/bin/provision-vultr-ops` to verify idempotence.
