Write below the difficulties encountered during work.
Include context to allow an AI agent to later solve the issues.
Format of the first line: `# yyyy-mm-dd - title`

# 2026-08-31 - Freebox GET can omit read-only IPv6 metadata

The first overlay-repair preflight authenticated to the Freebox but stopped
because the IPv6 configuration result differed from the closed schema. The
official API defines `ipv6ll` as read-only, and its GET example omits that
field. The broker now accepts `ipv6ll` as optional and validates it when it is
present. A second preflight still found a schema difference. The broker now
reports only missing and unknown field names, never response values or
credentials, so the next preflight can identify the remaining difference.
Accept a new field only after its type and effect are understood. Do not make
the delegation array, IPv6 enabled state, prefixes, or next hops optional.

# 2026-08-31 - Huawei IPv6 pinhole can become stale after prefix changes

A changing residential IPv6 prefix is a plausible cause of a later direct-path
failure from `k002-router` to `k001-router`. The Huawei IPv6 virtual-host form
stores the complete `k001-router` global address. Its device selector appears
to fill that literal address; the form exposes no MAC, DHCPv6 DUID,
interface-ID, or prefix-independent host binding. A new rule with the current
`/128` produced a direct UDP `41641` endpoint. The prior address and endpoint
state were not recorded, so this result does not prove that prefix drift was
the sole cause of the earlier failure.

The diagnosis and safe manual recovery are in
`klokast-dev/runbooks/47-overlay-ipv6-direct-repair.md`. Do not broaden the
destination, disable the Huawei IPv6 firewall, or automate its administration
as a workaround. A future improvement needs authoritative documentation for
the exact Huawei firmware and a checked renumbering test. If no stable host
binding exists, design a narrow, audited, rollback-capable update boundary
before any automation is added.

# 2026-08-25 - one-box verification requires a direct Tailnet path

The first signed box-connectivity adoption did not reach Ansible because the
root Apply program passed a root-only rollback file to the `smith`-owned
compiler process. The adoption execution path was corrected to keep rollback
storage root-only and make exact, read-only copies in its short-lived execution
directory. A later authorized rollback found that its separate preflight path
still passed the root-only file directly. That path now uses the same staged
copy. Keep tests for each code path when a root action delegates a read-only
operation to `smith`.

During recovery inspection, one controller ping reached `k001-router` only
through the Hong Kong DERP relay. A later closed legacy verification
established a direct peer-to-peer path and passed all checks. If the relay-only
condition becomes frequent, investigate the endpoint and NAT state. Do not
weaken the direct-path requirement to complete the source migration.

The condition occurred again during the unsigned adoption preflight. Both
peers reported working UDP and stable NAT mappings. The checked live router
verification also proved that the managed controller-side firewall allowed
the fixed Tailscale source port, STUN, established return traffic, and WAN
masquerade. Tailscale endpoint history then showed that the selected router
still used an obsolete public UDP mapping for the controller. The unprivileged
`tailscale debug restun` command failed closed because endpoint refresh needs
root. The human ran the command on both peers, and the direct path recovered
immediately. Add a narrow, checked, audited endpoint-refresh diagnostic that
can operate on exactly the active controller and one selected router. It must
not grant general root access, change Tailnet policy, or weaken the direct-path
acceptance check.

# 2026-08-25 - sealed builder cleanup depends on one Alpine mirror

Sealed build operations `0fdb18748cee` and `447ed5802533` compiled and cleaned
up their temporary guests, but the enclosing playbook refused each build when
the configured HUST Alpine mirror returned an I/O error during mandatory dom0
package reconciliation. A controller-dispatched probe confirmed that HUST
refused the connection while the official release-pinned Alpine HTTPS endpoint
worked. The public policy now uses the official endpoint. If that endpoint
also becomes unreliable, design a reviewed failover that does not weaken the
exact package policy or accept stale package sources silently.

# 2026-08-25 - Platform map refresh is slow when one VM is unreachable

The first box-connectivity observation refresh took several minutes because
`k001-iot` did not answer Tailscale SSH. The refresh correctly recorded the
VM as unreachable and completed, and Plan v3 kept the unrelated finding out
of the one-router action. Add bounded per-host connection timeouts or reuse a
fresh prior non-target VM observation so one offline VM cannot delay a narrow
router source change.

A later dom0-only refresh and one-router check also took several minutes while
`k001-dom0` and `k001-router` remained reachable. Each Ansible task opened a
new connection across the household link. Investigate safe connection reuse
or a narrow, bounded remote verifier so read-only acceptance does not incur
the connection delay once for every task.

# 2026-08-25 - failed Apply preflights need a retention policy

The first Authority State v1 conversion attempt stopped after signature
verification because nonce evidence incorrectly required a Plan hash. The
corrected Apply program binds a conversion nonce to its Authority State hash.
The failed attempt left an immutable preflight directory and an empty,
consumed nonce file. Keep both as failure evidence. Define a root-owned
retention and archival policy before these evidence directories need cleanup.

# 2026-08-25 - Go tests run only in the sealed builder

The infra-agent can run the Python and shell checks, but it has no Go or
Ansible executable. The active controller has Ansible but also has no Go
executable. The canonical engine build runs the complete vendored Go tests in
its pinned builder image. Run Ansible syntax checks on the controller with the
repository-local configuration and role path. Do not install extra build tools
on the infra-agent or controller only to duplicate these checks.

# 2026-08-22 - GitHub App cannot remove its last selected repository

GitHub rejects removal when a repository is the last selected repository of
an App installation. Do not change the temporary private-instance App to
**All repositories**, and do not create an unrelated carrier repository. For
a single-repository organization, uninstall the organization App installation
but keep the App definition and credentials until the signed controller action
proves that the installation ID no longer exists. The action must accept only
an exact not-found result, require the dedicated App to have no other
installation, verify the read-only deploy key, and then delete the controller
credential.

# 2026-08-22 - Terraform static-test execution locus

The airunner and active controller do not have Terraform. Source changes can
run Terraform `fmt`, `validate`, and `test` only on the trusted MacBook. Add a
credential-free CI job for these static checks. Do not install Terraform on
the controller only to work around this missing check.

# 2026-08-22 - catalog-driven cloud provider provisioning

Keep `cloud-providers.json` as the reviewed public identity catalog. Later
work can add pricing and region discovery, account registration, API-key
custody, and catalog-driven Terraform workflows.

# 2026-08-22 - add Tor as a shared service

The Platform should support these standardized connection modes:

- Tailscale, for example, for inbound requests from family users;
- Cloudflare, for example, for inbound static website requests from external
  users;
- a local access point, for example, for OS package downloads or traffic with
  torrent peers;
- a local VPN client, for example, for an airunner;
- a LAN, for example, for a user who plays music;
- Tor, for example, for Bitcoin Core.

Define how each mode uses router zones and how an app requests the mode. The
design must make later connection modes easy to add.

# 2026-08-22 - migrate this `todo.md` to GitHub "issues" and feature requests

Or to another ticketing system, to industrialize the monitoring and resolution
of tickets.

# 2026-08-21 - MacBook bootstrap wrappers require a real macOS integration run

The infra-agent host cannot run the interactive private-instance wrappers
because they require macOS, Tailscale access from the trusted MacBook, and
Apple CryptoTokenKit. Deterministic tests cover the separate controller and
approved-engine pins. The active controller can verify the sealed build and
the installed root wrapper. The human must still rerun
`prepare-private-instance-bootstrap` on the trusted MacBook to verify the
complete terminal and Touch ID path after a wrapper change.
More generally, the end-to-end User Experience of the various Klokast processes
must be mapped, reviewed, and improved. For example, review the authentication
process on the trusted MacBook. Avoid unnecessary fingerprint scans, and make
the signed intent clear to the user.

# 2026-08-16 - make Touch ID signing prompts more explicit

During the procedure in
[Private Instance Bootstrap](../klokast-dev/runbooks/40-private-instance-bootstrap.md),
the current Touch ID prompt identifies `ctcardtoken` and does not show the
intent. The prompt should identify Klokast and make the signed intent clear.
The standard Touch ID prompt cannot be customized. One proposal is a signed,
one-shot Klokast Approval app. It would validate
the intent, display it, compute its digest, and sign the same in-memory bytes.
Apple’s native authentication API supports an application name and a clear
`localizedReason`. Apple documents this behavior
(https://developer.apple.com/documentation/localauthentication/lacontext/localizedreason).
The Security framework can also associate an LAContext with keychain
authentication. Apple documentation
(https://developer.apple.com/documentation/security/ksecuseauthenticationcontext).
This change will probably require a signer migration or a new signature format
because the current OpenSSH security-key format contains more flags and counter
fields.

# 2026-08-09 - NanoKVM Tailscale Serve is runbook-managed

The NanoKVM HTTPS listener presents a self-signed certificate with
the certificate Common Name `localhost`. Tailscale connectivity and the operator grant can both work
while a browser rejects `https://oob.<tailnet>.ts.net/`. The active `oob`
device now uses a persistent Tailscale Serve configuration on TCP 443 that
proxies to `https+insecure://localhost:443` and supplies a valid certificate.

No Platform automation currently converges or verifies this Serve state after
a NanoKVM firmware update, factory reset, or Tailscale state replacement. Add
a narrow controller-owned reconciliation and verification path. Do not grant
`tag:ops` or `tag:airunner` direct web access as a test workaround. The device
also reported a Tailscale CLI version newer than the running daemon. Do not
restart Tailscale synchronously over Tailscale SSH; use a console-safe or
detached restart workflow.

# 2026-08-23 - restore the app store routing document

The root agent instructions require `apps/STORE.md` before app work, but that
file does not exist. `apps/README.md` contains the current supported-app list
and routing rules. Create `apps/STORE.md` or change the root instruction to
name the existing authority. Do not keep two app catalogs with different
content.

# 2026-08-09 - validate dom0 dependencies against the boot repository

The first boxa dom0 policy canary showed that the live Alpine 3.23 repository
keeps `libcurl` as a dependency of `xen`. The earlier disposable dependency
check did not detect this relationship. The steady-state policy now keeps
`libcurl` out of `/etc/apk/world` but permits APK to install it as a transitive
runtime dependency. The reboot canary also showed that Alpine's diskless
initramfs adds `openssl` to the boot transaction for `modloop` signature
verification. The exact world must therefore include `openssl`. The same
canary also found that `lbu` resolves through the system path and is not
installed at `/sbin/lbu` on the live image.

Add a test that resolves the exact dom0 world against the same APK indexes and
package files that the diskless boot repository uses. Fail the test if a
forbidden installed package is in the resolved dependency closure.

# 2026-08-09 - move remaining guest image work out of dom0

The exact dom0 APK policy makes `curl`, `xorriso`, `sfdisk`, `kpartx`,
`e2fsprogs-extra`, `gzip`, and `util-linux` temporary RAM-only tools. However,
the router, Podman template, clone personalization, Debian image import, and
Klokast CLI builder bootstrap paths still execute some image work on dom0.

Move this work into a versioned short-lived builder VM. Resolve the bootstrap
dependency first: the current sealed builder template is itself first created
with dom0 partition and filesystem tools. A replacement must start from a
signed, controller-supplied builder template or another independently verified
artifact. Dom0 must then only create or clone LVs, attach them, copy approved
boot artifacts, render Xen configuration, and control guest runtime.

# 2026-08-09 - boxa ops controller cannot reliably reach GitHub

During the exact APK-world rollout, `boxa-ops` repeatedly failed to fetch
`https://github.com/klokast/klokast-box.git`; one controller convergence ended
after 135 seconds with curl error 28. Its clean checkout had to be fast-forwarded
from an exact Git bundle created on active controller `boxb-ops`. The package
policy had already converged successfully, but the later public-checkout rehome
task could not finish, and the ops verification upstream probe is expected to
fail for the same reason. Investigate `boxa-ops` DNS, outbound routing, and
firewall policy, and bound the rehome fetch. Do not weaken the requirement that
controller checkouts match public upstream history.

# 2026-08-07 - compiler must assign Tailscale ports for containers
As documented in `doc/platform-resource-control-plane.md:316` and referenced
from `apps/README.md:24`:
- Dedicated ports are needed only for containers running their own tailscaled
identity. Containers inheriting the Podman VM’s Tailscale identity need no additional port.
- Allocation is per independent Tailscale identity, not simply per application.
Two containers with separate identities need two unique ports.
Hence, the intended contract is:
1. Reserve a unique stable UDP port from 41644–41999.
2. Start that container’s tailscaled with --port=<reserved-port>.
3. Generate router policy permitting that UDP source port to WAN peers.
4. Permit destination UDP 3478 for STUN.
5. Verify direct connectivity.
However, this is currently documented but not implemented in the resource compiler.
The schema rejects the illustrative underlay declaration, and such app-owned identities
must remain disabled until compiler validation, allocation, firewall rendering, runtime
propagation, and verification are implemented together.
Also, the documentation currently models the port as explicitly declared:
```
underlay:
  compute: private-ingress-runtime
  udp_listen_port: 41644
```
It does not yet specify automatic compiler allocation. Architecturally, deterministic
compiler allocation and uniqueness validation would be preferable, allowing applications
to request an identity symbolically without choosing infrastructure port numbers themselves.
