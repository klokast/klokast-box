# Music App Instructions

This app provides local music playback through a low-trust Raspberry Pi audio
endpoint. Keep app-specific automation under `apps/music/`.

## Architecture

- Placement: every enabled box has an independent local music stack.
- Runtime host: rootless Podman pod on `<box>-bak`.
- Streamer endpoint: Raspberry Pi on the router-controlled IoT segment, USB
  DAC attached to the Pi. Its Tailnet machine name is `<box>-streamer`.
- User UI: access-policy selected. Overlay-only boxes use private overlay
  ingress at `https://<box>-music.<tailnet>`; AP-local boxes use local ingress
  to myMPD inside the local backend pod.
- Upload: private overlay SSH at `<box>-music-upload` as user `music`.
- Playback path: MPD writes PCM to a shared pod volume FIFO; Snapserver streams
  to Snapclient on the Pi; Snapclient outputs to ALSA/USB DAC.

The Pi is not a media server. Do not install Jellyfin, Radarr/Sonarr,
qBittorrent, Docker, or media indexing on the Pi.

## Required Runtime

Backend VM, rootless Podman under `neo`:

- `klokast-music` pod.
- Containers: `music-snapserver`, `music-mpd`, `music-mympd`,
  `music-ui-ingress`, `music-upload-ingress`.
- Volumes: `klokast-music-library`, `klokast-music-playlists`, MPD/myMPD
  state, runtime FIFO, and Tailscale state volumes.
- OCI image: build on the active ops controller with
  `ansible/bin/platform-image-build`, then load onto `<box>-bak`. Do not run
  `podman build` on the backend VM during service deployment.

Raspberry Pi streamer, systemd services:

- `tailscaled`
- `klokast-snapclient`
- `nftables`

Network access is declared in `apps/music/platform-resources.yml` and applied
by the platform-owned `ansible/bin/platform-resources` workflow. App roles must
not mutate router, VM firewall, Tailnet policy, or dom0 state directly.

## Automation Entry Point

Use `apps/music/bin/musicctl` from the controller. Pass box names, not VM
hostnames:

```sh
apps/music/bin/musicctl install \
  --box k001 \
  --resources-registry ~/private/klokast/platform-resources.yml \
  --bootstrap-user pi
```

The private registry must enable `music`, select `placement.boxes`, and provide
the Pi MAC address under `devices.local-audio-endpoint.<box>.mac`.

For Mac-side library upload, use `klokast-dev/bin/kk music upload`; it streams
to the active `<box>-music-upload` ingress, never to the `bak` VM SSH identity.

Use `ansible/bin/platform-app remove music --dry-run` before removal. Normal
remove must hash and preserve `klokast-music-library` and
`klokast-music-playlists`. It can delete only the fixed reconstructable Music
resources and exact offline Tailnet identities. Data deletion requires the
separate `destroy music --wipe-data --yes` command.
