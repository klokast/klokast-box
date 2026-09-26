# Router release inspection

The router update profile is `router-alpine-v1`. It is separate from the shared
Podman VM profile. The shared build and replacement entry points continue to
reject routers.

Run router commands as `smith` on the active controller. The inspection command
uses the verified execution inventory and Ansible. It records package versions,
boot identities, effective SSH key fingerprints, and state-file metadata in
`/var/lib/klokast/updates/discovery/router`. It does not copy private state
contents. The command does not adopt a baseline or change a router.

```sh
ANSIBLE_CONFIG=ansible/ansible.cfg ansible-playbook -vv -i localhost, \
  ansible/playbooks/74-router-update-inspection-setup.yml
ansible/bin/platform-router-update inspect --box boxa
ansible/bin/platform-router-update resolve --branch v3.23
ansible/bin/platform-router-update build-template --box boxa \
  --inputs-directory /var/cache/klokast/updates/router/INPUT_OPERATION
```

`resolve` uses native APK in a fresh scratch root. It verifies repository and
package signatures and freezes the complete dependency closure, including
`linux-virt`, in `/var/cache/klokast/updates/router`. It executes no package
scripts. A failed resolution cannot publish a complete input manifest. These
inputs are build evidence; they do not authorize replacement.

The decision contract compares effective package inputs with an accepted
release and fresh live verification. An unrelated index change or patch
announcement does not require a build. Missing metadata defers the decision.
Invalid signatures, downgrades, and drift fail the decision. A held future
branch does not block an eligible current-branch package update. Repository
support is reported separately for `main` and `community`.

The fixed state-copy primitive is in `ansible/lib/router_state.py`. It is for a
stopped source and destination inside a disposable networkless Xen guest. It
preserves bytes, permissions, ownership, and lease timestamps. The caller must
keep both routers fenced during an interrupted copy. The receipt stays on box
storage. The primitive has no authority to attach disks or start a router.

`build-template` uses a clean public checkout and inputs frozen at that exact
commit. It takes the installation lock and builds a generic partitioned router
disk in a disposable networkless Xen guest. Dom0 partitions new opaque storage;
package scripts, filesystem creation, and filesystem inspection run inside Xen.
The kernel and modules come from the signed `linux-virt` package. This path does
not use an ISO or the shared Alpine asset paths.

The template must have the exact resolved package closure and no machine or
service identity. A disposable copy then boots with its own kernel and initramfs
to test modules and service syntax, followed by a normal OpenRC boot. The CLI
writes a release receipt only after these tests succeed and the engine commit
matches the controller's signed policy source. When the public implementation
is ahead of the approved engine, the CLI reports a qualified template with
`engine_approved: false` and no release receipt. A release receipt is build
evidence, not an accepted router assignment or replacement authority.

The rootfs role now has separate `legacy` and `template` modes. Provisioning
still uses the legacy mode until the common personalization and accepted-release
path is complete. Bootstrap integration, protected baseline adoption, native compatibility qualification,
the router cutover executor, boot recovery, signed policy dispatch, and the
unattended schedule must pass their qualification gates before replacement is
enabled. No router target has been added to the Instance policy contract. The
template test does not prove old/new service-state compatibility or rollback.

Each template operation uses an exact directory under
`/mnt/dom0_data/klokast-router-templates` on dom0. The controller stores bounded
evidence under `/var/lib/klokast/updates/discovery/router`. Failed operations
retain their recorded disks for diagnosis. A guest that cannot be confirmed
stopped also retains its attachments. Do not remove these resources until the
recorded Xen UUIDs and attachments are reconciled. Successful qualification does
not install an autostart entry or modify the production router.

Run the repository tests without contacting the Platform:

```sh
python3 -m unittest discover -s ansible/tests -p 'test_router_*.py'
python3 -m unittest discover -s ansible/tests -p 'test_vm_template_inputs.py'
```

For authority and scheduling, see [VM updates](platform-updates.md). For the
filesystem isolation rule, see [guest construction](architecture.md#guest-construction-and-runtime-state).

## Provisioning protections

Router provisioning checks the protected `accepted.json` and `pending.json`
paths under `/mnt/dom0_data/klokast-router-updates` before its first mutation.
An existing record blocks legacy convergence. This applies to playbooks 30 and
31, the rootfs builder, Xen rendering, VM base configuration, Tailscale client
configuration, and enrollment. A missing record does not constitute a baseline
adoption receipt.

`provision-box` and `provision-ops-vm` use the same installation lock. Nested
shell calls reuse its inherited descriptor. Another process must wait until the
holder exits. These wrappers refuse an absent or unsafe lock file.

The router role has `converge`, `render`, `activate`, and `verify` phases.
`render` writes configuration without package installation, live sysctl changes,
interface changes, service starts, or restart notifications. The explicit
normal dnsmasq lease path is `/var/lib/misc/dnsmasq.leases`. These phases do not
supply candidate authority or bypass assignment checks.

The Alpine asset role accepts separate output paths and an approved ISO digest.
Its defaults preserve the existing shared VM paths. Its extraction receipt
binds the ISO, output paths, kernel, initramfs, modloop, and APK index. Missing or
changed cache evidence causes extraction from the selected ISO. Supplying a
digest does not establish its authority: the router build must obtain that
digest from authenticated and approved input evidence.

## Copy guest boundary

`router-state-copy-guest` refuses execution outside a networkless Xen guest.
It uses fixed source, destination, scratch, and result VBDs. It checks the box
and router hostname on both filesystems. It checks the source filesystem with
native `e2fsck -fn`. If journal recovery is necessary, it copies the source
partition to the scratch VBD and repairs only that copy. It mounts the source
read-only with journal replay disabled. A failed copy has no complete result
receipt. The operation must keep both routers fenced until the guest has
stopped and all VBDs are detached.

The source staging playbook is
`ansible/playbooks/74-router-state-copy-source.yml`. It writes the two public
source files to the controller cache for an exact `router_copy_operation`.
It does not create, attach, or boot a VM. Dom0 dispatch, real VM interruption
tests, and service-version compatibility proof remain required before this
helper can be used with production state.
