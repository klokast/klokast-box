# Platform work queue

Record unresolved problems only. Git commit `186cfa9` contains the resolved
instance-transition difficulties and dated acceptance history.

## Verify signed-operation progress and replay reporting

The public helper now sends bounded progress to stderr and keeps stdout for the
final JSON result. It classifies only the exact nonce-reuse and expired-intent
controller messages as replay refusal. Repository tests pass. A human must now
run one signed, verification-only retired-state operation from the trusted
MacBook. Do not restore or delete retired inputs for this test. Keep the
ten-minute lifetime, validation order, signature checks, and nonce protection.

## Verify initial-bootstrap recovery

Run the complete private-instance bootstrap recovery procedure from the trusted
MacBook in an approved recovery exercise. Prove controller fencing, read-only
source reconstruction, credential reseeding, Touch ID approval, and cleanup.
Do not expose private instance state or credentials to an airunner.

## Replace the direct overlay IPv6 repair contract

The implemented repair depends on historical compatibility Plans and retired
YAML inputs. Do not restore those inputs to run it. Design an instance-only
Plan and signed executor before live use. Keep IPv4 and DERP available, bind all
Freebox and host preimages, and provide exact rollback. See
[Direct Overlay IPv6 Repair](../klokast-dev/runbooks/47-overlay-ipv6-direct-repair.md).

## Add retention for failed Apply preflights

Failed or expired approvals leave immutable preflight and consumed-nonce
evidence. Define a root-owned retention and archival policy before cleanup is
needed. Never remove evidence for an unresolved recovery result.

## Reduce Platform map latency

An unreachable VM can delay a map refresh because many tasks open separate
connections. Add bounded per-host connection timeouts or a narrow read-only
remote verifier. A cached observation must not hide current target drift or
enter desired-state authority.

## Keep sealed builds independent of one package mirror

The builder uses the official release-pinned Alpine HTTPS endpoint after a
prior mirror failed. Design reviewed mirror failover if needed. It must not
accept stale indexes or weaken the exact package policy.

## Add credential-free Terraform CI

The airunner and active controller do not have Terraform. Add CI for Terraform
format, validation, and tests without provider credentials. Do not install
Terraform on the controller for this purpose.

## Extend catalog-driven cloud provisioning

Keep `cloud-providers.json` as the reviewed public identity catalog. Future
work can add pricing and region discovery, account registration, API-key
custody, and catalog-driven Terraform workflows.

## Add Tor as a managed connection mode

Define Tor as a symbolic app resource. Platform-owned automation must map it to
router zones and network policy. Apps must not choose infrastructure rules or
grant their own access.

## Improve the trusted-Mac approval experience

Run the complete interactive wrappers after each relevant change. Review the
number of fingerprint prompts and make the exact signed intent clear. Any new
native approval application needs a separate signature-format and signer
migration design.

## Converge NanoKVM Tailscale Serve

Add a narrow controller-owned reconciliation and verification path for the
NanoKVM HTTPS Serve configuration. Do not grant controller or airunner tags
direct web access as a workaround. Restart Tailscale only through a console-safe
or detached recovery path.

## Restore the application routing document

The root instructions require `apps/STORE.md`, but `apps/README.md` currently
contains the catalog. Create one authoritative routing document or update the
instruction. Do not keep two divergent application catalogs.

## Validate dom0 dependencies against the boot repository

Resolve the exact dom0 world against the same APK indexes and packages used by
diskless boot. Fail if a forbidden package enters the dependency closure.

## Move guest image construction out of dom0

Move remaining router, Podman template, clone, Debian import, and builder
bootstrap image work into versioned short-lived builder VMs. Resolve the first
builder-template bootstrap boundary before removing the current tools.

## Diagnose standby-controller GitHub access

The standby controller has shown slow or failed GitHub fetches. Check DNS,
egress routing, and firewall policy, and bound checkout fetches. Do not weaken
the requirement that controller checkouts match public upstream history.

## Allocate Tailscale ports for container identities

Implement deterministic, unique UDP port allocation for containers with an
independent Tailscale identity. Render router policy, permit STUN, propagate the
port to runtime, and verify direct connectivity. Keep this feature disabled
until schema, compiler, firewall, runtime, and verification support land
together.

## Move work tracking to an issue system

Move this queue to GitHub Issues or another tracked system when that system is
available. Keep this file authoritative until the migration is complete.
