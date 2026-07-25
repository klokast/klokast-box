# Test Hetzner ops Automated Creation

## Summary

Run the test from the admin MacBook. Terraform creates the Hetzner server named ops; Ansible bootstraps it, enrolls it in Tailscale, installs the ops toolchain, generates a GitHub deploy key, and clones klokast-box so klokast-ops playbooks exist on ops.

## Steps

  1. Prepare credentials on the MacBook:

  cd ~/src/klokast/klokast-box
  git pull --ff-only

  export HCLOUD_TOKEN='...'
  export OPS_BOOTSTRAP_SSH_KEY_FILE="$HOME/.ssh/xiaoju_codex_hetzner"

  2. Create a reusable Tailscale auth key:

  - tag: tag:ops
  - reusable
  - pre-approved
  - not ephemeral

  3. Run static checks:

  cd ~/src/klokast/klokast-box/klokast-ops/terraform/hetzner-ops
  terraform fmt -check
  terraform init
  terraform validate

  cd ~/src/klokast/klokast-box/klokast-ops/ansible
  ansible-playbook --syntax-check playbooks/00-ops-server.yml \
    -e neo_password=dummy \
    -e tailscale_auth_key=dummy

  4. Create the Hetzner server:

  cd ~/src/klokast/klokast-box/klokast-ops/terraform/hetzner-ops
  terraform plan -out ops.tfplan
  terraform apply ops.tfplan
  terraform output -raw ipv4_address

  Optional hardening: pass -var 'bootstrap_ssh_source_ips=["YOUR_PUBLIC_IP/32"]' to plan if your current public IP is stable enough.

  5. Bootstrap with Ansible:

  cd ~/src/klokast/klokast-box/klokast-ops/ansible
  ansible-playbook \
    -i inventory/hosts.yml \
    playbooks/00-ops-server.yml \
    -e ansible_host="$(cd ../terraform/hetzner-ops && terraform output -raw ipv4_address)" \
    -e ansible_user=root \
    -e ansible_ssh_private_key_file="$OPS_BOOTSTRAP_SSH_KEY_FILE"

  When prompted:

  - set the local neo password
  - paste the Tailscale tag:ops auth key

  6. If Ansible stops with a GitHub deploy public key:

- copy only the public key it prints
- GitHub repo klokast/klokast-box > Settings > Deploy keys
- add the key with write access
- rerun the exact same Ansible command

  7. Verify:

```
tailscale ssh neo@ops 'hostnamectl --static && id neo && sudo -l'
tailscale ssh codex@ops 'id codex && codex --version'
tailscale ssh codex@ops 'ansible --version | head -n 1 && mosh --version | head -n 1 && tmux -V'
tailscale ssh codex@ops 'git -C ~/src/klokast/klokast-box status --short --branch'
tailscale ssh codex@ops 'test -f ~/src/klokast/klokast-box/klokast-ops/ansible/playbooks/00-ops-server.yml'
mosh neo@ops
```

  8. Re-run Ansible once to test idempotence.# Test Hetzner ops Creation

## Summary

Run the test from the admin MacBook. Terraform creates the Hetzner VM and firewall; Ansible bootstraps Ubuntu, enrolls Tailscale, installs the ops toolchain, generates a GitHub deploy key, and clones klokast-box.

# # Preflight

- Confirm Hetzner does not already have an unmanaged server named ops. If it does, stop and decide whether to import, destroy, or rename.
- In Tailscale, create a reusable, pre-approved, non-ephemeral auth key for tag:ops.
- Ensure you can add a deploy key with write access to klokast/klokast-box.
- On the MacBook:
```
cd ~/src/klokast/klokast-box
git pull --ff-only

export HCLOUD_TOKEN='...'
export OPS_BOOTSTRAP_SSH_KEY_FILE="$HOME/.ssh/xiaoju_codex_hetzner"
```

## Static Checks

```
cd ~/src/klokast/klokast-box/klokast-ops/terraform/hetzner-ops
terraform fmt -check
terraform init
terraform validate

cd ~/src/klokast/klokast-box/klokast-ops/ansible
ansible-playbook --syntax-check playbooks/00-ops-server.yml \
  -e neo_password=dummy \
  -e tailscale_auth_key=dummy
```

## Create And Bootstrap

```
cd ~/src/klokast/klokast-box/klokast-ops/terraform/hetzner-ops
terraform plan -out ops.tfplan
terraform apply ops.tfplan
terraform output -raw ipv4_address
```
  Then run Ansible:
```
cd ~/src/klokast/klokast-box/klokast-ops/ansible
ansible-playbook \
  -i inventory/hosts.yml \
  playbooks/00-ops-server.yml \
  -e ansible_host="$(cd ../terraform/hetzner-ops && terraform output -raw ipv4_address)" \
  -e ansible_user=root \
  -e ansible_ssh_private_key_file="$OPS_BOOTSTRAP_SSH_KEY_FILE" \
  -vv
```
  When prompted:

  - enter and confirm the local neo password
  - paste the Tailscale auth key for tag:ops
  - accept the initial Hetzner SSH host key if prompted

## GitHub Deploy Key Gate

On the first run, Ansible may stop and print a GitHub deploy public key.

In GitHub:

  1. Open klokast/klokast-box.
  2. Go to Settings > Deploy keys.
  3. Add the printed public key.
  4. Enable write access.
  5. Re-run the exact same ansible-playbook command.

Do not copy or move the private key from ops.

## Acceptance Checks

From the MacBook:
```
tailscale ssh neo@ops 'hostnamectl --static && id neo && sudo -l'
tailscale ssh codex@ops 'id codex && codex --version'
tailscale ssh codex@ops 'ansible --version | head -n 1 && mosh --version | head -n 1 && tmux -V'
tailscale ssh codex@ops 'git -C ~/src/klokast/klokast-box status --short --branch'
tailscale ssh codex@ops 'test -f ~/src/klokast/klokast-box/klokast-ops/ansible/playbooks/00-ops-server.yml'
mosh neo@ops
```

Then re-run the Ansible playbook once to confirm idempotence.

## Assumptions

- This test creates the real future ops server and keeps it running.
- Terraform state remains local in `klokast-ops/terraform/hetzner-ops`; preserve it outside git after success.
- Operator access after bootstrap is Tailscale SSH; OpenSSH client on ops is only for outbound GitHub Git-over-SSH.

