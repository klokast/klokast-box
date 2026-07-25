
# Tailscale `tag:nextcloud` OAuth wrapper

In Tailscale dashboard:
- create the `tag:nextcloud` (The codex agent can do it.)
- ensure the OAuth client stored in `/etc/klokast/tailscale-policy.env` can
  create auth keys for `tag:nextcloud`.

As user `neo` on ops:
```
sudo install -d -o root -g root -m 0755 /usr/local/sbin
sudo install -o root -g root -m 0755 \
  /home/codex/src/klokast/klokast-box/klokast-ops/tailscale/bin/ts-authkey-mint \
  /usr/local/sbin/ts-authkey-mint
sudo install -o root -g root -m 0755 \
  /home/codex/src/klokast/klokast-box/klokast-ops/tailscale/bin/ts-authkey-nextcloud \
  /usr/local/sbin/ts-authkey-nextcloud

sudo visudo -f /etc/sudoers.d/codex-tailscale-auth-nextcloud
```
Contents:
```
codex ALL=(root) NOPASSWD: /usr/local/sbin/ts-authkey-nextcloud
```

Validate:
```
sudo chmod 0440 /etc/sudoers.d/codex-tailscale-auth-nextcloud
sudo visudo -c
sudo -l -U codex
sudo -n /usr/local/sbin/ts-authkey-nextcloud --check-config --hostname next --tags tag:nextcloud >/dev/null
```
