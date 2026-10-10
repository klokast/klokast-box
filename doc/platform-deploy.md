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
and installs them from the workstation after promotion. See
[credential setup](../klokast-ops/runbooks/40-tailscale-wrapper-setup-policy.md).
Setup does not copy private state or credentials from another controller.

## Controller recovery

Before full power-off, record `ops-controller-ha status`. Check that the
standby has current, clean implementation and Instance checkouts, working
recovery access, and no provider mutation credentials. Controller recovery follows the
[controller authority model](architecture.md#controller).

For a planned handoff, commit and push the new active and standby placement
in the private Instance repository through the operator's ordinary Git
workflow. Pull that commit on the destination with `git pull --ff-only`.
Then run `ops-controller-ha switchover --old-active OLD --new-active NEW`.
The command validates the destination offline. Revoke the old controller's
provider clients through the provider and remove their local credentials before
fencing it and activating the destination. Install the destination's independent
credentials with the workstation tools. Completion requires credential and
Instance verification; a request for credentials is an incomplete handoff.
Pass `--old-credentials-revoked` only after provider-side revocation. The command
checks that the old controller's known provider credential files are absent;
that check cannot prove provider-side revocation. Repeat the same command after
installing credentials to finish verification. Emergency promotion uses the
same revocation acknowledgement in addition to `--old-active-fenced`.

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

Use an existing qualified image when its box and guest recipe match. Prepare a
new image only when required, on its own controller, with
`platform-update prepare --box BOX --profile ops-alpine-v1`. Record the qualified
build ID. Replacement consumes that ID without fetching inputs or rebuilding.
Move authority to the peer first if the target is active. Keep runner VMs running.

The replacement interface is `provision-ops-vm --box BOX --replace-existing
--image BUILD_ID`. Recovery uses `--resume OPERATION` or `--rollback OPERATION`
with the same box. Each action supports `--dry-run-plan`. Do not use these
interfaces for live deployment until their isolated replacement qualification
has passed. Initial provisioning without a local controller is separate work.

Before stopping the target, verify its disk assignment, recovery access,
capacity, image checksums, qualification, and absence of competing operations.
Legacy disks need a reviewed adoption record; ordinary replacement must refuse
unknown disks. Keep the old disk and boot files on the same box. Preserve only
explicitly allowlisted identity and private state while the old guest is stopped.
Rebuild managed configuration from Git. The installed HA role must match current
Instance placement. Package updates must not require a new Instance deploy key.

Use the protected operation ID to resume. Never select a different image during
resume. Unfinished operations execute the recorded public and Instance commits from
verified local snapshots, even if upstream has advanced. If these inputs cannot
be recovered and verified, resume stops. Current authority is checked separately.
After acceptance, installation commits are historical evidence. Use
`converge-ops-controller --box BOX` for explicit configuration updates; successful
verification is recorded in the protected assignment. An unchanged installed
image passes health checks without a new disk or guest restart, and reports
whether configuration convergence is needed. Rollback must reconcile private state written
after replacement boot; if this cannot be proved safe, retain both disks and
stop automatic rollback. Keep one previous accepted generation for recovery.

Retire generations beyond the current disk and one previous disk from the
active controller:

```sh
ansible/bin/provision-ops-vm --box BOX --retire-older --dry-run-plan
ansible/bin/provision-ops-vm --box BOX --retire-older
```

The command also checks failed qualification fixtures. Unproved ownership or
live references leave resources untouched and report the reason. Repeat the
command to resume its recorded deletion plan, including record publication and
persistent LVM metadata. Retained audit evidence does not retain deleted images.

The Ansible-managed `ops-controller-nightly` cron job runs at 03:00 UTC on both
controllers. The verified standby skips; only the verified active controller
works on the declared standby. `ops-controller-nightly --dry-run-plan` reports
the decision without building, converging, or replacing. The coordinator checks
approved source state, authority, pending operations, recovery and capacity,
cleans eligible resources, prepares a qualified image on the target's own
controller, and compares its build ID with the installed image. It can install
an already-built unused image. An unchanged image gets health and cleanup checks
only. Source-only changes require explicit convergence.

A changed image uses the existing replacement and reboot verification commands.
Incomplete operations block the next scheduled run. Use the reported
`provision-ops-vm --box BOX --resume OPERATION` command (with `--verify-reboot`
when reported). After boot, automatic rollback remains prohibited. Private
nightly logs and the machine-readable result are under
`/var/lib/klokast/ops-nightly`; retain at most 14 completed run logs. Cron does
not promote a controller or install provider credentials.

To qualify reboot persistence, run the accepted operation from its active peer:

```sh
ansible/bin/provision-ops-vm --box BOX --resume OPERATION --verify-reboot
```

This explicit test requires the accepted standby, holds the existing installation
lock, and reboots only that controller. It compares the boot ID, machine identity,
SSH host key, Instance read key, recovery keys, and Tailscale identity before and
after reboot, then runs the normal health checks. It does not prepare another
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
[`vm-infrastructure-finalize`](../ansible/roles/vm-template-builder/files/vm-infrastructure-finalize).
It includes controller machine and SSH identity, read-only Instance access,
recovery keys, the private operations journal, image records, and selected
private archives and application inputs, including the VPN inputs and cache.
It also retains mutation nonce and execution records and Freebox recovery
records. Files outside this list remain on the
retained old disk. The old operating system, Codex runtime, Instance worktree,
provider mutation credentials, and authority publications are not restored.
Review the list against the controller before replacement. Reconcile symlinks
or special files in selected trees before use; the transfer refuses them.

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

This uses the selected qualified image in two successive synthetic controller
generations. It records the selected image for each generation. With `--next-image`,
the second generation consumes that separate qualified local build. Both images
remain protected from cleanup until the test disks are removed. It uses the
same guest-recipe compatibility check as deployment for both selected builds,
before downloading test tools or allocating disks. An older qualified build can
have an obsolete finalizer; its qualification receipt alone does not make it
compatible with the current replacement code. The command does not rebuild it.
It uses the
production replacement functions with separate guest names, test disks and boot
files, and no guest network interfaces. It covers
partitioned legacy adoption, state transfer to a raw root, a second replacement
of that managed root, preserved numeric ownership and machine keys, new journal
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
