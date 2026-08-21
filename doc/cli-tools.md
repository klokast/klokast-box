# CLI Tools And Wrappers

This is an index of checked-in operator-facing CLI tools and wrappers. It
excludes tests, generated `.run/` artifacts, ordinary service entrypoints, and
third-party toolchains.

Locus terms:

- `controller`: active `<box>-ops` as `smith`, from `~/src/klokast/klokast-box`.
- `laptop`: the operator MacBook.
- `infra-agent`: `vultr-ops` as `agent`; use only for repo work and remote
  dispatch to the controller.
- `target`: the managed host, VM, container, or NanoKVM where the command is
  installed by Ansible.
- `root wrapper`: installed root-owned copy under `/usr/local/sbin`; do not run
  the source copy with `sudo` directly from the repo.

## Klokast Instance Specification CLI

Source: `cmd/klokast/`. A deployable binary must come from the active-controller
`platform-builder` output. Use it through `platform-plan`; do not install it as
an ambient controller command.

| Tool | Locus | What it does |
| --- | --- | --- |
| `klokast version --json` | trusted local host | Reports the builder-bound engine repository, ref, and full commit. |
| `klokast init` | trusted local host | Creates and stages a new offline Instance Specification v1 repository from one complete strict JSON instance file. It does not create a commit or remote. |
| `klokast check` | trusted local host | Performs an offline, non-mutating validation of a standalone Instance Specification v1 repository. |
| `klokast plan` | trusted local host | Compares Instance Specification v1 with all legacy desired-state inputs. With a fresh Observation v1 input and Instance Source Receipt v1, it emits a hashed, provenance-aware Plan v1 artifact. It does not apply changes. |

## Platform And Controller Wrappers

Source: `ansible/bin/`.

| Tool | Locus | What it does |
| --- | --- | --- |
| `archive-codex-sessions` | controller | Archives selected Codex session state from a retiring runner into controller private state. |
| `bootstrap-dom0` | controller | Runs phased blank-SSD bootstrap from Debian live ISO to verified Alpine dom0. |
| `bootstrap-live-iso` | controller | Converges the bootstrap ISO builder, builds the generic Debian live ISO, transfers it to NanoKVM, and cleans up. |
| `bootstrap-live-iso-release` | controller/laptop | Validates and publishes a secret-free bootstrap ISO and Alpine seed bundle as GitHub Release assets. |
| `converge-ops-airunner` | controller | Converges the canonical or blue-green candidate AI runner on an existing `<box>-ops`. |
| `converge-ops-controller` | controller | Reapplies the baseline to an existing `<box>-ops`; unauthorized APK world entries fail closed unless reviewed pruning is explicit. |
| `decommission-box` | controller | Stops guests, deletes stale Tailnet identities, wipes dom0 SSD state, and powers off or reboots one box. |
| `nanokvm-virtual-media` | controller/deployment server | Operates NanoKVM media, HID paste, token/password recovery, reboot, service restart, and USB reset over root SSH/API. |
| `ops-controller-ha` | controller/dispatcher | Manages active/passive ops controllers: status, resolve-active, standby bootstrap, sync, promote, demote, run, and reseed. |
| `platform-app` | active controller | Manages approved app lifecycle through `list`, `status`, `apply`, `verify`, `start`, `stop`, `restart`, `remove`, and `destroy`. |
| `platform-guest` | active controller | Manages durable `bak`, `dmz`, and `iot` Xen guest runtime intent through `list`, `status`, `apply`, `verify`, `start`, and `stop`. |
| `platform-check` | controller | Runs read-only Platform health checks for dom0, router, Podman VMs, ops, map, and resources. |
| `platform-check-remote` | infra-agent/laptop | Dispatches `platform-check` to the active controller over Tailscale SSH, optionally pulling first. |
| `platform-image-build` | active controller | Builds, loads, verifies, and cleans app OCI image archives from the controller. |
| `platform-instance` | active controller | Guides and validates the fixed private initialization values, seeds an Instance Specification v1 repository with a sealed-builder binary, and prepares, synchronizes, or reports the root-custodied read-only deployment source. |
| `platform-builder` | active controller | Builds the reviewed `klokast` CLI in a bounded, networkless, short-lived Xen guest and preserves verified outputs under `/var/lib/klokast/builds/`. |
| `platform-map` | controller | Discovers Platform state, writes the ignored summary JSON, validates it, and emits dynamic inventory. |
| `platform-plan` | active controller | Verifies one sealed-builder CLI receipt and one root-owned instance source receipt, creates Plan v1 from controller-private inputs, verifies its hash, and stores it without replacement under `/var/lib/klokast/plans/`. |
| `platform-resources` | controller | Compiles, lints, shows, diffs, applies, verifies, inventories, and grants Platform resource intent. |
| `provision-box` | controller/deployment server | Provisions one box from bootstrap ISO through dom0, Xen, router, and Podman VMs. |
| `provision-ops-vm` | current controller | Creates an in-Platform `<box>-ops` controller VM and optionally provisions it as standby. |
| `refresh-ops-secrets` | current controller | Copies root-only Tailscale OAuth env files from the current controller into an existing `<box>-ops`. |
| `reinstall-box` | controller | Loads a bootstrap ISO, decommissions the current box, waits for bootstrap enrollment, then runs `provision-box`. |
| `render-node-inventory` | local helper | Renders temporary per-box Ansible inventory for wrappers and playbooks. |
| `retire-ops-airunner-candidate` | active controller | Removes an offline candidate after canonical Mosh acceptance. |
| `secret-authority` | controller | Dispatches Secret Authority intent generation locally and approved actions through root `ksa-*` wrappers. |

## Tailscale Root Wrappers

Source: `klokast-ops/tailscale/bin/`; installed as root wrappers under
`/usr/local/sbin/` on the controller/deployment server.

| Tool | Locus | What it does |
| --- | --- | --- |
| `ts-authkey-mint` | root wrapper | Validates purpose, hostname, and tags, then mints a short-lived one-use Tailscale auth key via OAuth. |
| `ts-authkey-bootstrap` | root wrapper | Calls `ts-authkey-mint --purpose bootstrap` for bootstrap live hosts. |
| `ts-authkey-dom0` | root wrapper | Calls `ts-authkey-mint --purpose dom0` for steady-state dom0 identities. |
| `ts-authkey-vm` | root wrapper | Calls `ts-authkey-mint --purpose vm` for router, shared-zone, app, and per-user VMs. |
| `ts-authkey-ops` | root wrapper | Calls `ts-authkey-mint --purpose ops` for `<box>-ops` controller identities. |
| `ts-authkey-infra` | root wrapper | Calls `ts-authkey-mint --purpose infra` for standalone infra-agent hosts. |
| `ts-authkey-infra-agent` | root wrapper | Legacy alias for `--purpose infra-agent`. |
| `ts-authkey-back` | root wrapper | Calls `ts-authkey-mint --purpose back` for backend builder/container identities. |
| `ts-authkey-dmz` | root wrapper | Calls `ts-authkey-mint --purpose dmz` for DMZ container identities. |
| `ts-authkey-iot` | root wrapper | Calls `ts-authkey-mint --purpose iot` for IoT container identities. |
| `ts-authkey-usr` | root wrapper | Calls `ts-authkey-mint --purpose usr` for user-zone container identities. |
| `ts-authkey-nextcloud` | root wrapper | Calls `ts-authkey-mint --purpose nextcloud` for Nextcloud private ingress. |
| `ts-authkey-immich` | root wrapper | Calls `ts-authkey-mint --purpose immich` for Immich private ingress. |
| `ts-authkey-music` | root wrapper | Calls `ts-authkey-mint --purpose music` for music private ingress. |
| `ts-authkey-music-upload` | root wrapper | Calls `ts-authkey-mint --purpose music-upload` for music upload ingress. |
| `ts-authkey-print` | root wrapper | Calls `ts-authkey-mint --purpose print` for print ingress. |
| `ts-authkey-streamer` | root wrapper | Calls `ts-authkey-mint --purpose streamer` for Raspberry Pi streamer identities. |
| `ts-policy-pull` | root wrapper | Downloads the live Tailscale policy to the approved repo policy path. |
| `ts-policy-validate` | root wrapper | Validates the repo Tailnet policy file through the Tailscale API. |
| `ts-policy-apply` | active-controller root wrapper | Validates then applies the repo Tailnet policy through the Tailscale API. |
| `ts-devices-list` | root wrapper | Lists Tailnet devices through the scoped devices OAuth client. |
| `ts-device-delete-stale` | active-controller root wrapper | Deletes one stale offline Tailnet device only after re-fetching and matching id, hostname, tag, and offline status. |

## Secret Authority Wrappers

| Tool | Source | Locus | What it does |
| --- | --- | --- | --- |
| `ksa-static-site` | `klokast-ops/secret-authority/bin/` | root wrapper | Handles static-site Secret Authority intents and approved actions for GitHub App and Cloudflare token storage. |
| `ksa-instance` | `klokast-ops/secret-authority/bin/` | root wrapper | Verifies and registers the human-created private repository, registers its read-only deploy key, retires the temporary GitHub App, and synchronizes root-custodied instance source evidence. |
| `klokast-controller-guard` | `ansible/roles/ops-controller/files/` | controller target | Checks the controller HA marker and exits nonzero unless the local controller is active. |

## Laptop And Developer Convenience Tools

Source: `klokast-dev/bin/`.

| Tool | Locus | What it does |
| --- | --- | --- |
| `kk` | laptop | Mac-side convenience CLI for doctor checks, remote `platform-app`, music upload, streamer poweroff, and torrent open/status. |
| `install-tailscale-oauth` | laptop | Sends local Tailscale OAuth env files to the active controller as root-owned `/etc/klokast/` files. |
| `install-static-site-github-app` | laptop | Installs static-site GitHub App id, installation id, and private key into controller root Secret Authority storage. |
| `install-instance-github-app` | laptop | Installs the dedicated temporary private-instance bootstrap GitHub App credential into controller root storage. |
| `prepare-private-instance-bootstrap` | laptop | Lists and checks the private-instance bootstrap prerequisites, verifies the Touch ID approval signer, and writes a local non-secret runbook session file. |
| `prepare-private-instance-worktree` | laptop | Runs the guided owner-only values setup on the active controller, seeds with the pinned sealed build, streams the generated repository to the MacBook, and verifies its initial Git state without copying private values into arguments or the redacted journal. |
| `install-secret-authority-approval-signer` | laptop | Creates or reuses one Apple-native Touch ID signer for the selected authority scope and installs its public key on the controller. |
| `run-private-instance-action` | laptop | Displays, validates, signs with Touch ID, transfers, and runs one exact private-instance bootstrap action on the active controller. |
| `sign-secret-authority-intent` | laptop | Signs one existing Secret Authority intent with the exact purpose-specific Touch ID identity through a private, short-lived Apple agent. |
| `ingest-static-site-cloudflare-token` | laptop | Generates and signs a static-site Cloudflare token-ingestion intent with the static-site Touch ID identity, sends the token over stdin, and verifies redacted status. |
| `macbook-tailnet-direct` | laptop | Checks or applies macOS proxy bypass rules for Tailnet traffic and prints Clash DIRECT rules. |

## Cloud Infra-Agent Provisioning

Source: `klokast-ops/bin/`.

| Tool | Locus | What it does |
| --- | --- | --- |
| `provision-vultr-ops` | laptop/infra-admin workstation | Creates or updates the `vultr-ops` infra-agent host with Terraform, Ansible, Tailnet enrollment, GitHub deploy key, and repo checkout. |
| `provision-vultr-coder-guest` | laptop/infra-admin workstation | Creates a locked guest coding account on `vultr-ops`, creates a private GitHub repo, registers its deploy key, and clones it. |

## App Lifecycle Wrappers

Source: `apps/*/bin/`.

| Tool | Locus | What it does |
| --- | --- | --- |
| `household-vpnctl` | controller | Deploys, installs, verifies, and checks status for the household VPN gateway app VM. |
| `local-ingressctl` | controller | Deploys, installs, verifies, and checks status for local HTTPS ingress. |
| `torrentctl` | controller | Deploys, installs, verifies, and checks status for the torrent app VM. |
| `musicctl` | controller | Runs Music preflight, backend install, Pi endpoint install, full install, verification, and guarded removal. |
| `print-serverctl` | controller | Runs print-server preflight, install, and verify. |
| `nextcloudctl` | controller | Legacy Nextcloud active/passive preflight, install, verify, backup-check, promote/failback, and remove wrapper. |
| `nextcloud-v2ctl` | controller | Grant-based Nextcloud v2 image build, infra-prepare, install/start/stop/verify/backup/promote/remove wrapper. |
| `immichctl` | controller | Immich active/passive preflight, image checks, private ingress identity, install, verify, backup, promote/failback, remove, and destroy wrapper. |
| `immich-install-from-controller` | controller | Controller-side install flow that creates/reuses Immich secrets and runs app setup for active/passive placement. |
| `immich-install-from-mac` | laptop | Mac-side Immich install flow that creates/reuses local secrets and dispatches setup to the controller. |
| `static-sitectl` | controller | Static-site bootstrap-repo, preflight, install, verify, and remove wrapper. |

## Bootstrap ISO Direct Builders

Source: `apps/bootstrap-iso-debian/`.

| Tool | Locus | What it does |
| --- | --- | --- |
| `build-alpine-seed.sh` | local builder | Extracts a versioned Alpine diskless seed tarball from an Alpine standard ISO. |
| `build-bootstrap-iso.sh` | local builder | Builds the generic Debian live bootstrap ISO with the onboarding portal. |

## Installed Target Helpers

These are installed by Ansible roles or rendered from templates. They are
normally invoked by OpenRC, Ansible, or higher-level wrappers, not manually.

| Tool | Locus | What it does |
| --- | --- | --- |
| `klokast-node` | target Podman VM | Applies, verifies, or removes target-local desired app state for `nextcloud-v2` and `openclaw`. |
| `klokast-app-resources-reconcile` | target router/VM | Applies or verifies keyed nftables snippets produced by `platform-resources`. |
| `klokast-docker-user-firewall` | target Debian app VM | Installs a Docker `DOCKER-USER` baseline to avoid broad published-port ingress. |
| `debian-app-vm-image-builder-build` | builder target | Builds a Debian app-VM image from a rendered spec and authorized keys. |
| `debian-app-vm-image-builder-build-inside` | builder target | Inner rootfs/image construction helper for the Debian app-VM image builder. |
| `bootstrap-live-builder-build` | builder target | Builds the bootstrap live ISO and optional Alpine seed inside the builder workload. |
| `bootstrap-live-builder-deploy` | builder target | Deploys or refreshes the bootstrap-live-builder runtime service/container. |
| `airunner-shell` | ops target | Enters the optional AI runner container as the `agent` user. |
| `airunner-candidate-shell` | ops target | Enters the temporary blue-green candidate as the `agent` user. |
| `household-vpn-render` | household-vpn target | Renders Mihomo config from private VPN config and subscription data. |
| `household-vpn-refresh` | household-vpn target | Refreshes VPN subscription/config and restarts Mihomo when needed. |
| `torrent-vpn-render` | torrent target | Renders Mihomo config for the torrent VPN isolation path. |
| `torrent-vpn-refresh` | torrent target | Refreshes torrent VPN subscription/config and restarts Mihomo when needed. |
| `torrent-local` | torrent target | Local qBittorrent helper for status, pause-all, resume-all, and data paths. |
| `static-site-web-deploy` | static-site target | Starts or refreshes the static web server container. |
| `static-site-publisher` | static-site target | Polls the private Git repo and atomically publishes the configured static source tree. |
| `static-site-cloudflared-check` | static-site target | Verifies the static-site Cloudflare tunnel connector. |
| `nextcloud-backend-deploy` | Nextcloud target | Starts or refreshes legacy Nextcloud backend containers. |
| `nextcloud-cloudflared-deploy` | Nextcloud target | Starts or refreshes legacy Nextcloud Cloudflare tunnel connector. |
| `nextcloud-private-ingress-converge` | Nextcloud target | Converges legacy Nextcloud private Tailnet ingress. |
| `nextcloud-occ` | Nextcloud target | Runs Nextcloud `occ` inside the backend container context. |
| `nextcloud-backup-run` | Nextcloud target | Runs the legacy Nextcloud restic backup flow. |
| `nextcloud-image-source-preflight` | Nextcloud target | Checks Nextcloud image source/pin expectations before install. |
| `immich-backend-deploy` | Immich target | Starts or refreshes Immich backend containers. |
| `immich-private-ingress-converge` | Immich target | Converges Immich private Tailnet ingress. |
| `immich-backup-run` | Immich target | Runs the Immich backup flow. |
| `immich-image-source-preflight` | Immich target | Checks Immich image source/pin expectations before install. |
| `music-backend-deploy` | music target | Starts or refreshes the music backend container service. |
| `print-server-deploy` | print-server target | Starts or refreshes the print server container service. |
