# Platform Resource Registry Destructive Test Plan

This plan validates the platform-resource ownership ledger and keyed nftables
materialization against a live Platform deployment. It intentionally removes and
restores app-owned resource claims, so run it only from the active controller as
`smith`.

## Safety Rules

- Run from `<box>-ops` as `smith`, not from `vultr-ops` directly.
- Back up `~/private/klokast/platform-resources.yml` before any apply.
- Prefer generated temporary registry files under `.run/`; do not edit the
  private registry in place for test transitions.
- Keep management access open:
  - verify Tailscale SSH to all target routers and Podman VMs before removal;
  - keep `tailscale ssh neo@<host> sh -s` as the fallback path;
  - do not remove baseline `tailscale-management-input` rules;
  - after every phase, verify controller access to routers and app hosts.
- Preserve app data. Do not run app `remove --wipe-data` in this registry test.
- Restore the real private registry metadata at the end, even if the effective
  rules are equivalent to the last temporary registry.

## Preflight

1. Confirm the controller and repo state.

   ```sh
   hostname
   whoami
   cd ~/src/klokast/klokast-box
   git pull --ff-only
   git status --short --branch
   ```

2. Capture the baseline registry and compiled plan.

   ```sh
   RUN_DIR=".run/registry-destructive-test/$(date -u +%Y%m%dT%H%M%SZ)"
   mkdir -p "$RUN_DIR"
   REGISTRY="$HOME/private/klokast/platform-resources.yml"
   cp "$REGISTRY" "$RUN_DIR/original-platform-resources.yml"
   sha256sum "$REGISTRY" "$RUN_DIR/original-platform-resources.yml" | tee "$RUN_DIR/registry-sha256.txt"
   ansible/bin/platform-resources --registry "$REGISTRY" lint
   ansible/bin/platform-resources --registry "$REGISTRY" show > "$RUN_DIR/original-compiled.json"
   ```

3. Verify current resources and app reachability.

   ```sh
   ansible/bin/platform-resources --registry "$REGISTRY" verify
   curl -k -f --max-time 30 https://next.example.ts.net/status.php
   curl -k -f --max-time 30 https://next.klokast.ai/status.php
   curl -k -f --max-time 30 https://www.klokast.ai/
   ```

4. Probe management access.

   ```sh
   for host in boxa-router boxb-router boxa-bak boxb-bak boxa-dmz boxb-dmz boxa-iot boxb-iot; do
     tailscale ssh neo@$host sh -s <<'SH'
   set -eu
   hostname
   doas -n true
   SH
   done
   ```

## Registry Variants

Generate temporary registry variants from the private registry:

1. `01-family-disabled.yml`
   - `nextcloud.enabled: false`
   - `nextcloud-v2.enabled: false`
   - `static-site.enabled: false`

2. `02-nextcloud-v2-only.yml`
   - `nextcloud.enabled: false`
   - `nextcloud-v2.enabled: true`
   - `static-site.enabled: false`

3. `03-nextcloud-v2-plus-static.yml`
   - `nextcloud.enabled: false`
   - `nextcloud-v2.enabled: true`
   - `static-site.enabled: true`

4. `04-static-only.yml`
   - `nextcloud.enabled: false`
   - `nextcloud-v2.enabled: false`
   - `static-site.enabled: true`

5. `05-original-plus-v2-disabled.yml`
   - original production apps restored
   - explicit disabled `nextcloud-v2` entry retained only for the test

Each variant must pass:

```sh
ansible/bin/platform-resources --registry "$variant" lint
ansible/bin/platform-resources --registry "$variant" show > "$variant.compiled.json"
```

## Phase Checks

For each variant, run an app-scoped apply and verify:

```sh
COMMIT="$(git rev-parse HEAD)"
ansible/bin/platform-resources \
  --registry "$variant" \
  --app nextcloud \
  --app nextcloud-v2 \
  --app static-site \
  --approved-commit "$COMMIT" \
  apply
ansible/bin/platform-resources \
  --registry "$variant" \
  --app nextcloud \
  --app nextcloud-v2 \
  --app static-site \
  verify
```

After each phase, record:

- compiled effective resource count;
- owners for shared Nextcloud/static-site resource keys;
- target keyed nft snippet count on each router and Podman VM;
- target metadata registry SHA in `desired.json` and `last-applied.json`;
- reachability of:
  - `https://next.example.ts.net/status.php`;
  - `https://next.klokast.ai/status.php`;
  - `https://www.klokast.ai/`.

Expected behavior:

- Disabling the Nextcloud family removes only Nextcloud-family claims.
- Exact duplicate shareable claims materialize as one effective resource with
  multiple owners.
- Removing one owner keeps the effective resource while another owner remains.
- Removing the last owner removes the effective resource.
- Exclusive conflicts fail before apply.
- Static-site resources remain reachable when only Nextcloud-family claims are
  removed.
- Full-registry apply and app-scoped apply converge to equivalent effective
  keyed rules.

## Final Restore

Restore from the real private registry, not from the last temporary variant:

```sh
ansible/bin/platform-resources \
  --registry "$REGISTRY" \
  --app nextcloud \
  --app static-site \
  --approved-commit "$(git rev-parse HEAD)" \
  apply
ansible/bin/platform-resources --registry "$REGISTRY" --app nextcloud --app static-site verify
ansible/bin/platform-resources --registry "$REGISTRY" verify
```

Raw OpenSSH to steady-state Podman VMs is expected to be unavailable after the
Podman baseline removes bootstrap-only `sshd`. Use Tailscale SSH and the
target-local verifier to confirm the already-rendered state and, if needed,
restore metadata with the real registry SHA:

```sh
tailscale ssh neo@boxa-dmz sh -s -- boxa dmz <<'SH'
set -eu
doas -n /usr/bin/python3 /usr/local/libexec/klokast-app-resources-reconcile \
  verify \
  --desired /etc/klokast/platform-resources/desired.json \
  --node-name "$1" \
  --node-role "$2" \
  --scope-app=nextcloud \
  --scope-app=static-site
SH
```

The test is complete only when:

- real private registry SHA is present in every target `desired.json` and
  `last-applied.json`;
- target-local verifier passes on all router and Podman hosts;
- live nftables contains the expected keyed identities;
- Nextcloud private/public endpoints return HTTP 200;
- static site endpoint returns HTTP 200;
- no `platform-resources` or `ansible-playbook` process is left running.
