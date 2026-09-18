# Controller-managed VM updates

## Delivery status

This delivery implements discovery, the Instance policy contract, and signed
policy activation with `pause` and `resume`, candidate template construction,
offline base-image boot tests, and an optional Static Site web component test.
A dom0 disk-switch transaction and boot recovery helper are implemented but are not connected to a production executor.
Offline retained-data copy, staging, and final-sync primitives are implemented
and included in the synthetic candidate tests. They are not a complete
data-adoption workflow.
Discovery also reports storage refusals and catalog matches for the Music
library dataset. These matches are not approved retention or copy requests.
The read-only `retention` report compares these observations with declared
datasets from the root reader's checked Instance projection.
It does not implement unattended VM replacement. `adopt` and `run` are not available.
The existing guest installer remains in use. Do not activate automatic
replacement or treat a report as an
accepted template or release assignment.

The public `shared-alpine-v1` package profile includes the kernel, Tailscale,
Podman, and required base tools. Discovery compares all installed packages,
including dependencies, with signed indexes for the installed explicit branch.
It records the next adjacent stable branch as a build requirement. It does not
resolve or install a dependency closure or download application images. The
separate `prepare` command freezes the full base-package closure for a build.

The separate Python safety rules test artifact identity, kernel/module
agreement, dependency independence, the maintenance cutoff, and pre-acceptance
versus post-acceptance recovery. They do not prove a successful production
replacement. The separate dom0 helper is described below.

Normative input and state ownership rules are in the
[Instance specification](klokast-instance-specification.md#shared-vm-update-intent).
[Secret Authority](secret-authority.md#standing-vm-update-authority) owns
activation and execution authority. The private instance remains read-only on
the controller.

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

On 2026-09-17, a native read-only scan at source `81e0f03` completed storage
collection with stable inventories on all five running shared VMs. It found
both Music data volumes. Unknown volumes, bind mounts, writable layers, and
missing app or pod adapters remained explicit findings. Every assessment kept
adoption blocked. Exit status 1 reported these critical findings; it was not
a collection failure. Ansible syntax validation and 132 relevant local tests
passed. No production workload changed and no update schedule was enabled.

The controller retains the report at
`/var/lib/klokast/updates/discovery/storage-assessment-validation-81e0f03.json`.
This is discovery evidence only. It does not satisfy the production-adoption
or replacement acceptance gates below.

### Shared-VM host metadata

Shared Alpine targets also have a `host_assessment`. The controller selects this
inspection explicitly for `bak`, `dmz`, and `iot`; it does not traverse the
controller's private directories. The collector inventories account names and
numeric identities, paths outside APK ownership, service and cron script
checksums, runlevel link checksums, and filesystem boundaries. Container rows
also retain their observed runtime state, so stopped containers remain visible.

The host scan reads file metadata without reading application data. It excludes
password and account-description fields. It hashes bounded maintenance scripts
without emitting their contents. It does not follow directory symlinks or cross
other mounts. It records the standard Podman store as separately inventoried;
this does not approve the store's contents. Directory, path, output, and time
limits keep the inspection bounded. Missing or changed evidence stays unknown.
An unowned directory is an unresolved storage root. Its contents are not
enumerated by this metadata pass and must remain intact until an approved
adapter accounts for them. Mount boundaries inside such a root remain visible.
This prevents large application trees from hiding all other host evidence.

The two metadata passes check topology and ownership stability, not a consistent
data snapshot. APK path ownership is only a hint; it does not prove installed
file integrity. Files outside package ownership can include generated Platform
configuration, credentials, runtime state, and user data. They need separate
approved classifications before adoption. A matching package path, unchanged
script checksum, or empty path list cannot grant adoption authority.

The host inventory also correlates native init-script checksums, enabled
runlevels, and OpenRC state markers. It includes disabled scripts, manually
started services, scheduled starts, and markers with missing scripts. The
collector reads marker metadata in `/run/openrc` twice. It does not follow
marker links, emit their targets, run service scripts, or inspect daemon
arguments. Missing, malformed, excessive, or changing evidence blocks the
assessment. Reports from older collectors have unknown native service coverage.

`started` is an OpenRC marker, not proof that the daemon is alive or healthy.
`unmarked` does not prove that a service is stopped. Failed markers, scheduled
starts, transitions, and missing scripts have explicit findings. Native
service health, processes outside OpenRC, configuration, and maintenance
adapters still need separate checks. An empty container inventory cannot clear
these requirements. Discovery avoids `rc-status`: its
[dependency-cache loader](https://github.com/OpenRC/openrc/blob/0.63/src/shared/misc.c)
can rebuild the cache and execute dependency scripts.

On 2026-09-18, candidate source `9f1d977` completed native validation on all
five running shared VMs. Both inventory passes agreed. Native application
service markers remained visible on a VM with no containers; the assessment
kept adoption blocked. The stopped shared VM stayed stopped. All 184 relevant
local tests and the controller Ansible syntax check passed. Exit status 1
reported unresolved findings, not collection failure. The candidate did not
change the approved engine, installed wrappers, guest packages, or services.
The controller retains the report at
`/var/lib/klokast/updates/discovery/native-services-validation-9f1d977.json`.

On 2026-09-17, a native scan at `3a8c9f4` completed stable host metadata
collection on all five running shared VMs. The scan kept every target blocked
on unclassified paths, unqualified maintenance files, and incomplete adoption
checks. It also distinguished stopped application containers from running
services. The report is retained on the controller at
`/var/lib/klokast/updates/discovery/host-assessment-validation-3a8c9f4.json`.
No application, VM, or update schedule changed during this inspection.

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
On 2026-09-18, engine `5b2f4a8` passed live validation after signed promotion
and installed-wrapper convergence. Discovery completed for 12 VM entries.
The retention report matched both Music library volumes to declared retention
for an absent app. It kept unclassified application and host data, writable
container layers, and the remaining adoption gates blocked. Exit status 1
reported these findings; it did not indicate a reader failure.

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

On 2026-09-17, operation `eaafa2b0d7c96378108713e2` at source `b9dc4d0`
passed all six base test groups and all eight Static Site component checks on
k002-dom0. It used the unchanged catalog manifest
`sha256:1a5b9e155d6921968e9bfe5107774a57e3bedf00436675c7d95298c906a057e2`.
The candidate booted kernel `6.18.52-0-virt` with Podman `5.7.0-r6` and
Tailscale `1.90.9-r6`. Both lifecycle records report `cleaned`; the disposable
guests, loop attachments, and temporary test disks were removed. The six base
test groups include retained-data copying and its ten refusal or corruption
checks. The controller syntax check and 108 relevant local tests passed.

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

On 2026-09-17, candidate operation `a0eb34b4a6aaac0d48dbdc1e` at engine
`ae9cf11` passed all six native Xen test groups, including the retained-data
copy and ten refusal or corruption checks. Build and test lifecycle records
both report `cleaned`: both disposable domains, loop attachments, and temporary
test disks were removed. The candidate remains unaccepted. No production data,
VM, or application was changed. The relevant local suites passed 97 tests.
The earlier operation `c93d43e35ad6a5670ca5ec36` stopped before copying because
the UUID probe used unsupported util-linux options with BusyBox `blkid`.
Its cleanup passed. The corrected probe uses native BusyBox output and rejects
ambiguous records. Controller build receipts and logs retain both results.

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

### Exact machine identity files

The separate `klokast.vm-retained-stage.v2` contract adds typed entries to the
staging and final-sync helper. Each entry has `key`, `source`, and `type`.
`directory` uses the existing dataset rules. The initial `identity-file`
adapter supports only key `platform-tailscale-state` and legacy source
`var/lib/tailscale/tailscaled.state`. For a retained-data source, `source` must
equal that key. It cannot select a whole Tailscale directory, another credential
file, or an arbitrary host path. The original v1 directory contract stays valid
and refuses typed file entries.

The identity must be a nonempty regular file, at most 8 MiB, owned by root with
mode `0600` and one link. The helper refuses source path symlinks and preserves
numeric ownership, file bytes, timestamps, and extended attributes. Final sync
uses a checksum even when size and timestamp are unchanged. It applies no
directory deletion flags to the file. The helper neither parses nor logs
identity contents. Versioned receipts bind the exact typed request and record
only integrity evidence; they never grant adoption authority.

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

On 2026-09-18, source `14ff843` passed all eight native base test groups in
operation `466b474faf45e10a820f4f9f`. The identity test verified file contents,
numeric ownership, permissions, metadata, and the next retained generation.
It refused whole-directory and unsafe-permission requests. Both disposable VM
lifecycle records reported cleanup. All 193 relevant local tests and the
controller Ansible syntax check also passed. The result remains an unaccepted
candidate, with no production identity or data used. Evidence is under
`/var/lib/klokast/updates/discovery/builds/466b474faf45e10a820f4f9f` on the controller.

On 2026-09-17, operation `d1b0a326a02f008c4b083b17` at source `e646a3f`
completed `platform-update prepare` on k002 with all seven base test groups,
including staged retained-data synchronization. Both disposable guest lifecycle
records passed cleanup. The initial native attempt at `70102bd` passed the
guest tests but exposed a controller test-list mismatch; `e646a3f` corrects that
check and adds controller acceptance and refusal tests. Neither run adopted or
replaced a production VM. Candidate receipts remain under the matching
controller `discovery/builds/OPERATION` directory. Production snapshot staging,
backup qualification, writer fencing, and signed execution remain required.

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

The request, journal, and active role pointer stay under
`/mnt/dom0_data/klokast-vm-updates`. They are generated operation records,
outside the instance repository and diskless apkovl. Setup and execution verify
that these directories are on the writable ext4 data LV; an absent mount,
RAM filesystem, read-only mount, or different device is refused. The selected `/etc/xen`
definition and autostart link are derived configuration. Only the current role
pointer can select its boot assignment; historical records cannot override it.
An interrupted operation must retain both disk generations and its boot files.

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

Native validation on 2026-09-18 passed all eight disposable recovery cases,
including controller loss. The detached watcher restored the old generation
in 104 seconds without a controller recovery request. The accepted restart
case preserved its later write. Cleanup confirmed that all four test LVs and
the test domain were removed, and that production domain UUIDs were unchanged.
This is evidence for the local transaction primitive, not a production
replacement or physical dom0 reboot test.

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

On 2026-09-17, operation `61ab8c3297a98b8828a5e86f` passed all three native
Xen cases on k002-dom0 with candidate `0d66858d5082567778a3d3b3`. Test data
markers stayed unchanged. Cleanup removed the disposable guest and all four
test LVs, and verified that production domain UUIDs stayed unchanged. No
production VM was replaced and the production boot hook was not installed.
The final code at `2b9684f` passed the same native cases in operation
`407a52ac0bbe921c4e828c0b`, including cleanup. The relevant unit suites passed
74 tests. Native watchdog expiry and physical dom0 reboot remain untested at that commit.

The full native watchdog test at `718ecb0` passed in operation
`0b8065f399885cbfa958bfa6`. The unaccepted candidate recovered to its old disks
after 1814.725 seconds, including the complete 1800-second replacement wait.
Both data markers stayed unchanged. Cleanup removed the disposable guest and
all four LVs and verified unchanged production domain UUIDs. This closes the
native watchdog-expiry gate; physical dom0 reboot remains untested.

The final recovery code at `e96a911` passed all seven native cases in operation
`fa159d83e99659087e917d51`. This includes interrupted old-guest shutdown,
recovery in a new process, and preservation of the synthetic write made after
acceptance. Cleanup passed, including unchanged production UUIDs. The relevant
unit suites passed 82 tests. Neither run replaced a production VM, changed an
application, installed the production boot hook, or activated update policy.

To repeat on an approved test target, run from the active controller's public
candidate checkout. Use a new random 24-character lowercase hex operation ID
and an existing base-boot-tested candidate ID:

```sh
ANSIBLE_CONFIG=ansible/ansible.cfg ansible-playbook -vv \
  -i /home/smith/src/klokast/klokast-box/ansible/execution-inventory/hosts \
  ansible/playbooks/74-platform-update-recovery-test.yml --limit BOX-dom0 \
  -e 'recovery_box=BOX recovery_operation_id=NEW_ID recovery_candidate_id=CANDIDATE_ID'
```

The test requires 10 GiB of free LVM capacity and 3 GiB of free Xen memory.
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

## Replacement and recovery acceptance gates

### Controlled DMZ app retirement before adoption

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

The active controller receives the archive, manifest, and receipt under
`/home/smith/src/klokast/klokast-box/.run/vm-update-backups/static-site/OPERATION/HOST/`.
These are private backups, including any app credentials. Keep the directory
owner-only and its files mode `0600`. Do not commit or automatically prune it.
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

### Production replacement gates

The following work is required before enabling replacement:

1. Complete the restricted replacement executor under the signed standing
   policy. Keep general Apply contracts unchanged.
2. Qualify the base-image build and boot evidence against each target's approved
   application images and generated configuration. Construction and offline
   base tests are implemented above. Application compatibility and production
   network policy tests remain required before accepting a release.
3. Complete controlled retained-data adoption and fixed catalog maintenance
   adapters. Require a verified backup, ownership and mapping checks, writer
   quiescence, and retention of the original disk. Block unknown data and
   unrecorded container changes. The offline copy primitive above supplies only
   directory copying and integrity checks. The discovery storage assessment
   provides catalog matches and refusals, not approved adoption mappings.
4. Connect the dom0 transaction above to approved input staging, network
   fencing, data copying, and the signed executor. Verify recovery independence
   and capacity for separate old and candidate disks before stopping the guest.
5. Persist acceptance on dom0 before production access or background work.
   Verify a 24-hour healthy canary for each template before wider rollout.
   Use one installation-wide operation lock and stable box/role ordering.
6. Demonstrate one unattended replacement and a failed replacement with local
   recovery. Verify unchanged app versions and preserved data. Exercise
   controller disconnection and process restart from persistent journals at
   each transaction stage.

Physical dom0 reboot tests are deferred by the operator's current scope. They
do not block VM-only activation after the other gates pass. Do not reboot dom0
in this delivery, and do not describe process restart tests as physical reboot
tests. Report physical reboot recovery as unverified until a later hardware
test exercises the installed OpenRC ordering and persistent records.

### Remaining delivery order

1. Produce complete workload and
   storage coverage for selected VMs. Account for host services, timers, other
   runtime accounts, and unknown storage. Resolve undeclared workloads through
   approved intent; a catalog match cannot authorize adoption or removal.
   The delivered retention reader has passed approved-engine validation. Keep
   deferred VM roles outside adoption and replacement; inventory can still
   report their unresolved data. Carry durable exclusions in the reviewed
   Instance policy before activation.
2. Add fixed maintenance adapters for all declared workloads on selected VMs,
   including native services and retained data for absent apps. Qualify exact
   deployed images, configuration, backups, and synthetic application and
   network tests. Native application versions also stay unchanged; an
   incompatible package set blocks the branch candidate.
3. Complete separately signed adoption with measured capacity and time,
   read-only staging snapshots, writer shutdown, final synchronization, identity
   preservation, and recovery to the original disk generation. Staging and
   final-sync primitives above do not authorize this operation.
4. Add protected release and assignment records. Connect normal provisioning
   and reconciliation to those assignments before production adoption. Prevent
   legacy package resolution, old kernels, and old repository branches from
   replacing accepted state. Include execution records in controller recovery.
5. Connect the standing-policy executor to local recovery, persistent traffic
   fencing, background-work control, independent dependencies, and fresh checks.
   Persist acceptance before admitting production writes. Add bounded controller
   liveness checks without extending the replacement deadline.
6. Add automatic candidate selection, daily bounded preparation, the 02:00 UTC
   replacement schedule, the 03:00 cutoff, installation-wide serialization,
   hourly verification, and 24-hour canaries. Stop rollout after any failure.
   Keep stopped VMs stopped. Cleanup must preserve all referenced resources.
7. Promote the final engine and toolchain, obtain exact adoption signatures,
   prove a controlled failed pilot replacement and recovery, then demonstrate
   an unattended replacement and a healthy canary before wider rollout.

All steps retain the existing state ownership and authority contracts. Scheduled
commands do not write either Git repository. Production completion requires
real replacement and recovery evidence; a successful builder or unit suite is
not sufficient. Public implementation changes must be committed and pushed.

Until those gates pass, a VM problem uses the existing approved provisioning
and recovery procedures. Do not manually create an accepted-release record.
Do not roll back a data checkpoint after production acceptance: that can lose
new writes. A post-acceptance failure must stop further rollout and report a
critical finding. Recovery before acceptance must use only the recorded old
VM, OS disk, boot files, and checkpoint. Keep the router and controller running.

For console access, see [Platform deployment recovery](platform-deploy.md).
The boot-time recovery check must run before normal guest autostart and
must require no controller, backend VM, internet, DNS service in the target, or
package download.
