# Print appliance

Printing runs in a dedicated disposable Alpine PVH VM, `<box>-print`, in the
backend zone. The physical Ethernet printer remains a managed IoT device.
Native CUPS provides queue `epson`. Tailscale provides family IPP access only;
SSH, interactive login, remote CUPS administration, and runtime package
management are absent. See [workload placement](../../doc/architecture.md#workload-placement).

Declare `print-server` present in the private Instance, select its boxes, and
provide the printer binding and a free backend address:

```json
{
  "desired-state": "present",
  "placement": {"mode": "multi-box", "boxes": ["boxb"]},
  "app-vms": {"cups": {"boxb": {"vm-ipv4-address": "192.168.100.30"}}},
  "devices": {"printer": {"boxb": {
    "hostname": "boxb-iot-printer",
    "ipv4-address": "192.168.150.78",
    "mac": "02:00:00:00:00:78"
  }}},
  "features": {"lan-printing": true}
}
```

LAN printing requires the box's `rg-lan` capability and private LAN subnet.
The Platform owns router discovery, forwarding, printer DHCP, and firewall
policy. The application does not grant itself network access.

Run on the active controller as `smith`, from the public repository:

```sh
ansible/bin/platform-resources --app print-server --approved-commit "$(git rev-parse HEAD)" apply
apps/print-server/bin/print-serverctl preflight --box boxb
apps/print-server/bin/print-serverctl install --box boxb
apps/print-server/bin/print-serverctl verify --box boxb
```

Build on the selected box's own controller. `install` and `rebuild` construct
and qualify an image through the common verified APK inputs and isolated Xen
build transport, then replace the appliance. Dom0 never mounts its filesystems.
There is no state migration, historical image retention, or rollback. If a
replacement fails, correct the problem and build a new image. Source and build
metadata remain available; old image bytes do not.

The OS root is read-only. Print jobs and logs use bounded temporary storage.
Only Tailscale identity survives a reboot, in a writable partition on the same
disposable disk. Rebuilding replaces that identity. Remove the old offline
registration through the existing controller device broker before enrollment.
No infrastructure credentials enter the appliance; its enrollment key is
single-use and removed after successful enrollment.

Queue setup queries the physical printer at boot. Keep the printer connected
and powered on. If it is unavailable during boot, CUPS and Tailscale still
start, but queue setup reports a failure. Restore the printer link and restart
the VM through Xen. This does not require a guest login.

Verification uses IPP, Bonjour, and Xen evidence; it does not enter the guest.
The controller keeps build manifests and logs under
`/var/lib/klokast/print-appliance`. Diagnose failures there and through the
bounded Xen console. Rebuild instead of installing maintenance tools.

To print one test page and monitor its completion:

```sh
apps/print-server/bin/print-serverctl test-page --box boxb
```

On the residential LAN, add **Epson XP-55 (BOX)** through Mac printer discovery
and select **AirPrint**. For remote access, connect Tailscale and add the IPP
printer at `<box>-print.<tailnet>:631`, queue `printers/epson`, using AirPrint.
Remove manually configured queues that use the retired `-bak-printer` name.
