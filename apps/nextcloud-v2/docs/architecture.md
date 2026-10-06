# Nextcloud v2 Architecture

Nextcloud v2 uses a target-local application runner for convergence.

The source and tests are under `apps/nextcloud-v2/cmd/klokast-node/`. The
application-owned installer is
[`82-klokast-node.yml`](../ansible/playbooks/82-klokast-node.yml).
The installed command is `/usr/local/sbin/klokast-node` and accepts
`apply|verify|remove nextcloud-v2` only. There is no common application runner.

The current manual development flow is:

- controller applies Platform resources and exports a grant;
- controller writes desired JSON to each selected `<box>-bak` and `<box>-dmz`;
- `klokast-node apply nextcloud-v2` validates the bundle, renders kube YAML,
  and invokes app handlers;
- local OpenRC services start and stop already-rendered Podman pods;
- `/etc/periodic/15min/nextcloud-v2-verify` runs verify only and never repairs.

The active backend pod serves HTTP on the backend VM address and port `8080`.
The DMZ pod proxies private ingress to the backend. Passive pods are installed
but stopped until promotion.

Backend state and DMZ Tailscale state use named Podman volumes.
