# Controller-managed VM updates

## Completion scope

The reduced first release supports selected shared Alpine VMs that pass the
fixed `shared-alpine-no-application-v1` qualification. The private Instance
selects targets and exclusions. Discovery continues to inventory every managed
VM. Application adapters, application data migration, and replacement of other
VM types are separate follow-up work.

The release is **not complete**. The discovery, template builder, independent
backup and isolated restore, retained-data, personalizer, and dom0 recovery
components have native test evidence. Their production integration is unfinished.
`adopt prepare` writes classification and exact cleanup reports. It issues a
signed adoption intent only when qualification is complete. `prepare --auto`
uses the active signed policy and fresh discovery to select the adjacent stable
branch. It builds one shared template and transfers compressed, checked bytes
to each other selected box. The destination checks the unpacked artifact
hashes against the same build receipt. The root authority boundary now imports
the complete no-application release and transfer evidence into an immutable
record for the current policy activation. It checks the approved engine and
package manifest again. Artifact bytes must be checked again before use. A
failed transfer leaves an exact staging directory for review; it cannot select
a production disk. Signed old-generation adoption is implemented in the
candidate engine, but has not been promoted or used on production VMs.
The root authority also has a read-only release check that compares the fixed
dom0 artifact bytes with the protected release before staging.
Replacement and rollout remain unavailable. No production update schedule has
been enabled.

The [Instance specification](klokast-instance-specification.md#shared-vm-update-intent)
owns desired state and assignment rules. [Secret Authority](secret-authority.md#standing-vm-update-authority)
owns execution authority. Component commands, limits, and historical native
evidence are kept once in [VM update components](platform-update-components.md).
Generated records and secrets stay outside Git. The controller private checkout
stays read-only. Standby recovery copies grant no execution authority.

## Completion sequence

1. **Qualify the three targets.** Account for every file, service, process,
   timer, account, container store, mount, and package difference. A report
   gives each item a proposed class, evidence checksum, rule, and resolution
   status. A proposed class is not approval. Unknown items and pending cleanup
   block adoption. Verify generated configuration against approved inputs and
   classify storage without archiving unknown files as a substitute. Complete
   exact approved cleanup before adoption. Require both checked Instance intent
   and observed state to prove that no application or retained application data
   remains. Old packages and unsupported source branches are update reasons;
   they do not by themselves fail target safety.
2. **Connect authority, records, and provisioning.** Add closed signed adoption
   and standing-policy replacement operations without changing general Apply.
   Hold the same installation operation lock across adoption, replacement,
   provisioning, and release-changing reconciliation. Protect release and
   operation records on the controller. Bind exact artifacts, complete package
   manifests, qualification, generated configuration, source identities, and
   disk generations. Connect approved inputs to the personalizer. Preserve
   Tailscale state, SSH host keys, UID/GID, and subordinate ranges. Include
   records and their authority evidence in controller recovery. Conflicting
   assignments or local drift must stop mutation.
3. **Connect the production transaction.** Verify authority, fresh qualification,
   independent management access, artifacts, capacity, and measured duration.
   Qualify an independent backup through the existing copy and isolated restore
   path; bind freshness, source disks, and consistency evidence. Stage separate
   candidate OS and retained-data disks, configuration, and recovery resources.
   Arm dom0 recovery before stopping the old guest. Apply a persistent
   maintenance fence and stop background work. Stop the old guest, complete
   final synchronization, personalize, and boot with the matching kernel.
   Verify management identity, ownership, storage, frozen packages, rootless
   Podman, generated firewall rules, and allowed and denied network paths.
   Persist acceptance on dom0 before removing the fence.
4. **Add the unattended loop.** Select adjacent explicit stable Alpine branches
   under current policy. Resolve complete signed package sets; never install
   from `latest-stable`. Build exact inputs once and transfer identical verified
   artifacts to each selected box. Skip unchanged inputs. Keep the template
   fixed through canary and rollout. Discover at 00:10 UTC, prepare at 00:40,
   replace from 02:00, and verify hourly. Replace one VM at a time; permit no
   new start after 03:00. Use a 30-minute replacement budget and a separate
   30-minute recovery budget. Necessary recovery may continue past 04:00.
   Use one qualified canary per distinct template. Require 24 healthy hours
   before wider deployment. Recheck no-app
   eligibility before every operation. A new workload blocks further updates.
   Failure stops rollout; resume requires journal reconciliation and current
   authority. Cleanup removes only proven temporary or unreferenced resources.
5. **Promote and prove production operation.** Commit and push each milestone.
   Promote the final tested engine and toolchain before production activation.
   Obtain exact adoption signatures and activate standing policy through the
   existing trusted-workstation signing path. Complete every acceptance row
   below before declaring the reduced first release complete.

Old and candidate writable disks stay separate. Keep the original adoption
disk and verified backup through rollout acceptance. Dom0 must not mount guest
filesystems. Send controller heartbeats every 15 seconds to the 90-second dom0
watchdog. A failed executor stops heartbeats. A heartbeat never extends the
replacement deadline.

Before acceptance, recovery selects only the recorded old generation. After
acceptance, preserve production writes, stop rollout, and report a critical
finding. Keep current and previous successful releases, previous disk
generations, recovery backups, and audit evidence.

## Current commands

Run as `smith` on the active controller from `~/src/klokast/klokast-box`.
Use a separate public candidate checkout for implementation validation before
promotion. Do not change the approved checkout to bypass the private engine lock.

```sh
ansible/bin/platform-update scan
ansible/bin/platform-update adopt prepare --box BOX --role dmz
ansible/bin/platform-update adopt prepare --box BOX --role iot --json
ansible/bin/platform-update adopt apply --approval-intent INTENT --approval-signature SIGNATURE --signer-id human-platform-apply
ansible/bin/platform-update adopt reconcile --nonce PREPARED_NONCE
ansible/bin/platform-update-config-audit --box BOX --role dmz
ansible/bin/platform-update retention --json
ansible/bin/platform-update status --json
ansible/bin/platform-update verify
ansible/bin/platform-update prepare --box BOX --branch v3.24
ansible/bin/platform-update prepare --auto
```

`adopt prepare` requires one running DMZ or IoT target. It combines the installed
root registry and retention readers, verifies that both bind the same authority,
engine, private commit, and input bytes, and rechecks sources before writing.
When discovery and the checked source have the same commit, it compares 19
fixed guest files with the checked recipes and repeats the guest hash probe
before publication. A v2 qualification report binds the comparison receipt;
a v3 report also binds a historical firewall comparison when needed. A v4
report binds the old shadow file to the checked admin password input without
recording the hash. After engine approval and installation of the dom0 source
reader, a v5 report also compares the live Xen disk and boot artifacts with
the guest's fixed `/` and `/boot` partition evidence. A v6 report measures
the four fixed private machine files and binds them to the typed retention
adapter. It records hashes and metadata only. A v7 report marks classification
complete only after both the guest and dom0 answer separate controller Tailnet
checks and every item is resolved. It still grants no adoption authority or
application compatibility result.
For a complete v7 report, `adopt prepare` writes a one-hour signed adoption
intent under `/var/lib/klokast/updates/discovery/adoption-intents/`. Sign its
canonical bytes through the existing trusted-workstation path. `adopt apply`
checks the signature, refreshes the VM inventory, repeats target qualification,
and verifies the old disk and boot identities. Dom0 records only the running
old generation; it does not stop the guest or select a candidate. The root
controller stores the signed authority and resulting assignment separately.
If the controller stops after dom0 publishes the old assignment,
`adopt reconcile` verifies the archived signature, nonce, current policy, and exact
dom0 pointer before it writes the missing controller receipt. It cannot create
an assignment.
This path still needs production testing after engine promotion and policy
activation.
Only exact matching rows from an approved engine resolve; identity, storage,
boot, and other machine-input checks remain separate.
The fixed no-application rules also recognize the `neo` numeric account and
subordinate ranges, and enabled package-owned OpenRC links with exact targets.
The old Podman boot helper is an exact rendering of checked source at
`17cfd0b`. It is recognized only with its exact bytes, root-owned mode, boot
state, link target, and an empty rootless store. The candidate uses the current
recipe; it does not copy the old helper. The old helper clears two runroot
subtrees. The current boot recipe clears the full transient runroot only when
process inspection succeeds with no Podman process, the directory has the
expected owner and mode, and no filesystem is mounted inside it. An unsafe
check fails the service. Ansible convergence refuses stale boot state instead
of deleting a partial runroot. Use a boot with the guarded helper or review
and apply the exact no-application cleanup workflow. Other unowned
service scripts need their own evidence. The shadow file, retained keys, and
disks also remain separate checks.
The package-owned `/run/lock` and `/var/lib/tailscale` directories can have
runtime metadata that differs from the local package database. The fixed
profile accepts only stable, exact owner and mode observations with a matching
native package audit. It rebuilds the directories from candidate packages and
boot policy; it does not retain their old metadata or copy their contents.
The old APK world file is reconstructable only when its bounded requests,
checksum, ownership, installed packages, and fixed role set agree. The old
Podman template, Podman host role, and Tailscale role created the base set.
Former DMZ app roles added `nginx`, pinned `cloudflared`, and sometimes `curl`.
The candidate world comes from the signed template manifest. It does not copy
these old requests or install those former app packages.
The three old resolver files have the exact Tailscale-generated form. The
collector compares each file with the live Tailnet DNS suffix and records only
hashes. The candidate uses approved DNS input and Tailscale policy; it does
not copy the old resolver file.
The Podman host role inserted one `net/tun root:netdev 0666` line into
`/etc/mdev.conf`. Removing only that line restores the exact file from the
Alpine v3.23 `mdev-conf=4.9-r0` package. Native `apk verify` accepted the
signed package with SHA-256
`cb9b68c5804508ab1a7fb53685c514ebcab132ddb58a1f3f02c2098de2b208cb`.
The fixed qualification rule checks the restored file checksum and the live
package audit. The candidate rebuilds the device rule from its package and
checked Podman recipe.
The old `/etc/passwd` and `/etc/group` files are also checked against the
signature-verified Alpine v3.23 `alpine-baselayout-data=3.7.2-r0` files.
Its archive SHA-256 is
`e6bdc54b720b4053a60aad2610fdc7b7bcc61b4ba818b650fad4f216dcf98c81`.
The collector removes only exact `klogd`, `neo`, Tailscale, and declared DMZ
package account rows for comparison. It restores the two known group membership
changes before it checks the package checksum. The candidate regenerates these
files and preserves the approved numeric `neo` identity. The shadow check
restores the signed package baseline only after it verifies a locked root,
locked service accounts, and the exact declared account set. It reports a
digest of the `neo` password hash, not the hash. `adopt prepare` compares that
digest with the checked machine input. The comparison resolves only after the
engine is approved; the candidate regenerates its shadow file.
The registry alone cannot prove retained-data absence. The initial fixed
profile refuses present applications in the registry until compute placement
support is explicitly qualified. A backend-only known retention catalog does
not block an unrelated DMZ or IoT target; an unknown dataset on the box does.

Reports are under `/var/lib/klokast/updates/discovery/qualifications/REPORT_SHA256.json`.
They contain metadata and checksums, not private key or application contents.
Exit status 1 reports blocked qualification. Missing source evidence remains
an explicit refusal; it never falls back to reading private files directly.
These smith-owned reports are review evidence, not protected acceptance records.
No application test is recorded as successful when it was not run.

`platform-update-config-audit` compares a fixed set of guest files with the
checked recipes and inventory. It writes only path names, checksums, and match
results under `/var/lib/klokast/updates/discovery/config-audits/`. Exit status
1 means at least one file differs. This comparison is read-only review evidence.
It does not approve inputs, resolve other qualification rows, or authorize
adoption. Investigate each difference before assigning its source.
When the current firewall recipe differs, `adopt prepare` also compares the
guest file with the [old checked template](../ansible/update-profiles/legacy-shared-vm-firewall-v1.j2)
from `17cfd0b`. It ignores leading indentation only. It can record one absent,
declared DMZ Tailscale underlay UDP permit as a narrower historical policy.
Any added or changed rule remains unresolved. The closed comparison receipt
contains checksums only. It is review evidence; a matching old file resolves
its own package-difference row only after the engine is approved. The candidate
uses the current firewall recipe.

The selected guests' first-contact OpenSSH cleanup has a separate fixed
playbook, `74-platform-update-bootstrap-access-retire.yml`. It checks the
active controller and current no-application intent, probes the guest through
Tailscale SSH, and refuses a running or installed OpenSSH server. It checks the
exact bootstrap policy and authorized-key set before it removes only three
files. The protected controller receipt records hashes, not key contents.
Steady-state DMZ and IoT provisioning uses the same check after Tailnet
handoff. `vm-base` adds an admin OpenSSH key only while it manages OpenSSH.
Machine identity and generated configuration still need source binding before
adoption.

The existing explicit `prepare` options, including `--inputs-only`, remain
available for controlled component tests. A successful base-only build now
writes `release-evidence.json` beside its candidate record. This closed v2
record binds the complete frozen package manifest, boot artifacts, source
receipts, and passed base tests. It records application tests as `not-run`.
It is build evidence, not an accepted release or execution authority.
`prepare --auto` has no caller-selected box or branch. It obtains the current
signed policy through `ksa-apply`, requires the three selected running shared
VMs to have fresh complete discovery, and selects the supported branch adjacent
to the oldest guest. When all guests share one supported branch and no adjacent
branch is available, it checks that branch for signed package changes. Guests
can differ by one branch only in canary and rollout order.
It builds the template once on k001. A completed automatic build has one
controller pointer. Before rollout, current signed target-branch indexes and
installed signing keys must match its frozen inputs. After the canary advances,
the pointer and exact artifacts must remain available; changed repository
indexes cannot select a new template during that rollout. Changed signing keys
block it. The controller checks every published artifact on both boxes. Only
that exact complete build returns `state: unchanged` without a new build or copy.
This command does not copy production data, assign a candidate, or replace a
guest. `adopt apply` and `run` require the remaining integration. `status` and
`verify` read protected dom0 assignments for the three selected targets. An
absent reader, pending or missing assignment, or boot drift is critical.
Accepted-release and package verification still remain unavailable and are
reported as critical.

Read current qualification counts and cleanup candidates from the active
controller's private reports. They are deployment observations, not upstream
documentation or Instance inputs. The latest selected-target reports have no
unknown files or cleanup items. Their 27 unresolved evidence rows per target
remain until the candidate engine is approved. Signed production adoption and
the production transaction are still pending. The rules are in
[component evidence](platform-update-components.md#qualification-and-cleanup-evidence).

Use `platform-update policy prepare` with the complete fresh Plan v8 evidence
set for policy approval. Sign the exact intent on the trusted workstation, then
use `policy activate` with its intent, signature, and signer ID. `policy status`,
`pause`, and `resume` retain their existing authority checks. Do not interpret
policy activation as proof that replacement is available.

Install `74-platform-update-authority.yml` before using the updated
provisioning or Platform resource Apply wrappers. It creates the fixed operation
lock. Those wrappers hold the lock until their mutation work ends; policy
control uses the same file. Direct legacy playbook calls remain outside this
serialization and must not run during an update operation.

## Production acceptance evidence

| Required evidence | Status |
| --- | --- |
| Complete target qualification and approved final machine inputs | Pending engine approval; latest reports have no unknown files or cleanup items |
| Final tested engine/toolchain, signed adoption, and standing policy | Pending |
| Controlled failed pilot replacement with automatic local recovery | Pending; synthetic transaction tests are component evidence only |
| Successful unattended replacement with changed base packages | Pending |
| Healthy 24-hour canary for the deployed template | Pending |
| Completed rollout and clean verification of all three targets | Pending |
| Identity and retained-state preservation; excluded workloads unchanged | Pending production proof |

A same-release reinstall or a synthetic test does not satisfy the unattended
update row. If no newer qualified package set exists after adoption, keep that
row pending. Do not shorten the canary or replace elapsed healthy time with a
timestamp in a fixture.

Physical dom0 reboot tests remain deferred. Keep routers and controllers
running. Test process restart and recovery from persistent journals; report
physical reboot recovery as unverified. Use [Platform recovery](platform-deploy.md#controller-recovery)
for controller recovery and console access.

Deployment findings and receipts stay in private controller operational state.
Use the [operations journal](operations-journal.md) for short case status and
evidence references. The journal is not an evidence store or recovery copy.
