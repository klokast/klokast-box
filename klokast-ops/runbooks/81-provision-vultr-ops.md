# Provision Vultr Ops Infra-Agent

This creates `vultr-ops`, a Vultr-hosted Codex infra-agent. It is not the
trusted Platform controller. Trusted commands and secrets stay on `<box>-ops`;
`vultr-ops` connects to it through Tailscale SSH as `smith`.

## Prerequisites

- The current controller has Terraform, Ansible, `curl`, `jq`, and
  `shellcheck`.
- `/usr/local/sbin/ts-authkey-infra` exists on the controller and can
  mint `tag:infra` auth keys from root-only Tailscale OAuth material.
- The real Tailscale policy allows operator SSH to `agent@tag:infra`.
- The GitHub token can create deploy keys on `klokast/klokast-box`.
- The Vultr account has quota for one `vc2-1c-1gb` instance in `icn`.

Export controller-only secrets:

```sh
export VULTR_API_KEY='...'
export GITHUB_TOKEN='...'
export VULTR_OPS_BOOTSTRAP_SSH_KEY_FILE="$HOME/.ssh/klokast-infra-vultr-ops"
export VULTR_OPS_BOOTSTRAP_SSH_KEY_NAME='klokast-infra-vultr-ops'
```

Restrict public bootstrap SSH. If not set, the wrapper attempts to use the
controller's current public IPv4 as `/32`.

```sh
export VULTR_OPS_BOOTSTRAP_SSH_IPV4_CIDRS='203.0.113.10/32'
export VULTR_OPS_BOOTSTRAP_SSH_IPV6_CIDRS='2001:db8::10/128'
```

## Static Checks

```sh
cd ~/src/klokast/klokast-box

terraform -chdir=klokast-ops/terraform/vultr-ops fmt -check -recursive
terraform -chdir=klokast-ops/terraform/vultr-ops init -backend=false -input=false
terraform -chdir=klokast-ops/terraform/vultr-ops validate
terraform -chdir=klokast-ops/terraform/vultr-ops test

cd klokast-ops/ansible
ansible-playbook --syntax-check playbooks/01-vultr-ops-infra-agent.yml \
  -e tailscale_auth_key=dummy
cd ../..

shellcheck klokast-ops/bin/provision-vultr-ops
```

## Provision

Preview the Terraform changes only:

```sh
klokast-ops/bin/provision-vultr-ops --plan-only
```

Create or update the instance and converge it with Ansible:

```sh
klokast-ops/bin/provision-vultr-ops
```

What the wrapper does:

- uses checked-in Vultr OS ID `2760` for `Ubuntu 26.04 LTS x64`;
- imports the existing Vultr SSH key named `klokast-infra-vultr-ops` into
  Terraform state when needed;
- runs Terraform from `klokast-ops/terraform/vultr-ops`;
- fails before Tailscale enrollment if the exact `vultr-ops` MagicDNS name is
  already held by a stale or incompatible tailnet device;
- mints a short-lived single-use Tailscale auth key for `tag:infra`;
- runs `playbooks/01-vultr-ops-infra-agent.yml` over bootstrap SSH;
- uses OpenSSH `StrictHostKeyChecking=accept-new` for that first bootstrap
  connection, so a new host key can be recorded but a changed key is refused;
- registers the generated GitHub deploy public key using controller-side
  `GITHUB_TOKEN`;
- clones `klokast/klokast-box` as `/home/agent/src/klokast/klokast-box`.

## Acceptance Checks

```sh
tailscale ssh neo@vultr-ops 'hostnamectl --static && id neo && sudo -l'
tailscale ssh agent@vultr-ops 'id agent && codex --version'
tailscale ssh agent@vultr-ops 'ansible --version | head -n 1 && mosh --version | head -n 1 && tmux -V'
tailscale ssh agent@vultr-ops 'go version && command -v gofmt'
tailscale ssh agent@vultr-ops 'git -C ~/src/klokast/klokast-box status --short --branch'
tailscale ssh agent@vultr-ops 'test ! -f /etc/klokast/tailscale-policy.env'
tailscale ssh agent@vultr-ops 'test ! -d /etc/tailscale-auth'
```

Confirm the Tailscale machine has `tag:infra` and not `tag:ops`.

Then rerun the wrapper once to verify idempotence.

## GitHub Deploy Key Gate

The normal path is automatic registration. If GitHub registration fails, add
only the printed public key to `klokast/klokast-box` as a deploy key with write
access, then rerun the wrapper. Do not copy the private key off `vultr-ops`.

## State

Terraform state stays local in `klokast-ops/terraform/vultr-ops` and is
ignored by git. Preserve it in controller-controlled private storage after a
successful apply.
