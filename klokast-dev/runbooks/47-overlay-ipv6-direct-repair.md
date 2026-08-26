# Restore Direct Overlay IPv6

Use this runbook only to restore a direct Tailscale path between the active
`<box>-ops` controller and the unique peer `<box>-router`. The action routes
one Freebox `/64` to the active box `ops` network. All other downstream
networks stay IPv4-only.

Run Platform commands as `smith` on the active controller from
`~/src/klokast/klokast-box`. Run approval commands on the trusted MacBook. Do
not run these operations on an infra-agent or airunner.

The broker uses the official
[Freebox connection IPv6 API](https://dev.freebox.fr/sdk/os/connection/) and
[Freebox application authorization](https://dev.freebox.fr/sdk/os/login/).
It keeps the application token under `/etc/klokast` with mode `0600`. It uses
only the fixed local `mafreebox.freebox.fr` endpoint. It rejects redirects,
gateway identity drift, API version drift, unknown response fields, occupied
delegation slots, prefix drift, and a non-link-local next hop.

## 1. Install The Exact Controller Toolchain

Pull the new public commit on the active controller. Build and promote its
exact sealed engine as specified in `45-tailnet-authority-pilot.md`. Converge
the controller tools and create a Controller Toolchain v3 receipt:

```sh
ansible/bin/converge-ops-controller --box ACTIVE_BOX -- \
  --tags ops-controller-secret-authority-wrappers,ops-controller-apply
ansible/bin/controller-toolchain-receipt --build-dir "$BUILD_DIR"
```

Run the local syntax checks before you create evidence:

```sh
ANSIBLE_CONFIG="$PWD/ansible/ansible.cfg" \
ANSIBLE_ROLES_PATH="$PWD/ansible/roles" \
  ansible-playbook --syntax-check ansible/playbooks/83-overlay-ipv6-router.yml
ANSIBLE_CONFIG="$PWD/ansible/ansible.cfg" \
ANSIBLE_ROLES_PATH="$PWD/ansible/roles" \
  ansible-playbook --syntax-check ansible/playbooks/84-overlay-ipv6-ops.yml
```

Toolchain v3 binds the root Apply program, controller guard, Freebox broker,
router and ops network helpers, Platform resource compiler, Tailnet policy
helpers, and sealed engine.

## 2. Register The Freebox Application

Run this once on the active controller:

```sh
ansible/bin/install-freebox-application
```

Approve `Klokast ops IPv6 delegation` on the physical Freebox display. The
installer must report only a redacted gateway hash and the API version. Check
that `/etc/klokast/freebox-ipv6.json` is owned by `root:root` with mode `0600`.
Do not copy this credential to the standby controller or an airunner.

## 3. Change The Huawei Pinhole

Keep the Huawei recovery credential available. On the active controller, run:

```sh
ansible/bin/show-huawei-tailscale-pinhole
```

Use the current address in its output. In the Huawei IPv6 firewall:

1. Remove or disable `yii-jump-tailscale`.
2. Create `<peer-box>-router-tailscale`.
3. Set the destination to the printed peer router IPv6 `/128`.
4. Set the protocol to UDP and the port to `41641`.

Do not disable the Huawei IPv6 firewall. Do not add another destination or
port. Keep the old IPv4 and DERP paths.

## 4. Create Fresh Verification Evidence

Create fresh source and source-recovery receipts. Refresh the Platform map,
export a fresh Observation v1, and create a fresh Plan v3 as specified in
`46-box-connectivity-authority.md`.

The Plan must be verification-only for the selected box group. It must name
the active controller box and one other box. The repair does not change
Authority State or the source-ownership chain.

## 5. Check And Approve The Repair

On the trusted MacBook, first run the closed preflight without a signature:

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

The check must prove that the active router reaches the peer router directly
through an IPv6 UDP `41641` endpoint. It selects the lowest unused non-first
Freebox delegation slot, or it reuses the recorded slot. It checks the stable
router link-local address for collision. It saves read-only router, ops, and
firewall preimages. It does not change the Freebox or either host.

Run the same command without `--check`. Review the complete intent and approve
it with the `human-platform-apply` Touch ID signer. The action performs this
fixed sequence:

1. Prepare the router link-local address and narrow firewall rules.
2. Configure the selected Freebox delegation.
3. Assign and advertise the `/64` only on the active `ops` network.
4. Enable SLAAC on the active ops VM without restarting IPv4.
5. Run `tailscale debug rebind` and `restun` only on the active ops VM and peer
   router.
6. Require ops `tailscale netcheck` to report IPv6.
7. Require the ops-to-peer ping to use a direct IPv6 UDP `41641` endpoint.

If a step fails, Apply restores the exact Freebox delegation, router and ops
files, sysctl values, addresses, and live firewall rules. It then verifies the
IPv4 controller route and a working Tailscale path. It records
`recovery_required` if it cannot prove restoration.

## 6. Finish The Existing Source Acceptance

Create fresh evidence and complete the real box-connectivity rollback required
by `46-box-connectivity-authority.md`. Create a fresh adoption Plan and reapply
the box source action. Finish with a fresh verification-only Plan.

For the final overlay verification, rerun the repair command with
`--prove-replay-refusal` only if a fresh repair is required. The controller
must refuse the exact signed nonce on replay. Keep all intents, receipts,
preimages, Plans, and audit records.
