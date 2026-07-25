# Nextcloud v2 Resource/Reconciler Test Plan

This plan validates Nextcloud v2 as a Platform resource state machine and a
target-local reconciler. It intentionally tests registry ownership transitions
before app runtime behavior.

All Platform-changing commands run on the active controller, currently
`k002-ops`, as `smith`. If starting from `vultr-ops`, enter the
controller first:

```sh
tailscale ssh smith@k002-ops
cd ~/src/klokast/klokast-box
```

App install/verify commands that consume grants run as `minion`.

## Safety Baseline

Before mutating resources:

1. Confirm the repo is clean, pushed, and at the intended commit.
2. Refresh current Platform state:

   ```sh
   ansible/bin/platform-map refresh \
     --boxes k001,k002 \
     --resources-registry ~/private/klokast/platform-resources.yml \
     --remote-scope full
   ansible/bin/platform-map validate
   ```

3. Snapshot router, backend, and DMZ resource state on both boxes:
   - `/etc/nftables.nft`
   - `/etc/klokast/app-resources/`
   - `/etc/klokast/platform-resources/`
   - `nft list ruleset`
   - Podman pod/container state on `bak` and `dmz`

4. Verify management access is present before and after every apply:
   - controller can SSH to `k001-router`, `k001-bak`, `k001-dmz`,
     `k002-router`, `k002-bak`, and `k002-dmz`;
   - `tailscale-management-input` remains in persisted and live nftables state.

Do not run old Nextcloud and `nextcloud-v2` active ingress at the same time:
both use the stable `next` private hostname and `tag:nextcloud`.

## Registry State Sequence

Use a private copy of `~/private/klokast/platform-resources.yml`, not
`ops/platform-resources.example.yml`, and keep placements explicit even for
disabled apps.

### S0: Clean Old Nextcloud Resource Claims

Set `nextcloud.enabled: false` and `nextcloud-v2.enabled: false` with explicit
`active_master: k001` and `passive_backup: k002`.

Run:

```sh
ansible/bin/platform-resources --registry "$REGISTRY" lint
ansible/bin/platform-resources --registry "$REGISTRY" --app nextcloud diff
ansible/bin/platform-resources \
  --registry "$REGISTRY" \
  --app nextcloud \
  --approved-commit "$(git rev-parse HEAD)" \
  apply
ansible/bin/platform-resources --registry "$REGISTRY" --app nextcloud verify
```

Expected:

- old `app-nextcloud-` live rules and keyed snippets are gone;
- legacy aggregate include files are empty compatibility anchors;
- controller SSH remains working.

Repeat for `nextcloud-v2` if any previous v2 test state exists.

### S1: Empty Central Registry

Disable all apps while keeping placements explicit.

Run a full apply:

```sh
ansible/bin/platform-resources \
  --registry "$REGISTRY" \
  --approved-commit "$(git rev-parse HEAD)" \
  apply
ansible/bin/platform-resources --registry "$REGISTRY" verify
```

Expected:

- target metadata exists under `/etc/klokast/platform-resources/`;
- `app_resource_effective_files` is empty;
- keyed include directories contain only sentinel files;
- no non-management app path is reachable.

### S2: Enable Nextcloud v2 Only

Enable:

```yaml
nextcloud-v2:
  enabled: true
  placement:
    active_master: k001
    passive_backup: k002
  resources:
    cloudflare-tunnel-egress: false
```

Run:

```sh
ansible/bin/platform-resources --registry "$REGISTRY" --app nextcloud-v2 show
ansible/bin/platform-resources --registry "$REGISTRY" --app nextcloud-v2 diff
ansible/bin/platform-resources \
  --registry "$REGISTRY" \
  --app nextcloud-v2 \
  --approved-commit "$(git rev-parse HEAD)" \
  apply
ansible/bin/platform-resources --registry "$REGISTRY" --app nextcloud-v2 verify
```

Expected resources on both boxes:

- router forward from `dmz` to `bak` on TCP `8080`;
- backend VM input from `dmz` to `bak` on TCP `8080`;
- effective keyed snippets owned only by `nextcloud-v2`.

Then prepare app execution:

```sh
apps/nextcloud-v2/bin/nextcloud-v2ctl infra-prepare \
  --active-master k001 \
  --passive-backup k002 \
  --resources-registry "$REGISTRY"

apps/nextcloud-v2/bin/nextcloud-v2ctl resource-grant-check \
  --active-master k001 \
  --passive-backup k002 \
  --resource-grant /var/lib/klokast/approved-state/apps/nextcloud-v2/grant.json
```

Install `klokast-node` on selected Podman VMs if not already installed:

```sh
ansible-playbook -vv \
  -i ansible/inventory/hosts.yml \
  ansible/playbooks/82-klokast-node.yml \
  --limit k001-bak,k001-dmz,k002-bak,k002-dmz
```

Build and load digest-pinned OCI archives before runtime install. Then run:

```sh
apps/nextcloud-v2/bin/nextcloud-v2ctl install \
  --active-master k001 \
  --passive-backup k002 \
  --resource-grant /var/lib/klokast/approved-state/apps/nextcloud-v2/grant.json

apps/nextcloud-v2/bin/nextcloud-v2ctl verify \
  --active-master k001 \
  --passive-backup k002 \
  --resource-grant /var/lib/klokast/approved-state/apps/nextcloud-v2/grant.json
```

Expected:

- active backend pod is running on `k001-bak`;
- passive backend pod is stopped on `k002-bak`;
- active DMZ ingress/proxy is running on `k001-dmz`;
- passive DMZ ingress/proxy is stopped on `k002-dmz`;
- `/var/lib/klokast/status/nextcloud-v2.json` reports success;
- `https://next.<tailnet>/status.php` reaches the active site.

### S3: Add an Independent App

Enable `immich` on `k001/k002` and apply only that app:

```sh
ansible/bin/platform-resources \
  --registry "$REGISTRY" \
  --app immich \
  --approved-commit "$(git rev-parse HEAD)" \
  apply
ansible/bin/platform-resources --registry "$REGISTRY" --app immich verify
ansible/bin/platform-resources --registry "$REGISTRY" --app nextcloud-v2 verify
```

Expected:

- Immich adds distinct TCP `2283` resources;
- existing Nextcloud v2 keyed snippets are unchanged;
- both app resource verifications pass;
- if Immich runtime is installed, `https://photos.<tailnet>` is reachable.

### S4: Add Static Site Egress Resources

Enable `static-site` on `k001` and apply only that app:

```sh
ansible/bin/platform-resources \
  --registry "$REGISTRY" \
  --app static-site \
  --approved-commit "$(git rev-parse HEAD)" \
  apply
ansible/bin/platform-resources --registry "$REGISTRY" --app static-site verify
```

Expected:

- `static-site` adds GitHub SSH-over-HTTPS egress on TCP `443`;
- Cloudflare egress snippets for TCP/UDP `7844` are added;
- Nextcloud v2 backend HTTP snippets are unchanged;
- static-site is reachable if runtime secrets/images are configured.

### S5: Remove Nextcloud v2 Without Wiping Data

Set `nextcloud-v2.enabled: false`, keep placement explicit, and leave
`static-site` enabled.

Run:

```sh
ansible/bin/platform-resources \
  --registry "$REGISTRY" \
  --app nextcloud-v2 \
  --approved-commit "$(git rev-parse HEAD)" \
  apply
ansible/bin/platform-resources --registry "$REGISTRY" --app nextcloud-v2 verify

apps/nextcloud-v2/bin/nextcloud-v2ctl remove \
  --box k001 \
  --resource-grant /var/lib/klokast/approved-state/apps/nextcloud-v2/grant.json
apps/nextcloud-v2/bin/nextcloud-v2ctl remove \
  --box k002 \
  --resource-grant /var/lib/klokast/approved-state/apps/nextcloud-v2/grant.json
```

Expected:

- Nextcloud v2 runtime, timer, desired JSON, status JSON, rendered kube files,
  containers, and local config are removed;
- named Podman volumes are preserved;
- Nextcloud v2 TCP `8080` resources are removed because `static-site` no
  longer consumes the Nextcloud backend WebDAV endpoint;
- `next.<tailnet>` no longer serves v2.

### S6: Remove Static Site Resources

Disable `static-site` and apply only that app:

```sh
ansible/bin/platform-resources \
  --registry "$REGISTRY" \
  --app static-site \
  --approved-commit "$(git rev-parse HEAD)" \
  apply
ansible/bin/platform-resources --registry "$REGISTRY" --app static-site verify
```

Expected:

- static-site TCP `443` GitHub egress is removed;
- static-site Cloudflare egress snippets are removed;
- controller SSH still works to all selected hosts.

### S7: Destructive Wipe Test

Run only on a test box or after explicit backup confirmation:

```sh
apps/nextcloud-v2/bin/nextcloud-v2ctl remove \
  --box BOX \
  --wipe-data \
  --resource-grant /var/lib/klokast/approved-state/apps/nextcloud-v2/grant.json
```

Expected:

- named Podman volumes are deleted;
- no v2 runtime state remains.

## Acceptance Criteria

- App-scoped apply mutates only resource keys owned by the selected app.
- Full apply and repeated app-scoped applies converge to equivalent effective
  snippets.
- Duplicate shareable claims produce one keyed nft file with multiple owners.
- Removing one owner preserves shared resources.
- Removing the last owner deletes the effective keyed snippet.
- Disabled app resources disappear from persisted snippets and live nftables.
- `klokast-node verify` updates status only and does not restart or repair pods.
- Active/passive behavior is enforced: active reachable, passive stopped.
- Tailscale SSH management remains available after every apply.

## Assumptions

- Test boxes are `k001` active and `k002` passive.
- The active controller is `k002-ops`.
- Static-site is the shared-ownership test app because it reuses Nextcloud
  backend TCP `8080`.
- Immich is the independent coexistence test app because it uses backend TCP
  `2283`.
- Runtime cutover to `next.<tailnet>` happens in a maintenance window if old
  Nextcloud is production.
