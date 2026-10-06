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
      }
    }
  }
}
```

Validate and publish desired-state changes through the
[Instance workflow](../../doc/klokast-instance-specification.md). The controller
tools consume the validated resource view derived from Instance.
Installation also requires the printer MAC address and IP address in the resource view.

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
