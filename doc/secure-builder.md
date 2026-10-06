# Secure Klokast CLI builder

`platform-builder` builds `klokast` CLI artifacts in an isolated Xen guest.
This document describes that build procedure. Development controller setup
also builds the mutable CLI directly with vendored dependencies and installs
it at `/usr/local/bin/klokast`; see
[Development controller operations](platform-syscalls.md).
The [Platform lifecycle](platform-lifecycle.md) owns execution authority and
production release admission. A successful build does not admit a release.

Run `platform-builder` only as `smith` on the explicitly active controller:

```sh
ansible/bin/platform-builder build-klokast-cli \
  --box boxb \
  --approved-commit 0123456789abcdef0123456789abcdef01234567
```

The command requires a clean checkout of the canonical
`klokast/klokast-box` repository. The current safe branch must track its
matching `origin` branch, and `HEAD` and that upstream must both equal the
full approved commit. `--dry-run-plan` performs those authority and source
checks without downloading or creating build resources.

When the four dom0 builder tools are absent, use `--temporary-dom0-tools` only
after the package transaction is authorized for that dom0. The controller
then runs `72-dom0-builder-tools.yml` before and after the sealed build. The
tool step checks the exact managed APK world and all installed package
versions. It holds every installed version in the APK solver request, accepts
only new tool packages and dependencies, and restores the prior world and
installed package set after the build. Downloads use a private `/run` cache,
which cleanup removes after it verifies restoration. The tool step does not run `apk upgrade`,
`apk fix`, `lbu commit`, or a Xen reboot. The normal builder path still requires
tools to be present and does not change dom0 packages.

An interrupted controller process can leave the temporary tool world in RAM.
Inspect the exact builder operation and any live Xen guest before recovery.
From the same approved engine checkout, run:

```sh
ansible/bin/platform-builder recover-dom0-tools \
  --box boxb \
  --approved-commit 0123456789abcdef0123456789abcdef01234567 \
  --operation-id 012345abcdef
```

The recovery command removes only that operation's temporary virtual APK
package. It refuses package or world drift and leaves its RAM recovery record
in place when it cannot prove exact restoration. If an interrupted APK process
left the shared RAM unlock in place, recovery also refuses until an operator
checks that process and the unlock. Do not run `lbu commit` while a tool
transaction is open.

The controller exports the commit with `git archive` and obtains
`golang:1.24.13-bookworm` by its pinned linux/amd64 manifest digest. Ansible
copies those archives to dom0. Dom0 creates the sealed Alpine 3.23 template LV
when absent, then creates one 8 GiB COW snapshot and Xen domain named:

```text
<box>-builder-klokast-cli-<12-hex-operation-id>
```

The PVH guest has two vCPUs, 2048 MiB RAM, one disk, no VIF, no autostart
entry, no SSH, no Tailscale, and no network service. Its OpenRC one-shot loads
the image as an unprivileged user and runs rootless Podman with
`--network=none`, a read-only source mount, all capabilities dropped,
`no-new-privileges`, and fixed memory, CPU, PID, log, disk-COW, stage, and
guest time limits. It runs vendored tests before building `cmd/klokast` with
CGO disabled, path trimming, and VCS probing disabled.

The controller gives the guest the normalized engine repository, verified
branch ref, and approved commit. The guest binds all three values into the
binary. It also writes them to the receipt. The controller checks the receipt
values against its own inputs before it accepts the binary.

The sealed template marker identifies the Alpine base, purpose, and absence of
network or management services. It also records the job hashes that created
the template. A build does not require those template-creation job hashes to equal
the current reviewed job. Dom0 writes the current job and OpenRC helper only
to the new writable snapshot while that guest is stopped. The sealed template
stays read-only.

Dom0 reads results only after the guest stops. The controller independently
checks the input and binary hashes in the JSON receipt before installing the
root-owned binary, receipt, and redacted bounded log below:

```text
/var/lib/klokast/builds/klokast-cli/<commit>/<operation-id>/
```

Every exit path destroys the domain, Xen definition, snapshot LV, partition
mappings, mounts, and operation staging. A timeout or cleanup residue fails
the build and names every remaining resource in `cleanup.json`. The sealed
template LV is the only persistent Xen build resource.

Dependency updates are source maintenance, not artifact production. An
airunner may use an official checksum-pinned Go toolchain below its temporary
directory to update reviewed `go.sum` and `vendor/`; it must remove that
toolchain afterward, and its binaries are never deployable artifacts.

A Xen build guest uses the name `<box>-builder-<purpose>-<id>`. This naming
rule does not require every artifact build to run in a Xen guest.
