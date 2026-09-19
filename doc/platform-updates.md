# Controller-managed VM updates

## Completion scope

The reduced first release selects `k001-dmz`, `k002-dmz`, and `k002-iot`.
Each target must pass the fixed `shared-alpine-no-application-v1` qualification.
Both backend VMs remain excluded. `k001-iot` stays stopped. Discovery continues
to inventory every managed VM. Application adapters, application data migration,
and replacement of other VM types are separate follow-up work.

The release is **not complete**. The discovery, template builder, independent
backup and isolated restore, retained-data, personalizer, and dom0 recovery
components have native test evidence. Their production integration is unfinished.
`adopt prepare` now writes classification and exact cleanup reports. It does
not issue an adoption intent while qualification is incomplete. Signed adoption,
the standing replacement executor, automatic preparation, and rollout remain
unavailable. No production update schedule has been enabled by this work.

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
   Start with `k001-dmz`, then `k002-dmz`, then `k002-iot`. Require 24 healthy
   hours for each distinct template before wider deployment. Recheck no-app
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
ansible/bin/platform-update retention --json
ansible/bin/platform-update status --json
ansible/bin/platform-update verify
ansible/bin/platform-update prepare --box BOX --branch v3.24
```

`adopt prepare` requires one running DMZ or IoT target. It combines the installed
root registry and retention readers, verifies that both bind the same authority,
engine, private commit, and input bytes, and rechecks sources before writing.
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

The existing explicit `prepare` options, including `--inputs-only`, remain
available for controlled component tests. `adopt apply`, `prepare --auto`, and
`run` require the remaining integration. `status` and `verify` still report
missing accepted-release verification as a critical finding.

The 2026-09-19 08:58 UTC reports remain blocked with no unknown items. All
three selected guests now have 45 unresolved checks. Most require approved
machine inputs, preserved identity, source-disk binding, or package
provenance. Three per guest are bootstrap access files. The one-time Static
Site staging cleanup removed the 19 old staging rows from each DMZ after
verified controller copies were retained. The backup role also removes new
staging after verified transfer. Independent management qualification, signed
adoption, and the production transaction remain incomplete. Exact paths and
sources are in
[component evidence](platform-update-components.md#qualification-findings-on-2026-09-19).

Use `platform-update policy prepare` with the complete fresh Plan v8 evidence
set for policy approval. Sign the exact intent on the trusted workstation, then
use `policy activate` with its intent, signature, and signer ID. `policy status`,
`pause`, and `resume` retain their existing authority checks. Do not interpret
policy activation as proof that replacement is available.

## Production acceptance evidence

| Required evidence | Status |
| --- | --- |
| Complete target qualification and approved final machine inputs | Pending; classification reports identify unresolved items |
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

Detailed unresolved operational items stay in ignored `.run/todo.md`.
