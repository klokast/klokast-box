Write below the difficulties encountered during work.
Include context to allow an AI agent to later solve the issues.
Format of the first line: `# yyyy-mm-dd - title`

# 2026-09-06 - Signed box verification reads the protected preflight archive

The human completed both fresh preflights, entered the approval phrase, and
signed the verification intent with Touch ID. Engine `bd7d044` then refused
`verify-box-access` with `PermissionError` for the saved
`apply-preflights/<nonce>/effective-registry.yml`. At 11:19 UTC, all baseline
setting-source and desired-state hashes were unchanged. The controller
checkout still selected that engine and the private commit was unchanged.
No execution receipt was stored. This failure occurs after nonce consumption;
the failed nonce must remain used. A new attempt needs a new signed intent.

Preflight archives intentionally have a root-only parent and files. During
signed execution, `box_revalidate` creates a controller-readable runtime
copy and proves that its bytes and compiled values equal the approved inputs.
The verification branch then used `binding["effective_registry_path"]`,
which points to the root-only archive, instead of the revalidated
`current["effective_registry_path"]`. The helper runs as `smith` and cannot
traverse the archive. The earlier umask correction repaired runtime directory
modes but did not repair this path selection. Preflight-only live testing and
the earlier unit tests did not cover the handoff during signed execution.

The correction passes the revalidated runtime path to the verification helper.
Archive permissions, exact byte and hash checks, signature verification,
nonce handling, and network behavior remain unchanged. Adoption, rollback,
and restoration already stage their protected inputs before invoking the
controller helper. Their live rollback and re-adoption tests remain deferred.

The new regression test executes the verification branch with real saved
files, runtime staging, exact saved-byte comparison, nonce consumption, and
receipt storage under `umask 077`. External Plan/build validation, signature
verification, ownership changes, and the router command are test doubles.
It failed against the old path and passes with the correction. It covers a
verified receipt, a helper failure, refusal of altered saved bytes, cleanup,
unchanged archive modes, and nonce replay refusal after success or failure.
All 500 Python tests pass. The corrected engine still needs its sealed build,
matching installed tools, signed promotion, and live acceptance.

The MacBook printed a missing `ssh-askpass` notice, but then completed the
signature. The controller accepted that signature before this path failure.
The notice was not the cause of this refusal. Preserve the old evidence and
use the [current work queue](upstream-instance-target-architecture.md#current-work-queue)
to resume with the corrected engine. Do not widen archive permissions or
change network settings to force success.

# 2026-09-06 - First-box approval cancelled before Touch ID

The MacBook preparation batch produced fresh valid evidence. Both controller
preflights passed, and the second displayed the exact first-box
`verify_instance_authority` intent. The MacBook helper then printed
`approval cancelled` at its text confirmation prompt. This branch means that
the line read was not exactly `approve platform apply`. The transcript does
not show which line was read or whether cancellation was intentional.

At 10:54 UTC, the controller had no execution receipt for either prepared
intent. All saved setting-source and desired-state hashes remained unchanged,
and the public and private checkout commits still matched the acceptance
baseline. The fresh-evidence correction passed the real MacBook checks;
this cancellation occurred before the Touch ID signer was invoked.

A local pseudo-terminal simulation used the unchanged MacBook helper and
Python launcher with stub SSH and signing commands. A queued blank line
reproduced immediate cancellation. With no queued line, the helper waited
for the exact phrase and then reached the test signer. This is a possible
input cause, not proof of what occurred on the MacBook. Do not supply the
approval phrase through automation or treat cancellation as authorization.

The revised Python command clears queued terminal input after `--check`
with `termios.tcflush(sys.stdin.fileno(), termios.TCIFLUSH)`, then starts
`--prove-replay-refusal`. A second local pseudo-terminal simulation confirmed
that this discards the queued blank line and waits for a new typed phrase.
SSH and signing remained stubs in these simulations. The revised command
passed on the MacBook at 11:17 UTC: the human entered the phrase and Touch ID
produced a signature. This does not establish what caused the earlier input
cancellation. The command does not change the controller, the signing helper,
or any approval check. For an unintended cancellation,
the human must start a new approval and enter the exact phrase at its prompt.
The
[current work queue](upstream-instance-target-architecture.md#current-work-queue)
owns the remaining signed verification and replay gates.

# 2026-09-06 - Delayed approval used expired first-box evidence

The MacBook `apply-platform-intent --check` twice refused the saved Plan with
`Plan v3 inputs changed during exact revalidation`. A controller-side rerun
of the same sealed planner exposed diagnostic `fetched-at.stale`: the source
receipt was more than 30 minutes old. The receipt was created at 08:53:39 UTC,
and the observation at 08:55:21 UTC. At diagnosis, 10:21:15 UTC, both exceeded
their 30-minute limits. The earlier controller preflight had passed while
they were fresh. All baseline setting-source and desired-state hashes, the
public engine commit, and the private commit were unchanged. No signing or
execution occurred in these failed checks.

The handoff prepared evidence before the human was ready to sign, then gave
the human fixed paths that could expire during the wait. Prepare evidence
when the human starts the MacBook workflow. The controller preparation batch
uses the existing observation, source synchronization, recovery, toolchain,
and Plan commands. It checks the fixed commits, baseline hashes, and exact
verification-only groups, retains each attempt separately, and returns its
new approval arguments. The MacBook then runs `--check` and
`--prove-replay-refusal` with that same set. Source, observation, intent,
signature, and replay checks remain unchanged. No engine rebuild or
promotion is needed for an evidence refresh. See the
[first-box runbook](../klokast-dev/runbooks/46-box-connectivity-authority.md#3-create-plan-v3).

The first batch trial rejected the refreshed observation reference in the
read-only substrate action. The batch now checks that reference against the
new observation. It requires all other action fields to remain unchanged.
This comparison applies only between separate evidence attempts; root Apply
still revalidates each stored Plan exactly.

The corrected batch produced a valid deployable Plan, and the controller
preflight passed at 10:32 UTC under `umask 077`. The baseline hashes remained
unchanged. This confirms that fresh evidence resolves the unsigned refusal;
signed verification and replay refusal remain the live acceptance gates.

The root revalidation error does not distinguish expired evidence from a
content mismatch. A future error-reporting correction should expose the
bounded diagnostic code without private input content. This does not block
acceptance with fresh evidence. The
[current work queue](upstream-instance-target-architecture.md#current-work-queue)
owns the remaining acceptance gates.

# 2026-09-05 - Box preflight work directory loses group access under umask 077

The first-box forward-verification attempt stopped in the controller
`platform-apply preflight` used by the MacBook check workflow. The error was
`ksa-apply: installed platform-resources failed to compile the old registry`.
It occurred before router verification or creation of an approval intent.
No signed verification or live replay test ran. The active source record,
private desired-state files, legacy files, and both checkout commits remained
unchanged. The first-box milestone remains `implemented`.

Before this refusal, wrapper convergence and all ten Controller Toolchain v3
component checks passed against engine `d2c7c39`. Source synchronization,
activation validation, source recovery, and a fresh deployable Plan v3 also
passed. The Plan selected first-box and Tailnet verification and retained
the controller box's old registry. All 497 Python tests and the controller
Ansible syntax check passed. The existing sealed build recorded successful
Go tests and build output.

The controller preflight ran with `umask 077` to protect private evidence.
`ksa-apply.new_box_work` requests mode `0750` for its per-request directory,
but `Path.mkdir` applies the inherited umask. The function then changes
ownership to `root:smith` without setting the final directory mode. A
controller-side metadata-only reproduction confirmed mode `0700` under
that umask. This prevents the `smith` compiler from traversing the directory
even though staged registry files have mode `0440` and group `smith`.
The installed compiler successfully compiled the original controller-owned
registry in a separate read-only diagnostic. The failed helper's stderr was
not forwarded, and its temporary work directory was removed by the existing
cleanup path.

The correction sets the final request-directory mode to `0750` after
ownership changes. It grants group traversal without group write access or
access for other users. A regression test uses the real directory-creation
and registry-staging functions under umasks `022`, `077`, and `777`; it
checks directory modes, ownership requests, unchanged rollback-file mode
`0600`, and staged-file mode `0440`. Only ownership changes are mocked for
the unprivileged test runner. The test failed against the old code and
passes with the correction. Live account-boundary verification passed in
the resumed preflight under `umask 077` on 2026-09-06. The existing Ansible
wrapper installation carries the correction.

Review found the same missing final mode in all three Apply execution-receipt
directory writers. They now set mode `0750` after ownership changes so the
controller can read receipts under `umask 077`. A second regression test
reproduced the failure in each writer and checks directory mode `0750`, file
mode `0440`, ownership requests, and receipt content. Signature, nonce,
receipt-hash, rollback, and network behavior are unchanged.

The human authorized repair and resumption after the stopped attempt. Engine
`bd7d044678c9aa872c62a5d2b4f5f59219e8723f` passed the sealed Go build and tests,
all 499 Python tests, and the controller Ansible syntax check. All ten
installed controller components matched that engine. The human completed its
signed promotion and activation on 2026-09-06. The resumed preflight passed
with unchanged input hashes; DERP transport was informational. Keep that
engine fixed during the new attempt. Do not relax the caller's umask or
change network settings. Preserve the controller-private failure log,
baseline hashes, mode diagnostic, and Plan. Compiler error reporting still
needs a safe way to show the failed phase without private configuration.
The [current work queue](upstream-instance-target-architecture.md#current-work-queue)
owns resumption.

# 2026-09-05 - DERP must not block one-box source adoption or rollback

The one-router workflow used `tailscale ping --c 1` with its default
direct-only completion condition, then rejected any DERP reply. This made
working relayed management access an Apply or rollback failure. The human
approved accepting either transport for this workflow. It now uses a bounded
relay-capable probe after authenticated router configuration verification.
Successful DERP checks produce a fixed informational notice in Ansible output,
the human approval terminal, and the controller audit log. Notices do not
change signed JSON, source records, or authorization checks.

Live signed rollback and re-adoption remain unverified and deferred. The
[current work queue](upstream-instance-target-architecture.md#current-work-queue)
owns the remaining development gates. The separate direct-IPv6 repair is
deferred and retains its strict success checks. Its discovery false refusal
below is not fixed by this change.

# 2026-09-05 - Overlay prerequisite rejects successful direct-path discovery

Read-only diagnosis reproduced the repair refusal in the actual router
playbook. Its `tailscale ping --c 3` command returned one DERP reply followed
by the required direct IPv6 UDP `41641` reply, with exit code 0 in about
1.5 seconds. The next assertion rejected the complete output because it
contained `via DERP`. An immediate repeat against the same fully qualified
peer name returned only the direct endpoint. A separate ten-packet sample
after discovery used that same direct endpoint for every reply. This is a false refusal of a
successful discovery sequence, not proof that the direct path is blocked or
that the Huawei pinhole needs another change.

The installed Tailscale 1.90.9 help and
[versioned client source](https://github.com/tailscale/tailscale/blob/v1.90.9/cmd/tailscale/cli/ping.go)
describe initial relay use during discovery and stopping when a direct path
is found. The repair currently treats discovery and final verification as
one test. Its helper also truncates the failure output to the play recap,
which hides the successful direct reply and the exact failed assertion.

Proposed correction: separate bounded discovery from a fresh direct-only
verification sample. Keep the final expected peer, global IPv6 endpoint,
UDP port, no-relay requirement, and signed evidence integrity checks. Do not
accept discovery success as the final proof, discard unexpected lines, add
unbounded retries, or reset Tailscale to make the check pass. Keep discovery
diagnostics distinct from the signed verification evidence. Review the
router prerequisite, ops post-apply verification, root evidence parser, and
their tests together. Test initial relay-to-direct discovery, relay-only
failure, return to relay during verification, changed endpoints, and unknown
output. Report the failed phase without exposing private configuration.

No implementation or network configuration changed during this diagnosis.
The result does not prove long-term path stability or complete the signed
repair, ops-to-peer verification, or box rollback acceptance.

# 2026-09-05 - Overlay signed revalidation includes changing runtime measurements

Review after the repeated collision-check failure found a separate blocker.
`overlay_revalidate` compares fresh router, ops, and Huawei evidence byte for
byte with the approved preimages. Saved live evidence contains finite IPv6
address lifetimes, firewall packet counters, and measured ping latency.
These measurements can change while configuration and the direct endpoint
remain unchanged. The earlier unsigned preflight did not test that interval.

The human approved a narrow runtime comparison. The root Apply program now
checks the archived bytes against their signed hashes, then compares fresh
configuration under the closed rules in
[ops-only overlay repair](upstream-instance-target-architecture.md#112-ops-only-overlay-ipv6-repair).
Tests cover a simulated review delay, increased counters, changed latency,
configuration drift, altered archives, and unknown evidence fields. Audit
records identify the comparison version and fresh evidence hashes without
copying configuration content.

Live acceptance is still pending. Promote the reviewed engine, install its
exact tools, and repeat preparation and signed revalidation before claiming
this blocker is resolved on the controller. Lifetime extensions and firewall
counter resets deliberately require fresh approval. If these occur during
normal review, record the evidence and review the rules; do not widen the
exceptions or remove the direct-path requirement as a workaround.

# 2026-09-05 - Repeated IPv6 preflight mistook its failed probe for a collision

The first successful repair preflight probed the proposed WAN link-local
next hop. With no reply, Linux retained a `FAILED` neighbour-cache entry
without a hardware address. The next preflight treated any matching cache
entry as an occupied address and refused before approval.

The check now permits only bare `FAILED` or `INCOMPLETE` unresolved entries.
It still refuses resolved neighbours, unknown entry shapes, and ping replies.
It checks the cache again after probing to detect a neighbour that resolves
but blocks echo replies. Address and neighbour inspection errors stop the
check. The signed operation must still pass kernel duplicate address
detection after adding the address. Do not flush the neighbour cache as a
workaround. Tests cover consecutive failed probes and real collisions.

# 2026-09-05 - Ops IPv6 helper must use the controller local connection

After the router snapshot handoff was corrected, the repair preflight failed
because the ops helper tried to SSH from the active controller to itself.
The same read-only snapshot succeeded with Ansible's local connection.
The helper now requires the local hostname to match the selected ops host
and sets the local connection, localhost address, and controller Python path
explicitly, as controller convergence does. Tests must reject a different
local host before invoking Ansible. This preflight failure did not change
network configuration.

# 2026-09-05 - Overlay snapshot output needs a separate writable directory

The signed repair preflight passed Freebox inspection but failed when Ansible
saved the router snapshot. Apply created its runtime directory as root with
mode `0750`, while the snapshot helper ran as `smith`. The helper could read
the directory but could not create its output there. A controller-local
snapshot in a private `smith` directory succeeded and confirmed the cause.

Apply now gives the helper a separate temporary output directory. It then
removes helper access and copies bounded regular files into root-owned,
read-only evidence before hashing them. Symlinks and hard links are refused.
Keep a regression test for the output account boundary, as well as the
existing tests for root-only rollback input handoff. The failed preflight
did not change the Freebox delegation or network configuration.

# 2026-08-31 - Freebox GET can omit read-only IPv6 metadata

The first overlay-repair preflight authenticated to the Freebox but stopped
because the IPv6 configuration result differed from the closed schema. The
official API defines `ipv6ll` as read-only, and its GET example omits that
field. The broker now accepts `ipv6ll` as optional and validates it when it is
present. A second preflight found `ipv6_firewall` and
`ipv6_prefix_firewall`. The installed Freebox user interface defines both
fields as Boolean checkboxes. The broker now accepts only the complete pair,
validates both values as Booleans, binds them into rollback evidence, and
refuses a configure or restore operation if either value changes. It still
reports only missing and unknown field names, never response values or
credentials. Do not make the delegation array, IPv6 enabled state, prefixes,
or next hops optional.

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

# 2026-08-25 - Historical one-box direct-path and rollback input failures

The direct-only acceptance rule in this incident was replaced on 2026-09-05.
The current box source workflow accepts direct or DERP transport. Keep the
diagnosis and recovery evidence below; direct-path repair is deferred under
the [current work queue](upstream-instance-target-architecture.md#current-work-queue).

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
condition becomes frequent, endpoint and NAT state can be inspected in the
separate deferred repair work.

The condition occurred again during the unsigned adoption preflight. Both
peers reported working UDP and stable NAT mappings. The checked live router
verification also proved that the managed controller-side firewall allowed
the fixed Tailscale source port, STUN, established return traffic, and WAN
masquerade. Tailscale endpoint history then showed that the selected router
still used an obsolete public UDP mapping for the controller. The unprivileged
`tailscale debug restun` command failed closed because endpoint refresh needs
root. The human ran the command on both peers, and the direct path recovered
immediately. Any later endpoint-refresh diagnostic must be narrow, checked,
and audited, with only the active controller and one selected router in
scope. It must not grant general root access or change Tailnet policy. Do not
refresh endpoints to force a passing source acceptance result.

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
