# Test Hetzner Ops Provisioning

This test creates `hetzner-ops`. Terraform creates the Hetzner VM and
firewall. Ansible sets both the system hostname and the Tailscale machine name
to `hetzner-ops`, enrolls it with `tag:infra`, installs the runner tools, and
clones `klokast-box`.

Run this procedure from the trusted MacBook. Do not run `terraform apply` as
part of a static source test.

## Prerequisites

- Confirm that Hetzner does not have an unmanaged server named
  `hetzner-ops`.
- Create a reusable, pre-approved, non-ephemeral Tailscale auth key for
  `tag:infra`.
- Confirm that you can add a write-enabled deploy key to
  `klokast/klokast-box`.
- Export `HCLOUD_TOKEN` and the bootstrap SSH key path on the MacBook.

```sh
cd ~/src/klokast/klokast-box
git pull --ff-only

export HCLOUD_TOKEN='...'
export OPS_BOOTSTRAP_SSH_KEY_FILE="$HOME/.ssh/xiaoju_codex_hetzner"
```

## Static checks

```sh
cd ~/src/klokast/klokast-box/klokast-ops/terraform/hetzner-ops
terraform fmt -check
terraform init -backend=false -lockfile=readonly -input=false
terraform validate
terraform test

cd ../../ansible
ansible-playbook --syntax-check playbooks/00-ops-server.yml \
  -e neo_password=dummy \
  -e tailscale_auth_key=dummy
```

## Create and bootstrap

This step changes cloud state. Review the plan before you apply it.

```sh
cd ~/src/klokast/klokast-box/klokast-ops/terraform/hetzner-ops
terraform plan -out hetzner-ops.tfplan
terraform apply hetzner-ops.tfplan
terraform output -raw ipv4_address

cd ../../ansible
ansible-playbook \
  -i inventory/hosts.yml \
  playbooks/00-ops-server.yml \
  -e ansible_host="$(cd ../terraform/hetzner-ops && terraform output -raw ipv4_address)" \
  -e ansible_user=root \
  -e ansible_ssh_private_key_file="$OPS_BOOTSTRAP_SSH_KEY_FILE" \
  -vv
```

Enter the local `neo` password and the `tag:infra` Tailscale auth key when the
playbook requests them. If the first run prints a GitHub deploy public key,
register only that public key with write access, and then run the same Ansible
command again. Do not copy the private key from `hetzner-ops`.

## Acceptance checks

Run these checks from the MacBook:

```sh
tailscale ssh neo@hetzner-ops 'test "$(hostname -s)" = hetzner-ops && id neo && sudo -l'
tailscale ssh codex@hetzner-ops 'id codex && codex --version'
tailscale ssh codex@hetzner-ops 'ansible --version | head -n 1 && mosh --version | head -n 1 && tmux -V'
tailscale ssh codex@hetzner-ops 'git -C ~/src/klokast/klokast-box status --short --branch'
tailscale ssh codex@hetzner-ops 'test -f ~/src/klokast/klokast-box/klokast-ops/ansible/playbooks/00-ops-server.yml'
mosh neo@hetzner-ops
```

Run the Ansible playbook again to confirm idempotence. Keep the Terraform state
in admin-controlled storage after the test.
