# Using the Alpine seed tarball

The Alpine seed tarball is not bootable. It contains the signed Alpine boot
artifacts and package repository consumed later by
`ansible/playbooks/12-bootstrap-diskless-build.yml`.

Typical artifact:

```text
alpine-standard-3.23.3-x86_64.tar.gz
```

The Podman builder container on `yii-bak` writes the tarball under:

```text
/srv/bootstrap-live-builder/output/
```

The normal portal ISO helper does not upload this optional seed tarball. If a
future flow needs the tarball on the NanoKVM, copy it explicitly to:

```text
root@oob.<tailnet>.ts.net:/data/
```

The tarball is optional for the portal bootstrap itself. The Debian Live ISO can
boot, get DHCP, publish `kk.local` and `klokast.local`, and enroll in Tailscale
without it. Keep the tarball available for the Ansible diskless build phase if
the workflow is using prebuilt Alpine seed artifacts instead of downloading the
Alpine ISO during phase 12.
