The `klokast-ops/terraform/vultr-ops` code is intended to run from the current Ansible controller (typically before migration it is user `agent` on `hetzner-ops`  ; after migration it is user `klokast-agent` on `<box>-ops`.
It runs from whichever machine is acting as controller and has Terraform, Ansible, Tailscale wrapper access, `VULTR_API_KEY`, `GITHUB_TOKEN`, and the bootstrap SSH key.

The `vultr-ops` provisioned by this code is a cloud AI runner and persistent Control TCB authority. It runs Codex CLI as user `agent`, uses Tailscale tag `tag:infra`, and reaches `<box>-ops` as `smith`. It is not the Ansible execution locus or Platform credential custodian.

Provisioning currently runs from Hetzner Codex unless invoked from `<box>-ops`. After migration, trusted Ansible/Terraform workflows execute on `<box>-ops`, while every approved runner remains part of the Control TCB.

# Apply Codex CLI config changes

To apply a checked-in Codex CLI configuration change, such as the OpenAI Codex
CLI TUI status bar, rerun the provisioning wrapper from the current controller.
The wrapper is intended to be idempotent, but it still runs Terraform
init/plan/apply and reconverges the full Ansible playbook.

From the Hetzner `codex` machine as user `codex`:

```sh
cd /home/codex/src/klokast/klokast-box
git pull --ff-only

export VULTR_API_KEY='...'
export GITHUB_TOKEN='...'
export VULTR_OPS_BOOTSTRAP_SSH_KEY_FILE="$HOME/.ssh/klokast-infra-vultr-ops"
export VULTR_OPS_BOOTSTRAP_SSH_KEY_NAME='klokast-infra-vultr-ops'

klokast-ops/bin/provision-vultr-ops
```

If those environment variables are already present in the shell, only rerun:

```sh
cd /home/codex/src/klokast/klokast-box
git pull --ff-only
klokast-ops/bin/provision-vultr-ops
```

Verify that the config was written for user `agent` on `vultr-ops`:

```sh
tailscale ssh agent@vultr-ops 'grep -A2 "^\[tui\]" ~/.codex/config.toml'
```

# Main entrypoint

- klokast-ops/bin/provision-vultr-ops
    - Orchestrates everything.
    - Runs Terraform in klokast-ops/terraform/vultr-ops.
    - Mints a one-off Tailscale key with /usr/local/sbin/ts-authkey-infra.
    - Runs Ansible playbook klokast-ops/ansible/playbooks/01-vultr-ops-infra-agent.yml.
    - Requires controller-side VULTR_API_KEY, GITHUB_TOKEN, and VULTR_OPS_BOOTSTRAP_SSH_KEY_FILE.

# Terraform definition

- `klokast-ops/terraform/vultr-ops/main.tf`
    - Creates/imports Vultr SSH key.
    - Creates Vultr firewall group.
    - Allows bootstrap SSH from selected CIDRs.
    - Allows Tailscale `UDP 41641`.
    - Creates vultr_instance.ops.
- `klokast-ops/terraform/vultr-ops/variables.tf`
    - Defaults:
        - hostname/label: `vultr-ops`
        - region: icn Seoul
        - plan: `vc2-1c-1gb`
        - OS ID: `2760`, documented as Ubuntu 26.04 LTS x64
        - Vultr tags: `klokast`, `infra-agent`
- `klokast-ops/terraform/vultr-ops/outputs.tf`
      - Exposes instance ID, hostname, IPv4, IPv6, firewall group ID, SSH key ID.
  - `klokast-ops/terraform/vultr-ops/versions.tf`
      - Pins Terraform provider `vultr/vultr`.
  - `klokast-ops/terraform/vultr-ops/tests/vultr_ops.tftest.hcl`
      - Static shape tests for hostname, region, plan, OS, firewall rules.

# Ansible definition

- `klokast-ops/ansible/playbooks/01-vultr-ops-infra-agent.yml`
    - Target group: `ops`.
    - Sets:
        - ops_hostname: `vultr-ops`
        - ops_agent_user: `agent`
        - ops_tailscale_tags: [`tag:infra`]
        - exact hostname required
        - GitHub deploy key auto-registration enabled
    - Applies roles:
        - ubuntu-base
        - user-accounts
        - sudo-policy
        - tailscale-client
        - codex-cli
        - source-repositories
        - interactive-shell
- `klokast-ops/ansible/inventory/hosts.yml`
    - Defines placeholder host ops-bootstrap.
    - The wrapper injects real ansible_host, SSH key, and Tailscale auth key via a temporary extra-vars file.
- `klokast-ops/ansible/inventory/group_vars/all.yml`
    - Shared defaults for ops-like cloud hosts: packages, users, GitHub repo, deploy key paths, Codex install, allowed root wrappers.

# Important roles

- klokast-ops/ansible/roles/tailscale-client
    - Runs tailscale up --hostname vultr-ops --advertise-tags tag:infra --ssh.
- klokast-ops/ansible/roles/codex-cli
    - Installs nvm, Node LTS, and @openai/codex for user agent.
- klokast-ops/ansible/roles/source-repositories
    - Generates /home/agent/.ssh/github-klokast-box.
    - Uses controller-side GITHUB_TOKEN to register the public key.
    - Clones klokast/klokast-box into /home/agent/src/klokast/klokast-box.
- klokast-ops/ansible/roles/sudo-policy
    - Gives agent only narrow root wrappers:
        - codex-apt-update
        - codex-apt-upgrade
        - codex-tailscale-status
        - codex-tailscale-prefs

# Tailscale policy

- `klokast-ops/tailscale/policy.hujson.j2` (public topology template)
- `~/private/klokast/tailscale-policy.hujson` (rendered private policy, controller only)
    - Defines tag:infra.
    - Allows tag:infra -> tag:ops SSH as smith.
    - This is the key design point: vultr-ops can act as remote terminal into <box>-ops, but should not itself hold TCB credentials.
