# Music

Local music playback for each box through a Raspberry Pi USB-DAC streamer.

## Model

- `dmz`: torrent/VPN download edge.
- `bak`: rootless Podman music pod with volumes, MPD, Snapserver, myMPD,
  private UI ingress, and upload ingress.
- Raspberry Pi: low-trust LAN streamer only. It runs Snapclient and ALSA.
- Playback control is access-policy selected. Overlay-only boxes use
  `https://<box>-music.<tailnet>`; AP-local boxes should use the local ingress
  path instead. Upload remains `https://<box>-music-upload.<tailnet>`/SSH.

Enable the app in the private platform-resource registry:

```yaml
apps:
  music:
    enabled: true
    placement:
      boxes:
        - boxa
    devices:
      local-audio-endpoint:
        boxa:
          mac: b8:27:eb:00:00:00
          ipv4_address: 192.168.150.60
          hostname: boxa-streamer
    resources: {}
```

Apply platform resources from the controller as `smith`, then install:

```sh
ansible/bin/platform-image-build --app music --box boxa build
ansible/bin/platform-image-build --app music --box boxa load
apps/music/bin/musicctl install \
  --box boxa \
  --resources-registry ~/private/klokast/platform-resources.yml \
  --bootstrap-user pi
```

From a MacBook, import local files through the upload ingress:

```sh
klokast-dev/bin/kk music upload --from ~/Documents/music --to boxb
```

Use `--soundcard` when the USB DAC is not the default SMSL USB DAC. The value
should be a stable name from `aplay -L`, for example `hw:CARD=DAC,DEV=0`.
`musicctl install` and `musicctl backend-install` also run the same build/load
steps before deploying the backend pod.

## Notes

- Put the Pi on the box/router-controlled IoT port, not the residential gateway.
- After Pi install, LAN SSH is intentionally blocked; manage it over Tailscale.
- `bak` pulls completed downloads from `dmz`; `dmz` must not push into `bak`.
- Snapserver 0.34 stream config is `[tcp-streaming]`/1704; control is
  `[tcp-control]`/1705. Do not use deprecated `[tcp]` for the client stream.
- Default for the `boxb` SMSL DAC: `--soundcard hw:CARD=AUDIO,DEV=0`.
- The music ingress is box-scoped (`<box>-music`), not global `music`.
- Music files live in the `klokast-music-library` Podman volume on
  `<box>-bak`, not directly in the VM filesystem.
- Family uploads use `<box>-music-upload` over the overlay as user `music`.
- Operators can power off the Raspberry Pi with
  `klokast-dev/bin/kk poweroff <box>-streamer`.

## Verify

```sh
apps/music/bin/musicctl verify \
  --box boxa \
  --resources-registry ~/private/klokast/platform-resources.yml
```

Open the compiled playback-control surface from an allowed client and play
music from the selected local box.

## Remove

Preview the exact removal scope from the active controller:

```sh
ansible/bin/platform-app remove music --dry-run
```

The normal remove operation preserves the logical `library` dataset. This
dataset contains the `klokast-music-library` and
`klokast-music-playlists` volumes. The operation hashes and counts their
contents before and after cleanup and fails if they change.

After review, run:

```sh
ansible/bin/platform-app remove music --yes
```

The operation removes the fixed pod and containers, reconstructable MPD,
myMPD, runtime, and Tailscale state volumes, app configuration, and the app
image. It removes only exact offline Music and streamer Tailnet identities
through the guarded device-lifecycle wrapper. It disables Music in the legacy
registry, applies the app resource-cleanup scope, and writes a redacted audit
record to `~/private/klokast/app-lifecycle-audit.jsonl`.

`destroy music --wipe-data --yes` also removes the two declared data volumes.
Do not use destroy when the private Instance Specification keeps the Music
`library` data with `retention: preserve`.
