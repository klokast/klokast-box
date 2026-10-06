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
kk music upload --from ~/Documents/music --to boxb
```

Select the private Instance worktree as described in the
[`kk` interface](../../klokast-dev/README.md#application-commands-with-kk).
The client uses OpenSSH and rsync to reach the Music upload ingress. For direct
invocation of `apps/music/bin/music-client`, set
`KLOKAST_TAILNET_SUFFIX` to the deployment Tailnet DNS name for the UI URL and
streamer power-off target. Run these client commands from an authorized
MacBook or client machine. `musicctl` remains the controller deployment tool.

Run the client checks from the repository root:

```sh
python3 -m unittest discover -s apps/music/tests
```

The checks use stub tools and make no network calls.

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
  `kk music poweroff <box>-streamer`.

## Verify

```sh
apps/music/bin/musicctl verify \
  --box boxa \
  --resources-registry ~/private/klokast/platform-resources.yml
```

Open the compiled playback-control surface from an allowed client and play
music from the selected local box.

## Remove

Use the application-owned removal command from the active development
controller. It has no dry-run mode. Review the selected box, retained data,
and removal playbooks before execution.

The normal remove operation preserves the logical `library` dataset. This
dataset contains the `klokast-music-library` and
`klokast-music-playlists` volumes. The operation hashes and counts their
contents before and after cleanup and fails if they change.

After review, run:

```sh
apps/music/bin/musicctl remove \
  --box boxa \
  --resources-registry ~/private/klokast/platform-resources.yml \
  --yes
```

The operation removes the fixed pod and containers, reconstructable MPD,
myMPD, runtime, and Tailscale state volumes, app configuration, and the app
image. It removes only exact offline Music and streamer Tailnet identities
through the guarded device-lifecycle wrapper. It does not change desired state
or remove Platform network resources. Keep the declaration available while
the removal command resolves its targets. Then set Music to absent in the
Instance and use Platform resource reconciliation to remove its network
resources. Retained data remains binding.

Adding `--wipe-data` to this command also removes the two declared data volumes.
Do not use it when the private Instance Specification keeps the Music
`library` data with `retention: preserve`.

VM update discovery recognizes these two volumes through
[`vm-retention.json`](vm-retention.json), including when Music is absent.
See the [storage assessment](../../doc/platform-updates.md#storage-assessment)
for its limits. A catalog match does not approve migration or deletion.
