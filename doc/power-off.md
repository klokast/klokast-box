Before intentionally powering off every box, run a final controller HA sync and record which controller is active:

```sh
ansible/bin/ops-controller-ha status
ansible/bin/ops-controller-ha sync --from boxb --to boxa
```

Durable public automation lives in Git. Provider/API authority remains inside the active Platform controller and is not copied to the cloud runner or a general off-platform backup. After power returns, start one box, wait for its dom0 and `<box>-ops` Tailscale identities, then use:

```sh
ansible/bin/platform-check-remote --controller auto --box <box> --target dom0
```

If the previous active controller is unavailable, manually fence it, promote the standby with `ops-controller-ha promote --old-active-fenced`, and reseed provider/API authority from the MacBook-side wrappers before running provider mutations.
