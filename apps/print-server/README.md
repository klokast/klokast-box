# Print Server

Private CUPS printing for a box-local Ethernet printer.

## Model

- `bak`: rootless Podman CUPS pod and app-specific Tailscale ingress.
- `iot`: managed low-trust printer device only.
- Access: Tailnet IPP on `ipp://<box>-bak-printer.<tailnet>:631/printers/epson`.

Declare the application present in the private Instance. This fragment shows
its `apps` entry; keep the other required Instance fields:

```json
{
  "apps": {
    "print-server": {
      "desired-state": "present",
      "placement": {
        "mode": "multi-box",
        "boxes": [
          "boxb"
        ]
      },
      "devices": {
        "printer": {
          "boxb": {
            "hostname": "boxb-iot-printer",
            "ipv4-address": "192.168.150.78",
            "mac": "02:00:00:00:00:78"
          }
        }
      }
    }
  }
}
```

Validate and publish desired-state changes through the
[Instance workflow](../../doc/klokast-instance-specification.md). The controller
tools consume the validated resource view derived from Instance.
Replace the example device address and MAC with the printer binding for the
selected box. Remove its saved `inactive-apps` entry when enabling it.

Apply platform resources from the active controller as `smith`, then
install:

```sh
ansible/bin/platform-image-build --app print-server --box boxb build
ansible/bin/platform-image-build --app print-server --box boxb load
apps/print-server/bin/print-serverctl install \
  --box boxb \
  --resources-registry ~/private/klokast/platform-resources.yml
```

Verify:

```sh
apps/print-server/bin/print-serverctl verify \
  --box boxb \
  --resources-registry ~/private/klokast/platform-resources.yml
```

The default queue name is `epson`. The default printer URI is
`ipp://<printer-ip>/ipp/print`.

`print-serverctl install` also runs the same build/load steps before deploying
the backend pod. The explicit commands above are useful when validating the
trusted ops-side builder path independently.

## Print from a MacBook

Connect Tailscale with a family account. Open **System Settings → Printers &
Scanners → Add Printer, Scanner, or Fax**, then select the **IP** tab.

- Address: `<box>-bak-printer.<tailnet>:631`
- Protocol: **Internet Printing Protocol – IPP**
- Queue: `printers/epson`
- Name: a printer name that you can identify, such as `Klokast Epson`
- Use: **AirPrint**, when macOS offers it

Click **Add**. Open a document, press **⌘P**, and select this printer.
Keep Tailscale connected while you print. The MacBook and printer do not need
to use the same LAN.

## Bonjour and printing on the residential LAN

Enable the box's `rg-lan` capability and subnet as described in the
[Instance contract](../../doc/klokast-instance-specification.md#residential-gateway-lan-printing),
then set the app feature `lan-printing: true`. Prepare and deploy the current
`router-alpine-v3` image through `platform-update` before applying Platform
resources. Apply those resources, then run `print-serverctl install` and
`print-serverctl verify`. The queue is `epson`; a different queue is refused
when LAN discovery is enabled.

On a Mac connected to this LAN, open **Printers & Scanners → Add Printer**.
Select **Epson XP-55 (BOX)** in the default discovery list and click **Add**.
Tailscale is not required. Bonjour resolves `<box>-bak-printer.local` to the
router's LAN address; IPP jobs pass to the existing CUPS pod on `<box>-bak`.

The physical printer's DHCP name is `<box>-iot-printer`. It is not a VM.
Existing manual queues using the old `<box>-print` hostname must be re-added.
The deployment retains CUPS volumes and the existing Tailscale state while
renaming the service identity. Disable `lan-printing`, apply resources and
reinstall the service to withdraw LAN discovery and access.

To submit exactly one test page through the LAN path when enabled, run
`apps/print-server/bin/print-serverctl test-page --box BOX` on the active
controller. This command first checks Bonjour, IPP and administration denial.
It does not change the Mac's printer list.
