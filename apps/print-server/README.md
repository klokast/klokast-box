# Print Server

Private CUPS printing for a box-local Ethernet printer.

## Model

- `bak`: rootless Podman CUPS pod and app-specific Tailscale ingress.
- `iot`: managed low-trust printer device only.
- Access: Tailnet IPP on `ipp://<box>-print.<tailnet>:631/printers/epson`.

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
            "hostname": "boxb-printer",
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

- Address: `<box>-print.<tailnet>:631`
- Protocol: **Internet Printing Protocol – IPP**
- Queue: `printers/epson`
- Name: a printer name that you can identify, such as `Klokast Epson`
- Use: **AirPrint**, when macOS offers it

Click **Add**. Open a document, press **⌘P**, and select this printer.
Keep Tailscale connected while you print. The MacBook and printer do not need
to use the same LAN.
