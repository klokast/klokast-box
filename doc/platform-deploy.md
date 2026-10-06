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

## Independent controller setup

Each controller clones the public implementation over HTTPS. Setup generates
its own Instance read key at `/home/smith/.ssh/github-klokast-instance` and
shows the public key. Register that public key as a read-only deploy key in
the private GitHub Instance repository. Keep the private key on its controller.

On the active controller, set `KLOKAST_INSTANCE_ORIGIN` to the Instance SSH
origin, for example `git@github.com:OWNER/klokast-instance.git`, then run
`converge-ops-controller --box BOX`. Convergence clones or fast-forwards
`~/private/klokast/instance` and validates it with offline `klokast check`.
It refuses local edits, a different origin, or a branch other than `main`.
Use convergence after key registration; do not repeat VM cloning.

The operator creates two separate Tailscale OAuth clients for each controller
and installs them from the workstation before promotion. See
[credential setup](../klokast-ops/runbooks/40-tailscale-wrapper-setup-policy.md).
Setup does not copy private state or credentials from another controller.

## Controller recovery

Before full power-off, record `ops-controller-ha status`. Check that the
standby has current, clean implementation and Instance checkouts and its own
credentials. Controller recovery follows the
[controller authority model](architecture.md#controller).

For a planned handoff, commit and push the new active and standby placement
in the private Instance repository through the operator's ordinary Git
workflow. Pull that commit on the destination with `git pull --ff-only`.
Then run `ops-controller-ha switchover --old-active OLD --new-active NEW`.
The command validates the destination offline and demotes the old controller
before it activates the new one.

For emergency promotion, fence the previous active controller first. Update
and pull Instance placement as above, then run
`ops-controller-ha promote --old-active OLD --new-active NEW --old-active-fenced`.
The destination checks Git, Instance placement, and its own credentials before
activation. It does not need files from the failed controller. After promotion,
run `platform-check-remote --box BOX --target dom0` for fresh host inspection. Existing target-side
checks still stop operations that conflict with pending host transactions.

See [Development controller operations](platform-syscalls.md) for the
current controller entry points.
