# GL-inet Flint 2 MT6000 wifi router

## Links
https://openwrt.org/toh/gl.inet/gl-mt6000               # OpenWRT instructions for this router
https://downloads.openwrt.org/releases                  # OpenWRT firmware
https://static.gl-inet.com/www/images/products/datasheet/mt6000_datasheet_20251103.pdf    # datasheet
https://dl.gl-inet.com/router/mt6000/stable             # original gl-inet firmware
https://www.gl-inet.com/en-de/pages/app                 # smartphone app
https://www.gl-inet.com/en-de/blogs/support/gl-mt6000   # support

## 1. Install OpenWRT

1. Download on MacBook from `https://downloads.openwrt.org/releases/25.12.5/targets/mediatek/filogic/glinet_gl-mt6000-squashfs-sysupgrade.bin`
   - important: `sysupgrade`, not `factory`
   - glinet_gl-mt6000: is the router model.
   - adjust release version to most recent.
   - There is another way, using `Uboot`, but only as recovery process.
2. Connect by Ethernet to a LAN port
  - for example using a network adaptor on the macbook.
  - Required because after installation, the wifi radio is disabled.
3. In the GL.iNet admin UI, go to the local firmware upgrade page.
4. Upload the OpenWrt sysupgrade image.
5. Select do not keep settings / disable settings retention.
6. Flash and wait. Do not power-cycle.

## 2. Setup as dumb AP

1. After reboot, OpenWrt should come up on the usual OpenWrt default:
  - `http://192.168.1.1`
  - login: root
  - no password

2. Setup the router admin password
System > Administration > Router Password

3. Setup, and start, the wifi radio: Network → Wireless →
  - eSSID
  - password
  - Encryption: WPA3-SAE
  - Cipher: CCMP / AES
  - 802.11w Management Frame Protection: Required
  - WPS: disabled

4. Bridge the WAN port into LAN
So the physical WAN socket becomes just another AP uplink port.
Network > Interfaces > Devices > br-lan > Configure > Bridge ports > include: lan1, lan2, lan3, lan4, wan > save

5. Change LAN to DHCP client
Network > Interfaces > LAN > Edit > General Settings >
    Protocol: DHCP client
    Device: br-lan
The Flint will get IP, gateway and DNS from k001-router.

6. Disable DHCP server on Flint, the box will provide DHCP/DNS.
Network > Interfaces > LAN > Edit > DHCP Server > Ignore interface: checked > Save

7. Disable WAN and WAN6
Network > Interfaces > wan (then wan6) > Edit > Disable this interface: checked > save

8. Wi-Fi
Network > Wireless > select your SSID (typically one for 2.4 GHz and one for 5 GHz) > Edit > General Setup >
    > set the country code
    > scroll down > Interface Configuration > General Setup > Network > Select/check: lan > Save

9. Hostname
System > General Settings > hostname > k001-ap

10. Setup SSH server
System > Administration > SSH Access > Set/check:
  - Interface: lan or unspecified
  - Port: 22
  - Password authentication: enabled, at least temporarily
  - Root login with password: enabled, at least temporarily


`Save & Apply`

- Flint gets a DHCP lease from k001-router on 10.10.30.0/24.
- Phones/laptops also get 10.10.30.x leases from k001-router.
- Gateway/DNS for everything is 10.10.30.1.
- Flint web UI moves to whatever DHCP address it receives, likely 10.10.30.20-10.10.30.80.

Set the Flint hostname to something like flint2 so we can find its lease easily from k001-router.

  Then you can SSH into the Flint from a machine on the same 10.10.30.0/24 network: ssh root@<flint-dhcp-ip>