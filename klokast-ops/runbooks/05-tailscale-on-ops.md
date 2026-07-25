# Tailscale API in ops server
--> allow codex to remove stale machines from the Tailscale network

Tailscale Admin console > Settings > Trust credentials > Add credential > OAuth > give a name: xxxx >
 > permissions/scopes section >
  - devices > core > write
  - `tag:ops`
 > Generate credential

Tailscale Admin console > Settings > Unique IDs > Tailnet ID > get TAILNET_ID

on the deployment server:

sudo install -d -m 700 /etc/klokast/

sudo vi /etc/klokast/tailscale-devices.env
```
TAILNET_ID="xxx"
TS_OAUTH_CLIENT_ID="xxx"
TS_OAUTH_CLIENT_SECRET="tskey-client-xxx"

```

Log as `neo`, then escalate rights to root:
```
mosh nops
sudo su

chmod 600 /etc/klokast/tailscale-devices.env

install -o root -g root -m 0755 /home/codex/src/klokast/klokast-box/klokast-ops/tailscale/bin/ts-devices-list /usr/local/sbin/ts-devices-list

install -o root -g root -m 0755 /home/codex/src/klokast/klokast-box/klokast-ops/tailscale/bin/ts-device-delete-stale /usr/local/sbin/ts-device-delete-stale

ls -l /usr/local/sbin/ts-devices-list /usr/local/sbin/ts-device-delete-stale

tmp="$(mktemp)"

cat > "$tmp" <<'EOF'
codex ALL=(root) NOPASSWD: /usr/local/sbin/ts-devices-list
codex ALL=(root) NOPASSWD: /usr/local/sbin/ts-device-delete-stale --id * --hostname * --tag tag\:vm
codex ALL=(root) NOPASSWD: /usr/local/sbin/ts-device-delete-stale --id * --hostname * --tag tag\:dom0
codex ALL=(root) NOPASSWD: /usr/local/sbin/ts-device-delete-stale --id * --hostname * --tag tag\:bootstrap
codex ALL=(root) NOPASSWD: /usr/local/sbin/ts-device-delete-stale --id * --hostname * --tag tag\:ops
codex ALL=(root) NOPASSWD: /usr/local/sbin/ts-device-delete-stale --id * --hostname * --tag tag\:nextcloud
codex ALL=(root) NOPASSWD: /usr/local/sbin/ts-device-delete-stale --id * --hostname * --tag tag\:immich
codex ALL=(root) NOPASSWD: /usr/local/sbin/ts-device-delete-stale --id * --hostname * --tag tag\:music
codex ALL=(root) NOPASSWD: /usr/local/sbin/ts-device-delete-stale --id * --hostname * --tag tag\:music-upload
EOF

visudo -c -f "$tmp"
install -o root -g root -m 0440 "$tmp" /etc/sudoers.d/codex-tailscale-devices
rm -f "$tmp"
```

From other terminal tab as `neo`, test:
```
sudo visudo -c
```

From other terminal tab as `codex`, test if `codex` is able to sudo on that specific command (`-n` so it doesn't ask for password, rather fail):
```
sudo -n /usr/local/sbin/ts-devices-list >/tmp/ts-devices.json
python3 -m json.tool /tmp/ts-devices.json >/dev/null
rm -f /tmp/ts-devices.json
```

 #TODO
- add the narrow sudoers entries from `klokast-ops/runbooks/50-tailscale-wrapper-devices.md`


# Tailscale wrappers
- Copy the Tailscale scripts from the git repository (`klokast/klokast-ops/tailscale/bin`) into the deployment server (`/usr/local/sbin/`).
- Make them owned by `root`.
- Make them executable
