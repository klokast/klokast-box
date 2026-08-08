Write below the difficulties encountered during work.
Include context to allow an AI agent to later solve the issues.
Include the date when the problem occured.

- 2026-08-06: The full `ansible/tests` suite has one unrelated stale Immich
  assertion. `test_immich_backend_role.py` still expects the removed
  `podman_cmd unshare cat /proc/self/uid_map` implementation, while the deploy
  template now uses `ensure_volume_owner`. Reconcile the test with the current
  rootless volume-ownership design before treating the full suite as green.

- 2026-08-08: `provision-box --box k001 --from 22 --to 22` failed before
  contacting dom0 because `/tmp/klokast-dom0-known_hosts` was mode `0600` and
  owned by another controller account. The play succeeded when
  `dom0_known_hosts_file` was overridden to a smith-owned path under
  `~/.cache/klokast`. Make the phase wrapper use a per-user, per-run known-hosts
  path, as `platform-check` already does, instead of a shared `/tmp` file.
