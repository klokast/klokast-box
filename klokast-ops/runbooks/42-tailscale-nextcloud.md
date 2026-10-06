# Tailscale nextcloud ingress enrollment

The active controller's Ansible role installs the root-owned
`ts-authkey-nextcloud` wrapper and its declared privilege rules. Keep the OAuth
credential root-only on that controller. See [credential setup](40-tailscale-wrapper-setup-policy.md).

As `smith` on the active controller, check the ingress purpose before installation:

```sh
sudo -n /usr/local/sbin/ts-authkey-nextcloud \
  --check-config --hostname next --tags tag:nextcloud
```

Continue with the [application instructions](../../apps/nextcloud/README.md).
The application workflow owns ingress enrollment. A configuration check does
not change Tailnet policy or grant new access.
