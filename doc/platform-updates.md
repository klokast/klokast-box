# Controller-managed VM updates

## Delivery status

This delivery implements discovery, the Instance policy contract, and signed
policy activation with `pause` and `resume`, candidate template construction,
and offline base-image boot tests.
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
versus post-acceptance recovery. They are not a privileged executor, local
recovery job, or proof of a successful production replacement.

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
OS or retained-data volume. A second networkless guest has five minutes to boot a
copy of the root image with its matching kernel and initramfs. It tests module
availability, unenrolled Tailscale startup, a rootless Podman container made
from installed BusyBox files, and kernel support for nftables. The original
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
cleanup, production configuration checks, and application compatibility tests
remain required. Base-image boot evidence cannot pass release validation alone.

Application containers are not downloaded or updated. This path installs base
packages, including Tailscale and Podman, into the new generic image. It does
not enroll Tailscale or copy machine credentials into that image.

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

The following work is required before enabling replacement:

1. Complete the restricted replacement executor under the signed standing
   policy. Keep general Apply contracts unchanged.
2. Qualify the base-image build and boot evidence against each target's approved
   application images and generated configuration. Construction and offline
   base tests are implemented above. Application compatibility and production
   network policy tests remain required before accepting a release.
3. Implement controlled retained-data adoption and fixed catalog maintenance
   adapters. Require a verified backup, ownership and mapping checks, writer
   quiescence, and retention of the original disk. Block unknown data and
   unrecorded container changes.
4. Stage all dependencies locally, verify recovery independence, fence the old
   guest, and arm bounded box-local recovery before stopping it. Check retained
   storage capacity and checkpoint health before acceptance.
5. Persist acceptance on dom0 before production access or background work.
   Verify a 24-hour healthy canary for each template before wider rollout.
   Use one installation-wide operation lock and stable box/role ordering.
6. Demonstrate one unattended replacement and a failed replacement with local
   recovery. Verify unchanged app versions and preserved data. Exercise
   controller disconnection and dom0 reboot at each transaction stage.

Until those gates pass, a VM problem uses the existing approved provisioning
and recovery procedures. Do not manually create an accepted-release record.
Do not roll back a data checkpoint after production acceptance: that can lose
new writes. A post-acceptance failure must stop further rollout and report a
critical finding. Recovery before acceptance must use only the recorded old
VM, OS disk, boot files, and checkpoint. Keep the router and controller running.

For console access, see [Platform deployment recovery](platform-deploy.md).
The future boot-time recovery check must run before normal guest autostart and
must require no controller, backend VM, internet, DNS service in the target, or
package download.
