# UEFI Shell Useful Commands

reset                     # Warm reboot
reset -c                  # Cold reboot / hard reset
exit                      # Return to BIOS/UEFI boot manager

map                       # Show devices and filesystem mappings
map -r                    # Rescan devices

fs0:                      # Switch to filesystem fs0
blk0:                     # Switch to block device blk0

ls                        # List files
dir                       # Alternative to ls
cd EFI                    # Change directory
pwd                       # Show current directory

EFI\BOOT\BOOTX64.EFI      # Start generic EFI bootloader
EFI\debian\grubx64.efi    # Start Debian GRUB
EFI\ubuntu\grubx64.efi    # Start Ubuntu GRUB

cp a b                    # Copy file
mv a b                    # Rename/move file
rm file                   # Delete file
mkdir dir                 # Create directory

startup.nsh               # Run startup script
edit startup.nsh          # Edit startup script

help                      # List commands
help reset                # Help for one command
ver                       # Show shell version
memmap                    # Show memory map
devices                   # List devices
pci                       # Show PCI devices
dmpstore                  # Show UEFI variables

# Typical recovery flow:
map -r
fs0:
ls
cd EFI
ls
EFI\BOOT\BOOTX64.EFI
