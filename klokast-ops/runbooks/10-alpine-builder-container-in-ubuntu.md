///
quick and dirty not secure way to give access to codex to read the key:

sudo touch /etc/tailscale-auth/ts-auth-dom0-bootstrap.authkey
vi /etc/tailscale-auth/ts-auth-dom0-bootstrap.authkey      # just write the key, no quotes: tskey-auth-xxxxxx
sudo chmod 711 /etc/tailscale-auth
sudo chown root:codex /etc/tailscale-auth/ts-auth-dom0-bootstrap.authkey
sudo chmod 640 /etc/tailscale-auth/ts-auth-dom0-bootstrap.authkey

sudo ls -al /etc/tailscale-auth

//

sudo snap install lxd
sudo lxd init --auto
sudo adduser codex lxd       # add user `codex` to group `lxc`
sudo newgrp lxd              # we switch to shell the lxd group
lxc launch images:alpine/3.23 alpine-builder
lxc exec alpine-builder -- ash        # we enter the alpine container shell
apk update
apk add abuild alpine-conf bash fakeroot git grub mtools shellcheck squashfs-tools syslinux xorriso
abuild-keygen -a -n
```
		writing RSA key
		>>>
		>>> You'll need to install /root/.abuild/root-69c6a241.rsa.pub into
		>>> /etc/apk/keys to be able to install packages and repositories signed with
		>>> /root/.abuild/root-69c6a241.rsa
		>>>
		>>> Please remember to make a safe backup of your private key:
		>>> /root/.abuild/root-69c6a241.rsa
```

exit
lxc config device add alpine-builder klokast disk source=/home/codex/src/klokast path=/work/klokast
lxc exec alpine-builder -- ash           # back inside the container
cd /work/klokast/ansible/bootstrap-iso

shellcheck --version
apk info | grep -E 'abuild|grub|xorriso|mtools|squashfs-tools'

printf '%s\n' 'tskey-auth-xxxxxxxxxxxx' > ~/ts-auth-dom0-bootstrap.authkey
chmod 600 ~/ts-auth-dom0-bootstrap.authkey
cd /work/klokast/ansible/bootstrap-iso

  cd /work/klokast/ansible/bootstrap-iso
  mkdir -p /root/bootstrap-iso-out /root/bootstrap-iso-work
  
////
  grep -R -n -- '--no-chown' /root/bootstrap-iso-work/aports/scripts
  sed -i 's/ --no-chown//g' /root/bootstrap-iso-work/aports/scripts/mkimg.base.sh   # we patch
  grep -n -- '--no-chown' /root/bootstrap-iso-work/aports/scripts/mkimg.base.sh   #check: It should print nothing.
///
  
  
./build-bootstrap-iso.sh \
  --auth-key-file /root/ts-auth-dom0-bootstrap.authkey \
  --outdir /root/bootstrap-iso-out \
  --workdir /root/bootstrap-iso-work

  That should avoid the permission problem.

  If it succeeds, your ISO will be under:

  /root/bootstrap-iso-out

  and you can copy it back to the Ubuntu host with:

  exit
  lxc file pull alpine-builder/root/bootstrap-iso-out/klokast-bootstrap-duh-dom0-3.23.3-x86_64.iso .

  If you want all output filenames first:

  lxc exec alpine-builder -- ls -al /root/bootstrap-iso-out






  
  


./build-bootstrap-iso.sh --auth-key-file /etc/tailscale-auth/ts-auth-dom0-bootstrap.authkey


