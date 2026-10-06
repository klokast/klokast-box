# Torrent

Dedicated torrent download edge for a box.

## Model

- `<box>-torrent`: Alpine app VM in the DMZ, `tag:torrent`.
- qBittorrent-nox: WebUI at `https://<box>-torrent.<tailnet>`.
- Mihomo: VM-local VPN egress in TUN mode.
- nftables: qBittorrent UID may only egress through the VPN TUN device.
- Completed files: `/srv/torrent/complete`; media apps pull from there.

Enable in the private platform-resource registry:

```yaml
apps:
  torrent:
    enabled: true
    placement:
      active_master: boxb
    app_vms:
      torrent:
        boxb:
          vm_ipv4_address: 192.168.200.30
    resources: {}
```

Deploy from the controller as `smith`:

```sh
apps/torrent/bin/torrentctl deploy \
  --box boxb \
  --resources-registry ~/private/klokast/platform-resources.yml \
  --vpn-config ~/private/klokast/torrent-vpn.yml
```

Open the UI from an allowed Tailscale device:

```sh
kk torrent open --to boxb
```

Select the private Instance worktree as described in the
[`kk` interface](../../klokast-dev/README.md#application-commands-with-kk).
For direct invocation of `apps/torrent/bin/torrent-client`, set
`KLOKAST_TAILNET_SUFFIX` to the deployment Tailnet DNS name. Use
`kk torrent status --to boxb` to print the URL without
opening a browser. Both commands accept `--box` as an alias for `--to`.
`status` does not check runtime health. `torrentctl` remains the controller
deployment and verification tool.

Run the client checks from the repository root:

```sh
python3 -m unittest discover -s apps/torrent/tests
```

The checks use stub tools and do not open a browser or make network calls.

## Notes

- Family access is via Tailscale HTTPS only; qBittorrent itself listens on localhost.
- The default WebUI local-auth bypass is intentional behind Tailscale Serve.
- `torrent-export` has read-only access to completed files for pull-based imports.
- Keep stale Tailscale machines cleaned after app VM creation or rename.
