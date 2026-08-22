Here are the manual instruction for Human to setup CloudFlare.

# Agent reminder
When a task requires the human to operate a provider web UI, explain the
current dashboard actions and the shell commands needed to pass task-specific
secrets. Keep durable docs focused on stable intent and do not commit secret
values or long-lived click-path details that belong in a live answer.

# 0. Remarks
- Use case: small NextCloud deployment for lab or trusted-family, on top of the Klokast Platform.
- CloudFlare is public ingress only.
- Trusted family access should use the private Tailscale URL
  `https://next.<tailnet>` instead of Cloudflare when the client is on the
  tailnet.
- We use the Free zone plan Cloudflare free plan, with our domain (`example.tld`) onboarded to "Cloudflare DNS" and "Cloudflare Tunnel".
- Admin and recovery stay on Tailscale.
- As per `doc/architecture.md` and `apps/nextcloud/docs/architecture.md`, the CloudFlare Tunnels originate from the active `<box>-dmz` VM.
- No router DNAT to `80/443`.
- Use MFA/security keys
- Avoid shared admin use.
- Keep tunnel/API secrets outside git.
- We don't need a Cloudflare API token for this current manual flow. Later, if route switching is automated, create scoped tokens only, not a global API key.

- This CloudFlare solution is a trade-off:
  - The CloudFlare tunnel removes inbound exposure and hides the residential origin.
  - But CloudFlare can read the (HTTP layer) NextCloud traffic, unencrypted, because Cloudflare terminates public TLS at its edge.
  - To get both, we would need to setup Nebula.

- Browser, mobile, desktop sync, and WebDAV clients need native Nextcloud protocol behavior -->  Don't put Cloudflare Access in front of the main Nextcloud hostname. On those devices, use Nextcloud auth, 2FA, app passwords, and Nextcloud brute-force protection instead.

- Free Tier Limits: Free request body limit is 100 MB. Test large browser, desktop, mobile, and WebDAV uploads. If you see `413`, lower Nextcloud chunk size later or move to a higher Cloudflare plan.

- Manual failover: On promotion, fence/stop the old active, run
  `nextcloudctl promote --resources-registry path/to/platform-resources.yml`, then
  move `cloud.example.tld` to the new site's tunnel. Do not enable automatic
  Cloudflare Load Balancing until the platform has strong fencing and freshness
  checks, or Cloudflare may route users to a stale passive site.

# 1. Put a domain on Cloudflare
- Either register through Cloudflare Registrar or add an existing domain,
- Review DNS records,
- Update registrar nameservers.
- Use a dedicated hostname such as `next.example.tld` for the NextCloud app. Don't point the CloudFlare tunnels to the apex domain `example.tld`.
- This public hostname is separate from the private MagicDNS name
  `next.<tailnet>`.
- Keep mail records DNS-only.
- Cloudflare's onboarding flow is documented here: `https://developers.cloudflare.com/fundamentals/manage-domains/add-site/`

# 2. Create two Cloudflare Tunnels
- cloudflare.com > zero trust > start now > type team name: `klokast` > select free plan > enter credit card (won't be charged) > skip >
- now in the `zero trust` dashboard > Networks > Connectors > Add a tunnel > CloudFlared
 tunnel name: `klokast-nextcloud-boxa` > (save the key, but not in git)
- back to Connectors > and repeat with the passive tunnel `klokast-nextcloud-boxb`
- Copy each remote-managed tunnel token as `NEXTCLOUD_CLOUDFLARED_TOKEN_ACTIVE` and `NEXTCLOUD_CLOUDFLARED_TOKEN_PASSIVE`

- Only `klokast-nextcloud-boxa` should publish: "`next.klokast.ai` -> `http://192.168.100.10:8080`".
- Do not publish the same hostname on the passive boxb tunnel at the same time.
- During failover you move the route to the boxb tunnel after promotion.
- Confirm DNS has one proxied CNAME: `next.klokast.ai` -> `<boxa tunnel UUID>.cfargotunnel.com`
- No A/AAAA record to your WAN IP.
- No router DNAT for public 80 or 443.

# 3. Setup the active tunnel route: `next.klokast.tld` pointing to `http://192.168.100.10:8080`
Cloudflare dashboard > Zero Trust > Networks -> overview > Manage Tunnels > View tunnels > click the active tunnel (`klokast-nextcloud-boxa`)
(to create new) > Published Application route > add a published application route >
(to edit existing) > Published Application Routes > `next.klokast.ai` > edit

- Add or edit the route for Nextcloud:
  - Type: Published application
  - Subdomain: next
  - Domain: klokast.ai
  - Path: leave empty
  - Service type: HTTP
  - Service URL: http://192.168.100.10:8080 (beware: use `http`, not `HTTPS`, it's the backend Nextcloud HTTP upstream as seen from `boxa-dmz`)
- Save

# 4. Ensure the passive tunnel has no route
Cloudflare dashboard > Zero Trust > Networks -> overview > Manage Tunnels > View tunnels > click the passive tunnel (`klokast-nextcloud-boxb`) > Confirm there is no route for `next.klokast.ai` or delete that route

# 5. Confirm that CloudFlare created the DNS record automatically
Cloudflare dashboard > Domains > overview > `klokast.ai` > DNS > Records > Find "Tunnel" records for `next` > edit >
  1. Keep exactly one record:
      - Type: CNAME
      - Name: next
      - Target: <boxa tunnel UUID>.cfargotunnel.com
      - Proxy status: Proxied
      - TTL: Auto
  2. Delete any wrong records for next, especially:
      - A record to your WAN IP
      - AAAA record to your WAN IPv6
      - CNAME to the boxb tunnel
      - DNS-only gray-cloud record
  3. If the CNAME was not auto-created, create it manually
      - DNS -> Records:
        1. Select Add record.
        2. Type: CNAME
        3. Name: next
        4. Target: <boxa tunnel UUID>.cfargotunnel.com (Beware: use the tunnel UUID target, not the tunnel name)
        5. Proxy status: Proxied
        6. Save.

# 6. Do not configure WAN forwarding
Do not add router/firewall DNAT for public 80 or 443. The only intended path is:
```
Cloudflare edge
-> Cloudflare Tunnel
-> boxa-dmz cloudflared
-> http://192.168.100.10:8080 oxsn boxa-bak
```

# 7. Enable and apply platform resources
From the `ops` deployment server:

Enable `nextcloud` in the deployment platform-resource registry, set
`active_master: boxa`, `passive_backup: boxb`, and set
`resources.cloudflare-tunnel-egress: true`. The standard repo only ships a
disabled example at `ops/platform-resources.example.yml`; the enabled registry should
live in the deployment-specific config repo or file.

```
cd /home/codex/src/klokast/klokast-box
ansible/bin/platform-resources \
  --registry path/to/platform-resources.yml \
  --app nextcloud \
  --approved-commit "$(git rev-parse HEAD)" \
  apply
```

# 8. Set up NextCloud passwords on codex shell
From the `ops` deployment server (Enter your chosen passwords as prompted):

The deployment server must also have `/usr/local/sbin/ts-authkey-nextcloud`
installed. The target is OAuth-backed one-off key minting; a transitional
root-only `/etc/tailscale-auth/ts-auth-nextcloud.authkey` file is legacy only.

```
export NEXTCLOUD_RESTIC_REPOSITORY='sftp:neo@boxb-bak.example.ts.net:/srv/nextcloud-restic/boxa'

read -r -s -p 'NEXTCLOUD_ADMIN_PASSWORD: ' NEXTCLOUD_ADMIN_PASSWORD; echo; export NEXTCLOUD_ADMIN_PASSWORD

read -r -s -p 'NEXTCLOUD_POSTGRES_PASSWORD: ' NEXTCLOUD_POSTGRES_PASSWORD; echo; export NEXTCLOUD_POSTGRES_PASSWORD

read -r -s -p 'NEXTCLOUD_RESTIC_PASSWORD: ' NEXTCLOUD_RESTIC_PASSWORD; echo; export NEXTCLOUD_RESTIC_PASSWORD

read -r -s -p 'NEXTCLOUD_CLOUDFLARED_TOKEN_ACTIVE: ' NEXTCLOUD_CLOUDFLARED_TOKEN_ACTIVE; echo; export NEXTCLOUD_CLOUDFLARED_TOKEN_ACTIVE

read -r -s -p 'NEXTCLOUD_CLOUDFLARED_TOKEN_PASSIVE: ' NEXTCLOUD_CLOUDFLARED_TOKEN_PASSIVE; echo; export NEXTCLOUD_CLOUDFLARED_TOKEN_PASSIVE
```

# 9. Install and start NextCloud
This installs backend services, starts `nextcloud-private-ingress` on
`boxa-dmz`, installs optional `nextcloud-cloudflared`, and keeps the passive
DMZ services stopped on `boxb-dmz`:

```
cd /home/codex/src/klokast/klokast-box

apps/nextcloud/bin/nextcloudctl install \
  --active-master boxa \
  --passive-backup boxb \
  --domain next.klokast.ai \
  --resources-registry path/to/platform-resources.yml
```

# 10. Verify
```
apps/nextcloud/bin/nextcloudctl verify \
  --active-master boxa \
  --passive-backup boxb \
  --domain next.klokast.ai \
  --resources-registry path/to/platform-resources.yml
```

# 11. Test
From laptop or `ops` server:
```
curl -fsS https://next.<tailnet>/status.php
curl -I https://next.klokast.ai
curl -fsS https://next.klokast.ai/status.php
```
- test from browser: `https://next.<tailnet>` and, when Cloudflare is enabled,
  `https://next.klokast.ai`

/////////////

- main dahsboard > domains > `example.tld` > Rules > Overview > Create rule > Cache rule >
  - Rule name: `Bypass cache for Nextcloud`
  - condition: `Hostname` `equals` `next.example.tld`
  - cache eligility: Bypass cache
  > deploy

- See documentation: `https://developers.cloudflare.com/tunnel/setup/`

# 4. Create one WAF custom rule
- CloudFlare Free plan allows to create one free rule. Especially, don't block `/remote.php`, `/ocs`, `/index.php`, `/login`, `/status.php`, `/.well-known/*`
- main dashboard > domains > `example.tld` > security > Security Rules > create rule > managed rule

  Name: Block scanner paths on Nextcloud
  Action: Block

  Expression:

  http.host eq "next.klokast.ai" and (
    lower(http.request.uri.path) in {"/.env" "/.git/config" "/xmlrpc.php" "/wp-login.php" "/phpinfo.php" "/server-status"}
    or starts_with(lower(http.request.uri.path), "/wp-")
    or starts_with(lower(http.request.uri.path), "/wp/")
    or starts_with(lower(http.request.uri.path), "/cgi-bin/")
    or starts_with(lower(http.request.uri.path), "/vendor/phpunit/")
    or starts_with(lower(http.request.uri.path), "/actuator/")
  )

# 5. SSL/TLS
- main dashboard > SSL/TLS > full

# 6. Do not expose WAN ports
- That is the backend Nextcloud HTTP upstream as seen from the DMZ connector.
- Let Cloudflare create the proxied DNS record, or manually create the proxied CNAME to `<TUNNEL_ID>.cfargotunnel.com`.
- No router DNAT for public `80` or `443`. The only public path should be:
  Cloudflare edge -> Cloudflare Tunnel -> `<box>-dmz` cloudflared -> `<box>-bak:8080`.

# 8. Bypass cache for the Nextcloud hostname
- Caching is not worth the risk initially, as Nextcloud is authenticated, WebDAV-heavy, and file-path-heavy.
- Create cache Rule:
  hostname equals next.klokast.ai, cache eligibility Bypass.
- Put that rule above any broader cache rule.
- See `https://developers.cloudflare.com/cache/how-to/cache-rules/settings/`
- When testing, `cf-cache-status` may show DYNAMIC instead of BYPASS; that is normal for bypass cache rules.
 #TODO install `cf` the cloudflare CLI, with credentials, on `ops`



- - - - -
- - - - - - - - - - -

# 4. Leave Cloudflare Access off for the main Nextcloud hostname.

The app docs explicitly avoid Access in front of Nextcloud because browser, mobile, desktop sync, and WebDAV clients need normal
protocol behavior: apps/nextcloud/docs/architecture.md:36.



# 8. Export secrets on the ops server, without putting them in git or .env:

Here we paste the passwords into environment variables of the shell, manually, without exposing them to the shell history.

The first 3 passwords can be generated randomly.

export NEXTCLOUD_RESTIC_REPOSITORY='sftp:neo@boxb-bak.example.ts.net:/srv/nextcloud-restic/boxa'

```
read -r -s -p 'NEXTCLOUD_ADMIN_PASSWORD: ' NEXTCLOUD_ADMIN_PASSWORD; echo; export NEXTCLOUD_ADMIN_PASSWORD

read -r -s -p 'NEXTCLOUD_POSTGRES_PASSWORD: ' NEXTCLOUD_POSTGRES_PASSWORD; echo; export NEXTCLOUD_POSTGRES_PASSWORD

read -r -s -p 'NEXTCLOUD_RESTIC_PASSWORD: ' NEXTCLOUD_RESTIC_PASSWORD; echo; export NEXTCLOUD_RESTIC_PASSWORD
```
The 2 next passwords are the keys generated by CloudFlare tunnels:
```
read -r -s -p 'NEXTCLOUD_CLOUDFLARED_TOKEN_ACTIVE: ' NEXTCLOUD_CLOUDFLARED_TOKEN_ACTIVE; echo; export NEXTCLOUD_CLOUDFLARED_TOKEN_ACTIVE

read -r -s -p 'NEXTCLOUD_CLOUDFLARED_TOKEN_PASSIVE: ' NEXTCLOUD_CLOUDFLARED_TOKEN_PASSIVE; echo; export NEXTCLOUD_CLOUDFLARED_TOKEN_PASSIVE
```
# 9. Run Nextcloud:

apps/nextcloud/bin/nextcloudctl preflight \
  --active-master boxa \
  --passive-backup boxb \
  --domain next.klokast.ai

apps/nextcloud/bin/nextcloudctl install \
  --active-master boxa \
  --passive-backup boxb \
  --domain next.klokast.ai \
  --resources-registry path/to/platform-resources.yml

apps/nextcloud/bin/nextcloudctl verify \
  --active-master boxa \
  --passive-backup boxb \
  --domain next.klokast.ai \
  --resources-registry path/to/platform-resources.yml

# 10. Run the first backup manually, then check it:

From `ops` deployment server:
```
ssh boxa-bak 'doas /usr/local/sbin/nextcloud-backup-run'

apps/nextcloud/bin/nextcloudctl backup-check \
  --active-master boxa \
  --passive-backup boxb \
  --domain next.klokast.ai
```

# 11. Test
```
curl -fsS https://next.klokast.ai/status.php
curl -I https://next.klokast.ai/
```

`https://next.klokast.ai/`
- login: `ncadmin`
- password: the value of `NEXTCLOUD_ADMIN_PASSWORD`

- upload/download a small file
- upload a file larger than 100 MB (Cloudflare Free/Pro has a 100 MB request body limit, so large files depend on Nextcloud chunking.)
- test desktop/mobile sync

# 12. Set up user accounts

- Via GUI: admin avatar in top-right > Accounts or Users > New account / New user > Set username, display name, email, password, groups > save
- Via CLI, interactively:
```
ssh neo@boxa-bak.example.ts.net
doas /usr/local/sbin/nextcloud-occ user:add alice


# 13. And for family members with a Tailscale account

`https://next.<tailnet>`
