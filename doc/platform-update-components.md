# VM update components and validation evidence

The delivery checklist and production scope are in [VM updates](platform-updates.md).
These component tests do not establish production acceptance.

## Discovery commands

Run as `smith` on the active controller from `~/src/klokast/klokast-box`:

```sh
ANSIBLE_CONFIG=ansible/ansible.cfg ansible-playbook -vv \
  -i localhost, ansible/playbooks/74-platform-update-discovery.yml
ansible/bin/platform-update scan
ansible/bin/platform-update status --json
ansible/bin/platform-update verify
ansible/bin/platform-check --box BOX --target updates
```

The setup playbook creates the discovery directories. After this code is in
the approved controller engine, use
`-e '{"platform_update_discovery_scheduled":true}'` to enable two OS cron entries.
The default leaves the schedules disabled. Discovery runs daily at 00:10 UTC.
Evidence verification runs hourly at minute 20. Commands have time limits.
Enabling these schedules also starts and enables the controller's OS `crond`
service. Disabling them removes only these two jobs; it does not stop the
shared OS cron service.
There is no replacement schedule or new daemon.
Only the explicitly active controller can run discovery. The existing
`platform-check-remote` dispatcher can request the `updates` health target.

Before engine promotion, keep candidate source in a separate public checkout
on the controller. Do not pull candidate source into the fixed approved
checkout: the installed source reader requires that checkout to match the
private engine lock. A candidate `platform-update` uses the fixed approved
mapper and verifies its source reader before collecting facts. Candidate code
does not become a sealed engine through this inspection test.

`scan` refreshes the Platform map, inventories every configured or observed
Xen VM, and collects fresh facts from running guests. Stopped VMs
stay stopped. Unclassified guests remain visible as unsupported profiles. Missing
or failed guest collection does not reuse old facts. The collector records
OS, architecture, packages, running kernel and Tailscale versions, module
directories, configuration hashes, template markers, mounts, capacity,
numeric runtime ownership, subordinate IDs, container image IDs, and volume
mounts. It does not emit container environment variables or credentials.

### Storage assessment

Each supported running VM has a `storage_assessment` in its scan report.
The text report lists named volumes, catalog matches, and path checks. JSON
also includes bind mounts, observed numeric ownership, and unresolved adoption
checks. The Music `library` catalog mapping includes both the library and
playlist volumes, including volumes with no remaining containers.

The collector reads native [Podman container](https://docs.podman.io/en/latest/markdown/podman-container-inspect.1.html),
[volume](https://docs.podman.io/en/latest/markdown/podman-volume-inspect.1.html),
and store inspection output. It compares two inventories and the mount table to detect changes
during collection. A failed inspection stays unknown. It does not become an
empty inventory. Environment variables, labels, volume option values, and
mount options stay out of the result. The supported volume layout requires
the standard persistent `neo` store, local volumes with no options, directory
paths without symlinks, and no nested mounts. Custom stores, remote volumes,
ambiguous identities, conflicting mount paths, overlapping subordinate IDs,
and partial catalog datasets produce findings. Unknown volumes, host bind
mounts (including read-only mounts), and writable container layers also block
adoption assessment.
Image-less pod infrastructure remains visible and requires a fixed
reconstruction adapter. Missing image identity never hides a container's mounts.

A catalog match identifies public software conventions only. It cannot prove
that Instance intent retains that dataset on this box. The report always sets
`adoption_ready: false` and `retention_approved: false`. It cannot account for
all host files, host services, other container accounts, or unregistered
storage. It does not read application files, measure copy capacity, verify a
backup, freeze writers, or approve application configuration. The future
adoption executor must obtain sealed retention intent and fresh complete
evidence before producing a copy request. In particular, it must not infer
retention from the current compatibility registry projection, which omits
the Instance's retained datasets. The read-only projection below supplies
these declarations without approving a copy request.

The catalog is reviewed public implementation in
[`apps/music/vm-retention.json`](../apps/music/vm-retention.json). Its paths
and names do not expand app authority or authorize deletion. Other apps and
Music runtime or identity volumes remain unclassified by this initial mapping.

The controller retains the report at
`/var/lib/klokast/updates/discovery/storage-assessment-validation-81e0f03.json`.
This is discovery evidence only. It does not satisfy the production-adoption
or [production acceptance](platform-updates.md#production-acceptance-evidence).

### Shared-VM host metadata

Shared Alpine targets also have a `host_assessment`. The controller selects this
inspection explicitly for `bak`, `dmz`, and `iot`; it does not traverse the
controller's private directories. The collector inventories account names and
numeric identities, paths outside APK ownership, service and cron script
checksums, runlevel link checksums, and filesystem boundaries. Container rows
also retain their observed runtime state, so stopped containers remain visible.

The metadata pass reads file metadata without reading application data. It excludes
password and account-description fields. It hashes bounded maintenance scripts
without emitting their contents. It does not follow directory symlinks or cross
other mounts. It records the standard Podman store as separately inventoried;
this does not approve the store's contents. Directory, path, output, and time
limits keep the inspection bounded. Missing or changed evidence stays unknown.
An unowned directory is an unresolved storage root. A separate bounded pass
now enumerates metadata below these roots: file type, size, numeric ownership,
timestamp, inode, and link count. It hashes symlink targets without following
them and opens directories through descriptors that refuse symlink replacement.
It excludes recorded mounts and the standard Podman store. Two passes must
agree. The limit is 8,192 entries and 4 MiB of metadata; each pass has at most
eight seconds within the host inspection deadline.

An incomplete, excessive, or changed tree produces `host.unowned-tree-unknown`.
It does not hide the shallow host inventory or become an empty successful tree.
Complete metadata still produces `host.unowned-tree-unclassified`: every file
needs an approved retention or reconstruction rule. This inspection does not
read file contents, establish a consistent data snapshot, or authorize removal.

The two metadata passes check topology and ownership stability, not a consistent
data snapshot. APK path ownership is only a hint; it does not prove installed
file integrity. Files outside package ownership can include generated Platform
configuration, credentials, runtime state, and user data. They need separate
approved classifications before adoption. A matching package path, unchanged
script checksum, or empty path list cannot grant adoption authority.

A separate native [APK audit](https://github.com/alpinelinux/apk-tools/blob/master/doc/apk-audit.8.scd)
now compares package-owned files, including configuration and permissions,
against the local APK database. It uses `--system --check-permissions` with an
empty protected-path list; normal `--system` alone skips protected configuration.
Each of two commands has a 20-second limit. Their path/reason records must agree,
and the full installed database must match the host inventory before and after
both commands. File contents and native diagnostics are not emitted. Parsing
is bounded to 8,192 differences and 1 MiB of output. Native error rows, timeouts,
changed evidence, and unsupported output produce `host.package-audit-unknown`.
Differences produce `host.package-differences`; they require comparison with
approved recipes and generated configuration. Empty output is only a match with
the local database. It does not prove that the database or local configuration
matches approved signed inputs, and it does not clear the adoption gate.

A separate read-only pass inspects the standard rootful Podman store at
`/var/lib/containers/storage`. It compares two bounded metadata trees and, when
present, copies at most 8 MiB of the native SQLite database into memory. It
reads only fixed registration-table counts from that copy and checks the
[Podman schema version and graph-root binding](https://github.com/containers/podman/blob/v5.6.2/libpod/sqlite_state_internal.go).
The live database is never opened with SQLite. The pass does not invoke Podman,
initialize or migrate its store, or emit container names, configuration JSON,
or environment values. Database bytes and tree metadata must remain unchanged.
The overall pass has a ten-second limit; SQL inspection has a two-second limit.

Unsupported schemas, views, legacy Bolt databases, multiple databases, journal
sidecars, unsafe paths, mounts, changed evidence, or exceeded limits produce
`host.rootful-store-unknown`. Registered state produces
`host.rootful-registrations`. An absent database or zero object counts do not
prove that layers, volumes, custom stores, or other accounts contain no data.
Those cases remain `host.rootful-store-unqualified`; adoption stays blocked.

The host inventory also correlates native init-script checksums, enabled
runlevels, and OpenRC state markers. It includes disabled scripts, manually
started services, scheduled starts, and markers with missing scripts. The
collector reads marker metadata in `/run/openrc` twice. It does not follow
marker links, emit their targets, or run service scripts.
Missing, malformed, excessive, or changing evidence blocks the
assessment. Reports from older collectors have unknown native service coverage.

`started` is an OpenRC marker, not proof that the daemon is alive or healthy.
`unmarked` does not prove that a service is stopped. Failed markers, scheduled
starts, transitions, and missing scripts have explicit findings. Native
service health, configuration, and maintenance adapters still need separate
checks. An empty container inventory cannot clear
these requirements. Discovery avoids `rc-status`: its
[dependency-cache loader](https://github.com/OpenRC/openrc/blob/0.63/src/shared/misc.c)
can rebuild the cache and execute dependency scripts.

The same bounded inventory now reads live process identities twice. It records
the boot ID, PID, parent PID, start time, numeric user and group identities,
executable path, and kernel-thread flag. For `supervise-daemon`, it correlates
only a known service name with the native service inventory. Arguments,
environment values, and process titles are not emitted. The closed v2 process
record adds fixed no-application roles. Bounded command-line reads distinguish
PID 1, console getties, and the Podman pause process from other commands that
use the same executable. Only the actual collector and its observed ancestors
can have the inspection role. Other shells and Python processes remain unknown.
Role checks do not approve service configuration or a container store.
Deleted executables, missing user executables, and supervisors
without a started marker have explicit findings. Missing or changing process
coverage remains unknown. This detects unmarked services but does not approve
their code, configuration, health, or shutdown procedure.

### Fixed empty-store qualification

For a running shared guest with empty container and volume lists, discovery
also inspects the standard rootless store without invoking Podman. It reuses
the bounded metadata walk and reads a stable database copy into SQLite memory.
The fixed layout accepts only known empty directories, zero-length lock files,
exact format markers, empty image/layer indexes, and one database whose only
row is DBConfig. Unknown paths, nonempty indexes, registrations, symlinks,
mounts, unsafe ownership, or changed evidence block empty-store qualification.
The result has no adoption authority and does not qualify other account stores.
No application image is downloaded, loaded, or run by this inspection.
The storage lock accepts the legacy hexadecimal ID or the binary timestamp,
counter, PID, and random format defined by
[containers/storage v1.59.1](https://github.com/containers/storage/blob/v1.59.1/pkg/lockfile/lastwrite.go).
The binary timestamp must agree with file metadata within one second.

### Separate boot filesystem coverage

The fixed no-application assessment includes the separately mounted `/boot`
filesystem. The guest checks its mount identity, reads bounded file metadata
twice, and hashes only the fixed kernel, initramfs, configuration, and symbol-map
names. Linked artifacts, nested mounts, changed files, and incomplete coverage
fail the check. Other files remain unknown. This is source evidence; matching
boot artifacts to approved source inputs remains a separate requirement.
The check mounts no filesystem and executes no boot artifact.

### Qualification and cleanup evidence

A qualification report is an observation. It proposes a class for each file,
account, service, timer, package difference, mount, and container-store item.
An unresolved row can mean that a generated file needs comparison with approved
machine inputs; it does not imply that the file should be deleted. Preserve
machine identity and retained datasets. Reconstruct OS state from the selected
signed template and packages.

A cleanup must name exact paths and verify their origin, ownership, current
use, and replacement or backup evidence before removal. Keep the original
qualification report and the execution receipt in protected controller state.
Do not put host-specific hashes, operation IDs, private paths, or live status
in this public component guide or in the Instance JSON. The Instance declares
only desired state. The controller stores observed qualification and operation
records outside Git.

The Static Site backup role stages a bounded guest archive and verifies an
isolated restore. It transfers the archive, manifest, and receipt to the active
controller, verifies their hashes, and rechecks the source. After successful
transfer, its checked helper removes only the matching guest staging operation.
Unexpected files or a failed transfer remain for exact reconciliation. Retain
controller copies while recovery or audit rules require them.

The fixed no-application profile still requires approved machine inputs,
independent management access, exact source-disk and boot-artifact binding,
complete host and rootless Podman accounting, and no observed or declared
application workload. Bootstrap SSH keys require an independent access check
before retirement. A package or branch that is old is an update reason, not
proof that a target is unsafe to adopt.

### Declared retention report

After approved engine promotion and controller wrapper convergence, run on
the active controller as `smith`:

```sh
ansible/bin/platform-update scan
ansible/bin/platform-update retention --json
```

The scan can return 1 for known blockers. The retention command still reads its
completed report. It calls `ksa-apply vm-retention-status` to obtain logical
datasets from the exact Instance bytes validated by the existing sealed
checker. The reader includes retained data for apps whose desired state is
`absent`. It does not infer retention from app placement or observed volumes.
The [authority rules](secret-authority.md#standing-vm-update-authority) define
the reader's checks.

The report requires a clean public checkout, the approved engine, and a complete
scan from that engine no more than two hours old. Storage assessments must use
the same catalog checksum. Missing or old evidence stays unknown. It reports
declared datasets without catalog support, missing volumes, unsupported paths,
and catalog volumes without a retention declaration on that box. Existing
storage refusals remain visible. Stopped VMs stay stopped. `observed` means
that discovery listed all required volumes with supported paths; it does not
prove data integrity, backup recovery, or application consistency.

The command checks the source again before it emits a report. It writes only
to standard output. If saved, the result belongs in controller operational
storage. It contains private box and dataset bindings. It does not change
the scan, private checkout, update policy, or VM state. It always reports
`adoption_ready: false` and returns 1 while adoption gates remain incomplete.
Reader or command failure returns 2 without a report. An older installed reader
must be upgraded through approved engine and wrapper convergence; there is no
direct private-file fallback for candidate code.

The authority setup playbook verifies this read-only interface. Local tests
cover input hashes, source changes, absent apps, wrong-box declarations,
missing datasets, stopped VMs, stale evidence, and unchanged discovery files.

The controller retains the reports under
`/var/lib/klokast/updates/discovery/scan-validation-5b2f4a8.json` and
`retention-validation-5b2f4a8.json`. Daily discovery and hourly evidence checks
are enabled. No production adoption, replacement, or standing-policy activation
occurred. Stopped guests and application containers stayed stopped.

After a signed engine promotion, old installed wrappers can prevent the normal
execution inventory from loading. For this narrow wrapper-install step, use:

```sh
ansible/bin/converge-ops-controller --box BOX \
  --inventory ansible/inventory/hosts.yml \
  -- --tags ops-controller-secret-authority-wrappers
```

This uses the checked-in bootstrap inventory and limits execution to the selected controller. Verify
the installed readers afterward. It does not permit normal Platform operations
to use bootstrap inventory as an alternative desired-state source.

### Evidence storage and freshness

`scan --existing-map` uses a map no more than two hours old, but still collects
new guest facts. Use it only to repeat a failed discovery check. The normal
daily command always refreshes the map. An installation lock prevents two
concurrent scans. A new scan invalidates the preceding report before remote
work starts.

Reports and scan logs are owner-only files under
`/var/lib/klokast/updates/discovery`. Download staging uses
`/var/cache/klokast/updates`. `scan.log` contains controller-local Ansible
diagnostics. The CLI returns 1 for critical findings and 2 for command failure.
JSON reports use stable finding codes. An incomplete report must not be treated
as successful discovery.

`status` reports discovery older than 30 hours and verification older than two
hours. `verify` currently records a critical `release.unverified` finding: the
accepted-release verifier is not yet implemented. It never converts a recent
scan into proof of a healthy replacement.

## Signed policy setup

After approved engine promotion and matching controller toolchain convergence,
run `74-platform-update-authority.yml` from the active controller to create
the root-owned policy state directory. The installed `ksa-apply` must match
the approved source. Candidate code cannot activate a policy for an older
engine.

Use `platform-update policy prepare` with the same seven fresh evidence
arguments as `ksa-apply preflight`: `--plan`, `--authority-state`,
`--controller-toolchain-receipt`, `--source-recovery-receipt`,
`--instance-source-receipt`, `--observation`, and `--build-dir`. This returns
an exact intent for human review. The human signs it on the trusted Mac with
`sign-secret-authority-intent --purpose platform-apply --intent FILE`.
Then call `platform-update policy activate --approval-intent FILE
--approval-signature FILE.sig --signer-id human-platform-apply` on the
controller. The activation has a one-hour lifetime and a single-use nonce.
Changed evidence requires fresh preparation and a new signature.

`platform-update policy status` revalidates the accepted standing policy.
`platform-update pause` sets a local restriction, including when policy is
revoked. `platform-update resume` revalidates current authority before it
removes that restriction. These commands add no VM replacement schedule.
The result explicitly reports `replacement_executor_available: false` until
the production executor is implemented.

## Candidate template construction

Run `platform-update prepare --box BOX --branch v3.23` as `smith` on the active
controller. The source must be clean, committed, and match its public upstream.
Candidate source can use a separate checkout; private inventory still comes
from the installed approved source reader. This operator command does not use
standing policy to select or replace a production VM.

`prepare --box BOX --branch v3.23 --inputs-only` verifies the package closure
and assembles the disposable boot environment without starting Xen. Each run
gets a new operation ID. A failed or incomplete run is never reused.

The controller resolves all base packages from fresh signed indexes, records
exact versions and SHA-256 checksums, and verifies native APK signatures. Native
`apk extract` runs as unprivileged `smith` without package scripts to assemble
a disposable kernel and initramfs. Installation scripts, filesystem creation,
and initramfs generation then run in a new Xen guest with no network interface
or production credentials. The guest receives read-only package input and four
new writable output disks. It has 4096 MiB of RAM, two vCPUs, and a 25-minute
construction deadline. The generic root image is 4 GiB and contains no
application image store. This does not set the capacity of a production VM's
OS or retained-data volume. A second networkless guest has five minutes (ten
with the optional app test) to boot a copy of the root image with its matching kernel and initramfs. It tests module
availability, unenrolled Tailscale startup, a rootless Podman container made
from installed BusyBox files, kernel support for nftables, and retained-data
copying between two new 256 MiB test disks. The original
generic image receives no test account or runtime state. Construction occurs
before a replacement window; it does not consume the separate 30-minute
replacement and 30-minute recovery budgets.

A second cold boot starts `/sbin/init` and the normal OpenRC runlevels on the
same disposable copy. Its fixed test service requires the cgroup and local
mount services. It verifies cgroup v2, matching kernel modules, unenrolled
Tailscale, and rootless Podman with default overlay storage and cgroup options.
It does not use the first smoke test's `vfs` or `--cgroups=disabled` overrides.
The test has a separate five-minute limit and console log. The pipeline also
prepares and boots a separate personalized clone as described below. Candidate
publication requires all test phases, exact input identities, and complete guest cleanup.
These generic tests still do not qualify target-specific network rules or
application behavior.

The first v3.24 trial was refused because the custom-init smoke boot did not
run Alpine's device setup. Rootless Podman could not open `/dev/null`.
The smoke boot now runs the installed `mdev` coldplug rules and verifies the
standard device identities and permissions. The normal OpenRC boot verifies
those devices without repairing them. Both tests must pass for this branch;
the failure does not permit an exception to rootless Podman qualification.

Dom0 reads bounded raw output bytes and verifies their checksums. It never
mounts the generated filesystem. The root image, matching kernel, and initramfs
remain under `/mnt/dom0_data/klokast-vm-templates/candidates/OPERATION`. Candidate
and cleanup evidence remains on the controller under
`/var/lib/klokast/updates/discovery/builds/OPERATION`. This is unprivileged build
evidence, not an accepted release record. Package downloads and disposable
bootstrap files remain in `/var/cache/klokast/updates/build-OPERATION`.

The Ansible build job survives an SSH disconnect and stops the disposable VM
at its deadline. Failure keeps root-owned staging for inspection. A dom0 reboot
does not restart the builder: it has no autostart entry. Confirm that its exact
recorded UUID is absent before removing interrupted staging. Automated reboot
cleanup, production configuration checks, and full application compatibility
tests remain required. Base-image boot evidence cannot pass release validation alone.

The default path does not download application images. The optional component
test below stages one unchanged catalog image. This path installs base
packages, including Tailscale and Podman, into the new generic image. It does
not enroll Tailscale or copy machine credentials into that image.

### Setup artifact cleanup

Before standing policy activation, `74-platform-update-template-cleanup.yml`
can reclaim completed disposable build inputs and older unaccepted candidates.
Run it on the active controller with the approved inventory, one dom0 limit,
and `cleanup_box=BOX`. Its default action prints a plan. Inspect that plan,
then use `cleanup_action=apply` and `cleanup_plan_sha256=SHA256` from the plan.
The helper recalculates the plan under the builder lock before removal.

Cleanup checks exact artifact hashes, build receipts, and completed guest
cleanup. It keeps the two newest successful candidates, referenced candidates,
and unknown files. It saves removed candidate manifests and the removal plan
under persistent `klokast-vm-templates/cleanup/`. Build evidence remains in
place. A partial cleanup needs inspection; do not erase its audit directory
to force a retry.

This setup helper refuses an active standing policy, production transaction
records, running disposable guests, or incomplete recovery-test cleanup.
It is not the production retention collector. That collector must also use
protected controller release references and current/previous assignments.

## Isolated application component test

From the active controller's clean public candidate checkout, run:

```sh
ansible/bin/platform-update prepare --box BOX --branch v3.23 --test-app static-site-web
```

This optional test uses the existing Static Site amd64 image lock and public
server configuration. It does not select a new app version. The controller uses
native [Skopeo digest preservation](https://github.com/podman-container-tools/skopeo/blob/main/docs/skopeo-copy.1.md)
to copy that exact public image to an OCI archive. It verifies the archive's
manifest against the catalog digest. Registry access is anonymous. The test
requires the controller's existing Skopeo tool; it does not use its Podman
runtime or application image store.

The request records the image manifest and configuration identity, archive
checksum and size, server configuration checksum, and adapter checksum.
The Ansible builder transfers the capsule as opaque bytes. Dom0 attaches it
read-only to the disposable test VM after construction. The build VM and
published generic root disk never receive the application image. The test VM
verifies the capsule and installed adapter before loading the archive through
[Podman](https://docs.podman.io/en/latest/markdown/podman-load.1.html).

The fixed app adapter runs the pinned web container as the synthetic rootless
account. It checks the loaded manifest and image identity, read-only root and
mounts, exact HTTP content, directory redirects, missing pages, and stop/start
behavior. The server reads synthetic pages and the unchanged public server
configuration. The test verifies that both stay unchanged. The container can
use only loopback inside the networkless Xen VM. No publisher, tunnel,
credential, private checkout, production data, or production disk is attached.

Application evidence is part of the candidate's boot-test result and binds the
exact test selection. A missing check, changed image, changed configuration,
changed adapter, or failed cleanup prevents successful candidate publication.
Successful evidence explicitly records `production_qualified: false`. It is
component evidence only: production port forwarding, firewall rules, runtime
UIDs, publisher and tunnel behavior, and deployed-image/configuration agreement
remain required. Other catalog applications do not yet have adapters. This
option does not authorize adoption, release acceptance, or replacement.

Candidate and cleanup receipts remain in the controller's matching
`discovery/builds/OPERATION` directory. The generic candidate remains in the
box's matching `candidates/OPERATION` directory. No production VM, application,
or data was changed. Automatic replacement remains disabled. The complete
Static Site deployment, other catalog apps, production adoption, and signed
replacement still require implementation and acceptance tests.

## Offline retained-data copy primitive

`ansible/roles/vm-retained-data/files/retained_data.py` is a Platform-owned
library for a disposable networkless Xen guest. The candidate build installs
and tests it through the existing Ansible builder role. It has no standalone
production command, disk-attachment authority, or release-selection authority.
Compromise is confined to the disposable guest and its attached disks. Dom0
does not mount or inspect either filesystem.

The closed copy request binds an operation ID, two filesystem UUIDs, exact
numeric runtime UID/GID and subordinate ranges, and explicit directory mappings.
The caller mounts `/dev/xvdc` read-only at `/source` and a new `/dev/xvdd`
filesystem at `/retained`. Both must be distinct ext4 filesystems with no alias
or nested mounts. Each destination directory uses its mapping key. The source
must have the requested runtime identities. The helper refuses broad top-level
paths, path traversal, source-path symlinks, overlapping mappings, devices,
sockets, FIFOs, and hardlinks that extend outside one mapping.

Before copying, the helper measures each complete source tree and checks free
bytes and inodes, including a 128 MiB reserve. The capacity check conservatively
uses logical file sizes, including sparse files. A destination can contain only
an empty `lost+found`. A failed or interrupted operation leaves a pending marker
and its partial data. It cannot reuse that destination or delete unknown data.

Native [rsync](https://download.samba.org/pub/rsync/rsync.1) preserves numeric
owners, modes, timestamps, hardlinks, symlinks, sparse files, ACLs, and extended
attributes. The helper independently compares SHA-256 file contents, metadata,
hardlink groups, and extended attributes on source and destination after the
copy. It flushes data before it writes the result. The bounded result contains
mapping summaries and hashes, not file contents or filenames within datasets.
It always records `adoption_accepted: false`. A copy result is not backup,
application-consistency, or production-adoption evidence.

The template test creates only synthetic data. It tests sub-ID mismatch, wrong
filesystem identity, a writable source, unsafe entries, insufficient capacity,
unknown destination data, repeat execution, and changed copied bytes. The copy
test also checks numeric owners, hardlinks, nanosecond timestamps, extended
attributes, symlink preservation, and sparse files. This does not test a catalog
app, a recoverable backup, live staging, writer shutdown, or retained machine
identities.

The future adoption executor must still derive mappings from approved catalog
adapters and Instance intent, account for all observed storage, verify a
recoverable backup, stage data before the outage, stop writers, and fence the
source VM. It must attach the source disk read-only at the Xen boundary and
enforce the outer operation deadline. It must reconstruct configuration and
Podman metadata, retain required identity state, and verify mounts and numeric
ownership before acceptance. The copy library does not provide these gates.

### Staging and final synchronization

The separate `klokast.vm-retained-stage.v1` library contract adds `stage` and
`finalize` operations. It keeps the v1 copy operation's empty-destination rule.
The staged request binds one operation, filesystem UUIDs, numeric identities,
source layout, and exact dataset mappings. Final synchronization requires the
checksum of the completed stage receipt and unchanged staged contents. Unknown
files, changed mappings, altered receipts, and incomplete operations are refused
before synchronization. An interrupted final sync cannot be retried on that
destination.

The `legacy-root` layout reads identity from the old root filesystem. The
`retained-data` layout reads a narrow identity record from an already separated
data filesystem and permits only dataset-key directory mappings. A successful
final sync writes that identity record for the next generation. This record is
copy evidence, not retention intent or execution authority.

Final sync uses checksums, including when a file keeps the same size and
timestamp. It applies deletions only inside the operation's recorded dataset
directories. It verifies content and metadata, hardlinks, sparse files, numeric
ownership, free bytes, and free inodes. Pending records are durable before
copying starts. A completed receipt still reports `adoption_accepted: false`.

Each invocation has a maximum 30-minute budget. The outer executor must impose
the remaining transaction budget, account for all host data, create and monitor
the staging snapshot, verify a recoverable backup, and stop writers before the
final sync. It must prove disk exclusivity and enforce read-only source
attachment in Xen. The library does not create snapshots or perform these
production checks.

The candidate's synthetic disk tests include staging, final changes with
unchanged size and timestamp, deletion, identity and metadata preservation,
and operation or receipt mismatch. Local tests also cover interrupted stages,
interrupted final sync, tampered staging, and copying a subsequent retained-data
generation without an old `/etc` tree.

### Legacy partitioned source disks

The `klokast.vm-retained-stage.v3` contract retains the typed v2 mappings and
adds an explicit `source_partition`: `3` selects the existing legacy root
partition on `/dev/xvdc3`; `0` selects a whole-disk ext4 filesystem. A retained
data generation must use `0`. Other partitions, implicit selection, and old
contract versions with the new field are refused.

The copy VM checks the partition's parent disk, block-layer read-only state,
filesystem UUID, mount access, and other mounts from the source disk. The
executor must also verify the Xen read-only attachment and the assigned LV
before boot. These checks do not authorize migration or infer retention intent.
Stage and final-sync receipts bind the selected partition to the request.
Dom0 still does not mount the source filesystem.

Candidate preparation requires a ninth base test, `retained_partition`. It
constructs a synthetic partitioned source on a disposable test disk, refuses
a writable source disk, and verifies identity copying from partition 3. No
production disk or identity is attached to this test.

### Exact machine identity files

The separate `klokast.vm-retained-stage.v2` contract adds typed entries to the
staging and final-sync helper. Each entry has `key`, `source`, and `type`.
`directory` uses the existing dataset rules. The `identity-file` adapter supports
key `platform-tailscale-state` at legacy source
`var/lib/tailscale/tailscaled.state`, and keys `platform-ssh-rsa`,
`platform-ssh-ecdsa`, and `platform-ssh-ed25519` at their exact
`etc/ssh/ssh_host_TYPE_key` paths. For a retained-data source, `source` must
equal its key. It cannot select a whole Tailscale directory, another credential
file, or an arbitrary host path. The original v1 directory contract stays valid
and refuses typed file entries.

The identity must be a nonempty regular file, at most 8 MiB, owned by root with
mode `0600` and one link. The helper refuses source path symlinks and preserves
numeric ownership, file bytes, timestamps, and extended attributes. Final sync
uses a checksum even when size and timestamp are unchanged. It applies no
directory deletion flags to the file. The helper neither parses nor logs
identity contents. Versioned receipts bind the exact typed request and record
only integrity evidence; they never grant adoption authority.
The old guest discovery reader measures these four files twice and records
only hashes, size, owner, group, and mode. The selected old Tailscale state
uses root UID and Tailscale GID 102; SSH host keys use root UID and GID. A v6
qualification resolves these retained-identity rows only when the exact
receipt matches complete host accounting and Tailscale is running. The copy
and personalizer still verify the private file bytes before candidate boot.

The copy runs only in the networkless migration VM. The source must be attached
read-only. The outer signed executor must prove that the old identity is no
longer active before it boots a replacement. Personalization must configure
Tailscale's [`--state` path](https://tailscale.com/docs/reference/tailscaled)
to use the retained regular file before starting Tailscale. Its parent must
remain writable for state updates. Do not bind-mount the individual file:
Tailscale [replaces state through an atomic write](https://github.com/tailscale/tailscale/blob/main/ipn/store/stores.go).
Other Tailscale state,
including Taildrop data, still requires separate accounting. Copy integrity
does not prove that encrypted or hardware-bound state can be used by the new VM.
These production attachment, fencing, and personalization steps remain
unfinished. The copy helper must not be used to enroll a second live machine.

Candidate preparation requires an eighth base test, `retained_identity`. It
uses opaque synthetic state on disposable disks, checks exact-file final sync
and the next retained generation, and refuses whole-directory mappings and
unsafe permissions. No production identity enters a generic template or test
VM. Missing or failed identity-test evidence prevents candidate publication.

## Isolated backup restore verification

`retained_data.restore_backup` verifies a complete disk backup in a networkless
maintenance Xen guest. It requires an exact read-only backup on `/dev/xvdc`
and a separate disposable restore disk on `/dev/xvdd`, with the recorded size.
The request binds the engine, protected backup receipt, disk checksum, root
filesystem UUID, and numeric runtime identity. It supports a raw ext4 root
or the recorded legacy root on partition 3.
The v1 contract checks the legacy `/var/lib/tailscale/tailscaled.state` path and
runtime mappings in `/etc`. Do not pass an adopted retained-data LV as a legacy
root disk.

The separate v2 request requires the unpartitioned `retained-data` layout,
the exact final-sync receipt for that generation, and the complete typed dataset
list. It checks the retained numeric identity and the linked stage/final records,
then measures the restored datasets and all four management identity files.
Unknown top-level data, pending copy markers, missing datasets, and unsafe
identity metadata block verification. The final-sync hashes identify the
generation; they are not required to match current application or Tailscale
contents. The full-disk checksum and new measurements cover writes made after
acceptance. The signed caller must still prove source freshness, application
consistency, and the association between the accepted OS and data generations.
These facts are not inferred from a successful restore.

The module also supplies a dedicated PID 1 maintenance entry. It reads a
checksum-bound request from a separate read-only disk and writes a bounded
receipt to a separate result disk. It checks all five disk identities and
access modes before use. The boot starts no OpenRC services, Tailscale daemon,
or application. Candidate construction tests this entry in another disposable
Xen boot with a fresh generic root, a synthetic backup, and a writable restore
disk. The template and backup remain unchanged. Production authority and
backup-to-restore orchestration still belong to the future signed caller.

The helper checks the backup bytes before writing, copies the complete disk,
and reads the copy back. Only the disposable copy can receive journal replay.
It uses the native [`e2fsck` journal-only operation](https://manpages.debian.org/bookworm/e2fsprogs/e2fsck.8.en.html),
then requires a forced read-only filesystem check. It performs no broader
filesystem repair. The restored root is mounted read-only, with execution and
devices disabled, to check runtime mappings and private Tailscale state. The
original backup must retain its exact checksum through the entire test.

An interrupted or completed attempt cannot reuse its guest staging. The
receipt contains checksums and metadata, not secret contents. The controller
must separately prove snapshot freshness, independent allocation, adequate
capacity, application consistency, and authority. This helper does not allocate
production storage, make a production backup, or authorize adoption. Dom0 must
transfer opaque blocks and must not mount the backed-up filesystem.

Candidate tests require this as the eleventh base check. The synthetic legacy
partition fixture tests full restore, ownership and identity preservation,
read-only backup enforcement, changed-backup refusal before writes, and refusal
to reuse the restore staging. No production data enters the template builder.

### Independent disk copy

`vm-retained-data/files/vm_disk_backup.py` supplies the dom0 allocation and
opaque copy primitive. It accepts an exact source LV UUID, size, source-evidence
checksum, and engine. It checks request age and free space for the snapshot,
an independent backup, a later restore disk, and a 1 GiB reserve. It refuses
thin or other unsupported LV types, mounted sources, reused allocations, and
changed block identities. It has no public command or standing authority.
The future signed caller must hold the installation mutation lease.

It creates a read-only native LVM snapshot and copies all logical disk bytes
into a new ordinary LV. It checks snapshot validity and refuses 80 percent
or greater COW usage. It makes the backup read-only, verifies the copied bytes,
and removes only the exact recorded snapshot. It never mounts a guest
filesystem on dom0. Failed destinations and interrupted allocation records
remain for inspection; an unknown allocation is never reused or removed.

The receipt binds the source, independent backup, snapshot time, and disk
checksum. It leaves restore verification, source freshness, application
consistency, and adoption acceptance false. A live snapshot alone cannot prove
application consistency. The protected caller must use the isolated restore
connection below and fresh target qualification.

`vm_backup_verify.py` connects that copy receipt to the maintenance boot. It
checks the exact backup LV and bytes, clones a tested maintenance OS, allocates
a separate restore LV, and verifies the returned request and restore receipts.
It rechecks the unchanged backup after the guest stops. Its cleanup removes
only recorded temporary resources. The resulting `klokast.vm-verified-backup.v1`
record keeps source freshness, application consistency, and adoption acceptance
false. Signed target qualification and the installation lease remain required
before production use. This internal module has no public command.

`74-platform-update-backup-test.yml` tests the primitive with synthetic LVs.
It checks snapshot bytes and later source writes, then removes only recorded
test LVs. With an explicit candidate ID, it also restores a synthetic
partitioned or retained-data layout in an isolated maintenance VM and checks
numeric ownership and machine identity. No production data enters the test.

## Isolated clone personalization

`vm-personalize/files/vm_personalize.py` applies a closed machine configuration
to a cloned OS filesystem inside a networkless Xen guest. The OS and retained
filesystems must have their recorded ext4 UUIDs on distinct fixed devices.
Retained state is read-only at both the block and mount layers. Dom0 does not
mount either filesystem.

The caller must supply approved rendered files and exact template, package,
release, and retained ownership records. The helper checks template provenance
and the complete installed package set. It creates only the declared `neo`
account and home, preserves numeric UID/GID and subordinate ranges, refuses
identity collisions, and leaves the root account locked. It does not restore
an old home or `/etc`, run package commands, or download container images.

The retained Tailscale state remains on its data filesystem. The generated
OpenRC configuration selects that ordinary file with `--state`; it does not
bind-mount a file that Tailscale must replace atomically. Machine configuration
requires the exact completed final-sync receipt and verifies the opaque
identity's content and numeric metadata against it before any OS write.
Pending copies and changed identity bytes block personalization. The v2
personalization contract also requires all three retained SSH host keys. Native
`ssh-keygen` validates their format and algorithm before any OS write. The helper
copies them to the exact `/etc/ssh/ssh_host_TYPE_key` files with mode `0600` and
records their public-key digests. It rejects keys already in a generic image
and rejects the old v1 request, which did not require SSH identity preservation.
Tailscale [uses system SSH host keys when running as root](https://github.com/tailscale/tailscale/blob/main/ssh/tailssh/hostkeys.go);
the node state file alone does not preserve this identity. Hosts that use
Tailscale's fallback key directory require separate qualification and are not
supported by this initial key-copy layout.

Configuration
includes the retained mount, network and firewall files, and fixed boot
services. The receipt records source and file checksums without configuration
contents. A failed attempt leaves a persistent marker and the clone cannot
be reused.

Personalization input can contain a machine-specific encrypted admin password.
Keep that input in restricted machine staging, outside Git and generic template
artifacts. The synthetic test uses a locked account, dummy Tailscale state, and
new disposable SSH keys. No private key fixture is committed. The public base
profile includes `openssh-keygen` for native key validation.
The Ansible candidate builder requires this tenth base test group in addition
to the separate OpenRC boot checks. The small filesystem fixture verifies file
construction and numeric ownership on disposable ext4 disks.

The builder then starts a separate networkless preparation guest. It attaches
the sealed root image read-only, copies it to a new disk, and personalizes that
copy with synthetic identity data. The controller renders the test firewall,
registry, and boot helper from the public Ansible recipes. A final cold boot
starts real OpenRC on the personalized copy and checks its files, retained
mount, Tailscale state path, firewall policies, frozen packages, and rootless
Podman under the preserved `neo` account. Tailscale must remain unenrolled.
Each new phase has a five-minute bound. Preparation and boot receipts must
match before the candidate can be published. Cleanup checks all test domains
before removing disks or boot artifacts. These tests need 14 GiB of free
persistent filesystem space; the bounded Ansible job allows 55 minutes.

These synthetic checks do not qualify a real machine identity or production
network paths. Approved per-machine input generation, target network tests,
and signed adoption orchestration are still required before production use.

Before this run, the setup cleanup play reclaimed 3,735,310,336 allocated bytes
from nine older unaccepted candidates. It kept the two newest successful
candidates and left one candidate with incomplete evidence untouched. The
dom0 cleanup record is
`klokast-vm-templates/cleanup/1797f8a4de6ad2de39eedf530a4bf457fea7fc0c54b14fd654a09b4eb06d38fc/`.

## Dom0 transaction and recovery

`74-platform-update-recovery.yml` installs the root-only
`vm-update-transaction` helper and an OpenRC boot check. Do not install it as
proof that automatic replacement is ready. The signed controller executor,
data adoption, configuration staging, network fencing, and application checks
must supply its inputs before production use.

The helper accepts only `bak`, `dmz`, and `iot`. Its protected request records
the policy and release hashes, distinct old and candidate Xen UUIDs, LV UUIDs
and sizes, exact Xen definitions, and kernel/initramfs hashes. The candidate definition must include its recorded UUID. It checks live
disk attachments, guest device names, and write modes before each switch. Dom0 never mounts a guest filesystem.
Old and candidate writable LVs must be separate ordinary volumes. Recovery
selects the unchanged old volumes; it does not merge snapshots. This requires
capacity for both generations and a verified data copy before candidate boot.

Before first adoption, `vm-update-transaction source-status --role ROLE` gives
a read-only receipt for one running, unmanaged guest. Under the dom0
transaction lock, it checks the Xen definition, live UUID and disk mappings,
ordinary LV UUIDs and sizes, boot artifact hashes, and autostart link. It
refuses a current assignment pointer, a disk mounted on dom0, or another
guest that holds a source disk. This receipt is source evidence only. The
controller must still bind it to fresh guest boot and mount evidence, approved
machine inputs, and a signed adoption operation.
The guest discovery probe accepts only `/dev/xvda3` for `/` and `/dev/xvda1`
for `/boot`. It checks each block device against its mount identity, reads the
mount inventory twice, and reports device names and numbers only. A v5 qualification compares these
devices and guest boot hashes with the dom0 source receipt. A mismatch keeps
the four boot and mount rows unresolved. The source reader must be installed
from the approved engine before this comparison runs.

The request, journal, and active role pointer stay under
`/mnt/dom0_data/klokast-vm-updates`. They are generated operation records,
outside the instance repository and diskless apkovl. Setup and execution verify
that these directories are on the writable ext4 data LV; an absent mount,
RAM filesystem, read-only mount, or different device is refused. The selected `/etc/xen`
definition and autostart link are derived configuration. Only the current role
pointer can select its boot assignment; historical records cannot override it.
An interrupted operation must retain both disk generations and its boot files.

The root-only `vm-update-transaction assignment-status --role ROLE` command
reads that current pointer under the box transaction lock. It verifies the
request, journal, box identity, selected artifacts, LV identities, and live Xen
attachments. Its JSON report identifies the selected generation and reports
configuration and autostart drift without changing either. A pending operation
has no accepted selection. A recovered legacy generation has no inferred
release hash or template profile. Generated Xen definitions carry the operation,
source configuration checksum, and accepted release provenance.
This read-only report is not a mutation lease.

When a role already has a completed assignment, the next operation must name
that exact old UUID, disk set, boot artifacts, generated configuration, and
runtime intent. Local drift cannot become its recovery definition. The journal
retains the prior operation and release identity, so failed later replacements
report the restored accepted release. Only a first legacy adoption has unknown
old release identity.

The shared-VM runtime playbook uses `reconcile-assignment` for an assigned VM.
It supplies the inspected request checksum, compiled intent checksum, and
desired running or stopped state. The helper revalidates the pointer under
the box lock and keeps that lock through configuration publication and the
native runtime change. It uses only the recorded disks and boot artifacts.
Pending or incomplete operations block this path. Runtime intent is stored
separately from the immutable release request; boot recovery preserves an
approved stopped state. This is an internal root operation under existing
general Apply authority, not standing permission to select a release.

Legacy shared-VM installers, clones, kernel extraction, and guest package
roles now refuse a protected assignment before changing it. Runtime checks
report incomplete assignments and configuration drift. Production
personalization input generation, release qualification, controller recovery of update records,
and installation-wide serialization with legacy provisioning remain required
before adoption. The legacy guard is a preflight, not a lock for its later work.

Before old-guest shutdown, the helper starts a bounded local recovery process.
The process identity includes its PID, start time, boot ID, and operation ID.
It waits at most 30 minutes for acceptance, then allows up to 30 minutes for
recovery. It does not require the controller, DNS, a backend VM, or a download.
Native commands and lock waits have time limits. Nested limits retain the
outer deadline, including time already spent waiting for the operation lock.
A permanent daemon is not added. Recovery completes a pending graceful old-VM
shutdown before restarting that VM. It never force-stops old writers or treats
a guest with an outstanding shutdown request as recovered.

The versioned `klokast.vm-switch.v2` request also requires a fixed 90-second
controller-liveness limit. Its root-only `heartbeat` operation records dom0
time. The controller executor must send heartbeats every 15 seconds during
pre-acceptance work. A missing heartbeat or backward clock movement starts
local recovery. A late heartbeat cannot revive an expired transaction, and
heartbeats never extend the 30-minute replacement deadline. Historical v1
requests retain their original deadline behavior. Acceptance ends heartbeat
rollback authority: controller loss after acceptance cannot restore old data.

Add `-e '{"recovery_controller_loss":true}'` to the disposable recovery test to
exercise the detached watchdog's native 90-second expiry. The test supplies no
heartbeat and makes no controller recovery request. It uses synthetic disks
and does not install a production helper. Controller heartbeat delivery and
production fencing remain executor integration work.

Add `-e '{"recovery_generation_chain":true}'` to test successive accepted
releases. This allocates a third disposable OS/data pair and requires 14 GiB
of free VG space. The test keeps its protected assignment through a first
acceptance, a failed next replacement, recovery, and a successful retry. It
checks release identity, refusal of the obsolete original generation, and
preservation of writes on both accepted data disks through process restart.
These are synthetic transaction checks. They do not qualify application health,
data copying, a production release, or a physical dom0 reboot.

Acceptance is written and synced before the new boot assignment is published.
After acceptance, recovery can republish the new assignment but cannot select
old data. The boot check runs before normal Xen autostart. If a record is
invalid or recovery fails, it removes shared-VM autostart entries and reports a
critical error. Router and controller autostart remain available. Existing
OpenRC dependencies are retained. See the upstream
[OpenRC service guide](https://github.com/OpenRC/openrc/blob/master/service-script-guide.md)
for dependency ordering.

The unit suite covers interrupted shutdown and start, interrupted acceptance,
changed boot artifacts and disk identities, missing recovery processes,
expired budgets, foreign disk attachments, and stale role pointers.
`74-platform-update-recovery-test.yml` runs an additional disposable Xen test
using a previously boot-tested candidate. It allocates two new OS/data LV
pairs and tests recovery from stopped, booted, and accepted stages. It repeats
these stages through the boot-recovery entry point in a new process after
stopping the disposable guest. Another case interrupts control immediately
after the native old-guest shutdown request, then starts boot recovery from
the pending journal. The accepted case also checks a synthetic
post-acceptance write. This tests restart from persistent records, not a
physical reboot or OpenRC ordering. It does not install the production helper, change `/etc/xen`, or test real applications.
The test runs the native detached watchdog, with its process identity checks,
operation locks, command budgets, and journal dispatch. It substitutes only
the test domain name, assignment directory, and apkovl persistence interface.
Add `-e '{"recovery_watchdog_expiry":true}'` to test the full 30-minute
replacement deadline. This additional case leaves the candidate unaccepted and
requires the watchdog to restore the old guest without a controller recovery
command. It keeps both data markers unchanged. The test stops its workers
before removing its disks, including after a failure. It publishes success
only after cleanup passes. The receipt includes candidate and test-code
checksums. This mode has a 70-minute limit to allow the full replacement and recovery budgets plus test
setup. Physical dom0 reboot testing is deferred as described in the acceptance
section below; this test does not establish physical reboot recovery.

To repeat on an approved test target, run from the active controller's public
candidate checkout. Use a new random 24-character lowercase hex operation ID
and an existing base-boot-tested candidate ID:

```sh
ANSIBLE_CONFIG=ansible/ansible.cfg ansible-playbook -vv \
  -i /home/smith/src/klokast/klokast-box/ansible/execution-inventory/hosts \
  ansible/playbooks/74-platform-update-recovery-test.yml --limit BOX-dom0 \
  -e 'recovery_box=BOX recovery_operation_id=NEW_ID recovery_candidate_id=CANDIDATE_ID'
```

The default test requires 10 GiB of free LVM capacity; the generation chain
requires 14 GiB. Both require 3 GiB of free Xen memory.
Evidence stays under `/mnt/dom0_data/klokast-vm-recovery-tests/OPERATION`.
If interrupted, use its `resources.json` to check exact domain and LV identities
before cleanup. Do not reuse the operation or remove disks still attached to a
guest. This test does not authorize a production update.

## Package evidence

Use the official [release metadata](https://alpinelinux.org/releases.json),
[release support policy](https://alpinelinux.org/releases/),
[package indexes](https://dl-cdn.alpinelinux.org/alpine/), and
[security databases](https://secdb.alpinelinux.org/). `main` and `community`
have separate support dates. Community does not inherit the main support date.

Each scan uses a new empty APK cache, explicit HTTPS branch repositories,
installed Alpine signing keys, native index signature verification, and native
`apk version -t` comparisons. It records checksums for downloaded inputs and
signing keys. It does not change the controller package database. It reads
bounded APK v2 index records without extracting archive paths. Multiple package
versions use native APK comparison. Unknown package
formats, conflicting identities, missing packages, failed signatures, failed
security downloads, and old evidence produce unknown or blocked results.

`packages-current` applies only to the package comparison. It does not prove
application health, matching boot artifacts, approved configuration, adopted
storage, or permission to replace a VM. Guest template markers are evidence
only; they cannot establish accepted release authority.

Automatic branch selection considers only the adjacent stable Alpine branch
from current release metadata. It requires a published x86_64 release and
unexpired `main` and `community` support. An expired source branch does not
block selection. The selected branch is an input hint: the template freezer
must still verify signed indexes and resolve the complete package set.

## Controlled DMZ app retirement

`74-platform-update-dmz-cleanup.yml` is a separately invoked setup playbook.
It is not a standing-policy action. It requires an exact list of DMZ targets,
the active controller, and checked Instance intent with both Static Site and
Nextcloud disabled. It must never select a backend VM.

The playbook stops Static Site writers and archives its fixed content,
configuration, logs, and helper paths. A guest-local restore test compares
file bytes, numeric ownership, permissions, timestamps, hardlinks, symlinks,
and extended attributes. It refuses external links, special files, mount
boundaries, changed sources, excessive data, and reuse of an operation.
The native Python tar data filter is required; there is no unfiltered fallback.

The active controller receives the archive, manifest, and receipt in its
protected backup directory. These are private backups, including any app
credentials. Keep the directory owner-only and its files mode `0600`. Do not
commit or automatically prune it.
All selected targets must pass backup and transfer verification before the
removal play starts. A target with no Static Site files records their absence.

Removal stops Nextcloud ingress, including its native proxy, and removes only
the app files on the selected DMZ guests. Backend data and VM Tailscale state
are outside this operation. The playbook verifies the VM management identity
after removal. Its evidence does not establish complete host accounting,
adoption, accepted releases, or replacement readiness.

Run from a clean, pushed public controller checkout with the approved execution
inventory. Supply `vm_update_cleanup_boxes` and a new 24-character lowercase
hexadecimal `vm_update_cleanup_operation` through an owner-only JSON extra-vars
file. The `--limit` host set must match those boxes' DMZ VMs exactly. First run
the Ansible syntax check, then run with `-vv`. Keep the full log private on the
controller. No production execution is implied by installing this playbook.
