# Direct Overlay IPv6 Repair

Status: the first signed repair completed on K002. Its direct IPv6 path did not
stay selected. The revised repair below needs a new engine promotion and signed
execution. Do not use the retired Plan v3 repair contract.

Use this procedure only to restore a direct Tailscale path from the active
`<box>-ops` controller to the one peer `<box>-router`. The repair routes one
Freebox `/64` to the active box ops network. IPv4 TCP and DERP stay available.

Run Platform commands as `smith` on the active controller from
`~/src/klokast/klokast-box`. Run approval commands on the trusted MacBook. Do
not run these operations on an infra-agent or airunner.

The repair adds an exact `conf-file` include to the router's
`/etc/dnsmasq.conf`. This also supports legacy routers that do not load
`/etc/dnsmasq.d`. The main configuration is part of the signed preimage and
is restored on failure. A syntax check alone cannot prove that an unreferenced
advertisement file is loaded.

The router's forward chain also loads the exact `/etc/klokast/overlay-ipv6.nft`
fragment. The repair includes `/etc/nftables.nft` in the signed rollback files
and validates the candidate rules before loading them. This supports legacy
routers whose main firewall file does not yet include the fragment.

The router also drops WAN IPv4 UDP from the ops VM when its source port is
`41641`. This stops Tailscale from selecting a low bandwidth direct IPv4 path
to the peer. The rule is before the forward chain's established-traffic rule,
so an existing IPv4 UDP session cannot bypass it. It does not match local
management paths, other VM sources, TCP, or IPv6. The main firewall file and
loaded ruleset are part of the signed rollback evidence. If direct IPv6 stops,
Tailscale can use DERP over TCP for recovery. The signed verifier requires
direct IPv6 for repair success.

Preparation reuses the stable WAN link local address when the exact `/64` is
already assigned. It refuses the same address with a different prefix.

Both network helpers keep complete Ansible logs in owner-only files under
the controller checkout's `.run/overlay-ipv6-router` or
`.run/overlay-ipv6-ops`. Failure messages identify the failed task and the
private log path. Keep these logs on the controller. The router prerequisite
and final controller verification each sample ten Tailscale replies without
stopping at the first direct path. At least one reply must use the required
IPv6 UDP `41641` endpoint. An IPv4 or DERP reply alone fails verification.

## Safety contract

The v2 repair intent accepts only a closed, instance-only Plan v8 for a
two-box instance. It binds:

- the exact private and engine commits;
- active Authority State v5 and all six instance-owned groups;
- fresh instance-source, source-recovery, Observation, sealed-build, and
  Controller Toolchain evidence;
- the Freebox gateway identity, API version, delegation slot, `/64`, stable
  router link-local next hop, and complete delegation preimage;
- exact router, controller, sysctl, address, file, and firewall preimages;
- the peer-router Huawei `/128` UDP `41641` prerequisite.

The Freebox broker uses only `mafreebox.freebox.fr` and keeps its token
root-only. It refuses redirects, identity or API drift, occupied slots,
prefix drift, and unsafe next hops. The intent has a one-hour lifetime and
a single-use nonce. It does not change Authority State.

If verification fails after mutation starts, the executor restores the exact
Freebox, router, and controller preimages. It then verifies IPv4 controller
access and a working Tailscale path. A failed restoration records
`recovery_required`.

Restoration stops router advertisements before it removes new addresses on
the router and then the controller. It selects addresses by IPv6 CIDR and
preserves addresses recorded in the preimage. Recovery accepts a working
DERP path; a direct path is required only for the repair's success checks.

New preimages also record IPv6 routes on the ops-facing interfaces. Restoration
removes only newly learned routes for the delegated prefix and new RA default
routes. It preserves routes present in the preimage and unrelated routes.
Route expiry timers are excluded from approval comparison. Old preimages without
route evidence require a separate reviewed recovery; never assume an empty route
preimage. The router verifier checks advertisement files with Python because
Alpine BusyBox grep does not support `--exclude`.

Revalidation keeps configuration, address identity, prefix length, interface,
and address flags exact. IPv6 lifetimes can count down by at most 600 seconds.
Router advertisements can renew the same dynamic global address under
[RFC 4862, section 5.5.3](https://www.rfc-editor.org/rfc/rfc4862#section-5.5.3).
This repair permits a lifetime increase of at most 600 seconds between samples.
Static addresses cannot gain lifetime. Finite/infinite changes, unusable
addresses, and larger lifetime changes are refused. Signed snapshot bytes
remain unchanged. A refusal consumes the nonce and requires a fresh intent;
do not retry the failed signed request.

## Prepare the peer Huawei rule

Keep the Huawei IPv6 firewall enabled. On the active controller, run:

```sh
ansible/bin/show-huawei-tailscale-pinhole
```

Remove or disable the old broad rule. Create one rule for the current peer
router global IPv6 `/128`, UDP port `41641`. Do not use a whole `/64`, DMZ, an
unrestricted destination, or an assumed device-to-address binding. If the ISP
prefix changes, update the exact `/128` before repair preparation.

This manual rule is a prerequisite. The signed repair does not automate the
Huawei interface.

After the direct path works, record the exact address that you put in the
Huawei rule:

```sh
ansible/bin/check-huawei-tailscale-pinhole --record
```

For later checks, run:

```sh
ansible/bin/check-huawei-tailscale-pinhole
```

The check compares the current peer-router `/64` and exact `/128` with the
private recorded baseline. It also checks that Tailscale uses the exact IPv6
UDP `41641` endpoint and that both routers can reach a DERP region. It does not
read or change the Huawei gateway. Run `--record` again only after you update
the Huawei rule and prove that the new exact direct path works.

## Create current instance-only evidence

Use the exact active engine, its sealed build, and its exact Controller
Toolchain v8 receipt. Plan v8 also accepts historical Toolchain v7 receipts,
but do not use one for a new rollout. Synchronize the private instance, create
a fresh source-recovery receipt, refresh the Platform map, and export an
owner-only Observation. Then create Plan v8:

```sh
ansible/bin/platform-plan --instance-only \
  --build-dir BUILD_DIR \
  --instance /home/smith/private/klokast/instance \
  --observation OBSERVATION \
  --instance-source-receipt SOURCE_RECEIPT \
  --authority-state AUTHORITY_STATE \
  --controller-toolchain-receipt TOOLCHAIN_RECEIPT
```

Require a valid and deployable Plan v8 with exactly six verification-only
groups, complete Authority State v5 ownership, and no compatibility inputs.

## Check and approve

On the trusted MacBook, first prepare and review without a signature:

```sh
klokast-dev/bin/apply-platform-intent \
  --controller ACTIVE_BOX-ops \
  --plan PLAN --authority-state AUTHORITY_STATE \
  --controller-toolchain-receipt TOOLCHAIN_RECEIPT \
  --source-recovery-receipt SOURCE_RECOVERY_RECEIPT \
  --instance-source-receipt SOURCE_RECEIPT \
  --observation OBSERVATION --build-dir BUILD_DIR \
  --repair-overlay-ipv6-direct --check
```

The check stores protected preimages but makes no Platform change. It must
prove that the active router already reaches the peer router through the exact
IPv6 UDP `41641` endpoint without DERP.

After review, run the same command without `--check`. Add
`--prove-replay-refusal` when live replay classification is part of the
approved acceptance. Touch ID signs only the displayed v2 intent.

The executor performs this fixed sequence:

1. Prepare the router link-local address and narrow firewall rules.
2. Configure the selected Freebox delegation.
3. Assign and advertise the `/64` only on the active ops network.
4. Enable controller SLAAC without restarting IPv4.
5. Refresh Tailscale endpoints only on the active controller and the two
   routers.
6. Require controller IPv6 support and a direct peer endpoint on UDP `41641`.

Keep the intent, receipt, protected preimages, Plan, and audit record. Do not
publish private addresses or Freebox data in public documentation.

The old compatibility design is available in Git at commit `186cfa9`.
