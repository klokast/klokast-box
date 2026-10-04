# Regular Platform Updates

Routine operating-system and package updates are autonomous maintenance. They do not require human approval for each update.

This applies to `dom0` and Platform VMs such as `<box>-router`, `<box>-bak`, `<box>-dmz`, `<box>-iot`, and `<box>-ops`.

It does **not** define admission of a new Klokast Platform release; that is governed by `platform-lifecycle.md`.

## Principles

- **Scheduled:** updates run automatically from periodic jobs (for example cron/OpenRC jobs) under approved Platform automation.
- **Trusted channels:** use only configured authenticated upstream repositories. Changing repository/channel trust is a separate policy change.
- **Declared packages:** updates may advance installed packages, but must not silently expand the machine's intended package set.
- **Prefer replacement:** for VMs, prefer building/updating a replacement generation over mutating the running generation in place.
- **A/B when practical:** keep the current generation available while preparing the candidate; switch only after the candidate is ready.
- **Test before acceptance:** verify boot, required services, networking, storage, and role-specific health before declaring the update successful.
- **Rollback automatically:** if verification fails, return to the last known-good generation when possible.
- **Keep state separate:** persistent data and identity must not depend on the disposable OS generation. Copy or migrate only explicitly declared persistent state.
- **Preserve recovery:** do not destroy the previous known-good generation until the new generation has been accepted and a recovery path exists.
- **Limit blast radius:** update one redundant instance, box, or failure domain at a time when simultaneous failure would threaten Platform availability or administration.
- **Protect control paths:** infrastructure updates, especially `<box>-router`, `<box>-ops`, and `dom0`, must preserve a usable management/recovery path throughout the operation.
- **`dom0` is special:** prepare its updated diskless Alpine boot state, reboot into it, verify the box and guests, and retain a bootable rollback path. Avoid unnecessary mutable state on `dom0`.
- **Record outcome:** retain concise operational status: what was updated, from/to versions or generation, success/failure, and rollback if any.

## Failure rule

An update that cannot be verified is not accepted.

Failure should leave the machine on, or recoverable to, the last known-good state rather than forcing forward progress.
