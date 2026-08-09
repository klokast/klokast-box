- Tasks that don't call a role are written in bracket, as for example `(hostname)`. Such tasks are typically built-in Ansible modules or bash commands.
- The VM identity is the default service-network identity.
- User `root` is locked on the VMs.
- These playbooks target shared inventory groups. Run them box-scoped with `--limit <box_name>`, for example `--limit yii` or `--limit duh`.

# 30-vm-router-alpine-build.yml
Build the Alpine VIRT PVH root filesystem and boot artifacts without QEMU/HVM.

- `alpine-virt-assets`: download and verify the Alpine VIRT ISO, then extract `vmlinuz-virt` and `initramfs-virt`.
- `router-alpine-rootfs`:
  - create `/dev/vg0/lv_router`.
  - partition it with a boot partition and root partition `xvda3`.
  - install Alpine VIRT packages into the mounted root.
  - seed temporary root SSH plus backend-bridge first-contact networking.
- `xen-guest-artifacts`: copy `router-kernel` and `router-initramfs` out of the installed router disk for direct PVH boot.
- `xen-guest` (also in playbook 30): render the installed-stage router Xen definition and autostart symlink, then persist dom0 diskless state.
- `dom0-apk-policy`: permit `curl`, `xorriso`, `sfdisk`, and `kpartx` only in RAM for the image operation, then remove them and restore the exact world before persistence.
- (Persist router build state on the diskless dom0 host)

# 31-vm-router.yml
Boot the VM, onboard to Tailscale (connecting to internet via the dom0 bridge), then install the router stack. If the router is not already online through its steady-state Tailscale identity, seed first-contact root SSH plus a temporary WAN/backend bootstrap network config into the installed router disk, then start the guest.

- "Converge the dom0-side router prerequisites for the selected node"
  - "Persist the dom0 bridge configuration": `network-bridges`
  - "Ensure the dom0 runtime bridges exist": `dom0-bridge-runtime`
  - "Render the installed-stage router Xen guest configuration": `xen-guest` (also used in playbook 29):  render the installed-stage router Xen definition and autostart symlink, then persist dom0 diskless state.
  - "Flush dom0 handlers before live WAN bridge verification and router startup": `flush_handlers`: flush bridge-persistence handlers. Require the live WAN uplink to already sit on `br-wan` before any router start/restart.
  - "Collect the live master bridge for the WAN uplink": compare the running router VIF count with `node_xen_guest_specs.router.vifs`; when the rendered Xen definition or live VIF set is stale, stop the guest first.
  - "Assert the live WAN uplink already sits on br-wan before starting the router"
  - "Check whether the router steady-state Tailscale identity is online"
  - "Probe the current live router VIF count"
  - "Record whether the running router VIF set is stale"
  - "Restart the router guest when its Xen definition changed"
  - "Seed first-contact router SSH and bootstrap network access when Tailscale is absent"
  - "Ensure the router guest is running": `xen-guest-instance`.
- "Enroll the router guest into Tailscale over the dom0 bootstrap SSH path"
  - "Check whether the router steady-state Tailscale identity is online"
  - "Ensure the router bootstrap known-hosts file exists on the controller"
  - "Remove any stale router bootstrap SSH host key before first-contact enrollment"
  - "Wait for router bootstrap SSH over the dom0 bridge path"
  - "Ensure Python is available over the router bootstrap SSH path"
  - "Converge the router VM base access model before Tailscale enrollment"
  - "Ensure the router Tailscale client is installed and running"
  - "Enroll the router VM into Tailscale through the auth-key wrapper"
- "Wait for the router guest to be reachable"
  - "Wait for router connectivity after any Xen restart"
- "Bootstrap python on router guests": `bootstrap-python-alpine`
  - "Ensure Python is available on the Alpine router guest"
- "Converge router guest configuration": enroll the router over the dom0 bridge path at `{{ router_bootstrap_host }}` through `{{ router_bootstrap_proxyjump }}`
  - "Converge the router VM base access model": `vm-base`
  - "Keep the router Tailscale client converged first": `tailscale-client`
  - "Reset the SSH connection after router Tailscale SSH changes" : `reset_connection`
  - "Configure the router guest": `router`; it enforces default-drop inter-zone forwarding, source-scoped WAN egress, the routed `usr` zone, and no WAN DNAT for application ingress.
  - "Flush router handlers before verification": `flush_handlers`
  - "Reset the SSH connection after router service changes": `reset_connection`
  - "Verify router service and network readiness": `router-verification`
  - "Check controller reachability to the router over Tailscale"
  - "Assert the controller reaches the router directly over Tailscale" (that is, not via a Tailscale DERP server)
  - "Show verified controller-to-router Tailscale path"
