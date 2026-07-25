# `ntfy` on the `ops` deployment server

## Recommended design

Use the Hetzner VPS as a **monitoring sentinel**, not as a central orchestrator.

```text
Family servers over Tailscale
  ├─ run Podman + OpenRC + local Monit
  ├─ send periodic heartbeats to VPS
  └─ expose only selected health endpoints over Tailscale

Hetzner VPS
  ├─ receives heartbeats
  ├─ probes services over Tailscale
  ├─ decides when something is stale/down
  └─ sends ntfy push notifications
```

This gives what we want:

- no complex Kubernetes
- no GUI
- no public Podman API
- no cross-site control plane
- alerts even if a home server dies
- manual promotion/failover by Ansible when human approves it

`ntfy` is a good fit here because publishing can be done with plain HTTP `PUT`/`POST` or the CLI,
and topics can be private if using auth/access tokens.

---

## Two monitoring paths

### Path 1 — Push heartbeats from each server to the VPS

Each family server periodically says:

```text
I am alive.
My cron works.
My Tailscale path to the VPS works.
My local monitoring script ran.
```

Example from each server:

```cron
* * * * * /usr/local/sbin/heartbeat
```

`/usr/local/sbin/heartbeat`:

```sh
#!/bin/sh
set -eu

VPS="http://100.x.y.z:8088"
HOST="$(hostname)"

curl -fsS --max-time 10 \
  -X POST \
  -H "Authorization: Bearer ${HEARTBEAT_TOKEN}" \
  "$VPS/heartbeat/$HOST" >/dev/null
```

On the VPS, a tiny receiver records timestamp files:

```sh
#!/bin/sh
# /usr/local/libexec/heartbeat-receiver.cgi

set -eu

host="${PATH_INFO#/heartbeat/}"

case "$host" in
  home-a|home-b|parent-a|vps-a) ;;
  *) exit 1 ;;
esac

mkdir -p /var/lib/family-monitor/heartbeats
date +%s > "/var/lib/family-monitor/heartbeats/$host"

printf 'Status: 204 No Content\r\n\r\n'
```

You can run this behind a tiny local HTTP daemon bound only to the Tailscale IP, or avoid HTTP entirely and use SSH forced commands. For simplicity, HTTP over Tailscale with a bearer token is acceptable.

The VPS then checks freshness:

```sh
#!/bin/sh
# /usr/local/libexec/check-heartbeats

set -eu

dir="/var/lib/family-monitor/heartbeats"
now="$(date +%s)"
max_age="${MAX_AGE_SECONDS:-180}"

for host in home-a home-b parent-a; do
    file="$dir/$host"

    if [ ! -f "$file" ]; then
        /usr/local/sbin/notify "CRITICAL: $host missing heartbeat" \
          "No heartbeat file exists for $host"
        continue
    fi

    last="$(cat "$file")"
    age=$((now - last))

    if [ "$age" -gt "$max_age" ]; then
        /usr/local/sbin/notify "CRITICAL: $host heartbeat stale" \
          "Last heartbeat from $host was ${age}s ago"
    fi
done
```

This is the equivalent of Healthchecks.io, but tiny and fully under your control.

---

### Path 2 — VPS probes services over Tailscale

Heartbeats tell you **the host can run cron and reach the VPS**.

Probes tell you **the actual service works from another machine**.

Example probe config:

```yaml
checks:
  - name: nextcloud-home-a
    url: http://home-a:8080/status.php
    expect: '"maintenance":false'
    timeout: 10
    failures_before_alert: 3
    severity: critical
    action: manual

  - name: home-assistant-home-a
    url: http://home-a:8123/
    expect: "Home Assistant"
    timeout: 10
    failures_before_alert: 3
    severity: warning
    action: manual

  - name: camera-latest-upload
    command: /usr/local/libexec/check-remote-file-freshness parent-a /srv/camera/front '*.mp4' 900
    failures_before_alert: 2
    severity: critical
    action: manual

  - name: torrent-client
    url: http://home-a:8081/
    timeout: 10
    failures_before_alert: 5
    severity: warning
    action: restart-safe
```

You can generate this from Ansible vars.

A generic HTTP probe:

```sh
#!/bin/sh
# /usr/local/libexec/check-http

set -eu

url="$1"
expected="${2:-}"

body="$(curl -fsS --max-time 10 "$url")"

if [ -n "$expected" ]; then
    printf '%s' "$body" | grep -q "$expected"
fi
```

Then a simple runner on the VPS can call these scripts every minute from cron.

---

## ntfy notification script

Use one script everywhere:

```sh
#!/bin/sh
# /usr/local/sbin/notify

set -eu

title="$1"
message="$2"
priority="${3:-default}"

curl -fsS \
  -H "Authorization: Bearer ${NTFY_TOKEN}" \
  -H "Title: ${title}" \
  -H "Priority: ${priority}" \
  -H "Tags: warning" \
  -d "$message" \
  "https://ntfy.example.net/family-ops" >/dev/null
```

For your setup, host ntfy on the Hetzner VPS and expose it in one of two ways:

| Option | Recommendation |
|---|---|
| **Public HTTPS with auth token** | Best if you want phone push notifications anywhere without VPN dependency |
| **Tailscale-only ntfy** | Smaller exposure, but your phone must be connected to Tailscale to receive/subscribe directly |

For push notifications to a normal phone, public HTTPS with ntfy auth is more practical. Keep publishing locked down with tokens.

---

## VPS-side layout

Keep it deliberately boring:

```text
/etc/family-monitor/
  checks.yml
  hosts.yml
  secrets.env

/usr/local/libexec/
  check-http
  check-tcp
  check-dns
  check-heartbeats
  check-remote-file-freshness
  check-nextcloud
  check-bitcoin
  check-lnd

/usr/local/sbin/
  notify
  family-monitor-runner
  heartbeat-receiver

/var/lib/family-monitor/
  heartbeats/
  state/
  last-alerts/
```

Cron:

```cron
* * * * * /usr/local/sbin/family-monitor-runner
* * * * * /usr/local/libexec/check-heartbeats
```

State files prevent alert spam:

```text
/var/lib/family-monitor/state/nextcloud-home-a.failures
/var/lib/family-monitor/state/nextcloud-home-a.last_alert
/var/lib/family-monitor/state/nextcloud-home-a.status
```

---

## Avoid alert spam

You need three controls.

### 1. Consecutive failure threshold

Do not alert on first failure.

```text
fail once          → record
fail twice         → record
fail three times   → alert
```

### 2. Alert cooldown

Example:

```text
after first alert, do not repeat for 30 minutes
unless severity changes
```

### 3. Recovery notifications

Send one recovery message:

```text
RECOVERY: nextcloud-home-a is healthy again after 17 minutes
```

This is useful because otherwise you keep checking manually.

---

## Local Monit still has a role

Even with the VPS sentinel, I would still run local Monit on each Alpine host.

Use the split like this:

| Layer | Runs where | Purpose |
|---|---|---|
| **OpenRC** | each host | start/restart supervised Podman containers |
| **Monit** | each host | local checks and safe local restarts |
| **VPS sentinel** | Hetzner VPS | off-host reachability, heartbeats, central ntfy alerts |
| **Ansible** | your controller | deploy, repair, promote, update |

The VPS should generally **alert**, not mutate remote state.

Local Monit can restart safe services because it is close to the failure and has less risk of acting on a network partition.

---

## Promotion workflow

For master promotion, make the VPS send an actionable alert, not execute promotion.

Example alert:

```text
CRITICAL: Nextcloud primary home-a unavailable

Observed:
- home-a heartbeat stale: 6m 20s
- nextcloud public URL down
- nextcloud over Tailscale down
- replica parent-a reachable
- last successful sync to parent-a: 2m 05s ago

Manual action:
ansible-playbook playbooks/promote-nextcloud.yml -l parent-a
```

For each promotable app, encode:

```yaml
promotions:
  nextcloud:
    primary: home-a
    candidates:
      - parent-a
    max_replica_lag_seconds: 600
    manual_playbook: playbooks/promote-nextcloud.yml
```

Then your alert can include the correct command.

For Lightning nodes, I would **not** include an automated promotion path. Alert only.

---

## Security model

Because your VPS is in the Tailnet, use Tailscale ACLs/grants to restrict it.

Suggested model:

```text
tag:monitor
  may connect to selected health ports on family servers

family servers
  may POST heartbeat to monitor VPS

no server
  may access Podman API remotely

phone/laptop
  may access ntfy HTTPS and selected admin ports
```

Bind internal health endpoints to either:

```text
127.0.0.1
```

or the host’s Tailscale IP only, not public interfaces.

---

## Preferred exact implementation

### On every family server

Run:

```text
OpenRC
Monit
heartbeat cron
Podman health scripts
```

Each server sends:

```text
heartbeat → VPS
local Monit alerts → ntfy directly or via VPS
```

### On Hetzner VPS

Run:

```text
ntfy
family-monitor heartbeat receiver
family-monitor probe runner
```

The VPS checks:

```text
host heartbeats
HTTP over Tailscale
TCP over Tailscale
backup freshness
camera upload freshness
public Cloudflare URL if applicable
origin Tailscale URL if applicable
replica lag / sync freshness
```

### Do not run

```text
Prometheus
Grafana
Kubernetes
Nomad
Portainer
Cockpit
public Podman API
automatic cross-site failover
```

Not because they are bad, but because they are unnecessary for this scale and risk model.

---

## Final recommendation

Use your Hetzner VPS as:

```text
dead-man receiver + external probe runner + ntfy notification sender
```

Use each Alpine host as:

```text
local watchdog + local repair agent
```

Use Ansible as:

```text
the only thing that changes infrastructure state across machines
```

The result is:

```text
Failure detected locally?
  → Monit may restart safe services
  → alert via ntfy

Host/site unreachable?
  → VPS notices missing heartbeat or failed probe
  → ntfy alert

Master likely dead?
  → VPS sends facts + suggested Ansible promotion command
  → you approve manually

Container unhealthy but still running?
  → local Monit detects via generic Podman health script
  → safe apps restart automatically
  → stateful/high-risk apps alert only
```
