Here we setup the deployment server on Hetzner or Vultr, manually, then:
- add users `agent` and `neo`
- setup `vi`, `mosh`, `tmux`

# create a VPS in hetzner.com or vultr
- create ssh key on laptop, see [[cheat_sheet-ssh.md]]

- create VPS:
  - hetzner: cost optimized, x867_64, CX23, Helsinki, Ubuntu, yes public IPv4, yes public IPv6, copy ssh key into it, 1vCPU, 1-2 GB RAM
  - vultr: Seoul, shared CPU, not automatic backup, smallest size, Ubuntu most recent LTS, public IPv6 but no IPv4
- none of: volume, firewall, backups, placement groups, labels, no cloud config
- name: `hetzner-ops` or `vultr-ops`
- copy the IPv6 IP from the instance to first ssh into it:
  - hetzner: ssh to then new instance (beware the 1 at end of IPv6 address):
  `ssh -i /Users/xiaoju/.ssh/xiaoju_codex_hetzner root@2a01:____:____:____::1`
  - vultr:
  `ssh -i /Users/xiaoju/.ssh/vultr-ops-agent root@xxxx:xxxx:xxxx:xxxx:xxxx:xxxx:xxxx:xxxx`

# add entries in MacBook
`~/.ssh/config`
```
Host *
    AddKeysToAgent yes
    UseKeychain yes

Host vn
    Hostname vultr-ops
    User neo

Host va
    Hostname vultr-ops
    User agentß
```

sudo apt update
sudo apt upgrade

sudo apt install -y shellcheck jq
shellcheck --version
jq --version

# users
sudo adduser agent                       # will be normal user
sudo adduser neo                         # will be sudoer

# set vi as editor
sudo apt purge -y nano

vi ~/.bashrc                                         # then add at the end:
```
export EDITOR=vi
export VISUAL=vi
```
source ~/.bashrc                         # load it
echo $EDITOR                             # check it
echo $VISUAL
sudo visudo      # add 2 line after this one: `Defaults env_reset`

```
Defaults        editor=/usr/bin/vi
Defaults        env_keep += "EDITOR VISUAL"
```

git config --global core.editor vi            # set vi editor for git
git config --global --get core.editor         # then check
sudo update-alternatives --config editor      # then select 2: `vim-basic`

sudo visudo -f /etc/sudoers.d/neo             # file is empty. Add this: `neo ALL=(ALL:ALL) ALL`
sudo chown root:root /etc/sudoers.d/neo
sudo chmod 0440 /etc/sudoers.d/neov
sudo visudo -c                                # check

# user `agent` access rights

sudo visudo -f /etc/sudoers.d/codex-updates
```
agent ALL=(root) NOPASSWD: /usr/bin/apt update
agent ALL=(root) NOPASSWD: /usr/bin/apt upgrade *
agent ALL=(root) NOPASSWD: /usr/bin/apt-get update
agent ALL=(root) NOPASSWD: /usr/bin/apt-get upgrade *
agent ALL=(root) NOPASSWD: /usr/bin/apt-get dist-upgrade *

agent ALL=(root) NOPASSWD: /usr/bin/npm install -g @openai/codex
agent ALL=(root) NOPASSWD: /usr/bin/npm update -g *
```

# setup Tailscale

sudo curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up --ssh --advertise-tags=tag:ops

# mosh

sudo apt install -y mosh tmux
tmux -V
mosh --version
sudo locale-gen en_GB.UTF-8
sudo update-locale LANG=en_GB.UTF-8
locale                          # check
locale -a | grep en_GB          # output should be en_GB.utf8

- on MacBook:
```
brew update
brew upgrade
brew install tmux mosh
tmux -V                    # check
mosh --version

ssh codex                 # ssh into the Hetzner box as sudoer user neo
sudo apt update
sudo apt install -y openssh-server tmux mosh
sudo systemctl status ssh         # check is active
tmux -V
mosh --version
sudo locale-gen en_GB.UTF-8
sudo update-locale LANG=en_GB.UTF-8
locale                          # check
locale -a | grep en_GB          # output should be en_GB.utf8
```

# tmux

Authentication is handled by Tailscale SSH, then connection moves to mosh:

From MacBook, tailscale-ssh to `ops`
- once as user `agent`: `mosh vaops`,
- once again as user `neo`: `mosh vnops`

vi ~/.bashrc          # and add at the end, for both `agent` and `neo`:
```
echo 'export IGNOREEOF=999' >> ~/.bashrc

if command -v tmux >/dev/null 2>&1 && [ -z "$TMUX" ] && [ -n "$PS1" ]; then
    if tmux has-session -t main 2>/dev/null; then
        exec tmux attach-session -t main
    else
        exec tmux new-session -s main -c "/home/agent/src/klokast/klokast-box"
    fi
fi
```

source ~/.bashrc
exit

- test it: log in from MacBook with `mosh vaops` or `mosh vnops`
- and detach back to MacBook with `Ctrl b d`

vi ~/.tmux.conf               # Both on users `codex` and `neo`:
```
set -g mouse on
set -g history-limit 100000
set -gw mode-keys vi
set -s set-clipboard on
```
tmux source-file ~/.tmux.conf

# remove ssh
Maybe we could remove OpenSSH for the server, as Tailscale SSH got its own ssh implementation.
However, maybe some other apps depend on OpenSSH, such as possibly `scp`, `sftp`, `git+ssh`, `Ansible`, `rsync-over-ssh`.
So for now we just do this:

```
sudo systemctl disable --now ssh.socket
systemctl status ssh.socket ssh.service  # check
      # sudo systemctl enable --now ssh          # this to bring back ssh. We better don't bring back ssh.socket
systemctl status ssh.socket              # should show disabled
sudo systemctl reload ssh                 # reload ssh

sudo passwd -l root                      # lock the passwords of these accounts.
sudo passwd -l agent
sudo passwd -S agent                       # check the presence of `L` in the output
sudo passwd -S root
```

#TODO
set up full saving to files of the outputs, maybe this way:

2. Log the session
If you want a permanent transcript of a Codex run:
script -f ~/codex-session.log
codex
exit
Or with tmux itself:
tmux pipe-pane -o 'cat >> ~/codex-pane.log'

said again, for each codex session:
tmux pipe-pane -o 'cat >> ~/logs/codex-#{session_name}-#{window_index}.log'
Then review with less, grep, or rg, instead of depending on scrollback.

# trick:
Keep one pane for Codex, one for notes
A nice pattern is:
left pane: Codex
right pane: less +F ~/codex-pane.log or scratch notes
