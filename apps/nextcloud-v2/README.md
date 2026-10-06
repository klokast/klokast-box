# Nextcloud v2

Nextcloud v2 is the first target-local reconciler version of the app. It lives
beside the current `apps/nextcloud` implementation and does not replace it yet.

Optional public ingress uses the semantic Instance Specification feature
`public-ingress: cloudflare-tunnel`. It requires `edge-ingress` on both
placement boxes.

## Flow

Run this manual development workflow as `smith` on the active `<box>-ops`
controller. Use the placement in the Instance and the actual Instance Tailnet
DNS name. Automatic installation from Instance is not implemented. The
production requirement is in
[Platform lifecycle](../../doc/platform-lifecycle.md#application-installation).

Install the application-owned runner before building images or installing the
runtime. This also prepares its controller build and image directories.

```sh
ANSIBLE_CONFIG=ansible/ansible.cfg ansible-playbook -vv \
  -i ansible/execution-inventory/hosts \
  apps/nextcloud-v2/ansible/playbooks/82-klokast-node.yml \
  --limit boxa-bak,boxa-dmz,boxb-bak,boxb-dmz

export NEXTCLOUD_V2_MAGICDNS_SUFFIX='<Instance tailnet-dns-name>'

apps/nextcloud-v2/bin/nextcloud-v2ctl build-images --builder boxb-ops

apps/nextcloud-v2/bin/nextcloud-v2ctl infra-prepare \
  --active-master boxa \
  --passive-backup boxb \
  --resources-registry ~/private/klokast/platform-resources.yml

apps/nextcloud-v2/bin/nextcloud-v2ctl install \
  --active-master boxa \
  --passive-backup boxb \
  --resource-grant /var/lib/klokast/approved-state/apps/nextcloud-v2/grant.json
```

`infra-prepare` runs `platform-resources apply --app nextcloud-v2` as
infrastructure authority and writes an app-scoped grant. App commands consume
only that grant.

## Operations

```sh
apps/nextcloud-v2/bin/nextcloud-v2ctl verify \
  --active-master boxa \
  --passive-backup boxb \
  --resource-grant /var/lib/klokast/approved-state/apps/nextcloud-v2/grant.json

apps/nextcloud-v2/bin/nextcloud-v2ctl backup-check \
  --active-master boxa \
  --passive-backup boxb \
  --resource-grant /var/lib/klokast/approved-state/apps/nextcloud-v2/grant.json

apps/nextcloud-v2/bin/nextcloud-v2ctl promote \
  --old-active boxa \
  --new-active boxb \
  --resource-grant /var/lib/klokast/approved-state/apps/nextcloud-v2/grant.json

apps/nextcloud-v2/bin/nextcloud-v2ctl remove \
  --box boxa \
  --resource-grant /var/lib/klokast/approved-state/apps/nextcloud-v2/grant.json
```

Use the application-owned maintenance interface to start the runtime with an
enabled grant that declares `runtime_state: running`:

```sh
apps/nextcloud-v2/bin/nextcloud-v2ctl start \
  --active-master boxa \
  --passive-backup boxb \
  --resource-grant /var/lib/klokast/approved-state/apps/nextcloud-v2/grant.json
```

The tool's `stop` command requires an enabled grant that declares
`runtime_state: stopped`. The current Instance workflow generates a running
grant for a present application. It does not provide a supported stop workflow.
Grant files are generated Platform output; do not edit them to change runtime
state. See [Instance desired state](../../doc/klokast-instance-specification.md)
for the accepted application fields.

Use `verify` to check runtime state. The runner writes status to
`/var/lib/klokast/status/nextcloud-v2.json` on each selected target.

Removal preserves named Podman volumes. Use `--wipe-data` only in an explicit
destructive test or decommission path.
