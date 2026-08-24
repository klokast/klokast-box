# NanoKVM Recovery Skill

When to use this:
- Sipeed NanoKVM (`oob`) UI/API is unavailable and `ansible/bin/nanokvm-virtual-media` fails, or
- to work on the code of `ansible/bin/nanokvm-virtual-media`.

## Key facts

- NanoKVM service binary: `NanoKVM-Server`
- Normal service restart command:

```sh
/etc/init.d/S95nanokvm restart
```

- Server config: `/etc/kvm/server.yaml`
- App/server directory: usually `/kvmapp/server/`
- HID paste API route: `POST /api/hid/paste`
- Local API base from inside NanoKVM:
  - `http://127.0.0.1`
  - or `https://127.0.0.1`
- Auth cookie name, if auth is enabled: `nano-kvm-token`
- Paste form fields:
  - `content`: text to type through HID
  - `langue`: optional keyboard layout, e.g. `fr` or `de`
- Paste limit: keep each request below `1024` characters.
- NanoKVM helper from the deployment server:
  - `ansible/bin/nanokvm-virtual-media --status`
  - `ansible/bin/nanokvm-virtual-media --check-deps --for upload-url`
  - `ansible/bin/nanokvm-virtual-media --upload-url <url> --as <image.iso>`
  - `ansible/bin/nanokvm-virtual-media --delete <image.iso>`
  - `ansible/bin/nanokvm-virtual-media --load <image.iso>`
  - `ansible/bin/nanokvm-virtual-media --unload`
  - `ansible/bin/nanokvm-virtual-media --hid-paste <text>`
  - `ansible/bin/nanokvm-virtual-media --reset-api-password`
  - `ansible/bin/nanokvm-virtual-media --restart-service`
  - `ansible/bin/nanokvm-virtual-media --reboot`
  - `ansible/bin/nanokvm-virtual-media --usb-reset`
  It uses root SSH to NanoKVM and the same USB gadget state manipulated by the
  NanoKVM UI. Password reset uses the NanoKVM local API so the web/API password
  and the NanoKVM Linux `root` password stay synchronized.

## Recovery procedure

### 1. SSH into NanoKVM

From the `ops` deployment server (replace with the valid Tailscale MagicDNS address):
```sh
ssh root@oob.tail123456.ts.net
```

Pre-requisite: SSH to NanoKVM setting is enabled in NanoKVM web UI:

```text
Settings -> Devices -> SSH
```

### 2. Restart the NanoKVM service

From the deployment server, prefer the checked-in helper:

```sh
ansible/bin/nanokvm-virtual-media --restart-service --host oob
```

Or from a root SSH session on NanoKVM:

```sh
/etc/init.d/S95nanokvm restart
```

Verify:

```sh
ps | grep '[N]anoKVM-Server' || true
ss -lntp 2>/dev/null | grep -E ':80|:443' || \
  netstat -lntp 2>/dev/null | grep -E ':80|:443' || true
```

Expected listeners:

```text
*:80     NanoKVM-Server
*:443    NanoKVM-Server
```

If the browser page is blank or the Tailscale URL times out, separate service health from Tailnet reachability by testing locally from inside NanoKVM:

```sh
curl -kfsSI https://127.0.0.1/ || true
curl -kfsSI http://127.0.0.1/ || true
```

With the default HTTPS config, local HTTP usually returns a `307 Temporary Redirect` to HTTPS and local HTTPS should return `200 OK`.

Check the active server config and init script before changing anything:

```sh
sed -n '1,220p' /etc/kvm/server.yaml
sed -n '1,260p' /etc/init.d/S95nanokvm
```

The usual config has HTTPS enabled and these ports:

```yaml
proto: https
port:
    http: 80
    https: 443
```

### 2.1. Debug blank web UI or unreachable web ports

Use the correctly formed HTTPS URL from the operator laptop:

```text
https://oob.<tailnet>.ts.net/
```

With the default config, `http://oob.<tailnet>.ts.net/` should redirect to HTTPS.

From the `ops` deployment server, first check DNS and WireGuard reachability:

```sh
getent hosts oob.<tailnet>.ts.net
tailscale ping --timeout=5s oob.<tailnet>.ts.net
tailscale status
```

Then SSH into NanoKVM and check the device-local service:

```sh
hostname
date
ps | grep '[N]anoKVM-Server' || true
ss -lntp 2>/dev/null | grep -E ':80|:443' || \
  netstat -lntp 2>/dev/null | grep -E ':80|:443' || true
curl -kfsSI https://127.0.0.1/ || true
curl -kfsS https://127.0.0.1/ | sed -n '1,40p' || true
find /tmp/server/web -maxdepth 2 -type f | sed -n '1,80p' || true
```

Interpretation:
- `NanoKVM-Server` running, `*:80` and `*:443` listening, and local `https://127.0.0.1/` returning `200 OK` means NanoKVM itself is serving the UI.
- If the local HTML contains `<div id="root"></div>` plus JS/CSS assets under `/assets/`, the UI is the normal NanoKVM single-page app. A blank browser page can then be stale cached JS/CSS, blocked assets, or client-side failure. Hard-refresh or clear site data for `oob.<tailnet>.ts.net`.
- If SSH over Tailscale works but HTTP/HTTPS from `ops` time out, check the Tailscale policy before changing NanoKVM. Current policy can intentionally allow `tag:ops` to reach `tag:oob` only on SSH while allowing `group:operators` to reach `tag:oob` on TCP `80` and `443`.
- If local HTTPS fails or no `80`/`443` listener exists, restart NanoKVM and re-check `/etc/kvm/server.yaml` and `/etc/init.d/S95nanokvm`.

If the service is healthy but an operator device cannot open the MagicDNS URL,
reconcile the policy from the active controller. Keep NanoKVM web access
limited to `group:operators`; do not add `tag:ops`, `tag:airunner`, or workload
tags to the web grant.

```sh
cd ~/src/klokast/klokast-box
git pull --ff-only
ansible/bin/render-tailscale-policy \
  --deployment-config ~/private/klokast/deployment.yml \
  --output ~/private/klokast/tailscale-policy.hujson
doas -n /usr/local/sbin/ts-policy-validate \
  /home/smith/private/klokast/tailscale-policy.hujson
```

Stop after validation. The infra account has no direct policy-mutation
privilege. The current authorized Apply executor accepts only the three
private Tailnet identity inputs and cannot apply a public-template change.
Do not bypass this boundary during NanoKVM recovery. Use the local console
path, or complete a separate architecture decision for topology mutation.

After apply, test `https://oob.<tailnet>.ts.net/` from an operator-owned
device. A request from `tag:ops` or `tag:airunner` is not an equivalent test
because those machine identities intentionally have no NanoKVM web access.

If TCP 443 connects and `curl -k` returns the NanoKVM page, but the browser
still refuses the page, inspect the certificate:

```sh
curl -vk https://oob.<tailnet>.ts.net/
```

The NanoKVM default certificate uses the Common Name `localhost` and is self-signed. It is
not valid for the MagicDNS name. Configure Tailscale Serve on NanoKVM to
terminate TLS with an automatically managed certificate and proxy to the
device-local HTTPS listener:

```sh
tailscale ssh root@oob tailscale serve \
  --bg --yes --https=443 https+insecure://localhost:443
tailscale ssh root@oob tailscale serve status
```

Expected status:

```text
https://oob.<tailnet>.ts.net (tailnet only)
|-- / proxy https+insecure://localhost:443
```

Use the full MagicDNS URL. A request to the Tailscale IP can bypass Serve and
still receive the NanoKVM self-signed certificate.

Verify from an operator-owned device without `-k`:

```sh
curl -v https://oob.<tailnet>.ts.net/
```

To roll back the proxy:

```sh
tailscale ssh root@oob tailscale serve --https=443 off
```

Tailscale stores the Serve configuration in its existing state. This does not
add a daemon. Recheck `tailscale serve status` after a NanoKVM firmware update,
factory reset, or Tailscale state replacement.

If the NanoKVM menu works but the target display is blank:

```sh
cat /proc/cvitek/vi_dbg
cat /kvmapp/kvm/state
printf '%s x %s\n' "$(cat /kvmapp/kvm/width)" "$(cat /kvmapp/kvm/height)"
```

- `VIDevFPS=0` means NanoKVM is not seeing HDMI input.
- `VIDevFPS!=0` and `VIFPS=0` means HDMI parameters were detected incorrectly; for Cube/Lite, unplug/replug HDMI, or reboot NanoKVM.
- If the menu works but H.264 video is blank in the browser, try Chrome or switch the image format to `MJPEG`.

For UEFI or text-console recovery, reduce stream load. This does not change the target's HDMI mode; it only changes what NanoKVM transmits to the browser:

```sh
printf 600 > /kvmapp/kvm/res    # 800x600 stream
printf 24 > /kvmapp/kvm/fps     # 24 fps cap
printf 2000 > /kvmapp/kvm/qlty  # medium H.264 bitrate
```

Useful stream files:

```sh
cat /kvmapp/kvm/res      # 0 auto, 480, 600, 720, 1080
cat /kvmapp/kvm/fps      # maximum transmitted fps
cat /kvmapp/kvm/now_fps  # current transmitted fps
cat /kvmapp/kvm/type     # h264 or mjpeg
```

`/kvmapp/kvm/width` and `/kvmapp/kvm/height` are read-only HDMI input resolution. To really lower capture load, lower the target UEFI/OS display mode itself, for example to `800x600` or `640x480`.

### 3. Test HID paste into the target host

The target machine must be focused on a shell, login prompt, rescue console, installer shell, or initramfs prompt.

From the deployment server, prefer the checked-in helper. It logs in through
the NanoKVM local API and never prints the token:

```sh
ansible/bin/nanokvm-virtual-media --hid-paste 'echo NANOKVM_HID_OK' \
  --api-password-file /home/codex/nanokvm/oob.password
```

For a French keyboard layout and a final Enter:

```sh
ansible/bin/nanokvm-virtual-media --hid-paste 'echo NANOKVM_HID_OK' \
  --hid-layout fr \
  --hid-enter \
  --api-password-file /home/codex/nanokvm/oob.password
```

Create or rotate the password file outside git from `ops`:

```sh
ansible/bin/nanokvm-virtual-media --reset-api-password --api-user neo
```

This saves `/home/codex/nanokvm/oob.password` with mode `0600`. The reset
action calls `POST /api/auth/password` on NanoKVM, which updates both
`/etc/kvm/pwd` and the NanoKVM Linux `root` password. If the old web password
is unavailable, the helper temporarily rewrites `/etc/kvm/pwd` only to obtain
a token, restores it, and then performs the durable API password change.

If normal API login is unavailable but root SSH to NanoKVM still works, mint a
temporary token by explicitly opting into the emergency password-file rewrite:

```sh
ansible/bin/nanokvm-virtual-media --mint-token \
  --token-out /run/klokast/nanokvm-oob.token
ansible/bin/nanokvm-virtual-media --hid-paste 'echo NANOKVM_HID_OK' \
  --token-file /run/klokast/nanokvm-oob.token
```

Without auth:

```sh
curl -kfsS -X POST http://127.0.0.1/api/hid/paste \
  --data-urlencode 'content=echo NANOKVM_HID_OK'
```

With auth:

```sh
TOKEN='<nano-kvm-token-cookie-value>'

curl -kfsS -X POST https://127.0.0.1/api/hid/paste \
  -b "nano-kvm-token=$TOKEN" \
  --data-urlencode 'content=echo NANOKVM_HID_OK'
```

### 3.1. Normal automated HID access

For automation, do not ask the user to extract the browser cookie. Log in through the normal API, get a short-lived `nano-kvm-token`, then call `/api/hid/paste`.

Normal setup:
- The user sets the NanoKVM password in the NanoKVM web UI.
- The target machine is at the prompt that should receive typed HID input.
- `ops` can SSH to NanoKVM as `root`.
- Set `NANOKVM_USER` first if the NanoKVM web username is not `neo`.
- The NanoKVM password is typed at runtime or read by a future root-owned wrapper outside git.

Acquire a token with the real NanoKVM password:

```sh
set -euo pipefail

OOB='root@oob.<tailnet>.ts.net'
NANOKVM_USER="${NANOKVM_USER:-neo}"
read -rsp 'NanoKVM password: ' NANOKVM_PASSWORD
printf '\n'

CIPH="$(
  printf '%s' "$NANOKVM_PASSWORD" |
    openssl enc -aes-256-cbc -a -salt \
      -pass pass:nanokvm-sipeed-2024 -md md5 2>/dev/null |
    tr -d '\n'
)"

LOGIN_B64="$(
  python3 - "$NANOKVM_USER" "$CIPH" <<'PY'
import base64
import json
import sys
import urllib.parse
payload = {"username": sys.argv[1], "password": urllib.parse.quote(sys.argv[2], safe="")}
print(base64.b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode())
PY
)"

RESP="$(
  ssh "$OOB" "
    set -e
    printf '%s' '$LOGIN_B64' | base64 -d > /tmp/klokast-login.json
    curl -kfsS -X POST https://127.0.0.1/api/auth/login \
      -H 'Content-Type: application/json' \
      --data @/tmp/klokast-login.json
    rm -f /tmp/klokast-login.json
  "
)"
unset NANOKVM_PASSWORD

TOKEN="$(
  python3 - "$RESP" <<'PY'
import json
import sys
rsp = json.loads(sys.argv[1])
if rsp.get("code") != 0:
    raise SystemExit(f"NanoKVM login failed: {rsp}")
print(rsp["data"]["token"])
PY
)"
unset CIPH LOGIN_B64 RESP
```

Then use `$TOKEN` with the HID paste examples below. Do not print or store the token.

The checked-in helper replaces this interactive password flow for normal
automation. Keep the raw examples below for diagnosis and for understanding
the API request shape.

### 3.2. Emergency HID access when only NanoKVM SSH works

Use this only when the normal API-login path above is unavailable, but `ops` can still SSH to NanoKVM as `root`. This keeps NanoKVM authentication enabled. It temporarily replaces only `/etc/kvm/pwd`, logs in through the normal API to obtain a real `nano-kvm-token`, restores the original password file immediately, then uses that token for HID paste.

Why this is needed:
- `POST /api/hid/paste` is normally protected by the `nano-kvm-token` cookie.
- A blank `jwt.secretKey` in `/etc/kvm/server.yaml` does not mean the JWT secret is blank at runtime; recent NanoKVM builds generate an in-memory random secret when the configured value is empty.
- The login API expects the password encrypted like the web UI does, using the `nanokvm-sipeed-2024` passphrase and URL encoding.

Prerequisites on `ops`:
- `python3` with the `bcrypt` module
- `openssl`
- SSH access to NanoKVM as `root`

Acquire a token without keeping the temporary password:

```sh
set -euo pipefail

OOB='root@oob.<tailnet>.ts.net'
TMPPW="klokast-hid-$(openssl rand -hex 12)"

HASH="$(
  python3 - "$TMPPW" <<'PY'
import bcrypt
import sys
print(bcrypt.hashpw(sys.argv[1].encode(), bcrypt.gensalt()).decode())
PY
)"

ACCOUNT_B64="$(
  python3 - "$HASH" <<'PY'
import base64
import json
import sys
account = {"username": "neo", "password": sys.argv[1]}
print(base64.b64encode(json.dumps(account, separators=(",", ":")).encode()).decode())
PY
)"

BACKUP="/tmp/klokast-pwd-backup-$(date -u +%Y%m%dT%H%M%SZ)"
RESTORED=0

restore_nanokvm_pwd() {
  if [ "$RESTORED" -eq 0 ]; then
    ssh "$OOB" "
      if [ -f '$BACKUP' ]; then
        cp '$BACKUP' /etc/kvm/pwd
        chmod 0644 /etc/kvm/pwd
      fi
    " >/dev/null 2>&1 || true
    RESTORED=1
  fi
}

trap restore_nanokvm_pwd EXIT

ssh "$OOB" "
  cp /etc/kvm/pwd '$BACKUP'
  printf '%s' '$ACCOUNT_B64' | base64 -d > /etc/kvm/pwd
  chmod 0644 /etc/kvm/pwd
"

CIPH="$(
  printf '%s' "$TMPPW" |
    openssl enc -aes-256-cbc -a -salt \
      -pass pass:nanokvm-sipeed-2024 -md md5 2>/dev/null |
    tr -d '\n'
)"

ENC="$(
  python3 - "$CIPH" <<'PY'
import sys
import urllib.parse
print(urllib.parse.quote(sys.argv[1], safe=""))
PY
)"

LOGIN_B64="$(
  python3 - "$ENC" <<'PY'
import base64
import json
import sys
payload = {"username": "neo", "password": sys.argv[1]}
print(base64.b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode())
PY
)"

RESP="$(
  ssh "$OOB" "
    printf '%s' '$LOGIN_B64' | base64 -d > /tmp/klokast-login.json
    curl -kfsS -X POST https://127.0.0.1/api/auth/login \
      -H 'Content-Type: application/json' \
      --data @/tmp/klokast-login.json
  "
)"

restore_nanokvm_pwd

TOKEN="$(
  python3 - "$RESP" <<'PY'
import json
import sys
rsp = json.loads(sys.argv[1])
if rsp.get("code") != 0:
    raise SystemExit(f"NanoKVM login failed: {rsp}")
print(rsp["data"]["token"])
PY
)"

trap - EXIT
```

Then paste to the target console:

```sh
cat >/tmp/kvm-target-commands.txt <<'EOF'
search --no-floppy --label ALPINE_EFI --set=root
linux ($root)/boot/vmlinuz-lts modules=loop,squashfs,sd-mod,usb-storage,nvme quiet modloop=/boot/modloop-lts alpine_dev=LABEL=ALPINE_EFI
initrd ($root)/boot/initramfs-lts
boot
EOF

TARGET_B64="$(base64 -w0 /tmp/kvm-target-commands.txt)"

ssh "$OOB" "
  printf '%s' '$TARGET_B64' | base64 -d > /tmp/kvm-target-commands.txt
  curl -kfsS -X POST https://127.0.0.1/api/hid/paste \
    -b 'nano-kvm-token=$TOKEN' \
    --data-urlencode content@/tmp/kvm-target-commands.txt
"
```

The API endpoint is reached locally from NanoKVM. Do not run that `curl` on `ops` unless `127.0.0.1` is intentionally forwarded to NanoKVM.

For GRUB recovery, ensure the final command is followed by Enter. Shell command substitution strips trailing newlines, so constructing a payload in a variable can leave `grub> boot` waiting at the prompt. If that happens, send a single newline:

```sh
printf '\n' >/tmp/kvm-enter.txt
ENTER_B64="$(base64 -w0 /tmp/kvm-enter.txt)"

ssh "$OOB" "
  printf '%s' '$ENTER_B64' | base64 -d > /tmp/kvm-enter.txt
  curl -kfsS -X POST https://127.0.0.1/api/hid/paste \
    -b 'nano-kvm-token=$TOKEN' \
    --data-urlencode content@/tmp/kvm-enter.txt
"
```

### 4. Paste a recovery script into the target

Create or copy the payload onto NanoKVM:

```sh
cat >/root/recovery.payload.sh <<'EOF'
#!/bin/sh
set -eu

echo "Recovery started on $(date)"
ip addr
ip route
EOF
```

Or use scp to copy the payload from deployment server onto NanoKVM:
```sh
scp tmp/recovery.payload.sh root@oob.tail123456.ts.net:/data/
```

Note that Tailscale ACLs:
  - allow `tag:ops` to ssh into `tag:oob` (`ops --ssh--> oob`)
  - disallow `tag:oob` to ssh into `tag:ops`
  - allow `tag:bak` to ssh into `tag:oob` (`bak --ssh--> oob`)
  - disallow `tag:oob` to ssh `tag:bak`

Paste it into the target as a here-doc:

```sh
cat >/tmp/kvm-target-commands.txt <<'EOF'
cat >/tmp/nanokvm-recovery.sh <<'SCRIPT_EOF'
#!/bin/sh
set -eu

echo "Recovery started on $(date)"
ip addr
ip route
SCRIPT_EOF
chmod +x /tmp/nanokvm-recovery.sh
/tmp/nanokvm-recovery.sh
EOF
```

Send it through HID:

```sh
curl -kfsS -X POST https://127.0.0.1/api/hid/paste \
  -b "nano-kvm-token=$TOKEN" \
  --data-urlencode content@/tmp/kvm-target-commands.txt
```

For French keyboard layout:

```sh
curl -kfsS -X POST https://127.0.0.1/api/hid/paste \
  -b "nano-kvm-token=$TOKEN" \
  --data-urlencode content@/tmp/kvm-target-commands.txt \
  --data-urlencode langue=fr
```

## Important constraints

- NanoKVM Cube currently runs Buildroot and has no package manager such as
  `apt`, `apk`, or `opkg`. Use `--check-deps` or `--ensure-tools` to verify
  required tools; do not model NanoKVM as a general managed Linux host.
- If `NanoKVM-Server` is dead, `/api/hid/paste` is also dead. Restart the service first.
- HID paste only types into the currently focused target console.
- Prefer ASCII shell scripts; keyboard layout issues can break symbols.
- Split long payloads into chunks under `900` characters to stay below the API limit.
- The PC-USB/HID cable must be connected from NanoKVM to the target host.
- Do not disable NanoKVM authentication except as a short emergency measure on a trusted network.

## Source anchors

- Official NanoKVM server README: service binary, config path, `/kvmapp/server/`, restart command.
  https://github.com/sipeed/NanoKVM/tree/main/server

- Official NanoKVM FAQ: SSH, logs, restart command.
  https://wiki.sipeed.com/hardware/en/kvm/NanoKVM/faq.html

- Official NanoKVM user guide: stream resolution, frame rate, image quality,
  and browser/video troubleshooting.
  https://wiki.sipeed.com/hardware/en/kvm/NanoKVM/user_guide.html

- Official NanoKVM development guide: `/kvmapp/kvm` stream files.
  https://wiki.sipeed.com/hardware/en/kvm/NanoKVM/development.html

- Official NanoKVM quick start: PC-USB/HID connection simulates keyboard/mouse.
  https://wiki.sipeed.com/hardware/en/kvm/NanoKVM/quick_start.html

- Source route for HID paste:
  https://github.com/sipeed/NanoKVM/blob/main/server/router/hid.go

- Source implementation for paste fields, layout handling, and 1024-char limit:
  https://github.com/sipeed/NanoKVM/blob/main/server/service/hid/paste.go

- Source routes for NanoKVM image download/storage operations:
  https://github.com/sipeed/NanoKVM/blob/main/server/router/download.go
  https://github.com/sipeed/NanoKVM/blob/main/server/router/storage.go

- Source auth middleware for `nano-kvm-token` cookie:
  https://github.com/sipeed/NanoKVM/blob/main/server/middleware/jwt.go
