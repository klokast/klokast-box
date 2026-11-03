# GitHub accounts and Apps

Account inventory confirmed by the human administrator on 2026-11-03.
This inventory does not define an authorization model.

## Accounts and organizations

| Account | Role | Registered and installed GitHub Apps |
| --- | --- | --- |
| `xiaoju` | Human GitHub account; also the MacBook login name | None |
| `klokast` | Organization that owns `klokast/klokast-box` | `klokast-static-broker`, for the static website |
| `jfm-home` | Instance organization | `klokast-update-instance-jfm-home`, for the Instance update workflow |

The two Apps are each registered and installed in their listed organization.
App registration and App installation are separate settings. Check both before
creating another App. `klokast-mac` is a separate GitHub CLI account, not
the human `xiaoju` account.

## Public source deploy keys

`klokast/klokast-box` has two write-enabled deploy keys:

| GitHub title | Runner | SHA-256 fingerprint |
| --- | --- | --- |
| `k002-ops-airunner` | `k002-air` | `FZOb2JhyAGe695LR2lVZNqzLdQFwWO6UhvFWAjPqVu0` |
| `Klokast k001-air public source` | `k001-air` | `s+lHWe59SOgEeWqi3nwgrDnp3LbvUfKKKycqYBOnSE4` |

Both runners can push with their existing keys. No new App is needed for
these pushes. Each private key stays on its runner.

Automatic runner key management is not configured. Its separate App setup is
in [Runner Git provisioning](../klokast-dev/runbooks/26-air-github-app.md).
Do not repurpose the existing website or Instance App for that setup. The
[runner credential model](architecture.md#runner-git-credentials) owns the
authority and credential rules.
