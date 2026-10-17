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

Router phases 30 and 31 call `provision-router` to prepare and accept the
first router. Resume through this runner, not through individual enrollment
or boot commands. See [router provisioning and recovery](platform-updates.md#provisioning-and-boot-recovery)
for retained records and the deployment gate for the extracted installer.

Run SSD wipe/bootstrap phases separately from shared guest phases 41–43.
Establish the target controller before shared guest creation.

BAK phase 41, DMZ phase 42 and IoT phase 43 use
`platform-update provision --box BOX --role ROLE` with `bak`, `dmz` or `iot`.
All three phases require the target box's own controller for image preparation.
Check this prerequisite before selected provisioning phases change state.
Initial controller setup is separate work. Shared guest creation uses the same
image and configuration as replacement; see
[shared guest storage and replacement](platform-updates.md#dmz-creation-and-reconstruction).
Phase 69 excludes all three roles because their image workflow owns
configuration and enrollment.

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
and installs them from the workstation during initial controller setup. See
[credential setup](../klokast-ops/runbooks/40-tailscale-wrapper-setup-policy.md).
Setup does not copy private state or credentials from another controller.

## Controller recovery

Before full power-off, record `ops-controller-ha status`. Check that the
standby has current, clean implementation and Instance checkouts, working
recovery access, and independently provisioned credentials. Controller recovery follows the
[controller authority model](architecture.md#controller).

For a planned handoff, run `ops-controller-ha switchover --old-active OLD
--new-active NEW` from the old active controller. The command checks both
controllers, waits for the existing operation and image locks, publishes and
synchronizes the Instance placement, verifies demotion, and activates the new
controller. Repeat with the reported operation ID after an interruption.
Routine switching retains independent credentials on both controllers; it does
not require provider revocation or a new credential installation. Initial setup
must finish before switching. See the [authority model](architecture.md#controller).
The public repository check can use the existing temporary `with-proxy`
download command if direct GitHub access fails and the controller is a
declared optional VPN client. This adds no persistent proxy setting. Instance
Git access continues to use that controller's own SSH credential.

Install both public controller contacts on runners with
`ansible-playbook -vv -i ansible/execution-inventory/hosts
ansible/playbooks/69-air-controller-contacts.yml` from the active controller.
The resolver reads JSON contacts from `~/.config/klokast/controller-ha.yml`
without additional Python packages. Contacts are transport hints only. Exactly
one contacted controller must return a verified Instance-backed active response.
Zero or conflicting active responses stop dispatch.

For emergency promotion, fence the previous active controller first. Update
and pull Instance placement as above, then run
`ops-controller-ha promote --old-active OLD --new-active NEW --old-active-fenced`.
The destination checks Git and Instance placement before activation. Revoke
the failed controller's provider clients first. Install and verify the new
controller's independent credentials after activation. It does not need files
from the failed controller. After completed promotion,
run `platform-check-remote --box BOX --target dom0` for fresh host inspection. Existing target-side
checks still stop operations that conflict with pending host transactions.

See [Development controller operations](platform-syscalls.md) for the
current controller entry points.

## Routine controller replacement

Controllers use the [common VM lifecycle](platform-updates.md#controller-and-runner-lifecycle-migration).
Use `platform-update update --box BOX --role ops` from the active peer. Move
authority through the planned handoff before replacing a current
active controller. Do not stop runner VMs as part of controller replacement.

First migration and routine replacement share image qualification, frozen inputs,
retained data, reboot checks, recovery and generation retirement with other VMs.
Use `--retire-older` on the common update command to inspect or apply cleanup.
Use `converge-ops-controller --box BOX` for explicit configuration changes; verified
convergence updates the current common assignment without changing its boot request.

Historical operations recorded by `provision-ops-vm --replace-existing` retain
that interface for `--resume OPERATION`, `--verify-reboot` and permitted pre-boot
recovery. Their snapshots remain under `~/private/klokast/ops-replacement-inputs/`.
Complete them before first migration. Common operations use
`~/private/klokast/vm-replacement-inputs/` and the resume command reported by
`platform-update`. Old commands refuse a controller owned by the common lifecycle.
Initial controller provisioning without a local controller is separate work.

The Ansible-managed `ops-controller-nightly` cron job runs at 03:00 UTC on both
controllers. The verified standby skips; only the verified active controller
works on the declared standby. `ops-controller-nightly --dry-run-plan` reports
the decision without building, converging, or replacing. The coordinator checks
approved source state, authority, pending operations, recovery and capacity,
cleans eligible resources, prepares a qualified image on the target's own
controller, and compares its build ID with the installed image. It can install
an already-built unused image. An unchanged image gets health and cleanup checks
only. Source-only changes require explicit convergence.

The coordinator accepts repeatable `--service-role ROLE` selection. This option
cannot accompany `--services`, which selects all supported service roles.
For a new schedule, the Ansible-managed default selects `--service-role vpn-egress`,
`--service-role dmz`, `--service-role bak` and `--service-role router`.
Installed role selections are preserved unless explicitly changed. Select `air`
only after its supervised retained-data qualification. Busy runners are skipped
before image preparation. Router runs last on each box. Before deploying this schedule to an installed
box, complete its [retained-state migration qualification](platform-updates.md#router-lifecycle-integration).
`ops_controller_nightly_service_roles` sets the role list; an
empty list selects controller updates only. The existing
`ops_controller_nightly_services: true` selects all supported services when no
role list is set. An explicit nonempty role list cannot accompany that option.
Each selected
role checks authority and pending work, retires eligible generations, prepares
its image and replaces only when the build ID changes. An unchanged image gets
health and cleanup checks and reports configuration changes awaiting rebuild.
A selected absent BAK, DMZ or IoT VM is created through the same qualified image
workflow. Select optional IoT updates only after live creation, replacement,
reboot and data-retention qualification. After acceptance, generation
retirement runs again before image cleanup. This keeps
only the current accepted controller or runner root. Other roles also keep one previous
accepted root. Retirement releases obsolete image references. Failed verification
stops before this final cleanup.
Wait for completion under the existing locks and execution bounds. VPN relay
probes remain disabled; the coordinator adds no continuous health traffic.

A changed image uses the existing replacement and reboot verification commands.
Incomplete operations block the next scheduled run. Use the exact resume command in the result. Historical controller operations
can still report `provision-ops-vm --resume` with `--verify-reboot`; common
operations report `platform-update update --role ROLE --resume OPERATION`. After boot, automatic rollback remains prohibited. Private
nightly logs and the machine-readable result are under
`/var/lib/klokast/ops-nightly`; retain at most 14 completed run logs. Cron does
not promote a controller or install provider credentials.

For a historical controller operation, qualify reboot persistence from its active peer
with the command below. Common replacement includes this check before acceptance:

```sh
ansible/bin/provision-ops-vm --box BOX --resume OPERATION --verify-reboot
```

This explicit test requires the accepted standby, holds the existing installation
lock, and reboots only that controller. It compares the boot ID, machine identity,
SSH host key, Instance read key, recovery keys, and Tailscale identity before and
after reboot, then runs the normal health checks. The protected replacement
record stores the baseline before reboot and completion after health passes.
Resume a pending verification with the same flag; a detected new boot is checked
without another restart. A new explicit test after completion requests a new
reboot. It does not prepare another
image. Repeat `--resume OPERATION` without this flag for health verification
without another reboot. `--dry-run-plan` also supports this test.

If boot-package loss prevents Tailscale from connecting, use
`provision-ops-vm --box BOX --resume OPERATION --recover-boot` from the active
peer. This uses the existing `neo` bootstrap SSH path through that box's dom0.
It requires the exact accepted replacement disk, verifies the selected image
and its frozen package manifest, restores those package versions, then starts
the firewall and restarts Tailscale with its existing state. It runs the normal
resume health checks after reconnection. It does not clone a disk, register an
identity, rebuild an image, or restore an older controller generation.
If that recovery SSH path is unavailable, keep both disks and use console
recovery; do not start the previous controller by hand.

The same-box state allowlist is `PRESERVE` in
[`vm_controller_state.py`](../ansible/roles/vm-template-builder/files/vm_controller_state.py).
It includes controller machine and SSH identity, read-only Instance access,
recovery keys, the private operations journal, image records, and selected
private archives and application inputs, including the VPN inputs and cache.
It also retains mutation nonce and execution records and Freebox recovery
records. Files outside this list remain on the
retained old disk. The old operating system, Codex runtime, Instance worktree,
authority publications are not restored. Explicitly allowlisted same-controller
credentials are preserved with their ownership and permissions.
Review the list against the controller before replacement. Common migration copies
links as links and refuses symbolic-link parents of retained mappings. Historical
root-copy operations retain their stricter link and special-file checks.

For a legacy controller without an Instance read key, run
`converge-ops-controller --box BOX -- --tags ops-controller-instance` with
`KLOKAST_INSTANCE_ORIGIN` set as described above. Register the displayed public
key once, then repeat convergence to create the independent checkout. A legacy
read key already held by that same controller can be preserved during replacement.

Legacy adoption requires an exact reviewed LV UUID and the SHA-256 of
`/etc/xen/ops.cfg`:

```sh
ansible/bin/provision-ops-vm --box BOX --adopt-existing \
  --expected-lv-uuid LV_UUID --expected-config-sha256 CONFIG_SHA256
```

The wrapper starts a bounded controller job and returns its private log path.
A job start is not completion. Read the log until Ansible completes. The target
record `/mnt/dom0_data/klokast-infrastructure/ops/replacement.json` contains the
operation ID even when the terminal disconnects. Resume and rollback consume
that record. Keep the selected image and all pending records until the operation
is accepted or reconciled. Disk rollback before normal boot leaves both disks
in place. After normal boot, automatic rollback stops for private-state
reconciliation. Do not start the previous generation by hand.

Run native qualification on the target's own controller before live replacement:

```sh
ansible/bin/provision-ops-vm --box BOX --qualify-replacement --image BUILD_ID
# Qualify an update between two independently prepared image builds:
ansible/bin/provision-ops-vm --box BOX --qualify-replacement \
  --image FIRST_BUILD_ID --next-image SECOND_BUILD_ID
```

This replaces a synthetic legacy controller and verifies unchanged-image replay
after a configuration change. It records the selected image for each step. With
a different `--next-image`, the second step replaces the managed root with that
separate qualified local build. Both images
remain protected from cleanup until the test disks are removed. It uses the
same guest-recipe compatibility check as deployment for both selected builds,
before downloading test tools or allocating disks. An older qualified build can
have an obsolete finalizer; its qualification receipt alone does not make it
compatible with the current replacement code. The command does not rebuild it.
It uses the
production replacement functions with separate guest names, test disks and boot
files, and no guest network interfaces. It covers
partitioned legacy adoption, state transfer to a raw root, a second replacement
of that managed root when a different build is selected, preserved numeric ownership and machine keys, new journal
writes, unchanged replay, and refusal of rollback after boot. It checks every
test disk UUID and tag before cleanup. Failure retains the scoped records and
disks for inspection. Test inputs and evidence stay on that box. The command
persists the resulting LVM recovery metadata on diskless dom0. It refuses to
commit unrelated pending dom0 configuration changes. The command uses bounded
jobs and reports the private log, as the replacement command does.
Qualification downloads verified packages for its disposable test environment.
It does not rebuild the selected image. Replacement itself does not download
image inputs or build an image.
State preservation rejects links except the generated image-record inventory
link to the fixed public `ansible/inventory-policy/group_vars` directory. It
copies that link without following it. Managed configuration is still rebuilt.
Offline interruption tests additionally exercise each durable stage and two
selected image IDs: `python3 -m unittest discover -s ansible/tests -p
 test_ops_replacement.py`.

## Controller network migration

Run on the active controller as `smith`, from the approved public checkout:

```sh
ansible/bin/converge-ops-controller --box BOX --network-only --dry-run-plan
ansible/bin/converge-ops-controller --box BOX --network-only
ansible/bin/converge-ops-controller --box BOX --network-only --status
ansible/bin/converge-ops-controller --box BOX --network-only --resume
ansible/bin/converge-ops-controller --box BOX --network-only --rollback
```

The [architecture](architecture.md#box-ops) owns the network and authority
contract. This command changes an existing controller in place. It does not
build an image, replace its root, rotate identity, or hand off authority.
Migrate the standby first, then the active controller. Pause the existing
nightly schedule for the migration window. Complete any accepted replacement's
reboot verification before changing its network. Preserve its accepted disk.

The workflow holds the existing installation lock. Ops saves network preimages
locally, installs its transition firewall, and arms a bounded 20-minute rollback
worker before dom0 attaches `eth1`. A temporary OpenRC oneshot restores old
files before networking if a reboot interrupts the switch. Failed DHCP,
connectivity or identity checks refuse acceptance. Do not assume that a lost
controller connection stopped remote work.

After independent DNS, HTTPS, routing and identity verification, dom0 records
the new boot configuration and ops accepts the change. The workflow then
removes only router `eth6`, its DNS binding, its controller rules and its old
ops IPv6 hooks. Workload NIC numbers, addresses and MACs stay fixed. It publishes
a new protected router topology record when a legacy accepted baseline exists;
the previous record remains immutable. The router keeps running.

Ops recovery state is in `/var/lib/klokast/network/ops-uplink`. Dom0 holds only
public topology and boot evidence under its existing protected ops assignment.
Use `--resume` after checking the recorded phase. `--rollback` restores an
unaccepted controller network; it refuses an accepted revision. Router cleanup
has exact file preimages and supports resuming publication after interruption.
No controller credentials are copied to dom0.

Verify each ops VM after a reboot. Check its Tailnet identity, active/standby
role, disks, private dom0 access, default routes, DNS, uplink firewall and
workload health. A router outage test is a separate explicit exercise; normal
migration does not stop the router. Restore the nightly schedule after success.
Controller health compares public source with the invoking approved checkout
or the operation's frozen commit. It does not fetch GitHub during a health
check. Optional VPN downloads must not become a controller health dependency.
Initial controller bootstrap and active-controller image replacement remain
separate workflows.
