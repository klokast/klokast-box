# Router provisioning phases

Run these phases through `ansible/bin/provision-box` on the active controller.
The wrapper holds the Platform installation lock and runs the common router
lifecycle commands between the playbooks. Use `--from 30 --to 31` to resume a
recorded first installation. The wrapper uses the protected dom0 installation
record and its controller pointer to keep the same disk and enrollment attempt.

## 30-vm-router-alpine-build.yml

The playbook checks that the live WAN uplink is on the approved dom0 bridge.
The wrapper then installs the router state reader and boot recovery, checks the
protected router status, selects an eligible Alpine release, builds and tests
the generic router template, and prepares one retained first-install disk.
The prepared guest is not started. An accepted router is preserved.

## 31-vm-router.yml

The wrapper resumes the exact prepared installation. It starts the recorded
Xen guest, enrolls one Tailscale identity, removes temporary first-contact
access offline, boots the final disk, verifies the router, and publishes its
first accepted generation. The playbook then verifies the protected running
assignment, router services, packages, kernel, and configuration. It makes no
package or Xen changes. On a later provisioning run, the wrapper preserves the
accepted assignment and the playbook verifies it.

`ansible/bin/provision-ops-vm` also runs playbook 31. It requires a previously
accepted router and cannot create or reinstall one.

Automatic replacement updates are retired. These phases retain only first
installation and accepted-router verification. See
[VM inspection and tests](../../doc/platform-updates.md).
