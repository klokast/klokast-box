# Router updater coordination

## Current status

- Date: 2026-09-25 UTC
- Phase: repository investigation and design plan complete
- Plan refinement: explicit upstream release and package detection added.
- Plan refinement: bootstrap compatibility requirements and acceptance added.
- Implementation: not started
- Live Platform changes: none
- Live Platform state inspection: none
- Private context: the required public journal procedure and the latest relevant
  private update case and forum entries were read. No private content is copied
  into this directory.
- Primary plan: `work-plan.md`

## Decisions

- Use replacement, not in-place package upgrades.
- Check upstream Alpine releases and the full router package set daily and on
  demand. Build only when an eligible change affects the accepted router.
- Report update availability, eligibility, and verification failures separately.
- Use the same router recipe for initial installation and replacement, with
  separate lifecycle and authority checks. See Bootstrap compatibility in the
  plan for rerun protection, first-install state, and shared asset rules.
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
- Router provisioning lacks accepted-generation protection. Controller
  provisioning also calls the router convergence playbook.
- Router and shared VM bootstrap use Alpine asset paths that are not versioned.
- The current Tailscale precheck can see the old canonical peer and mistake
  that result for candidate readiness.
- The Instance update schema rejects `router` targets.
- The shared Alpine update profile accepts only `bak`, `dmz`, and `iot`.
- `platform-map` does not report router OS generations or a router-state LV.
- The normal router role preserves an existing enabled overlay IPv6 repair but
  cannot reconstruct it on a fresh OS disk.

## Todo

1. Add the router release/profile contract and negative role-dispatch tests.
2. Implement the upstream check and decision report from the plan as part of
    milestone 1. Test package-only and dependency-only changes, unchanged inputs,
    branch delays, and missing or invalid metadata before connecting the schedule.
3. Parameterize the router rootfs builder and Alpine asset paths. Connect
   bootstrap and replacement to the same recipe, with separate lifecycle modes.
4. Add generic-template absence tests.
5. Split router configuration into render, activate, and verify phases.
6. Add a restricted candidate Xen definition and local management path.
7. Add the state LV contract and synthetic migration tests. Create the state LV
   directly on fresh installation and record the first accepted release.
8. Add supervised one-time migration from the current router OS disk.
9. Add the bounded dom0 cutover, rollback, and reboot recovery transaction.
10. Extend router verification and Platform Map generation reporting.
11. Run a supervised update and forced rollback on one box. Complete the plan's
    bootstrap compatibility tests, including safe provisioning reruns and shared
    VM builds after a router update.
12. Enable the signed schedule only after production acceptance evidence exists.

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
