# Hardening
## Firewall #todo
```
sudo apt install -y ufw
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp
sudo ufw allow 60000:61000/udp
sudo ufw enable
sudo ufw status verbose
```
## Automatic updates #todo
```
sudo apt update
sudo apt upgrade -y
sudo apt install -y unattended-upgrades
sudo dpkg-reconfigure --priority=low unattended-upgrades
```
## sandbox mode #todo
```
approval_policy = "never"
sandbox_mode = "danger-full-access"

[notice]
hide_full_access_warning = true

[tui]
status_line = ["model-with-reasoning", "context-remaining", "current-dir", "session-id", "run-state"]
```
## Hetzner firewall
#todo
## App Armor #todo
```
sudo apt install -y apparmor-profiles apparmor-utils
sudo aa-status
```
