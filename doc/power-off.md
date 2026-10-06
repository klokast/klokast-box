Before intentionally powering off every box, check standby readiness and record which controller is active. See [controller recovery](platform-deploy.md#controller-recovery).

```sh
ansible/bin/ops-controller-ha status
```

Durable public automation lives in Git. Each controller keeps its own
provider/API credentials. Do not copy these credentials to a cloud runner or
a general off-platform backup. After power returns, start one box and wait
for its dom0 and `<box>-ops` Tailscale identities. Then use:

```sh
ansible/bin/platform-check-remote --controller auto --box <box> --target dom0
```

If the previous active controller is unavailable, fence it first. Commit, push, and pull the new Instance controller placement, then promote the standby with `ops-controller-ha promote --old-active-fenced`.
