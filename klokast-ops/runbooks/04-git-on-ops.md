# Git On Ops

The current MVP configures Git for the `codex` user through Ansible. It does
not require GitHub CLI on `ops`.

Operator access to `ops` uses Tailscale SSH only. The SSH client on `ops` is
for outbound GitHub Git-over-SSH traffic, because GitHub does not use
Tailscale SSH.

The role `source-repositories` does this on the server:
- creates `/home/codex/.ssh`
- pins GitHub SSH host keys in `/home/codex/.ssh/known_hosts`
- writes `/home/codex/.ssh/config`
- generates `/home/codex/.ssh/github-klokast-codex` if missing
- configures the `codex` Git identity
- clones `git@github.com:klokast/klokast-box.git` into
  `/home/codex/src/klokast/klokast-box`

`klokast-ops` is currently nested inside `klokast-box`, so this checkout makes
the ops playbooks available at:

```text
/home/codex/src/klokast/klokast-box/klokast-ops/ansible
```

## First Run

On a new server, the playbook generates the deploy key and checks whether
GitHub accepts it. If the key is not registered yet, the playbook stops and
prints the public key.

In GitHub, register only the printed public key:
1. Open `klokast/klokast-box`.
2. Go to Settings > Deploy keys.
3. Add the printed public key.
4. Enable write access.
5. Re-run `klokast-ops/ansible/playbooks/00-ops-server.yml`.

Do not copy the private key out of `/home/codex/.ssh/github-klokast-codex`, and
do not store it in git.

## Checks

From the MacBook:

```bash
tailscale ssh codex@ops 'ssh -T git@github.com || true'
tailscale ssh codex@ops 'git -C ~/src/klokast/klokast-box status --short --branch'
tailscale ssh codex@ops 'test -f ~/src/klokast/klokast-box/klokast-ops/ansible/playbooks/00-ops-server.yml'
```

The `ssh -T` command should say that GitHub authenticated the key and does not
provide shell access.
