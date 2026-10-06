- The `klokast-dev` directory contains code and instructions to setup the developer machine.
- Manual instructions are in `klokast-dev/runbooks`.
- `bin/kk doctor [--install]` checks MacBook prerequisites and can install
  supported missing tools, including Homebrew Bash. It is the only `kk` command.
  Follow [MacBook setup](runbooks/10-macbook-preparation.md) to set `PATH`.
- Application client commands are documented by [Music](../apps/music/README.md)
  and [Torrent](../apps/torrent/README.md).
- `bin/install-tailscale-oauth` reseeds root-only Tailscale OAuth env files
  onto a promoted ops controller.
- From Mac/client machines, use OpenSSH (`ssh`, `rsync -e ssh`) for Tailnet
  SSH paths. Do not use `tailscale ssh` as an rsync remote shell; rsync passes
  OpenSSH flags such as `-l`. Wrappers may use `tailscale status` only to
  discover the active 100.x Tailnet IP.
