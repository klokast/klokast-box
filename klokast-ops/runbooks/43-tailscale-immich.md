# Tailscale `tag:immich` OAuth wrapper

In Tailscale dashboard:
- create the `tag:immich`
- ensure the OAuth client stored in `/etc/klokast/tailscale-policy.env` can
  create auth keys for `tag:immich`.

As user `neo` on ops:

```sh
sudo install -d -o root -g root -m 0755 /usr/local/sbin
sudo install -o root -g root -m 0755 \
  /home/codex/src/klokast/klokast-box/klokast-ops/tailscale/bin/ts-authkey-mint \
  /usr/local/sbin/ts-authkey-mint
sudo install -o root -g root -m 0755 \
  /home/codex/src/klokast/klokast-box/klokast-ops/tailscale/bin/ts-authkey-immich \
  /usr/local/sbin/ts-authkey-immich

sudo visudo -f /etc/sudoers.d/codex-tailscale-auth-immich
```

Contents:

```sudoers
codex ALL=(root) NOPASSWD: /usr/local/sbin/ts-authkey-immich *
```

Validate:

```sh
sudo chmod 0440 /etc/sudoers.d/codex-tailscale-auth-immich
sudo visudo -c
sudo -l -U codex
sudo -n /usr/local/sbin/ts-authkey-immich --check-config --hostname photos --tags tag:immich >/dev/null
```
