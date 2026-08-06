Write below the difficulties encountered during work.
Include context to allow an AI agent to later solve the issues.
Include the date when the problem occured.

- 2026-08-06: The full `ansible/tests` suite has one unrelated stale Immich
  assertion. `test_immich_backend_role.py` still expects the removed
  `podman_cmd unshare cat /proc/self/uid_map` implementation, while the deploy
  template now uses `ensure_volume_owner`. Reconcile the test with the current
  rootless volume-ownership design before treating the full suite as green.
