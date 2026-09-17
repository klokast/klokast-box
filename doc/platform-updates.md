# Controller-managed VM updates

## Delivery status

This delivery implements the first report stage and the Instance policy
contract. It does not implement unattended VM replacement. `prepare`, `adopt`,
`run`, `pause`, and `resume` are not available. The existing guest installer
remains in use. Do not activate automatic replacement or treat a report as an
accepted template or release assignment.

The public `shared-alpine-v1` package profile includes the kernel, Tailscale,
Podman, and required base tools. Discovery compares all installed packages,
including dependencies, with signed indexes for the installed explicit branch.
It records the next adjacent stable branch as a build requirement. It does not
resolve or install a dependency closure or download application images.

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

1. Add a signed standing-policy activation and a restricted executor bound to
   the current engine and toolchain. Keep general Apply contracts unchanged.
2. Build complete dependency-frozen templates in a separate disposable Xen VM.
   Test the root disk with its matching kernel and initramfs. Preserve approved
   application images and configuration. Do not use the sealed Go builder or
   run package scripts on dom0.
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
