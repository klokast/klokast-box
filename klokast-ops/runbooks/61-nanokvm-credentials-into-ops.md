Here we reset the NanoKVM web/API password from `ops`, keep the NanoKVM
`root` password synchronized with it, and save the generated password outside
git for `ansible/bin/nanokvm-virtual-media`.

No need to save the username in a file. The username can be passed as a
parameter to the wrapper, for example `--api-user john`. If omitted, the
wrapper defaults to username `neo`.

Log into `ops` as user `codex`:

```sh
cd /home/codex/src/klokast/klokast-box
ansible/bin/nanokvm-virtual-media --reset-api-password --api-user neo
```

By default, the wrapper saves the generated password to:

```text
/home/codex/nanokvm/oob.password
```

The directory is created with mode `0700` and the password file with mode
`0600`. Do not commit it, do not put it in inventory, and do not pass it on the
command line.

The reset action uses root SSH to `oob`, then calls the NanoKVM local API
`POST /api/auth/password`. Current NanoKVM builds use that API path to update
both `/etc/kvm/pwd` for the web/API account and the NanoKVM Linux `root`
password. If the current web password is unknown, the wrapper temporarily
writes a throwaway `/etc/kvm/pwd` account only to obtain an API token, restores
the original account, and then performs the durable password change through
the API.

If you already know the current NanoKVM web/API password and have it in a
file, pass it explicitly:

```sh
ansible/bin/nanokvm-virtual-media --reset-api-password \
  --api-user neo \
  --api-password-file /home/codex/nanokvm/oob.password
```

Test from `ops` as user `codex`:

```sh
ansible/bin/nanokvm-virtual-media --hid-paste 'echo test' \
  --hid-enter \
  --api-user neo \
  --api-password-file /home/codex/nanokvm/oob.password
```

If a root-owned password file is needed for a specific deployment, copy the
generated file after the reset:

```sh
sudo install -d -o root -g codex -m 0710 /etc/klokast
sudo install -d -o root -g codex -m 0710 /etc/klokast/nanokvm
sudo install -o root -g codex -m 0640 \
  /home/codex/nanokvm/oob.password \
  /etc/klokast/nanokvm/oob.password
```
