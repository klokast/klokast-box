# Platform Deployment

Platform deployment starts from the operator workstation, provisions an
authoritative `<box>-ops` controller, and then runs checked-in Ansible
workflows from that controller. Controllers consume the public
`klokast/klokast-box` source repository over HTTPS. Deployment bindings and
private state stay under `~/private/klokast/`.

Guest installation should be unattended. Steady-state infrastructure and
service VMs should come from versioned Alpine templates cloned onto dom0 LVM
storage; deployment then clones, attaches, boots, and finalizes identity and
network details.

## Dom0 console recovery

NanoKVM recovery requires local console login as `neo` with a per-box password
held by the human operator. Tailscale SSH is the normal path, but it cannot be
the only recovery path. Blank root console access is forbidden, and the
`root` password is locked.

The NanoKVM recovery invariant is represented outside Git:

```yaml
# ~/private/klokast/dom0-console.yml
dom0_console_password_hashes:
  <box-id>: "$6$..."
```

Only hashes are installed on dom0. Health checks fail closed when `neo` lacks
a usable password hash or when root is not locked.

## Controller recovery

Before full power-off, record `ops-controller-ha status` and synchronize
non-provider private state to the standby. After recovery, start one controller
and use `platform-check-remote`. Emergency promotion requires the previous
active controller to be fenced; provider authority is then reseeded from the
operator workstation.
