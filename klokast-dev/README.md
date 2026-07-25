- The `klokast-dev` directory contains code and instructions to setup the developer machine.
- Manual instructions are in `klokast-dev/runbooks`.
- `bin/kk music upload --from DIR --to <box>` imports local music through
  `<box>-music-upload` and triggers MPD indexing.
- `bin/kk poweroff <box>-streamer` powers off the local Raspberry Pi streamer.
- `bin/install-tailscale-oauth` reseeds root-only Tailscale OAuth env files
  onto a promoted ops controller.
- From Mac/client machines, use OpenSSH (`ssh`, `rsync -e ssh`) for Tailnet
  SSH paths. Do not use `tailscale ssh` as an rsync remote shell; rsync passes
  OpenSSH flags such as `-l`. Wrappers may use `tailscale status` only to
  discover the active 100.x Tailnet IP.
