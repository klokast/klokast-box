# Print appliance instructions

Follow the [Platform workload placement rule](../../doc/architecture.md#workload-placement)
and [print appliance lifecycle](../../doc/architecture.md#print-appliance).

The runtime is native CUPS in `<box>-print`, in `bak`; the physical printer is
in `iot`. Tailscale provides family IPP transport only. Do not add maintenance
accounts, SSH, escalation tools, Podman, or runtime package installation.
Do not migrate old state or implement image rollback. Jobs are disposable.

Application commands live here. Platform-owned automation owns VM images,
provisioning, enrollment, router policy, and destruction. Use the existing
verified APK input and isolated Xen build mechanisms; dom0 must not mount a
guest filesystem. There is no guest Ansible connection.

CUPS accepts authorized print submissions without separate user passwords.
Remote administrative operations must remain denied. Keep printer and LAN
flows symbolic in the manifest and resolve them in the Platform compiler.
