This directory contains the automated path for the future deployment server
named `ops`.

Use the `mosh` + `tmux` path as the current supported operator workflow.

Automation lives here:
- Terraform: `/home/codex/src/klokast/klokast-box/klokast-ops/terraform/hetzner-ops`
- Vultr infra-agent Terraform: `/home/codex/src/klokast/klokast-box/klokast-ops/terraform/vultr-ops`
- Ansible: `/home/codex/src/klokast/klokast-box/klokast-ops/ansible`
- Runbook: `/home/codex/src/klokast/klokast-box/klokast-ops/runbooks/20-hetzner-ops-mosh-tmux-mvp.md`
- Vultr infra-agent runbook: `/home/codex/src/klokast/klokast-box/klokast-ops/runbooks/81-provision-vultr-ops.md`

- Hetzner VM: `ops`
- Vultr VM: `vultr-ops`, an authoritative AI runner with `tag:infra`; it is
  not the Ansible execution locus or Platform credential custodian
- users: `neo` and `codex`
- Vultr users: `neo` and `agent`
- optional Vultr coder guest users are provisioned with
  `klokast-ops/bin/provision-vultr-coder-guest`; they must not receive
  Platform private state, controller credentials, or another user's Codex auth.
- access: Tailscale SSH plus mosh and tmux
- Codex toolchain on `vultr-ops`: `git`, `bubblewrap`, Node LTS, and
  `@openai/codex`. Deployable Go artifacts are built by the controller-owned
  ephemeral builder. A dependency-maintenance task may unpack a
  checksum-pinned Go toolchain below the airunner's temporary directory, but
  must remove it afterward and must not publish binaries built there.
- `vultr-ops` packages are declared in
  `klokast-ops/ansible/inventory/group_vars/all.yml`.
- `<box>-ops` controller packages are declared in
  `ansible/inventory/group_vars/ops.yml`.
- deployment toolchain on `ops`: Ansible, not Terraform

For reference, the older manual runbook:
`/home/codex/src/klokast/klokast-box/klokast-ops/runbooks/03-hetzner-mosh-tmux-on-ops.md`
