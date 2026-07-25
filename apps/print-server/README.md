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
        - k002
    devices:
      printer:
        k002:
          mac: "02:00:00:00:00:02"
          ipv4_address: 192.168.150.78
          hostname: k002-printer
    resources: {}
```

Apply platform resources from the active controller as `smith`, then
install:

```sh
ansible/bin/platform-image-build --app print-server --box k002 build
ansible/bin/platform-image-build --app print-server --box k002 load
apps/print-server/bin/print-serverctl install \
  --box k002 \
  --resources-registry ~/private/klokast/platform-resources.yml
```

Verify:

```sh
apps/print-server/bin/print-serverctl verify \
  --box k002 \
  --resources-registry ~/private/klokast/platform-resources.yml
```

The default queue name is `epson`. The default printer URI is
`ipp://<printer-ip>/ipp/print`.

`print-serverctl install` also runs the same build/load steps before deploying
the backend pod. The explicit commands above are useful when validating the
trusted ops-side builder path independently.
