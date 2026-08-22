# Multisite Nextcloud

This directory owns the Nextcloud application automation for the Platform. It
replaces the older root-level private-only proof of concept.

## Deployment Model

The deployment is active/passive:

- active master: writable Nextcloud instance
- passive backup: restore-ready instance, stopped until promotion
- trusted-user ingress: Tailscale Serve at `https://next.<tailnet>` from the
  active DMZ VM
- public ingress: optional Cloudflare Tunnel from the active DMZ VM when
  enabled in the deployment platform-resource registry
- administration and recovery: Tailscale to the Platform VMs

Do not serve normal authenticated downloads from the passive backup. It is a
recovery target, not a read replica.

## Install

Set the required secrets in the controller environment:

```sh
export NEXTCLOUD_ADMIN_PASSWORD='...'
export NEXTCLOUD_POSTGRES_PASSWORD='...'
export NEXTCLOUD_RESTIC_PASSWORD='...'
export NEXTCLOUD_RESTIC_REPOSITORY='sftp:neo@boxb-bak.example.ts.net:/srv/nextcloud-restic/boxa'
```

The ops server also needs a root-owned auth-key wrapper for the private
Nextcloud identity. The target is OAuth-backed one-off key minting from
`/etc/klokast/tailscale-policy.env`; legacy reusable key files under
`/etc/tailscale-auth/` are transitional only.

Install and remove also use the ops Tailscale device lifecycle wrappers
`/usr/local/sbin/ts-devices-list` and
`/usr/local/sbin/ts-device-delete-stale` to reclaim the stable
`next.<tailnet>` private identity when an offline stale device blocks it.

If the deployment registry enables `cloudflare-tunnel-egress`, also set:

```sh
export NEXTCLOUD_CLOUDFLARED_TOKEN_ACTIVE='...'
export NEXTCLOUD_CLOUDFLARED_TOKEN_PASSIVE='...'
```

Apply platform resources first. The standard repo ships only a disabled example
at `ops/platform-resources.example.yml`; real enabled placement should live in the
deployment-specific config repo or file.

```sh
ansible/bin/platform-resources \
  --registry path/to/platform-resources.yml \
  --approved-commit "$(git rev-parse HEAD)" \
  apply
```

Run `apply` without `--app` so the full private platform-resource registry is
converged and other enabled apps keep their managed firewall rules. Use
`--app nextcloud` for Nextcloud-scoped preview and verification.

Run preflight and install:

```sh
apps/nextcloud/bin/nextcloudctl preflight \
  --active-master boxa \
  --passive-backup boxb \
  --domain cloud.example.tld \
  --allow-missing-passive-for-test

apps/nextcloud/bin/nextcloudctl install \
  --active-master boxa \
  --passive-backup boxb \
  --domain cloud.example.tld \
  --resources-registry path/to/platform-resources.yml
```

Use `--allow-missing-passive-for-test` only while `boxb` is not ready. A real
install requires both boxes.

## Verify

```sh
apps/nextcloud/bin/nextcloudctl verify \
  --active-master boxa \
  --passive-backup boxb \
  --domain cloud.example.tld \
  --resources-registry path/to/platform-resources.yml

apps/nextcloud/bin/nextcloudctl backup-check \
  --active-master boxa \
  --passive-backup boxb \
  --domain cloud.example.tld
```

The private trusted-user URL is `https://next.<tailnet>`.

## Promote

Promotion is manual to avoid split-brain.

```sh
apps/nextcloud/bin/nextcloudctl promote \
  --old-active boxa \
  --new-active boxb \
  --domain cloud.example.tld \
  --resources-registry path/to/platform-resources.yml
```

Promotion stops the old private Tailscale ingress, deletes the offline exact
`next` Tailscale machine for `tag:nextcloud`, then starts the new active
private ingress. If Cloudflare is enabled, move the Cloudflare route for
`cloud.example.tld` to the new site tunnel and verify clients before declaring
recovery complete.

## Remove

Remove services while preserving persistent data:

```sh
apps/nextcloud/bin/nextcloudctl remove --box boxa
```

Removal also stops the private ingress, deletes the offline `next` Tailscale
machine for `tag:nextcloud`, and clears the private ingress Tailscale state.
This deprovisions the app-specific network principal without deleting user
files.

Delete persistent data only with the explicit wipe flag:

```sh
apps/nextcloud/bin/nextcloudctl remove --box boxa --wipe-data
```

To close platform-managed firewall resources, first apply a deployment registry
where `apps.nextcloud.enabled` is `false` and the old placement is still listed:

```sh
ansible/bin/platform-resources \
  --registry path/to/platform-resources-disabled.yml \
  --approved-commit "$(git rev-parse HEAD)" \
  apply

ansible/bin/platform-resources \
  --registry path/to/platform-resources-disabled.yml \
  --app nextcloud \
  verify
```

The disabled app verify step asserts that persisted and live nftables rules no
longer contain `app-nextcloud-` comments on the selected boxes.
