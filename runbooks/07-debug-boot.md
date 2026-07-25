# diagnosis commands

From a failed alpine boot:

mkdir -p /mnt/efi
mount /dev/nvme0n1p1 /mnt/efi
dmesg > /tmp/dmesg.txt
awk '/r8169/' /tmp/dmesg.txt        # prints lines containing r8169.
grep -Ein -C 3 'eth|enp|link up|link down|dhcp|firmware|r8169|r8125|igc|e1000|e1000e|atl' /tmp/dmesg.txt
