# Ops Controller

`<box>-ops` is the optional in-Platform infrastructure controller VM. It moves
trusted infrastructure automation and private state into the Platform TCB. It
currently supports a transitional Codex/OpenAI runner container. Controller
private state is not mounted into the container, but the runner remains a
persistent Control TCB authority because it can connect as `smith`. The target
architecture uses a dedicated `<box>-airunner` VM.

## Provision

Prerequisites:

- the selected box already has dom0, router, and the Alpine VM template;
- the current controller has root-only OAuth material in
  `/etc/klokast/tailscale-policy.env` and
  `/etc/klokast/tailscale-devices.env`;
- Tailnet policy allows `tag:ops` to reach `tag:ops` as `smith`.

Run from the current controller:

```sh
ansible/bin/provision-ops-vm --box k001
```

The controller clones the public `klokast/klokast-box` repository over HTTPS.
It does not hold a GitHub credential and never authors source commits.
Write-enabled repository identities belong only to approved airunners.

The role then clones or fast-forwards the repo on `<box>-ops`:

```text
/home/smith/src/klokast/klokast-box
```

The provisioning flow also migrates `/home/codex/private/klokast/` into
`/home/smith/private/klokast/`, copies the root-only Tailscale OAuth
files into `/etc/klokast/`, and removes legacy reusable auth-key files from
`/etc/tailscale-auth/` on the new controller.

Routine controller-side updates are intentionally simple:

```sh
tailscale ssh smith@k001-ops \
  'cd ~/src/klokast/klokast-box && git pull --ff-only'
```

The one-time private-to-public cutover uses
`ansible/bin/rehome-public-checkout`. It verifies the new public `main`,
preserves an unrelated private-history checkout beside the canonical path, and
installs a clean public clone. It never resets or deletes the old checkout.

## Active/Standby Controller HA

The Platform uses one active controller at a time. Runtime markers, not a
preferred controller in Git, identify the active controller. Inspect them with:

```sh
ansible/bin/ops-controller-ha status
ansible/bin/ops-controller-ha resolve-active
```

Provision the standby from the active controller without copying provider/API
authority:

```sh
ansible/bin/ops-controller-ha bootstrap-standby --box k001 --active k002
ansible/bin/ops-controller-ha sync --from k002 --to k001
ansible/bin/ops-controller-ha status
```

For a planned, no-outage handoff, both repositories must be clean and current:

```sh
ansible/bin/ops-controller-ha switchover \
  --old-active k001 \
  --new-active k002
ansible/bin/ops-controller-ha sanitize-standby \
  --box k001 \
  --active k002 \
  --confirm
```

The standby receives controller tooling, the private Platform registry,
approved-state exports, and non-secret Secret Authority metadata. It does not
retain Tailscale OAuth files, GitHub App private keys, Cloudflare authority,
Cloudflare tunnel tokens, Tailscale machine state, or controller GitHub keys.

Manual promotion requires the old active controller to be fenced:

```sh
ansible/bin/ops-controller-ha promote \
  --new-active k001 \
  --old-active k002 \
  --old-active-fenced
```

After promotion, reseed provider/API authority from the MacBook-side wrappers:

```sh
ansible/bin/ops-controller-ha reseed --controller k001-ops
klokast-dev/bin/install-tailscale-oauth \
  --controller k001-ops \
  --policy-env path/to/tailscale-policy.env \
  --devices-env path/to/tailscale-devices.env
```

Steady-state Ansible access should use Tailscale SSH and Tailnet ACL/SSH rules,
not copied controller private keys. Bootstrap-only SSH key paths remain only
for pre-enrollment first contact.

## Boundary

- `smith`: infrastructure authority, private state, SSH keys,
  Tailscale wrappers, Tailnet policy apply, Platform resource apply, builders,
  and dom0/router authority.
- `minion`: app install and verification account; it may read approved
  grants and call explicitly delegated app wrappers, but it must not read infra
  credentials, private registries, or mutate infrastructure.

Approved app grants live under `/var/lib/klokast/approved-state/apps/`.
They are written by `smith` after `platform-resources apply` and read
by `minion` during app installation.

The same-host user split is a convenience boundary. Public app code and app
agents must still run without TCB credentials or inside separate disposable
sandboxes.

## AI Runner

`vultr-ops` and transitional `<box>-ops-airunner` containers can coexist as
approved Codex/OpenAI runners. Replace an in-Platform runner blue-green:

```sh
ansible/bin/converge-ops-airunner --box k002 --instance candidate
# Verify Mosh from the Mac to k002-ops-airunner-candidate.
ansible/bin/converge-ops-airunner \
  --box k002 --instance canonical --require-candidate-ready
# Verify Mosh from the Mac to k002-ops-airunner.
ansible/bin/retire-ops-airunner-candidate --box k002
```

The container and Tailnet names follow `<box>-<vm>-<container>`:
`k002-ops-airunner` and `k002-ops-airunner-candidate`. Runtime services are
`airunner` and `airunner-candidate`; Platform state and image namespaces remain
under `/var/lib/klokast/` and `localhost/klokast/`.

The Debian image includes Codex, Git, Mosh, tmux, Vim, jq, ShellCheck, ripgrep,
and a basic native build toolchain. Interactive `agent` shells automatically
attach tmux session `main` in `/home/agent/src/klokast/klokast-box`.
The repository, GitHub deploy key, Codex authentication, configuration, and
sessions live in the persistent `/home/agent` host bind mount, not in the
image. Convergence creates the airunner-owned GitHub key and clones the
repository when absent, but never updates, resets, or replaces an existing
checkout. A new key must be registered as a write-enabled
`klokast/klokast-box` deploy key before convergence can continue.

The runner uses the `agent` account and must not mount `/home/smith`,
`/etc/klokast`, `/var/lib/klokast`, controller deploy keys, private registries,
OAuth files, or broker state. Approve it for routine use only after Codex auth,
GitHub access, remote-terminal access to `smith@k002-ops`, session archiving,
and negative secret-read checks pass. Keep the approved runner set small because
each runner is a persistent Control TCB authority. These checks verify secret
custody and accidental path exposure; they do not make the container
non-authoritative or provide a VM-strength compromise boundary. Candidate and
canonical instances temporarily share `/home/agent`; do not run concurrent
Codex or Git mutations during cutover.
The `neo` account is ephemeral recovery access; only `agent` state is
persistent. Tmux survives network interruption, not container replacement.
