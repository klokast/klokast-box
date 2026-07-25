
# General shape

```
Ansible
  ├─ installs Podman + podman-openrc
  ├─ creates users/subuids/subgids
  ├─ creates networks/volumes/secrets/configs
  ├─ creates containers or pods
  ├─ writes OpenRC init scripts
  └─ enables/starts services

OpenRC
  ├─ starts containers on boot
  ├─ restarts crashed containers
  └─ provides simple CLI lifecycle

Podman
  ├─ runs containers
  ├─ manages pods/networks/volumes
  └─ stays daemonless

Tailscale
  └─ connects hosts securely
```

```
 Ansible inventory
  group_vars/
  host_vars/
  roles/
    podman_host/
    podman_app/
    openrc_podman_service/
  apps/
    nextcloud/
    bitcoin-core/
    home-assistant-io/
    monit/
```

Each app has:
```
apps/myapp/
  containers.yml      # Ansible vars, or kube YAML
  files/
  templates/
  openrc-service.j2
```

# Principles:
- Declarative desired state: Ansible vars
- Runtime engine: `Podman`
- Supervisor: OpenRC. OpenRC is the only thing starting/stopping services -> use state: `present`
- Restart on crash: supervise-daemon
- Boot startup: `rc-update`
- No `systemd`
- No `Kubernetes`
- No GUI
- No central daemon

# Ansible job:
- Pull image
- Create network
- Create volumes
- Create container or pod
- Install `/etc/init.d/<app>`
- Enable service with `rc-update`
- Start/restart service when config changes

# Ansible variables:
```
podman_apps:
  - name: adguard
    image: docker.io/adguard/adguardhome:latest
    ports:
      - "53:53/tcp"
      - "53:53/udp"
      - "3000:3000/tcp"
    volumes:
      - adguard-work:/opt/adguardhome/work
      - adguard-conf:/opt/adguardhome/conf
    restart: openrc

  - name: homepage
    image: ghcr.io/gethomepage/homepage:latest
    ports:
      - "3001:3000/tcp"
    volumes:
      - /srv/homepage:/app/config:ro
    restart: openrc
```

# Create containers with Ansible:
```yaml
- name: Create Podman containers
  containers.podman.podman_container:
    name: "{{ item.name }}"
    image: "{{ item.image }}"
    state: present
    recreate: true
    publish: "{{ item.ports | default(omit) }}"
    volumes: "{{ item.volumes | default(omit) }}"
  loop: "{{ podman_apps }}"
```

# OpenRC template:
```jinja2
#!/sbin/openrc-run

name="{{ item.name }}"
description="Podman container: {{ item.name }}"

supervisor=supervise-daemon
command="/usr/bin/podman"
command_args="start -a {{ item.name }}"

respawn_delay=5
respawn_max=10
respawn_period=60

depend() {
    need net
    after firewall
}

start_pre() {
    /usr/bin/podman container exists {{ item.name }} || return 1
}

stop() {
    ebegin "Stopping {{ item.name }}"
    /usr/bin/podman stop -t 20 {{ item.name }}
    eend $?
}
```

# Deploy service
```yaml
- name: Install OpenRC service files
  ansible.builtin.template:
    src: podman-container.openrc.j2
    dest: "/etc/init.d/{{ item.name }}"
    mode: "0755"
  loop: "{{ podman_apps }}"

- name: Enable services
  ansible.builtin.command:
    cmd: "rc-update add {{ item.name }} default"
    creates: "/etc/runlevels/default/{{ item.name }}"
  loop: "{{ podman_apps }}"

- name: Start services
  ansible.builtin.service:
    name: "{{ item.name }}"
    state: started
  loop: "{{ podman_apps }}"
```

# Multi-server deployment

We keep things simple. We don't build sophisticated "clusters".

- Use Ansible groups

```ini
[home]
home-a
home-b

[parent_house]
parent-a

[vps]
vps-a

[podman_hosts:children]
home
parent_house
vps
```
- Then assign apps by host or group:

```
# host_vars/home-a.yml
podman_apps:
  - name: adguard
    image: docker.io/adguard/adguardhome:latest
  - name: immich
    image: ghcr.io/immich-app/immich-server:release

# host_vars/parent-a.yml
podman_apps:
  - name: backup-receiver
    image: ghcr.io/example/backup-receiver:latest
```

# Networking
- WireGuard or Tailscale
- private host-to-host connectivity
- containers bind only to private IPs or localhost (127.0.0.1 or Tailscale IP)
- reverse proxy only where needed
- use firewall rules outside Podman too
- do not expose Podman API sockets over the network


# Image updates through Ansible

```yaml
- name: Pull latest images
  containers.podman.podman_image:
    name: "{{ item.image }}"
    force: true
  loop: "{{ podman_apps }}"
  notify: restart podman app
```

Then restart only affected services: explicit, auditable, and no surprise restarts.

# Health monitoring

--> Layered  model:

1. Local per-host watchdog → detects broken containers/services and optionally restarts safe ones
2. External/off-host probe → detects whether a site/service is reachable from another location
3. Notification sink → sends alerts to your phone/email/Matrix/etc.
4. Manual failover runbook → you approve “promote this replica as master”

- VPS receiver dead-man checks, self-hosted on the `ops` deployment server.
- ntfy for push alerts
- Ansible for deploying all checks and runbooks
- Optional small shell probes for app-specific health

## 1. process crash: handled by OpenRC:
container main process exits  → `podman start -a` exits → OpenRC restarts service

## 2. Monit
as the local watchdog on each Alpine host
--> Monitor processes, programs, files, directories, filesystems, network sockets, and HTTP endpoints.
--> Take actions: restart a service, execute a script, or send an alert.
--> Simple & minimal system. No dashboard.

Typical jobs:
- “Is this OpenRC service running?”
- “Is this TCP port accepting connections?”
- “Does this HTTP endpoint return 200?”
- “Is disk space too low?”
- “Is memory too high?”
- “Is this file too old?”
- “Is this container unhealthy?”
- “Should I restart this local service?”

Example Monit check for an OpenRC-managed Podman container:
```
check program nextcloud-container with path "/usr/local/libexec/check-podman-container nextcloud"
  if status != 0 then alert
  if status != 0 for 3 cycles then exec "/sbin/rc-service nextcloud restart"
```

Example check for a local HTTP endpoint:
```
check host nextcloud-local with address 127.0.0.1
  if failed port 8080 protocol http
     request "/status.php"
     with timeout 10 seconds
     for 3 cycles
  then alert
```

Example check for disk space:
```
check host nextcloud-local with address 127.0.0.1
  if failed port 8080 protocol http
     request "/status.php"
     with timeout 10 seconds
     for 3 cycles
  then alert
```

## 3. Tiny VPS receiver
as dead-man checks for monitoring if the host died
If the whole host dies, local Monit also dies.
So each host periodically pings the `ops` deployment server.


# What to monitor

--> generic check primitives first
- check-podman-container
- check-http
- check-tcp
- check-dns
- check-file-freshness
- check-command
- check-disk
- check-backup-age

--> Then app-specific checks (in Ansible vars)
- what to check
- how many failures before alert
- whether auto-restart is allowed (see severity classes A, B, and C)
- what manual runbook to mention

## 1. Generic script for all
- Generic checks for every container
- One script for all Podman containers:
```
#!/bin/sh
set -eu

name="$1"

podman container exists "$name" || exit 10

state="$(podman inspect --format '{{.State.Status}}' "$name")"

case "$state" in
  running)
    ;;
  *)
    echo "$name state is $state"
    exit 20
    ;;
esac

health="$(podman inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$name")"

case "$health" in
  healthy|none)
    exit 0
    ;;
  starting)
    echo "$name health is starting"
    exit 1
    ;;
  unhealthy)
    echo "$name is unhealthy"
    exit 30
    ;;
  *)
    echo "$name unknown health: $health"
    exit 40
    ;;
esac
```

Monit:
```
check program {{ app.name }}-container with path "/usr/local/libexec/check-podman-container {{ app.name }}"
  if status != 0 for 3 cycles then alert
{% if app.auto_restart | default(false) %}
  if status != 0 for 5 cycles then exec "/sbin/rc-service {{ app.name }} restart"
{% endif %}
```

Generic checks for every host
- disk free
- inode free
- load average
- memory pressure
- swap usage if relevant
- time sync
- DNS resolution
- Tailscale connectivity
- backup freshness
- critical mount availability
- container runtime sanity

For example, in Monit:
```
check filesystem rootfs with path /
  if space usage > 85% then alert
  if space usage > 95% then exec "/usr/local/libexec/alert 'critical disk' 'rootfs above 95%'"

check program dns-resolution with path "/usr/local/libexec/check-dns example.com"
  if status != 0 for 3 cycles then alert

check program tailscale with path "/usr/local/libexec/check-tailscale"
  if status != 0 for 3 cycles then alert
```

## 2. Specific checks per app beyond the generic "process is running"

### Nextcloud
--> alert on failure, but not always auto-restart. A bad database mount, full disk, or failed upgrade needs human review.

Monitor:
- HTTP /status.php
- database reachable
- Redis reachable, if used
- data directory mounted
- background cron freshness
- disk space
- TLS certificate expiry if exposed

Example:
```
#!/bin/sh
set -eu

url="${1:-http://127.0.0.1:8080/status.php}"

json="$(curl -fsS --max-time 10 "$url")"

echo "$json" | grep -q '"installed":true'
echo "$json" | grep -q '"maintenance":false'
```

### Bitcoin Core
--> track that block height changes over time. A node can be “healthy” but stuck.
--> Do not expose RPC outside Tailscale/localhost.

Monitor:
- process/container running
- RPC reachable over localhost or Tailscale only
- block height progressing
- headers close to network height
- disk space
- mempool not required unless you care
- wallet disabled/encrypted status if relevant
- Minimal local check:
```
#!/bin/sh
set -eu

cli="podman exec bitcoin bitcoin-cli"

$cli getblockchaininfo >/tmp/bitcoin-chain.json

blocks="$(jq -r .blocks /tmp/bitcoin-chain.json)"
headers="$(jq -r .headers /tmp/bitcoin-chain.json)"
initial="$(jq -r .initialblockdownload /tmp/bitcoin-chain.json)"

[ "$initial" = "false" ] || exit 2
[ "$headers" -ge "$blocks" ] || exit 3
```

### Lightning node
--> avoid automatic restarts after every failed probe. Some transient failures are normal; some states require care. Alert first. Manual action second.

Monitor:
- daemon running
- wallet unlocked, if applicable
- synced to chain
- channels active
- on-chain backend reachable
- disk space
- backup freshness: channel backup / static backup

### Torrent client
--> Is it accidentally running without the intended VPN/proxy/network namespace? If that condition fails, automatic stop is safer than automatic restart.

Monitor:
- web UI reachable over localhost/Tailscale
- download path mounted
- free disk space
- VPN/proxy status if required
- public leakage check if relevant

### Home Assistant
Home Assistant has useful HTTP endpoints, but you may need a token for deeper API checks. Keep the token local and deployed via Ansible Vault.
Auto-restart can be okay, but avoid restart loops during migrations.

Monitor:
- HTTP API reachable
- critical integrations available if scriptable
- database size
- backup freshness
- Zigbee/Z-Wave USB device presence, if relevant

### Webcam / offsite footage storage

Monitor:
- camera reachable
- recent footage file exists
- recent upload completed
- offsite host reachable
- free disk space local and remote
- age of newest file
- age of newest remote file

```
#!/bin/sh
set -eu

dir="/srv/camera/front"
max_age_seconds=300

latest="$(find "$dir" -type f -name '*.mp4' -printf '%T@ %p\n' | sort -n | tail -1 | cut -d' ' -f1)"

now="$(date +%s)"
latest_int="${latest%.*}"

age=$((now - latest_int))

[ "$age" -lt "$max_age_seconds" ]
```


# Severity model

We define 3 severity classes.
Every new services installed to the platform must define which class it belongs to.

## Class A — Auto-repair safe
Examples:
- stateless reverse proxy
- homepage
- simple exporter
- torrent web UI, maybe
- camera uploader, maybe

Policy:
- alert after 3 failed checks
- restart after 5 failed checks
- alert if restart happened

## Class B — Alert only by default
Examples:
- Nextcloud
- Home Assistant
- Bitcoin Core
- databases
- backup jobs

Policy:
- alert after repeated failure
- do not auto-restart unless failure mode is known safe

## Class C — Never auto-repair blindly
Examples:
- Lightning node
- primary database
-anything controlling master election
- anything involving funds or single-writer state

Policy:
- alert
- include diagnostic facts
- require manual runbook
