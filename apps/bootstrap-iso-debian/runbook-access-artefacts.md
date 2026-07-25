# How to access manually the output artifacts
From the deployment server as `codex`, ask the managed builder helper to upload
from inside the Tailscale-enrolled builder container:
```sh
cd /home/codex/src/klokast/klokast-box
ansible/bin/bootstrap-live-iso build-iso
ansible/bin/bootstrap-live-iso transfer-iso
```

# useful commands

From the deployment server:
```sh
tailnet="$(tailscale status --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["MagicDNSSuffix"].rstrip("."))')"
ssh "neo@yii-bak.${tailnet}"
doas -n podman ps -a --filter name=bootstrap-live-builder               # basic info
doas -n podman inspect bootstrap-live-builder                           # details
doas -n podman exec -it bootstrap-live-builder sh -lc 'ls -lh /output'  # see the artifacts from inside the container
ls -lh /srv/bootstrap-live-builder/output/                              # see the artifacts from the `yii-bak` VM

doas -n podman exec -it bootstrap-live-builder sh                       # enter a shell
ls /src/klokast/apps/bootstrap-iso-debian                               # copied from the git repo: scripts and .md files
ls /output                                                              # the artifacts outputs
ls /root                                                                # Tailscale OAuth keys
exit                                                                    # exit the shell

doas -n /usr/local/sbin/bootstrap-live-builder-build                    # build and upload from the builder container

exit                                                                    # exit ssh

ssh "root@oob.${tailnet}"                                               # ssh into `oob` over Tailscale from the deployment server
exit
```

# the artifacts
klokast-bootstrap-debian-trixie-amd64.iso
alpine-standard-3.23.3-x86_64.tar.gz

alpine-standard-3.23.3-x86_64.tar.gz.sha256
alpine-standard-3.23.3-x86_64.tar.gz.sha512
klokast-bootstrap-debian-trixie-amd64.iso.sha512
