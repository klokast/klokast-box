# Direct Overlay IPv6 Repair

Status: implemented but not deployed. Group its controller rollout with other
reviewed public changes. Do not use the retired Plan v3 repair contract.

Use this procedure only to restore a direct Tailscale path from the active
`<box>-ops` controller to the one peer `<box>-router`. The repair routes one
Freebox `/64` to the active box ops network. IPv4 and DERP stay available.

Run Platform commands as `smith` on the active controller from
`~/src/klokast/klokast-box`. Run approval commands on the trusted MacBook. Do
not run these operations on an infra-agent or airunner.

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
prefix drift, and unsafe next hops. The executor has a ten-minute lifetime and
a single-use nonce. It does not change Authority State.

If verification fails after mutation starts, the executor restores the exact
Freebox, router, and controller preimages. It then verifies IPv4 controller
access and a working Tailscale path. A failed restoration records
`recovery_required`.

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
