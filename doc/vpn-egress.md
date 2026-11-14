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
in Instance or public Git. The controller verifies the pinned Mihomo archive
checksum. Preparation downloads use its IPv4 web path, with size limits and a
90-second total timeout; private URLs pass to curl through stdin. It retains the protected source record for reconstruction and installs
the subscription URL in `/etc/vpn-egress/config.yml`, readable by the proxy account.
Subscription credentials and downloads do not pass through a runner.
The guest cache and logs are private. Native errors can include the URL; do not
publish raw logs. The status and refresh commands withhold private error details.

```sh
ansible/bin/platform-vpn-egress deploy --box k001
ansible/bin/platform-vpn-egress configure --box k001
ansible/bin/platform-vpn-egress verify --box k001
ansible/bin/platform-vpn-egress verify --box k001 --exercise
ansible/bin/platform-vpn-egress status --box k001
ansible/bin/platform-vpn-egress refresh --box k001
```

For first creation, `deploy` builds or reuses a qualified `vpn-egress-alpine-v1` image on the target
box, clones it through the infrastructure guest workflow, enrolls the VM
through the [common enrollment transport](architecture.md#shared-guest-image-and-enrollment),
installs the proxy, verifies public HTTPS and private-target rejection, then
converges client settings. The image has no OpenSSH server and the router gets
no bootstrap rule. Run `deploy` again to resume a failed enrollment; it does
not issue a second key for a recorded attempt. Image preparation can reuse same-box package downloads. Native APK checks
those bytes against the newly fetched signed indexes before reuse; it does not
reuse old indexes or transfer package caches between boxes.
The template contains reusable shared Alpine
qualification packages; only the proxy and normal VM services are enabled.

The service account and its primary group are both named `vpn-egress`.
Its home is `/var/lib/vpn-egress`, its shell is `/sbin/nologin`, and it has no
additional group memberships.
`configure` sets and verifies this layout, including for an existing account;
Alpine's Ansible user module alone does not change an existing primary group.
The account UID and numeric file ownership remain unchanged. Replacement
uses the recorded numeric identities and recreates account definitions from
policy. An explicit rebuild can repair home, shell and managed-service drift.
Conflicting numeric ownership stops reconstruction.

VPN image qualification runs the production finalizer and OpenRC script with
synthetic account and file data in the existing networkless test VM. It seeds
extra packages, changed service files and unrelated state on the old fixture.
It checks that reconstruction removes them, retains allowed identity, refuses
account conflicts, starts the approved service and passes a second boot.
The service process in this fixture only checks file access and identity; it
does not contain subscription credentials or test proxy traffic. The separate
native Mihomo tests and live gateway checks cover proxy behavior.

`verify --exercise` briefly stops the proxy to test failure without fallback,
checks that client DNS fails and public resolver bypass is blocked,
restores it in an Ansible `always` block, reboots only the gateway, then repeats
the public and private-destination probes. It holds the installation lock.

`configure` converges an installed gateway, its network rules, and client
settings from the protected source record and qualified artifacts. It uses the same controller
authority and installation lock as deployment. It does not prepare an image
or replace an existing subscription cache. It fetches and validates a seed for a missing cache. Use it after changing Platform implementation or the declared client list.
It preserves the installed routing profile. Profile edits apply only at guest
creation or replacement. Converge the controller CLI before using new Instance
fields. During migration, configure can record an existing profile only when
the live values equal Instance and the guest UUID matches its protected assignment.

Mihomo's HTTP proxy provider downloads the subscription every 86400 seconds,
independently of nightly image maintenance. Downloads use direct HTTPS. Both
the controller and native provider send `User-Agent: clash.meta` to request the
same Clash YAML subscription format. An exact
DNS policy for the subscription hostname uses the installed bootstrap resolvers,
so the initial download does not require a working relay. Platform policy permits
subscription HTTPS on TCP 443 and 5000; the provider cannot add other ports.
Controllers also receive TCP 5000 for seed downloads while a gateway is declared.
Deployment and configuration establish this network policy before fetching a seed. Provider redirects
must remain reachable under the existing guest and router firewall rules.
The native provider imports proxy definitions only. Native exclusions retain
SS, Trojan, VMess, VLESS and Hysteria2. The VPN group disables client UDP;
Hysteria2 ignores the per-server UDP override. Provider overrides also clear
subscription-supplied dialer, interface and routing-mark settings. Reserved group
names and direct/DNS/reject proxy types are excluded. Native parsing replaces the
old controller field allowlist. Guest and router firewalls enforce private-network
rejection and permitted transport ports even when subscription fields change.
Review exclusions when qualifying a new Mihomo version.
A fixed fallback payload keeps the VPN group nonempty if its subscription cache
is missing or damaged and downloading fails. It contains one unusable SS entry
on loopback port 1, which the service firewall rejects. Native subscription data
replaces this placeholder when loading succeeds. This prevents Mihomo v1.19.25
from selecting its built-in direct fallback for an empty group.

`refresh` requests a native provider update through the authenticated loopback
API. It does not restart the service, converge clients or run deployment probes.
The native provider retains its loaded list on download or parse failure and uses
its own retry timer. `status` reports update time, server count, selected server
and existing health history. Neither command requests relay tests. Status can
fail before the HTTP provider migration is installed; use `configure` to migrate.

The cache is `/var/lib/vpn-egress/proxies/<URL-SHA256>.yaml`. Before creation or
replacement, the controller downloads an initial YAML list and validates it with
the pinned Mihomo parser and the same filters and overrides. It freezes the seed
and its download time with the operation inputs before stopping the old VM.
A fresh root receives that seed. Ordinary convergence preserves an existing
cache and its age. URL changes use a different cache path. Mutable list contents
do not enter the desired configuration signature. Frozen unfinished operations
retain their original seed and implementation. Reboots use native cache age to
resume the update schedule. A successful update interval is 24 hours; failed
updates use native retry timing.

Routing uses Mihomo's native domain rule provider and the maintained list
selected by the installed Instance routing profile.
No application domain list is hard-coded in the Platform. The provider
downloads its list on first start and updates it every 24 hours through the VPN.
Its reconstructable cache is `/var/lib/vpn-egress/rules/<URL-SHA256>.yaml`.
A different URL has a different cache. A null URL omits the domain provider.
An update failure retains the last usable list. Verification requires a loaded, nonempty list when a URL is selected. List matches use the VPN; other public destinations use the
gateway's direct Internet connection. This is list-based routing, not a
per-request reachability test. Private destination rejection has priority
over the maintained list.

All shared VM VPN gateways exclude Hong Kong servers by subscription name.
The native provider filter matches `Hong Kong`, `Hong-Kong`, `Hong_Kong`,
`HongKong`, `香港`, `🇭🇰`, and `HK` without regard to letter case. `HK` can have
numbers or punctuation next to it, but cannot be part of another alphabetic word.
This rule depends on correct provider labels; it does not verify exit-IP location.
It applies when loading a cache and on each subscription update. An update with
no eligible servers fails and retains the loaded list. Startup without an eligible
list retains the unusable placeholder. VPN traffic has no direct fallback.
Run `configure` to install this policy on an existing gateway. The service restart
clears any previous Hong Kong selection. Household VPN gateways are separate.

The VPN group uses native `fallback` selection through a Platform-owned HTTP
relay provider. The provider has `health-check.enable: false`; both health-check intervals
are zero. This disables startup and scheduled relay tests. Do not put direct
`proxies` members in the group: Mihomo replaces their zero interval with a
300-second timer. Subscription-supplied providers and health-check settings are ignored.

Real connection or supported handshake failures trigger a relay test round:
two failures within 60 seconds, or an immediate connection refusal. Each relay
gets a test request to `https://www.gstatic.com/generate_204`, with a five-second
timeout. The group selects the first available relay in subscription order.
It does not select by latency. Mihomo v1.19.25 `url-test` keeps its relay object
from before a subscription update, with frozen test results, so it can stay on
a dead relay until restart. `fallback` reads the current relay state on each dial.
An unavailable relay does not cause direct fallback. Continued real failures
can trigger further rounds; there is no background recovery timer.
This detects connection failures, not slow transfers on an established stream.
Clients must retry failed requests; an existing TCP connection cannot move to
another relay. The first subscription entry is tried until a failure triggers
measurement. Restart and each subscription update clear measurements, so the
first requests after them can fail once more on a dead first entry. Manual
selection is not restored, so a stored fixed choice cannot override automatic
selection.
An application response such as GitHub's API rate-limit error does not trigger
relay selection. API reachability verification uses GitHub's `/rate_limit`
endpoint, which does not consume the primary API quota.

The daily domain-list download continues; it is separate from relay tests.

### Gateway-local feedback

Mihomo owns subscription refresh, relay tests and relay selection. Use these
native mechanisms rather than adding a second relay scheduler, failure counter
or exit-selection loop. OpenRC captures native warnings and errors in
`/var/log/vpn-egress.log`, owned by `vpn-egress` with mode `0600`.
The existing `platform-vpn-egress status --box BOX` command reports the native
selected relay, provider state and health history without requesting new tests.
This is local feedback; it does not collect logs centrally or trigger an agent.
Client routing and direct fallback when the gateway is unavailable are separate
from Mihomo's recovery inside the gateway.

The service runs under OpenRC and starts after networking and nftables.
It provides DNS on the gateway address at TCP/UDP port 53 for the same declared
clients. The guest firewall redirects these requests to unprivileged port 1053.
Normal resolution uses HTTPS DNS through the VPN group, including names whose
services are blocked from China. Only gateway and proxy-relay bootstrap use the
installed bootstrap resolvers. Clients use the gateway as their managed
OpenResolv source and block ordinary DNS bypass. A gateway outage does not add
a direct resolver fallback. Verification checks TCP and UDP answers before
client convergence, with bounded retries for the first relay selection after a restart, then checks system resolution and Tailscale DNS health.
The network startup hook registers a persistent OpenResolv source on each boot.
Guest creation and replacement record the selected bootstrap policy.
Controller convergence restores its declared client DNS after a gateway
readiness check. Live convergence briefly releases Tailscale DNS management while updating that
source, then restores the previous DNS preference. It does not restart the VM.
Generated `resolv.conf` and `/run/resolvconf` are not replacement inputs.
Before a normal service replacement, converge DNS so the operation can record
the startup hook and filter checksums. An old unfinished operation continues
to use its frozen inputs; repair its live DNS configuration and resume the
recorded operation. Do not start another replacement to repair DNS. Tailscale retains local Tailnet DNS handling.
[Mihomo's proxy listener](https://wiki.metacubex.one/en/config/general/) and
[rule semantics](https://wiki.metacubex.one/en/config/rules/) define the upstream
configuration format.

Before changing the pinned Mihomo version or relay-group settings, run
`ansible/tests/test_vpn_egress_mihomo.py` on the controller with
`MIHOMO_TEST_BINARY` set to a checksum-verified executable. It uses local test
relays only. It checks failure-triggered selection and keeps successful traffic
active for more than five minutes to detect an unwanted default timer.

Workload client convergence supplies login shell variables, system GitHub HTTPS proxy
settings, explicit Ansible download environments, and a root-owned setting for
non-login `platform-update` downloads. Existing shells need a new login to
load changed variables. Software that ignores proxy settings needs explicit
integration. This service does not route arbitrary VM traffic.

A declared controller tries the gateway first for its public downloads, then
direct access. The [gateway architecture](architecture.md#box-vpn-egress) owns
this rule. Run one other download through the same transport explicitly:

```sh
# Run as smith on the declared same-box controller, active or standby.
ansible/bin/platform-vpn-egress with-proxy --box BOX -- curl -fL https://example.com/artifact
```

The command verifies local controller placement and validated Instance client
membership, opens a temporary loopback SSH forward, and supplies proxy variables
to that command only. The command and tunnel end together on completion,
failure, or interruption. There is no privilege change or direct retry.
This uses [Tailscale SSH's existing OpenSSH client support](https://github.com/tailscale/tailscale/blob/main/cmd/tailscale/cli/ssh.go).
Controller DNS and normal update/recovery access remain independent of the VPN.

Remove a client from Instance and run `deploy` to revoke access and remove its
managed settings. All online supported same-box VMs are reconciled. An offline
former client retains its local settings until it returns and deployment runs
again; its gateway access is already denied. An empty client list denies all
clients. Remove both the capability and gateway object, then run `deploy` to
stop the gateway and disable autostart. Storage is retained. Disk deletion
requires a separate explicit retirement decision. Read the root LV UUID from
`lvs -o lv_name,lv_uuid vg0` on the box dom0, then run on the active controller:

```sh
ANSIBLE_CONFIG=ansible/ansible.cfg ansible-playbook \
  -i ansible/execution-inventory/hosts ansible/playbooks/69-vpn-egress-retire.yml \
  -e vpn_egress_retire_box=BOX -e vpn_egress_retire_lv_uuid=UUID
```

It refuses a gateway that Instance still declares, a gateway owned by the
common update lifecycle, and an LV with another UUID or owner tag. It erases
only that LV and the boot files of the gateway. It keeps the assignment record
as `retired-vpn-egress-<uuid>` under `/mnt/dom0_data/klokast-infrastructure`,
so a later declaration creates a new gateway. It then removes the offline
Tailnet identity. Repeat the command after an interruption.

Maintenance uses Tailscale. A remote active controller reaches the gateway
through SSH forwarding on its same-box ops VM. This uses the declared local
Tailscale transport and avoids a separate long-distance relay connection.

If the proxy fails, repair it over Tailscale from the active controller. Client
requests to the proxy fail; VPN-routed requests have no automatic direct fallback. Do not alter default
routes to recover the proxy. Restore the pinned private artifacts and rerun
`deploy` to reconstruct the service. Keep operation results and unresolved
problems in the private operations journal.

## Routing profile lifecycle

Instance owns the [routing profile fields](klokast-instance-specification.md#box-country-and-routing-profile).
`status` shows desired and installed values. An Instance edit, routine reboot,
`configure`, or subscription `refresh` does not select a new profile. There is
no watcher. A new replacement freezes the selected profile with its existing
operation inputs; resume never substitutes newer values. An unchanged image
is still a health-only update and does not allocate disks for a profile edit.

The protected dom0 assignment records installed routing inputs. Existing
resource execution reads these inputs for gateway DNS flows, while current
Instance client grants and gateway capability remain authoritative. Unknown
or incomplete assignments stop maintenance with recovery instructions.
Creation and replacement may select new DNS flows. No new daemon or
credential store is introduced. Other guests retain their ordinary bootstrap
DNS until their own replacement; workload VPN client grants still select gateway DNS.

## Clean reconstruction and retention

Run from the active controller as `smith`:

```sh
ansible/bin/platform-update update --box k001 --role vpn-egress
ansible/bin/platform-update update --box k001 --role vpn-egress --rebuild
ansible/bin/platform-update update --box k001 --role vpn-egress --retire-older --dry-run-plan
ansible/bin/platform-update update --box k001 --role vpn-egress --retire-older
```

Creation and replacement use the same public image personalizer and the
`vpn-egress-runtime` Ansible role. Packages come from the qualified image.
The replacement copies only Tailscale state, an existing machine ID and SSH
host keys. Legacy images can have no machine ID. The operation records that
absence and preserves it; it does not invent an old identity.
It recreates accounts with the required numeric IDs. It does not copy old
configuration, executables, home directories or VPN caches. The controller
validates private artifacts and a nonempty selected domain list before
shutdown, then freezes their bytes and rendered configuration in the protected
operation snapshot. First creation binds that snapshot in its existing dom0
allocation record; repeat `deploy` to resume from the recorded inputs. Resume checks these checksums and uses the frozen Python
modules, inventory and Ansible roles. Current authority is checked separately.

An unchanged-image update performs health checks and reports configuration
changes awaiting `--rebuild`. A rebuild prepares the current image and inputs,
then replaces the root even when that image is unchanged. It cannot accompany
image selection, resume or retirement. It can repair packages and managed
configuration while maintenance access and numeric ownership remain valid.
Unknown persistent storage stops it. A failed boot or configuration check
leaves the new generation pending; use the reported resume command.

Retention follows the [service replacement contract](architecture.md#routine-service-vm-replacement).
Cleanup preserves unproved resources and records the reason. An accepted
operation whose generation was retired returns audit status when replayed;
it does not restore disks, boot files or image references. Historical pending
operations need their verified original implementation and inputs.

The existing coordinator supports `--service-role vpn-egress`. Its
[Ansible-managed default](platform-deploy.md#routine-controller-replacement)
selects this role on both controllers after live qualification. The standby skips.
The active controller waits for retirement, image preparation, replacement and
reboot verification. It adds no periodic relay tests or controller handoff.
