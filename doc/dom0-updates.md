# Dom0 updates

This document describes how a diskless dom0 gets a new boot bundle. The
[architecture](architecture.md#layer-1-baremetal-host--xen-hypervisor) owns the
authority of host replacement. The common image preparation rules are in
[VM images and replacement](platform-updates.md).

## Bundle preparation

`platform-update prepare --box BOX --profile dom0-alpine-v1` prepares public
diskless boot files on the matching controller. It uses that box's saved
Alpine mirrors and declared download transport. `--inputs-only` freezes the
signed packages without starting a builder.

The networkless PVH builder installs and checks the runtime package closure,
then exports Xen, the LTS kernel, initramfs, modloop, and the original signed
repository indexes with all runtime APKs. The dom0 package policy supplies
the runtime world; build tools are separate. Tailscale uses the signed upstream
stable archive and is exported separately from APKs.
Firmware packages must be selected for the target hardware; the preparation
profile does not include the complete firmware collection.

Results are under `discovery/dom0-builds/ID`. Failed public inputs remain under
`/var/cache/klokast/updates/dom0-build-ID`, and the opaque bundle remains under
`/mnt/dom0_data/klokast-dom0-builds/ID/bundle.tar` on the same box. Failed
builds retain diagnostics. Successful preparation removes its input staging,
keeps the shared package cache and compact build evidence, and verifies reuse
of a saved bundle when the package versions and construction recipe match.
Preparation reports `hardware_qualified: false`.
Preparation does not change EFI boot files, commit an overlay, enroll an
identity, reboot, or enable a dom0 schedule.

### Tailscale in the bundle

Dom0 freezes the current signed upstream stable Tailscale release. Its bundle
contains the binaries, OpenRC service, and module request. Boot selection
places the service and module request in the persisted overlay and removes
Tailscale from APK world. The binaries stay out of the overlay: `lbu status`
unpacks the overlay three times into the RAM-backed `/tmp`, which cannot hold
them. The protected image policy records their SHA-256 values. At boot, the
`klokast-dom0-tailscale` one-shot copies them from the accepted generation,
checks them, and runs before `tailscale`. Selection includes this one-shot
explicitly, because `lbu` saves only symlinks below `/etc/init.d`. After the
`lbu` commit, selection reads the saved overlay. If a boot file is missing or a
binary is present, the update stops before the reboot. After the reboot,
verification saves the Tailscale profile data and excludes the regenerable
log buffers and DNS cache. The update compares the upstream component as well
as Alpine package versions, and verifies the daemon version and file hashes
after reboot. The previous overlay and boot selection remain available for
console recovery.

Legacy dom0 convergence verifies the upstream archive on the controller before
it removes the Alpine package and installs the upstream binaries. The temporary
first SSD bootstrap can use Alpine Tailscale until this convergence. Upstream
self-update stays disabled; the controller owns version selection.
Until its next dom0 update, a dom0 booted from a bundle without upstream
Tailscale keeps the previous world with Alpine `tailscale` and
`tailscale-openrc` instead of `nftables`. A dom0 booted from an older bundle
can also keep `openresolv` and Tailscale DNS. Dom0 health and update
preparation accept only these differences, so the update can replace them.
Before the `lbu` commit, selection removes the `openresolv` udhcpc hooks and
`RESOLV_CONF="no"`, and sets `--accept-dns=false`. After the reboot, `udhcpc`
writes the WAN DHCP resolvers to `/etc/resolv.conf`.

## Activation

Run from the active peer controller as `smith`:

```sh
ansible/bin/platform-update update --box BOX --role dom0 --image BUILD_ID --dry-run-plan
ansible/bin/platform-update update --box BOX --role dom0 --image BUILD_ID
ansible/bin/platform-update update --box BOX --role dom0 --status
ansible/bin/platform-update update --box BOX --role dom0 --resume OPERATION_ID
ansible/bin/platform-update update --box BOX --role dom0 --verify-reboot
```

A human can run `kk update BOX-dom0` from the MacBook. The command starts a
`vm-image-update` job for role `dom0` on the active controller. The job runs
the recorded workflow above with `--manual-dom0`. It uses the
[request authority](controller.md#vm-image-update-requests) of VM image
updates; it adds no credential or controller authority.

### Runner check and closure

The command checks the declared runner of the target box, `BOX-air`, through
`image-idle`, because the host reboot stops it. A
[running agent](airunner.md#runner-activity), an unavailable check and a
[runner closure](airunner.md#runner-closure) of another operation block the
command; an attached tmux client does not. The error names the runner and the
open work. The runners of other boxes are not checked: they keep working
during the update.

The job repeats the runner check before selection and reboot, and closes the
target runner to new connections after it saves its workspace. A reboot or an
update with a selected `--image` closes the runner immediately after the first
runner check of the job. An update that builds its image closes it after the
build. If the job fails before the host has a pending transaction, it opens
the runner again. A dom0 update that starts while no host update is pending
first opens a runner that an ended dom0 update left closed. Updating the
runner's own box interrupts that runner, including the agent that started the
command. Run this command from the MacBook after closing the affected sessions.

### Controller switchover

The active controller's box is never an activation target. If the target holds
the active controller, the command first promotes the declared peer through
`ops-controller-ha switchover` and verifies its authority. It leaves that peer
active. No switch back is required. If the switch stops, use
`kk promote --status` and finish its recorded operation before retrying the
update.

### Job stages and display

The job publishes each stage and keeps its 100 most recent stages. The command
prints one line for each stage, also for a stage that ends between two status
checks, and repeats the current stage every two minutes. While dom0 reboots,
the stage shows the minutes of the 20-minute verification bound. Ctrl-C or a
closed terminal stops only the display; the job continues on the controller.
`--detach` returns after the request; `--cancel` stops the job before it
changes the host. A running job ends its current stage, for example the dom0
image build, and stops before it copies boot files to the EFI partition, or,
for a reboot, before the reboot test. A runner that the job closed opens again.
`kk status BOX-dom0` shows the stages.

### Resume a job

Run `kk update BOX-dom0` again to follow the job, or, after a failure, to
start a new worker for the same job and its pending host transaction. The
controller [selects the job](../klokast-dev/README.md#runner-and-maintenance-commands).
When it continues a job, the command does not repeat the runner check, because
a reboot can have already stopped the runner; the job checks it while the
reboot has not started. Only the runner closure of that transaction is
allowed on resume. On the controller, use the recorded resume command after a
timeout or connection loss, also after a power cycle.

### Activation checks

The first activation requires an explicit successful same-box build. Later
scheduled updates prepare current stable inputs and compare the installed
package set and kernel release. Equal versions do not cause another reboot.
The coordinator checks host health, records the existing identity and running
guest UUIDs, and checkpoints an idle runner through its existing maintenance
interface. Busy runners defer the operation. Unknown pending guest operations
block it.

### Boot selection

The transaction verifies all bundle files, checks EFI capacity with a recovery
reserve, and stages a versioned directory. The EFI partition holds an A/B
pair of generations. Before the capacity check on an accepted host, the
transaction points the GRUB `Recovery:` entry at the running accepted
generation. It then deletes all other generations, older recovery overlays,
the pre-policy root `cache`, and the legacy installer kernel files in `/boot`.
If space stays too low, the error gives the free and needed MiB.

Selection keeps the chosen mirror, sets the accepted branch and isolated APK
cache, commits the small overlay with `lbu`, and writes a trial GRUB selection.
Its default entry stays the accepted generation with the overlay saved on EFI.
The `Trial:` entry starts the candidate with its explicit offline repositories.

The detached reboot worker requests clean guest shutdown and stops if any guest
remains after five minutes. It never force-destroys a guest. Just before the
reboot, it sets `next_entry=trial` in `boot/grub/grubenv`. GRUB clears this
value before it starts the candidate, so the candidate boots one time only. If
the candidate does not come back, a power cycle starts the accepted generation.

### Guest restart and acceptance

The peer waits for the new boot, exact package set and kernel, and unchanged
Tailscale identity. The guests that ran before the reboot are not the
reference. When the recovery services and the Xen autostart (`xendomains`)
have finished, the peer starts each VM that the Platform declares running on
the box and that does not run. The declaration is the Platform roles of the
Instance and the app VMs of `platform-resources`. The peer then waits up to 15
minutes until each of these VMs is connected to Tailscale, and shows how many
are online. A VM that Xen cannot start or that does not come online fails the
job with its name. The job result warns about each declared VM without a link
in `/etc/xen/auto` and each link that starts a VM that is not declared.

Then the peer shuts down cleanly each VM that runs but is not declared, and
names it in the result. It never force-destroys a VM: a VM that does not stop
within four minutes fails the job. Then the peer runs host health checks.
Acceptance then makes the candidate the GRUB default, and the runner is
released. If the peer finds the accepted generation running, it rolls back:
dom0 restores the saved overlay and GRUB file, commits only the known boot
outputs, and the update fails with this cause.

### Reboot test

`--verify-reboot` checks another boot of the accepted bundle through the same
runner checkpoint, clean shutdown, identity, guest, and health checks. It
creates a new operation record and installs no new bundle. It compares the
guests before the shutdown with the guests that run when the test starts, not
with the guests of the acceptance: a VM update gives a guest a new Xen UUID.
From the MacBook, `kk reboot BOX-dom0` runs it as a controller job, with the
same session check and controller switch as `kk update BOX-dom0`.

### Abandon and console recovery

Before reboot starts, `--resume OPERATION_ID --abandon-unbooted` restores the
saved selection and overlay. After reboot starts, this action refuses, except
when the accepted entry of a trial selection is running. Operations prepared
before the trial selection make the candidate the default at selection, so a
power cycle starts the candidate again.

In other cases, use the GRUB `Recovery:` entry through the local console. It
selects the previous accepted generation and saved pre-update overlay, and
starts `/bin/sh` without guest autostart. The first update of a host keeps the
installer boot files as its recovery. Provisioning phase 23 does not apply to
an image-managed dom0. Recovery can expose older identity state; inspect it
before restoring normal startup. The original GRUB file and overlay are also
retained under `/mnt/dom0_data/klokast-dom0-updates/operations/OPERATION_ID`.
EFI holds a recovery overlay under `klokast-dom0-recovery/OPERATION_ID`. Do not
restore it blindly after a candidate has run.

### Records and logs

The controller keeps step logs under
`/var/lib/klokast/updates/discovery/dom0-operations/OPERATION_ID`.
Dom0 retains `pending.json`, `accepted.json`, and the operation's frozen helper
under `/mnt/dom0_data/klokast-dom0-updates`. The helper and its VM engine and
router copies freeze when the operation records `pending.json`. Before that,
each `status` or `prepare` step stages them again from the controller checkout,
so a retry of a failed reboot test runs the current code. A reboot test changes
no boot files, so a resume of a pending reboot test also stages its helper
again. A verification fix then reaches a test that waits after its reboot.

Acceptance of an image update adds an entry to the
[update history](platform-updates.md#update-history). A reboot test does not.

A pending transaction blocks further supported mutations until it completes or
is recovered. Unknown or insufficient storage stops activation; the updater
does not repartition the disk.

## FAT boot repair

For a FAT dirty flag or boot filesystem warning, run the boot maintenance
playbook from the active controller before resuming a reboot test:

```sh
ANSIBLE_CONFIG=ansible/ansible.cfg ansible-playbook -vv \
  -i ansible/execution-inventory/hosts ansible/playbooks/74-dom0-boot-check.yml \
  --limit BOX-dom0 -e boot_check_box=BOX
```

It takes the existing native operation locks and permits only an unstarted
reboot test when a host transaction is pending. It extracts the signed Alpine
`dosfstools` package into an isolated RAM directory if needed, with package
scripts disabled. It removes that directory and checks that the host package
database and world did not change.

If the read-only module image holds the boot mount busy, the procedure copies
that exact image to dom0 data storage, checks its checksum, and remounts it at
`/.modloop`. The copy stays under `klokast-dom0-updates/fat-modloop-*` until
reboot; do not delete it while a loop device uses it. No other busy reference
is removed.

The procedure syncs and fully unmounts the boot partition, saves a complete
partition image and checksum on dom0 data storage, runs automatic FAT repair,
and requires a clean read-only check. It remounts the partition read-only and
verifies every boot file's contents. A failed unmount stops repair. A failed
repair or changed file stops the procedure and requires inspection before
reboot. Backups remain under `klokast-dom0-updates/fat-check-TIMESTAMP`.
This procedure does not reboot the host or close runner sessions.

## Nightly enablement

Enable `ops_controller_nightly_dom0: true` through the existing nightly
playbook after hardware qualification. It adds `--dom0` and preserves VM role
selection and the Instance UTC schedule. Only the standby box can update, and
dom0 runs after VM work. A successful host replacement permits the existing
planned handoff on the next night. Unqualified boxes skip scheduled activation.
