# Platform Deployment

Platform deployment starts from the operator workstation, provisions an
authoritative `<box>-ops` controller, and then runs checked-in Ansible
workflows from that controller. Controllers consume the public
`klokast/klokast-box` source repository over HTTPS. Deployment bindings and
private state stay under `~/private/klokast/`.

Guest installation should be unattended. Steady-state infrastructure and
service VMs should come from versioned Alpine templates cloned onto dom0 LVM
storage; deployment then clones, attaches, boots, and finalizes identity and
network details.

## Retire the fixed shared user VM

The [user VM model](architecture.md#box-usr-slug) uses dedicated VMs.
Inspect fixed guest and disk ownership from the active controller first:

```sh
ANSIBLE_CONFIG=ansible/ansible.cfg ansible-playbook -vv \
  -i ansible/execution-inventory/hosts ansible/playbooks/75-platform-usr-retirement.yml
```

After authorization to delete the fixed VM and its data, run the same command
with `-e '{"platform_usr_retirement_apply":true}'`. The playbook removes only
the exact fixed guest, proven exclusive disks and boot files, and its exact
offline Tailnet identity. It also installs the enrollment validator on the
controllers. Ambiguous storage, snapshots, mounted disks, or failed shutdown
stop deletion. Dedicated VMs and the zone remain intact.

The playbook does not replace the controller CLI or rewrite accepted router
generations. Deploy the inventory change with the controller's supported
upgrade path. Network convergence must respect the protected router assignment;
retirement does not permit checksum changes to an accepted generation.

## Dom0 provisioning entrypoints

Run `ansible/bin/provision-box --box BOX` on the active controller. It owns
phase selection, inventory generation, operator gates, Ansible execution,
logs, and temporary-file cleanup.

`ansible/bin/bootstrap-dom0 --node BOX` is a compatibility entrypoint for
the same runner. It defaults to phases 10–22 and accepts only the bootstrap
phases in that range. It requires typed confirmation before phase 11 wipes
the SSD; it does not accept `--yes`.

Both entrypoints use the existing Platform installation lock. Controller
setup creates this lock; a missing, unsafe, or busy lock stops execution.
`--dry-run-plan` does not acquire the lock or run provisioning phases.
Both entrypoints write phase logs under
`~/private/klokast/logs/provision-box/`.

`--private-state-root DIR` overrides `KLOKAST_PRIVATE_ROOT`, which defaults
to `~/private/klokast`. Phase 20 loads `dom0-console.yml` from the selected
root. Resume instructions use `provision-box --box BOX` and include the
selected `--to` limit. Reapply any custom inventory, private-state, or other
options when you resume.

## Dom0 console recovery

NanoKVM recovery requires local console login as `neo` with a per-box password
held by the human operator. Tailscale SSH is the normal path, but it cannot be
the only recovery path. Blank root console access is forbidden, and the
`root` password is locked.

The NanoKVM recovery invariant is represented outside Git:

```yaml
# ~/private/klokast/dom0-console.yml
dom0_console_password_hashes:
  <box-id>: "$6$..."
```

Only hashes are installed on dom0. Health checks fail closed when `neo` lacks
a usable password hash or when root is not locked.

## Controller private-state transfer

Controller provisioning validates the source
[Instance checkout](klokast-instance-specification.md) before it copies private
state. It validates the destination checkout before it transfers provider
credentials. Both checks use the offline `klokast check` command, so the
destination can be a standby controller. The legacy `platform-resources.yml`
file is not required.

Ansible check mode validates the source only. It skips destination validation
because it does not copy the checkout.

## Controller recovery

Before full power-off, record `ops-controller-ha status` and synchronize
non-provider private state to the standby. After recovery, start one controller
and use `platform-check-remote`. Emergency promotion requires the previous
active controller to be fenced; provider authority is then reseeded from the
operator workstation.

Synchronization includes app grants, native VM update records, rollback
material, and the Instance checkout. It does not copy operation signers or
approval ledgers. A standby cannot execute updates until the previous active
controller is fenced and the new controller is marked active. Recheck the
Instance, accepted dom0 assignments, and recovery readiness after promotion.

See [Development controller operations](platform-syscalls.md) for the
controller migration and current entry points.
