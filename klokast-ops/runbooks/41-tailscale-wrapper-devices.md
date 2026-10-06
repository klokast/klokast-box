# Tailscale device lifecycle

The controller Ansible role installs `ts-devices-list` and
`ts-device-delete-stale` as root-owned wrappers, with narrow privilege rules.
The active controller holds the separate root-only device OAuth credential.
See [credential setup](40-tailscale-wrapper-setup-policy.md).

Use the owning workflow for normal cleanup. As `smith` on the active
controller, preview stale identities for one declared box:

```sh
cd ~/src/klokast/klokast-box
ANSIBLE_CONFIG=ansible/ansible.cfg ansible-playbook \
  -i ansible/execution-inventory/hosts \
  ansible/playbooks/68-vm-tailscale-stale-machines.yml --limit boxa
```

Inspect the exact offline blockers in the preview. After deletion is authorized,
run the same playbook with `tailscale_stale_machine_apply=true` and
`tailscale_stale_machine_confirm=delete stale tailscale machines for boxa`.

The role deletes only exact-name offline matches with the expected tag. The
installed delete wrapper re-fetches and checks ID, hostname, tag, and offline
state before calling the API. Online devices and unknown state are preserved.

For whole-box decommission, use `ansible/bin/decommission-box --box BOX` and
its preview and operator gates. See the [CLI index](../../doc/cli-tools.md).
An OAuth authentication failure requires credential correction; do not substitute
an unrelated credential or add broad privileges.
