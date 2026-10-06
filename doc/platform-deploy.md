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
destination can be a standby controller.

Ansible check mode validates the source only. It skips destination validation
because it does not copy the checkout.

## Controller recovery

Before full power-off, record `ops-controller-ha status` and synchronize
non-provider private state to the standby. After recovery, start one controller
and use `platform-check-remote`. Emergency promotion requires the previous
active controller to be fenced; provider authority is then reseeded from the
operator workstation.

Synchronization includes app grants, recovery records, rollback material,
and the Instance checkout. A standby cannot perform controller operations
until the previous active controller is fenced and the new controller is marked active. Recheck the
Instance, accepted dom0 assignments, and recovery readiness after promotion.

See [Development controller operations](platform-syscalls.md) for the
current controller entry points.
