# Router updater coordination

## Current status

- Date: 2026-09-25 UTC
- Phase: repository investigation and design plan complete
- Implementation: not started
- Live Platform changes: none
- Live Platform state inspection: none
- Private context: the required public journal procedure and the latest relevant
  private update case and forum entries were read. No private content is copied
  into this directory.
- Primary plan: `work-plan.md`

## Decisions

- Use replacement, not in-place package upgrades.
- Create a generic, versioned router template.
- Build the template locally on each dom0.
- Keep identity and lease data on one small per-box router-state LV.
- Do not enroll a candidate in Tailscale.
- Use a restricted dom0 bootstrap path for candidate tests.
- Use a separate router release profile and executor.
- Reuse only the existing update policy, schedule, lock, and authority
  primitives that have the correct scope.
- Keep one previous accepted OS generation.
- Refuse non-reconstructable router state before cutover.

## Open issues

### Required before implementation

- Confirm the exact dhcpcd DUID and lease paths for the installed Alpine
  package.
- Confirm the exact dnsmasq lease path, then set it explicitly in the managed
  configuration.
- Select the state LV size, filesystem label, mount point, and filesystem.
- Define the authenticated Alpine release input. The current ISO and checksum
  download from one HTTP origin is not sufficient for unattended use.
- Define the candidate-only management address and bridge behavior without
  changing production topology.
- Decide the exact Tailscale stable identity field that proves identity
  retention across the switch.
- Define how the existing signed ops-only IPv6 repair becomes reconstructable.
  Until then, its enabled marker must block automatic replacement.
- Confirm that all generated router firewall includes can be rendered and
  staged before activation from the current Instance and compiler inputs.
- Define the minimum dom0-local WAN and DNS probes that do not depend on the
  controller remaining reachable.

### Known repository gaps

- The current router rootfs builder writes to the production LV.
- The current router rootfs contains per-box hostname, network, and bootstrap
  key data.
- Production boot artifacts and the Xen definition have fixed names.
- The current Tailscale precheck can see the old canonical peer and mistake
  that result for candidate readiness.
- The Instance update schema rejects `router` targets.
- The shared Alpine update profile accepts only `bak`, `dmz`, and `iot`.
- `platform-map` does not report router OS generations or a router-state LV.
- The normal router role preserves an existing enabled overlay IPv6 repair but
  cannot reconstruct it on a fresh OS disk.

## Todo

1. Add the router release/profile contract and negative role-dispatch tests.
2. Parameterize the router rootfs builder and version all output paths.
3. Add generic-template absence tests.
4. Split router configuration into render, activate, and verify phases.
5. Add a restricted candidate Xen definition and local management path.
6. Add the state LV contract and synthetic migration tests.
7. Add supervised one-time migration from the current router OS disk.
8. Add the bounded dom0 cutover, rollback, and reboot recovery transaction.
9. Extend router verification and Platform Map generation reporting.
10. Run a supervised update and forced rollback on one box.
11. Enable the signed schedule only after production acceptance evidence exists.

## Coordination rules

- Before code changes, read the repository `AGENTS.md` inputs and
  `doc/git.md`.
- Before Platform operations, read `doc/operations-journal.md` and the latest
  relevant private case and forum entries on the active controller.
- Write a short update here when work starts, stops, changes scope, or finds a
  blocker.
- Record the files being changed so another agent can avoid overlap.
- Do not put secrets, host-specific private state, logs, signed intents, or live
  inventory in this directory.
- Do not use this file as execution authority. Use the installation lock,
  signed policy, active-controller guard, and protected operation records.
- Commit and push implementation changes in small verified milestones.

## Active work claims

None.

## Handoff template

Add a new section with these fields:

- UTC time:
- Agent:
- Status:
- Scope and files:
- Completed:
- Tests:
- Blockers:
- Next action:
- Commit:
