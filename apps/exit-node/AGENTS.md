# Exit appliance instructions

Follow the [Platform workload placement rule](../../doc/architecture.md#workload-placement)
and the [exit appliance authority model](../../doc/architecture.md#exit-appliance).

The runtime is kernel forwarding and upstream `tailscaled` in `<box>-exit`, in
`dmz`. Do not add maintenance accounts, SSH, escalation tools, Podman, proxies,
DNS services, or runtime package installation. There is no state to migrate.

Platform-owned automation owns the image, enrollment, router policy, Tailnet
grant and destruction. Keep network flows symbolic in the manifest. The Tailnet
policy template owns who can use the exit; the manifest cannot grant it.
