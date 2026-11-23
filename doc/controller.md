# Controller

This document describes the mechanisms of the Platform controller on
`<box>-ops`. The [controller authority model](architecture.md#controller) owns
active and standby authority, credential custody and fencing. Procedures are in
[Platform deployment and controller recovery](platform-deploy.md).

## Local image work

Before dispatching local image work, check the owning controller's public
source checkout for the requested revision. If it is behind, try a bounded
fetch and fast-forward update before image preparation or cleanup. Local
changes, divergent history, or a failed fetch stop the request with the
controller name and required commit. Execute image work from a separate clean
checkout at the requested revision. An interrupted operation keeps its frozen
revision; a source update does not change its inputs.

Local image preparation includes public input downloads, template construction,
isolated qualification, same-box reuse, and checked cleanup of unused images.
Cleanup can retire legacy image copies on that box after it verifies their
original build evidence and all local references. A replacement image must
have been built and qualified on the local box. These checks constrain
supported operations; they do not contain a compromised development account
with root access.

Local image preparation keeps its inputs and qualification records on the
local `ops`; images stay on its dom0. Only public build receipts may be copied
between controllers for local history or deployment consumers. No input
archives or image disks are distributed across boxes. If the local `ops` is
absent, preparation stops; first-controller provisioning is a separate workflow.

The nightly controller coordinator may request image preparation for the
declared standby at the
[Instance update time](klokast-instance-specification.md#nightly-update-time).
The active controller alone coordinates this work; standby scheduled
invocations skip. See
[image preparation](platform-updates.md#golden-image-builds-and-isolated-tests).
After a standby update installs a new image, the next scheduled run first makes
that standby active through the [planned switch](#planned-switching). See
[routine controller replacement](platform-deploy.md#routine-controller-replacement).

## Credentials

Provision each controller's own provider credentials and development Instance
Git credentials once. Keep them on that controller during routine switching.
Install public Tailnet tools on both controllers and Freebox tools only on
controllers for boxes in France.

### Public upstream metadata credentials

Each box controller can use its own dedicated, expiring fine-grained GitHub
token with public repository read access and no additional permissions. Keep
the token in `/etc/klokast/secret-authority/github-metadata.json`, owned by root
with mode `0600`. Never reuse a write-capable App, Git key or interactive login.
The human selects the token permissions in GitHub. A successful API check proves
that the token works; it does not prove the full permission selection.

The installed root-owned `ksa-github-metadata` command owns this capability.
Only controller `smith` can invoke it through the dedicated doas rule. Its
catalog currently contains only `codex-stable`, the public latest-release API
for `openai/codex`. Callers select a catalog name, not a URL, method or header.
The command checks matching local controller authority, bounds the response,
and returns metadata without returning credentials. Both active and standby
controllers can use this read-only capability for their own image work. It
grants no deployment, promotion, VPN management or private repository access.

The attacker in scope is a compromised downloader or dependency. It must not
obtain the token or choose arbitrary authenticated requests. The token is used
only for verified HTTPS to the fixed GitHub API endpoint. Redirects are refused.
Public artifact downloads have no Authorization header and retain checksum
verification. The optional proxy-port argument selects only a loopback HTTP
CONNECT transport opened by the existing `with-proxy` action; it grants no VPN
control. HTTPS certificate verification protects the token from that proxy.
Development `smith` remains privileged; this is not a boundary against
controller root or privileged debugger access.

The broker serializes calls and records response ETags, retry times and request
outcomes in controller-local protected state. It returns saved metadata only
after an authenticated unchanged response. Failed freshness checks stop the
update. Rate-limit responses defer requests until the server retry time, with
bounded exponential backoff when no time is supplied. There is no background
worker, exit rotation or cross-box fetch service.

Same-controller replacement preserves this read-only credential. It is not a
provider mutation credential and can remain on a standby controller. Never copy
it or its cache between controllers. Revoke the affected token in GitHub after
compromise, then install a new token on that controller. A reconstructed
controller needs its own credential; an absent cache is safe to rebuild.
See [metadata token setup and recovery](../klokast-dev/runbooks/27-github-metadata.md).

## Planned switching

Planned switching verifies both controllers, credentials, current repositories
and recovery access before changing markers. Acquire the existing operation
and image locks on both controllers in box-name order, with the operation lock
before the image lock on each box. Demote and verify the old controller before
activating the destination.

Record progress privately and resume interrupted transitions explicitly. A
switch that fails before synchronization changes no marker; it is `abandoned`
and cannot be resumed. A failed transition can leave both controllers inactive.

## Routine controller replacement

Routine controller replacement preserves the identity of the same controller;
it is separate from credential rotation. The active peer replaces only the
standby, using an explicitly selected image built and qualified on the target
box. Image preparation, authority handoff, and replacement are separate
operations. Protected infrastructure records on the target dom0 bind the
image, configuration, exact disks, and progress. Unknown disks require an
explicit one-time adoption record before replacement.

First migrate allowlisted state to retained storage through the
[common lifecycle](platform-updates.md#retained-controller-and-runner-state).
Routine replacement reuses that volume and copies only the small machine
identity records inside a networkless Xen guest on that box. Dom0 does not
mount guest filesystems. Never boot both copies of an identity. Reconstruct
managed configuration from public code and Git-approved Instance. Never restore
an old active marker. Preserve only the explicit same-controller credential
allowlist; never copy another controller's credentials. After replacement boot,
rollback must preserve newer private state or refuse automatic recovery. Apply
the [root retirement](#root-retirement) rules after verification. The runner has
a separate disk and lifecycle; controller replacement grants no authority to
stop, copy, or change it.

## Frozen inputs and recovery repair

An unfinished replacement freezes its image, public implementation commit,
Instance commit, and rendered configuration. Execute its playbooks from verified
controller-local source snapshots. An operation records both commits from the
current upstream `main` when it starts. Later upstream pushes do not stop it.
Before the snapshot, each recorded commit must still be on upstream `main`; a
history rewrite that drops one refuses the operation. A workflow that runs a
verifier from the live controller checkout also requires that checkout to still
hold the recorded commits. Recheck current controller authority before
destructive actions; a historical snapshot cannot authorize a former standby.

If a frozen reader cannot read its own protected pre-boot record because of a
code defect, the active controller can stage a corrected recovery reader from
approved public code. Use it only to recover and abandon that exact unbooted
attempt. Keep the frozen request and inputs unchanged. Install corrected boot
recovery only after recovery completes, then use a new replacement operation.

In development, an already booted router can use the bounded
[read-only verifier repair](platform-updates.md#repair-a-read-only-router-verifier-in-development)
when a verifier defect blocks acceptance. Its original request, Instance,
image, native probes and all mutating recovery code stay frozen. The active
controller can run corrected read-only verification from a clean published
public commit, then recheck the original exact configuration before submitting
checks through the existing frozen native actions. This does not authorize a
policy change or rollback. Record the verifier commit in the existing private
operation log. Production cannot use this development repair.

After acceptance, these inputs are historical evidence. Explicit configuration
updates use `converge-ops-controller` and record verified convergence in the
protected assignment. A source change alone does not replace a controller disk.

## Root retirement

Retain only the current accepted controller root and its boot files after reboot
verification. Keep the source root until replacement and reboot checks pass.
Incomplete operations retain all required resources. The retained data volume
is excluded from root retirement.

After a current controller or runner generation is accepted, retirement can also
remove an abandoned candidate root whose protected transaction proves recovery
before candidate boot. This also applies when the recovered accepted source is
still current. Keep the candidate's checked staged configuration for recovery
reads, its audit records, and retained data. The unused root and kernel
artifacts can be removed to make space for a new attempt.

Retirement records an exact plan before deletion and verifies disk UUIDs,
ownership, boot checksums, and absence of guest, mount, and loop references.
An untagged legacy controller root requires its protected adoption record and
unchanged LV UUID. New ownership tags stop its retirement. Unknown resources
stay in place. Keep immutable common audit records and checked retirement
receipts; accepted replay must not restore retired references. Completed legacy
controller records are compacted after checked retirement. Failed qualification
cleanup has its own evidence and does not imply success.

## Nightly coordinator

The nightly coordinator uses existing locks and bounded jobs, waits for their
completion, and keeps private results. On each controller it also applies the
[log retention policy](../ansible/ansible.md#log-retention). It may retire old
resources, prepare a qualified standby image, and replace the standby when its
build ID or [configuration identity](platform-updates.md#configuration-identity)
differs. It performs no in-place convergence, authority handoff, credential
installation, or automatic rollback after boot. An incomplete replacement or
reboot verification blocks a new replacement and reports its resume command.
The [nightly schedule](platform-deploy.md#routine-controller-replacement)
describes its role selection and the planned switch after a standby update.

## Request interfaces

These interfaces let the MacBook and the runner request existing controller
workflows. They use the existing development controller authority. They add no
listener, credential, Tailnet rule or persistent state, and they cannot bypass
the checks of the workflow that they start.

<a id="air-image-update-requests"></a>

### VM image update requests

The MacBook `kk update BOX-ROLE` and runner `air-update` commands request the
existing active-controller
[replacement workflow](platform-updates.md#common-vm-replacement) through SSH.
They start one bounded `vm-image-update` job on the active controller.
`kk update BOX-dom0` starts the same kind of job for the
[dom0 activation](dom0-updates.md#activation); the job applies the dom0
authority check, which refuses the box of the active controller. `air-update`
cannot select dom0. They do not grant runner root access or transfer controller
credentials. A compromised runner or MacBook can misuse its existing
development-controller access.

A request selects one declared guest of a common lifecycle role. The controller
applies the same target check as `platform-update update`: it refuses an
undeclared guest, a guest declared stopped and the active controller. A request
cannot select an image, grant first-migration or data-discard consent, or move
controller authority. `air-update` selects only its own runner.
`kk update BOX-ROLE --cancel` stops a job only before it changes the VM. The
worker checks the request while it waits, so a cancel cannot interrupt a
replacement.

<a id="promotion-requests"></a>

### Promotion and status requests

The MacBook `kk promote BOX-ops` command requests the
[planned switch](#planned-switching) on the old active controller through SSH.

`kk promote --status` and `kk status` run the read-only
`ansible/bin/promote-status` command on each declared controller, because each
controller keeps its own operation records. `kk status` also reads the
controller guard status, the runner workspace status and
`vm-image-update list`; it changes no state. For the machine list it runs the
read-only `ansible/bin/machine-status` command on the active controller. That
command runs one fixed script on each declared machine, through the existing
Tailscale SSH path and without privilege escalation. The script reads the
package database and the modification time of public files. The controller
validates each returned line; a machine can state only its own values.

The `promote-status` command returns only the target, operation ID, phase,
start time, time of the last write, recorded error and process state of each
unfinished record. Each workflow writes the start time when it creates its
record. It writes the error only while it holds its operation lock.
