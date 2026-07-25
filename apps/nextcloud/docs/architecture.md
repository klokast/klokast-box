# Nextcloud Architecture

Nextcloud runs as an active/passive multisite application on the Platform.
Each site has one box, and each box has a backend VM and a DMZ VM.

## Active Site

- `<box>-bak` runs Nextcloud FPM, nginx, PostgreSQL, Redis, cron, and backup.
- `<box>-dmz` runs the private Tailscale ingress as the stable
  `next.<tailnet>` identity for trusted users.
- `<box>-dmz` runs Cloudflare Tunnel as an OpenRC service under `neo` only
  when enabled in the deployment platform-resource registry.
- The backend publishes only the Nextcloud HTTP upstream on the backend zone
  address.
- PostgreSQL and Redis have no host ports.
- Router and backend VM firewall access is applied by the platform
  platform-resource workflow, not by the Nextcloud installer.

## Passive Site

- The same backend and DMZ services are installed.
- Services stay stopped until the site is promoted.
- Encrypted backups are restored here for recovery drills and failover.

## Reads

The passive site is not a low-latency read replica. Nextcloud reads depend on a
consistent database, filesystem, cache, session, permission, share, preview, and
lock state. Serving authenticated reads from an hourly restored site can return
stale or unauthorized results.

For lower latency later, use one of these explicit designs:

- separate read-only file mirror outside Nextcloud semantics
- two independent federated Nextcloud instances
- true active-active with shared database, storage, session, and Redis/locking
  layers

## Ingress

Tailscale is the default trusted-user access path. Family devices use
`https://next.<tailnet>`, which terminates on an app-specific DMZ identity
tagged `tag:nextcloud`, then proxies to the backend upstream. The backend VM
is not directly reachable by family devices.

Cloudflare Tunnel is optional public ingress and requires the deployment
registry to enable the `cloudflare-tunnel-egress` app resource. The site router
must not DNAT WAN `80` or `443` to Nextcloud.

Cloudflare Access is not placed in front of the main hostname because browser,
mobile, desktop sync, and WebDAV clients need normal Nextcloud protocol
behavior. Use Cloudflare Tunnel, WAF/rate limits where compatible, Nextcloud
2FA, and Nextcloud brute-force protection.
