# LLM-assisted secure private cloud operations

I built Klokast as a small-scale private cloud for self-hosted services where
the security model had to fit real domestic operations: a few physical sites,
limited hardware, intermittent residential connectivity, and no permanent
operations team. The platform uses one mini PC per site. Each box runs Alpine
Linux from RAM as a minimal Xen dom0, then isolates work into VMs for routing,
backend services, DMZ services, IoT workloads, user agents, and trusted
operations. Applications normally run as rootless Podman workloads inside those
zone VMs. A Tailscale overlay is the current management plane, with Cloudflare
Tunnel used only for selected public ingress.

## Architecture decisions

The main design decision was to treat the LLM as an operations accelerator, not
as the trusted authority. Codex runs on a cloud infra-agent host because it has
reliable OpenAI connectivity, but that host is explicitly outside the trusted
controller boundary. The active controller is an in-platform `<box>-ops` VM,
tagged as `tag:ops`, where Ansible, private state, deploy keys, Tailscale OAuth
material, and platform-resource registries live. The infra-agent host has only a
narrow remote-terminal path into the controller as `smith`. This means a
prompt, model error, or compromised cloud runner should not by itself become a
persistent infrastructure authority.

Inside the controller I split authority again. The `smith` account owns
private state, platform resource application, Tailnet policy wrappers, builders,
and dom0/router authority. The `minion` account runs app installation and
verification from sanitized grants. Same-host Unix accounts are not treated as a
perfect sandbox, but they are useful as an operational boundary: app automation
can consume approved state without reading infrastructure credentials.

The most important control-plane primitive is `platform-resources`. App
manifests declare intent in a narrow schema: compute, network, Tailnet, and
artifact needs. The compiler rejects unknown fields, rejects TCB-owned placement
or credential fields in app manifests, resolves symbolic zones into concrete
boxes, normalizes firewall resources into stable keys, detects exclusive
ownership conflicts, records the approved Git commit, and exports app-scoped
grants. App installers can verify that required router and VM firewall rules
exist, but they do not edit router nftables, Tailnet policy, dom0 Xen state, or
private placement data.

This pushed many risky decisions into versioned, deterministic code. For
example, Tailscale enrollment uses root-owned wrappers that mint short-lived,
single-use auth keys from scoped OAuth material. The wrappers validate the
purpose, hostname, and requested tags before calling the API, so an app workflow
cannot freely mint a `tag:ops` or `tag:vm` identity. Similarly, firewall changes
are rendered as keyed nftables snippets with ownership metadata, then verified
against both persisted desired state and the live ruleset. Codex can edit or
review these tools quickly, but the tools keep the actual authority narrow and
auditable.

## Trade-offs

The architecture trades generality for a small trusted surface. I deliberately
did not start with Kubernetes or a generic orchestrator because the target is
one to five sites, not hundreds of nodes. A zone-VM model gives most workloads a
clear network and failure boundary with less control-plane complexity. Dedicated
app VMs are still available for workloads that need stronger isolation, such as
untrusted code execution, rootful Docker, hardware passthrough, or a materially
different lifecycle. Xen adds operational complexity compared with a monolithic
home server, but it provides a cleaner blast-radius model: router, DMZ, backend,
IoT, agent, and controller concerns do not all fail inside one host OS.

The platform also favors manual promotion over automatic failover for stateful
apps. Immich and Nextcloud style workloads are modeled active/passive. Promotion
requires the operator to confirm that the old active site is down or fenced and
that the latest backup has been restored. This is slower than automatic
failover, but it avoids split-brain, which is a more serious failure mode in a
residential multisite system where power, ISP, and overlay reachability can fail
independently.

## Failure modes

Several failure modes shaped the implementation. The first was credential drift:
early reusable auth-key files are treated as transitional TCB debt and removed
from the migrated controller when possible. The second was authority confusion:
docs, wrappers, and dispatchers distinguish the cloud infra-agent from the
active controller, and remote platform checks dispatch to `<box>-ops` rather
than inspecting state locally on the LLM runner. The third was firewall drift:
resources are compiled into a ledger, materialized as keyed snippets, and
verified against live nftables comments. The fourth was app-manifest overreach:
schema validation prevents apps from smuggling topology, placement, OAuth, or
owner fields into their declared needs. The fifth was unsafe recovery:
provisioning keeps NanoKVM out-of-band access available and includes operator
gates around destructive SSD wipe and reinstall paths.

Builder trust was another explicit failure mode. Checksums verify that an
artifact arrived intact, but they do not prove the builder was trustworthy. The
target architecture therefore treats builders as TCB, to be run on `<box>-ops`
or short-lived controller-owned builder VMs. Where builder work is still
transitional, the code marks it as TCB-adjacent and avoids pretending it is
ordinary app co-tenancy.

## Codex in safe operations

Codex accelerated the work in ways that were useful precisely because the
system stayed deterministic. It searched the repo, reconciled documentation
with implementation, drafted Ansible roles and shell wrappers, wrote focused Go
and Python utilities, found inconsistencies in trust-boundary docs, and turned
manual runbook sequences into repeatable commands. It also made safe operations
faster by producing remote dispatch wrappers, status tools, validation paths,
and test plans that a human operator could run from the correct locus. The
workflow is closer to "LLM-assisted infrastructure engineering" than "AI
autopilot": Codex proposes and edits the code, but Git history, approved
commits, controller-local private state, root-owned wrappers, schema
validation, and human gates decide what can actually change the platform.

The result is a practical secure-operations pattern for small infrastructure:
use the LLM for high-leverage latent work, such as writing and reviewing
automation, but keep secrets, state mutation, identity issuance, and policy
application inside deterministic, least-privilege control paths. That made the
platform faster to build without making the model itself part of the permanent
trusted computing base.

Repository evidence includes `doc/architecture.md`, `doc/secret-authority.md`,
`ansible/bin/platform-resources`, `ansible/roles/ops-controller`,
`klokast-ops/tailscale/bin/ts-authkey-mint`,
`ansible/roles/app-resources/files/reconcile-app-resources.py`, and
`cmd/klokast-node/main.go`.
