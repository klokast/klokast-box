# Print Server

Private CUPS printing for a box-local Ethernet printer.

## Model

- `bak`: rootless Podman CUPS pod and app-specific Tailscale ingress.
- `iot`: managed low-trust printer device only.
- Access: Tailnet IPP on `ipp://<box>-print.<tailnet>:631/printers/epson`.

Enable the app in the private platform-resource registry:

```yaml
apps:
  print-server:
    enabled: true
    placement:
      boxes:
        - boxb
    devices:
      printer:
        boxb:
          mac: "02:00:00:00:00:02"
          ipv4_address: 192.168.150.78
          hostname: boxb-printer
    resources: {}
```

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
