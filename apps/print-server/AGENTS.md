# Print Server App Instructions

This app provides a private CUPS print queue for one box-local IoT printer.
Keep app-specific automation under `apps/print-server/`.

## Architecture

- Runtime host: rootless Podman pod on `<box>-bak`.
- Printer endpoint: low-trust Ethernet printer on the router-controlled IoT
  segment.
- User access: app-specific Tailscale identity `<box>-print` with `tag:print`.
- Job submission, including document transfer and job closure, uses the
  existing Tailnet authorization. It must not require a separate CUPS account.
  Keep administrative operations local and authenticated.
- Print path: clients submit IPP jobs to `<box>-print`; the backend CUPS queue
  forwards them to the IoT printer over the narrow Platform resource flow.

Do not run CUPS in the `iot` VM. The `iot` zone is for low-trust devices and
device integration; the queue and access policy belong in `bak`.

## Required Runtime

Backend VM, rootless Podman under `neo`:

- `klokast-print-server` pod.
- Containers: `print-cups`, `print-ingress`.
- The containers share a rootless pod network namespace. The Tailscale
  ingress uses `/dev/net/tun` and `NET_ADMIN` inside that namespace. CUPS
  receives IPP directly from Tailnet addresses and accepts the declared print
  DNS names. The existing Tailnet grants authorize clients; CUPS administration
  stays on loopback and the Unix socket. Do not publish the CUPS port on the VM.
- A compromised sibling workload must not gain print-ingress identity or CUPS
  administration. The boundary is the pod network namespace and the existing
  Tailnet policy. The ingress capability does not grant VM or host network
  administration. Do not use host networking or a privileged container.
- Volumes: CUPS configuration, spool/cache state, and Tailscale state.
- OCI image: build on the active ops controller with
  `ansible/bin/platform-image-build`, then load onto `<box>-bak`. Do not run
  `podman build` on the backend VM during service deployment.

Network access is declared in `apps/print-server/platform-resources.yml` and
applied by the platform-owned `ansible/bin/platform-resources` workflow. App
roles must not mutate router, VM firewall, Tailnet policy, or dom0 state
directly.

## Automation Entry Point

Use `apps/print-server/bin/print-serverctl` from the controller. Pass box names,
not VM hostnames:

```sh
apps/print-server/bin/print-serverctl install \
  --box boxb \
  --resources-registry ~/private/klokast/platform-resources.yml
```

The validated Instance resource view must enable `print-server`, select `placement.boxes`, and
provide the printer MAC address under `devices.printer.<box>.mac`.
