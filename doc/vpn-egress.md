# Shared VM VPN egress

Run from `~/src/klokast/klokast-box` as `smith` on the active controller.
[Instance](klokast-instance-specification.md#shared-vm-vpn-egress) declares the
gateway and its clients. The [architecture](architecture.md#box-vpn-egress)
owns its authority and trust boundaries. This service is separate from the
household LAN VPN.

Before the first deployment, converge the controller tools and run
`doas /usr/local/sbin/platform-maintenance network` to install the gateway tag
ownership through the normal Tailnet policy workflow. Gateway deployment
reconciles its compiled rules through the existing resource includes. It does
not run the legacy router baseline convergence on an accepted router.

The existing private `~/private/klokast/openclaw-vpn.yml` supplies the pinned
Mihomo archive, subscription URL and API secret. No secret belongs in Instance
or public Git. The protected `~/private/klokast/vpn-egress/` cache must match
its recorded checksums. Existing protected subscription artifacts can seed
that cache through controller Ansible. Subscription downloads and secrets do
not pass through an infra-agent host. Only rendered proxy credentials reach
the gateway; the subscription URL stays on the controller.

```sh
ansible/bin/platform-vpn-egress deploy --box k001
ansible/bin/platform-vpn-egress configure --box k001
ansible/bin/platform-vpn-egress verify --box k001
ansible/bin/platform-vpn-egress verify --box k001 --exercise
ansible/bin/platform-vpn-egress status --box k001
ansible/bin/platform-vpn-egress refresh --box k001
```

`deploy` builds or reuses a qualified `vpn-egress-alpine-v1` image on the target
box, clones it through the infrastructure guest workflow, enrolls the VM,
installs the proxy, verifies public HTTPS and private-target rejection, then
converges client settings. A failed enrollment retains the temporary bootstrap
rule so deployment can resume. A successful enrollment disables OpenSSH and
removes the bootstrap rule. Image preparation can reuse same-box package downloads. Native APK checks
those bytes against the newly fetched signed indexes before reuse; it does not
reuse old indexes or transfer package caches between boxes.
The template contains reusable shared Alpine
qualification packages; only the proxy and normal VM services are enabled.

`verify --exercise` briefly stops the proxy to test failure without fallback,
restores it in an Ansible `always` block, reboots only the gateway, then repeats
the public and private-destination probes. It holds the installation lock.

`configure` converges an installed gateway, its network rules, and client
settings from the validated cached artifacts. It uses the same controller
authority and installation lock as deployment. It does not prepare an image
or download a new subscription. Use it after changing the Platform routing rules.

`refresh` validates the downloaded subscription before replacing its cache.
Only explicit proxy records with supported fields and Platform relay ports
are accepted. It never adopts subscription routing or listener settings.
The VM keeps its working configuration when a download or validation fails.
Routing uses the Platform's checked-in domain rules. GitHub (`github.com`,
`githubusercontent.com`, `githubassets.com`, `github.io`) and Google
(`google.com`, `googleapis.com`, `gstatic.com`) use the VPN. Other public
destinations use the gateway's direct Internet connection. Rules cover each
domain and its subdomains. This is an explicit blocked-service list, not a
per-request reachability test. Add another blocked service to
`VPN_DOMAINS` in `ansible/lib/platform_vpn_egress.py` and run `configure`.
Private destination rejection has priority over these rules. Relay health
checks are lazy, with a 30-minute interval, to reduce idle VPN traffic.
The service runs under OpenRC and starts after networking and nftables.
Its internal DNS resolver uses the declared public DNS servers. It does not
use system MagicDNS, which the proxy account cannot reach through its private
network filter, or expose a DNS listener.
[Mihomo's proxy listener](https://wiki.metacubex.one/en/config/general/) and
[rule semantics](https://wiki.metacubex.one/en/config/rules/) define the upstream
configuration format.

Client convergence supplies login shell variables, system GitHub HTTPS proxy
settings, explicit Ansible download environments, and a root-owned setting for
non-login `platform-update` downloads. Existing shells need a new login to
load changed variables. Software that ignores proxy settings needs explicit
integration. This service does not route arbitrary VM traffic.

Remove a client from Instance and run `deploy` to revoke access and remove its
managed settings. All online supported same-box VMs are reconciled. An offline
former client retains its local settings until it returns and deployment runs
again; its gateway access is already denied. An empty client list denies all
clients. Remove both the capability and gateway object, then run `deploy` to
stop the gateway and disable autostart. Storage is retained. Disk deletion
requires a separate explicit retirement decision.

Maintenance uses Tailscale. A remote active controller reaches the gateway
through SSH forwarding on its same-box ops VM. This uses the declared local
Tailscale transport and avoids a separate long-distance relay connection.

If the proxy fails, repair it over Tailscale from the active controller. Client
requests to the proxy fail; VPN-routed requests have no automatic direct fallback. Do not alter default
routes to recover the proxy. Restore the pinned private artifacts and rerun
`deploy` to reconstruct the service. Keep operation results and unresolved
problems in the private operations journal.
