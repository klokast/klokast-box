Write below the difficulties encountered during work.
Include context to allow an AI agent to later solve the issues.
Format of the first line: `# yyyy-mm-dd - title`

# 2026-08-09 - validate dom0 dependencies against the boot repository

The first k001 dom0 policy canary showed that the live Alpine 3.23 repository
keeps `libcurl` as a dependency of `xen`. The earlier disposable dependency
check did not detect this relationship. The steady-state policy now keeps
`libcurl` out of `/etc/apk/world` but permits APK to install it as a transitive
runtime dependency.

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

# 2026-08-09 - infra-agent host lacks Ansible CLI

The `vultr-ops` infra-agent checkout has Python and PyYAML but does not have
`ansible-playbook`. Repository YAML and Python tests can run locally, but
Ansible syntax checks must run from the active controller after the reviewed
commit is pushed. Do not install Platform controller tooling or private state
on the infra-agent host as a workaround.

# 2026-08-09 - k001 ops controller cannot reliably reach GitHub

During the exact APK-world rollout, `k001-ops` repeatedly failed to fetch
`https://github.com/klokast/klokast-box.git`; one controller convergence ended
after 135 seconds with curl error 28. Its clean checkout had to be fast-forwarded
from an exact Git bundle created on active controller `k002-ops`. The package
policy had already converged successfully, but the later public-checkout rehome
task could not finish, and the ops verification upstream probe is expected to
fail for the same reason. Investigate `k001-ops` DNS, outbound routing, and
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
