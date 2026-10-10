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

Instance `vpn-egress.subscription-ref` selects the private controller record
that supplies the pinned Mihomo archive, subscription URL and API secret. The
compatibility default is `~/private/klokast/openclaw-vpn.yml`. No secret belongs
in Instance or public Git. The protected `~/private/klokast/vpn-egress/` cache must match
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
Routing uses Mihomo's native domain rule provider and the maintained list
specified by [the gateway architecture](architecture.md#box-vpn-egress).
No application domain list is hard-coded in the Platform. The provider
downloads its list on first start and updates it every 24 hours through the VPN.
Its reconstructable cache is `/var/lib/vpn-egress/rules/gfw.yaml`.
An update failure retains the last usable list. Verification requires a loaded,
nonempty list. List matches use the VPN; other public destinations use the
gateway's direct Internet connection. This is list-based routing, not a
per-request reachability test. Private destination rejection has priority
over the maintained list.

The VPN group uses native `url-test` selection through a Platform-owned inline
relay provider. The provider has `health-check.enable: false`; both intervals
are zero. This disables startup and scheduled relay tests. Do not put direct
`proxies` members in the group: Mihomo replaces their zero interval with a
300-second timer. Subscription providers and health-check settings are ignored.

Real connection or supported handshake failures trigger a relay test round:
two failures within 60 seconds, or an immediate connection refusal. Each relay
gets a test request to `https://www.gstatic.com/generate_204`, with a five-second
timeout. The group selects the available relay with the lowest measured latency.
An unavailable relay does not cause direct fallback. Continued real failures
can trigger further rounds; there is no background recovery timer.
This detects connection failures, not slow transfers on an established stream.
Clients must retry failed requests; an existing TCP connection cannot move to
another relay. The first subscription entry is tried until a failure triggers
measurement. Restart clears measurements and manual selection is not restored,
so a stored fixed choice cannot override latency selection.
An application response such as GitHub's API rate-limit error does not trigger
relay selection. API reachability verification uses GitHub's `/rate_limit`
endpoint, which does not consume the primary API quota.

The daily domain-list download continues; it is separate from relay tests.
The service runs under OpenRC and starts after networking and nftables.
Its internal DNS resolver uses the declared public DNS servers. It does not
use system MagicDNS, which the proxy account cannot reach through its private
network filter, or expose a DNS listener.
[Mihomo's proxy listener](https://wiki.metacubex.one/en/config/general/) and
[rule semantics](https://wiki.metacubex.one/en/config/rules/) define the upstream
configuration format.

Before changing the pinned Mihomo version or relay-group settings, run
`ansible/tests/test_vpn_egress_mihomo.py` on the controller with
`MIHOMO_TEST_BINARY` set to a checksum-verified executable. It uses local test
relays only. It checks failure-triggered selection and keeps successful traffic
active for more than five minutes to detect an unwanted default timer.

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
