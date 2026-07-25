# Immich Architecture

Immich runs as an active/passive multisite application on the Platform. Each
site has one box, one backend VM, and one DMZ VM.

## Active Site

- `<box>-bak` runs Immich server, machine-learning, PostgreSQL, Valkey, and
  backup hooks in a rootless Podman pod with named volumes.
- `<box>-dmz` runs the stable `photos.<tailnet>` Tailscale identity in a
  rootless Podman ingress pod for trusted users.
- The backend publishes only the Immich HTTP upstream on the backend zone
  address, TCP `2283`.
- PostgreSQL, Valkey, and machine-learning have no host ports.
- Router and backend VM firewall access is applied by the platform-resource
  workflow, not by the Immich installer.

## Passive Site

- The same backend and DMZ pods are installed.
- Services stay stopped until the site is restored and promoted.

## Ingress

Tailscale is the only v1 access path. Family devices use
`https://photos.<tailnet>`, which terminates on an app-specific DMZ identity
tagged `tag:immich`, then proxies to the backend upstream. The backend VM is
not directly reachable by family devices.

Host `tailscaled`/`nginx` ingress services are legacy only.

The site router must not DNAT WAN `80` or `443` to Immich.
