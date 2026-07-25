# Hetzner Ops MVP: Terraform + Ansible + Tailscale + Mosh + Tmux

This is the minimum viable deployment for the future deployment server named
`ops`. The current deployment server is `codex`; keep it running until `ops`
has passed the checks below.

It does six things:
- creates one Ubuntu 24.04 Hetzner VM named `ops`
- attaches a Hetzner Cloud firewall at server creation
- bootstraps the VM from the admin MacBook over public IPv4 SSH
- enrolls it into Tailscale as `tag:ops`
- installs the `neo` and `codex` users, Codex CLI, Ansible, mosh, and tmux
- generates a GitHub deploy key for `codex` and clones `klokast-box`

Terraform is an admin-side tool for this flow. It is not installed on `ops`.

## Access Model

Operator access to `ops` is Tailscale SSH: `tailscale ssh neo@ops` and
`tailscale ssh codex@ops`. Public IPv4 SSH is only the initial bootstrap path
before the server is enrolled in Tailscale.

`openssh-client` on `ops` does not create an operator login path. It provides
the outbound `ssh` binary that Git uses for `git@github.com:...` clone and
push operations with the GitHub deploy key.

## Success

Success means:
- Hetzner has one server named `ops`
- the server shape is `cx23` in `hel1`
- `hostnamectl --static` reports `ops`
- both local users exist: `neo`, `codex`
- `neo` can run `sudo` and is prompted for the `neo` password
- `codex` is password-locked and can run only the narrow root wrappers in
  `/usr/local/sbin/codex-*`
- `tailscale status --json` on the server reports `Running`
- the node is enrolled with `tag:ops`
- `tailscale ssh neo@ops` works from the MacBook
- `tailscale ssh codex@ops` works from the MacBook
- `codex --version` works as user `codex`
- `ansible --version`, `mosh --version`, and `tmux -V` work on `ops`
- `/home/codex/src/klokast/klokast-box` is cloned on `ops`
- the nested `klokast-ops` playbook exists in that checkout
- `mosh neo@ops` works from the MacBook
- the playbook can be run twice without failing

## Human Interactions

The human steps are explicit in this MVP:
- create or confirm the reusable, pre-approved Tailscale auth key for `tag:ops`
- confirm the tailnet ACL/grants allow SSH and mosh access to `tag:ops`
- run Terraform and Ansible from the admin MacBook
- trust the new host key when SSH first connects to the public IPv4
- type the local `neo` password when `ansible-playbook` prompts for it
- paste the Tailscale auth key when `ansible-playbook` prompts for it
- if the playbook stops with a GitHub deploy public key, register that key on
  `klokast/klokast-box` with write access and re-run the playbook
- run `codex login --device-auth` as user `codex` and complete the browser
  flow, including MFA if prompted

## Prerequisites On The MacBook

- Tailscale installed from the official `.pkg`, logged into the correct tailnet
- Terraform `>= 1.11`
- Ansible installed
- `mosh` and `tmux` installed
- the SSH private key matching Hetzner key `xiaoju_codex_hetzner`
- `HCLOUD_TOKEN` exported in the shell before running Terraform
- GitHub deploy keys enabled for the `klokast` organization
- permission to add a deploy key to the `klokast/klokast-box` repository

Example:

```bash
export HCLOUD_TOKEN='...'
export OPS_BOOTSTRAP_SSH_KEY_FILE="$HOME/.ssh/xiaoju_codex_hetzner"
```

## Tailscale Prerequisites

Create a key in the Tailscale admin console with these settings:
- reusable
- pre-approved
- not ephemeral
- tags: `tag:ops`

The real tailnet policy must allow the operator to reach `tag:ops` over SSH and
mosh. The public repo contains `klokast-ops/tailscale/policy.hujson.j2`;
the active controller renders the deployable policy to
`~/private/klokast/tailscale-policy.hujson`.

## Terraform

From [hetzner-ops](/home/codex/src/klokast/klokast-box/klokast-ops/terraform/hetzner-ops):

```bash
cd /path/to/klokast/klokast-box/klokast-ops/terraform/hetzner-ops
terraform init
terraform plan
terraform apply
terraform output -raw ipv4_address
```

The Terraform defaults are:
- hostname: `ops`
- server type: `cx23`
- location: `hel1`
- image: `ubuntu-24.04`
- public IPv4: enabled
- public IPv6: disabled
- bootstrap SSH key name: `xiaoju_codex_hetzner`
- bootstrap SSH source CIDRs: `0.0.0.0/0`

The Hetzner Cloud firewall permits only:
- inbound TCP 22 from `bootstrap_ssh_source_ips`
- inbound UDP 41641 for Tailscale direct connectivity

Outbound traffic remains unrestricted because no outbound firewall rules are
defined. Terraform state is local and ignored by git; keep it in
admin-controlled storage after `apply`, because it is needed to manage this
server later.

## Ansible

From [ansible](/home/codex/src/klokast/klokast-box/klokast-ops/ansible):

```bash
cd /path/to/klokast/klokast-box/klokast-ops/ansible
ansible-playbook \
  -i inventory/hosts.yml \
  playbooks/00-ops-server.yml \
  -e ansible_host="$(cd ../terraform/hetzner-ops && terraform output -raw ipv4_address)" \
  -e ansible_user=root \
  -e ansible_ssh_private_key_file="$OPS_BOOTSTRAP_SSH_KEY_FILE"
```

The playbook prompts for:
- the local password to set on `neo`
- the reusable Tailscale auth key for `tag:ops`

GitHub does not use Tailscale SSH. The playbook therefore generates
`/home/codex/.ssh/github-klokast-codex` on `ops`, pins GitHub SSH host keys,
checks whether GitHub accepts that deploy key, then clones
`git@github.com:klokast/klokast-box.git` into
`/home/codex/src/klokast/klokast-box`.

On the first run for a new `ops` server, the playbook can stop after printing
the public deploy key. In GitHub, add that public key to
`klokast/klokast-box` under Settings > Deploy keys, enable write access, then
re-run the same playbook. Do not move the private key through git or chat.

## Post-Deploy Checks

From the MacBook:

```bash
tailscale ssh neo@ops 'hostnamectl --static && id neo && sudo -l'
tailscale ssh codex@ops 'id codex && codex --version'
tailscale ssh codex@ops 'ansible --version | head -n 1 && mosh --version | head -n 1 && tmux -V'
tailscale ssh codex@ops 'git -C ~/src/klokast/klokast-box status --short --branch'
tailscale ssh codex@ops 'test -f ~/src/klokast/klokast-box/klokast-ops/ansible/playbooks/00-ops-server.yml'
tailscale ssh codex@ops 'sudo /usr/local/sbin/codex-tailscale-status >/dev/null && sudo /usr/local/sbin/codex-tailscale-prefs >/dev/null'
```

Then test the operator shell:

```bash
mosh neo@ops
tmux new -As ops
```

Then authenticate Codex:

```bash
tailscale ssh codex@ops
codex login --device-auth
```

## Tests

Run these checks on the MacBook before live provisioning:

```bash
cd /path/to/klokast/klokast-box/klokast-ops/terraform/hetzner-ops
terraform fmt -check
terraform init -backend=false -lockfile=readonly -input=false
terraform validate

cd /path/to/klokast/klokast-box/klokast-ops/ansible
ansible-playbook --syntax-check playbooks/00-ops-server.yml -e neo_password=dummy -e tailscale_auth_key=dummy
```

Run these checks after provisioning:
- re-run `ansible-playbook` once more and confirm it succeeds again
- verify `tailscale ssh neo@ops` and `tailscale ssh codex@ops`
- verify the `codex`, `ansible`, `mosh`, and `tmux` binaries
- verify the `klokast-box` checkout and nested `klokast-ops` playbook
- verify `mosh neo@ops`
- reboot `ops`, then re-check Tailscale SSH, mosh, tmux, Codex, and Ansible

## Next Iterations

The next hardening and ergonomics steps should be:
- narrow `bootstrap_ssh_source_ips` to the admin MacBook's current public CIDR
- disable or further restrict the public bootstrap SSH path after Tailscale is
  healthy
- add a host firewall on `ops`
- store future Tailscale auth keys on `ops` with root-only wrappers
- install GitHub CLI only if it becomes necessary for operator workflows
- add an OpenAI API-key backup flow for `codex`
