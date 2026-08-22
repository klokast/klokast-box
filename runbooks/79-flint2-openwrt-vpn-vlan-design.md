# Flint 2 / OpenWrt VPN + Direct Network Design

## Purpose

This document describes:

- the immediate temporary setup where the Residential Gateway remains the
  router and the Flint 2 is only a dumb AP; and
- a later proposed home-network design where:
  - the Residential Gateway remains the physical WAN entry point because it
    converts optical fiber to Ethernet;
  - the Residential Gateway is placed in bridge mode if possible;
  - the GL.iNet Flint 2 / MT6000 runs vanilla OpenWrt;
  - the Flint 2 becomes the main router, firewall, DHCP/DNS authority, Wi-Fi
    AP, VLAN controller, and VPN policy router;
  - household clients and server VMs can choose between direct internet access
    and internet access through a VPN tunnel.

The preferred separation mechanism is **SSID/VLAN membership**, not per-device manual gateway configuration.

The current Platform-owned ranges are:

```text
Residential Gateway LAN:
  Current temporary upstream for Flint-as-AP.
  Use the RG's existing DHCP range. If the RG is still at its common default,
  this is likely 192.168.1.0/24 with gateway/DNS 192.168.1.1, but verify in
  the RG admin UI.

Klokast household/direct:
  VLAN 10
  subnet 10.10.10.0/24
  gateway 10.10.10.1
  DHCP 10.10.10.50-10.10.10.180

Klokast admin/AP-management:
  VLAN 20
  subnet 10.10.20.0/24
  gateway 10.10.20.1
  DHCP 10.10.20.50-10.10.20.100
  intended AP management address 10.10.20.2

Klokast AP-uplink/dumb-AP client bridge:
  untagged
  subnet 10.10.30.0/24
  gateway 10.10.30.1
  DHCP 10.10.30.20-10.10.30.80
  intended Flint reservation 10.10.30.2

Klokast IoT:
  subnet 192.168.150.0/24
  DHCP 192.168.150.50-192.168.150.150
```

The VPN client LAN does not yet exist in the checked-in inventory. If this
design is implemented later, reserve it explicitly before use. A consistent
candidate is VLAN 40 / `10.10.40.0/24` with gateway `10.10.40.1`, but that is
not active until added to the Platform registry and router automation.

---

## Immediate Goal: Flint as AP Behind RG

This is the next step after `runbooks/78-install-wifi-router.md`.

Do **not** put the Residential Gateway into bridge mode for this temporary
setup. The RG remains the router, firewall, DHCP server, DNS forwarder, and
internet gateway. The Flint only bridges Ethernet and Wi-Fi clients onto the
RG LAN.

Expected addressing:

```text
RG LAN gateway/DNS:  RG's current LAN address, likely 192.168.1.1
Flint address:      DHCP lease from RG, likely 192.168.1.x
Wi-Fi clients:      DHCP leases from RG, likely 192.168.1.x
Client gateway:     RG LAN address, likely 192.168.1.1
```

If the RG uses another private range, use that range instead. In this temporary
topology, clients should **not** receive `10.10.30.x` addresses; that range is
for connecting the dumb AP to `boxa-router`, not directly to the RG.

### Step-by-step connection

1. Confirm the RG is still in normal router mode:
   - RG DHCP server enabled.
   - RG Wi-Fi may stay enabled or be disabled, depending on whether you want to
     use only the Flint Wi-Fi.
   - RG bridge mode disabled.

2. Keep the Flint in the dumb-AP state from `runbooks/78-install-wifi-router.md`:
   - `br-lan` includes the physical LAN ports and, if configured there, the WAN
     port.
   - OpenWrt `lan` protocol is DHCP client.
   - OpenWrt DHCP server is disabled on `lan`.
   - OpenWrt `wan` and `wan6` interfaces are disabled.
   - Wi-Fi SSID(s) are attached to network `lan`.

3. Cable one RG LAN port to the Flint:
   - Preferred first test: RG LAN port -> Flint `lan1`.
   - If `runbooks/78-install-wifi-router.md` already bridged the Flint `wan`
     port into `br-lan`, RG LAN port -> Flint `wan` is also valid.
   - Do not connect RG fiber/ONT to Flint for this temporary AP mode.

4. Reboot the Flint or restart networking from LuCI:
   - LuCI: `System` -> `Reboot`; or
   - LuCI: `Network` -> `Interfaces` -> `Restart`.

5. Find the Flint's RG-side DHCP lease:
   - In the RG admin UI, check the DHCP/client list.
   - Look for hostname `boxa-ap`, `flint2`, or the Flint MAC address.
   - Record the address, for example `192.168.1.23`.

6. Connect a phone or laptop to the Flint SSID.

7. Verify the client received RG addressing:
   - Client IPv4 is in the RG LAN range, likely `192.168.1.x`.
   - Client gateway/router is the RG LAN address, likely `192.168.1.1`.
   - Client DNS is the RG LAN address or the DNS server distributed by the RG.

8. Verify internet access from the Wi-Fi client:
   - Open a normal website.
   - On macOS, `route -n get default` should show the RG as the gateway.
   - `curl https://ifconfig.me` should show the ISP/RG public exit, not a VPN.

9. Verify Flint administration:
   - Open `http://<flint-rg-dhcp-ip>/`.
   - SSH, if enabled: `ssh root@<flint-rg-dhcp-ip>`.

### Stabilize the Temporary RG/AP Setup

After basic Wi-Fi internet works, make the Flint management address stable and
verify that the RG remains the only DHCP server.

1. Reserve the Flint address on the RG:
   - In the RG admin UI, open the DHCP/client reservation page.
   - Select the Flint by MAC address.
   - Reserve a stable address in the RG LAN range.
   - Current confirmed reservation: `192.168.1.46` for the Flint MAC.

2. Verify the Flint admin paths:

   ```sh
   ssh root@192.168.1.46
   ```

   Open LuCI:

   ```text
   http://192.168.1.46/
   ```

3. Record the currently confirmed Wi-Fi state:

   ```text
   Flint SSID: klokast-1
   MacBook client address observed from Flint Wi-Fi: 192.168.1.48
   Client gateway observed from Flint Wi-Fi: 192.168.1.1
   ```

4. Check that the Flint `lan` interface is a DHCP client:
   - LuCI: `Network` -> `Interfaces` -> `LAN` -> `Edit`.
   - `Protocol` should be `DHCP client`.
   - `Device` should be `br-lan`.

5. Check that the Flint DHCP server on `lan` is disabled:
   - LuCI: `Network` -> `Interfaces`.
   - Find `LAN`.
   - Click `Edit`.
   - Open the `DHCP Server` tab.
   - Under `General Setup`, `Ignore interface` must be checked.
   - If changed, click `Save`, then `Save & Apply`.

6. Check the same setting over SSH:

   ```sh
   uci get dhcp.lan.ignore
   ```

   Expected output:

   ```text
   1
   ```

   Full section check:

   ```sh
   uci show dhcp.lan
   ```

   Expected line:

   ```text
   dhcp.lan.ignore='1'
   ```

7. Check that OpenWrt `wan` and `wan6` are disabled:
   - LuCI: `Network` -> `Interfaces`.
   - `WAN` and `WAN6` should be disabled for this temporary dumb-AP topology.
   - The physical port labeled `wan` may still be valid as an AP uplink only
     because `runbooks/78-install-wifi-router.md` bridged it into `br-lan`.

8. Optional: disable RG Wi-Fi after Flint Wi-Fi is confirmed stable, so the
   Flint is the only active AP.

At this point the temporary setup is complete. Stop here if the goal is simply
to use `klokast-1` Wi-Fi through the RG's internet connection. The next
architectural step is a deliberate migration decision: either keep this
temporary RG-router/Flint-AP topology, or move later to the target topology
where the RG enters bridge mode and the Flint becomes the main router.

### If Wi-Fi Associates But Has No Internet

Check these in order:

1. The RG-side cable is in an RG LAN port, not an RG WAN/fiber-only port.
2. The Flint `lan` interface is a DHCP client.
3. The Flint DHCP server is disabled.
4. The Flint Wi-Fi SSID is bound to OpenWrt network `lan`.
5. The Flint physical port used for RG uplink is a member of `br-lan`.
6. The RG DHCP server has free leases and sees the Flint/client MACs.

### Temporary Topology Summary

```text
Fiber
  |
Residential Gateway
router, firewall, DHCP, DNS, NAT
  |
  | RG LAN, likely 192.168.1.0/24
  |
Flint 2 / OpenWrt
dumb AP / bridge only
  |
  +-- Wi-Fi clients receive RG DHCP leases
  +-- Wired clients receive RG DHCP leases
```

---

## High-Level Recommendation

Use the following target architecture later, after the temporary RG/AP setup is
working:

```text
Fiber
  |
Residential Gateway
bridge / fiber-to-Ethernet role only
  |
  | Ethernet WAN
  |
Flint 2 / OpenWrt
main router, firewall, DHCP, DNS, VLANs, Wi-Fi, VPN client
  |
  +-- SSID: home-direct  -> Direct VLAN -> Direct WAN
  |
  +-- SSID: home-vpn     -> VPN VLAN    -> VPN tunnel only
  |
  +-- Server NIC A       -> Direct network / direct VM bridge
  |
  +-- Server NIC B       -> VPN network / VPN VM bridge
```

The key design principle is:

> **Direct vs VPN should be represented as separate logical networks.**

That separation can be implemented with:

- separate SSIDs;
- separate VLANs;
- separate physical ports;
- separate VM bridges on the server;
- or a combination of these.

Given that the home server has many available NICs, the first implementation can avoid unnecessary trunking complexity by using separate physical NICs for direct and VPN VM bridges. VLAN trunks can be introduced later if needed.

---

## Why This Design Is Preferred

### Residential Gateway in bridge mode

The Residential Gateway remains physically necessary because it converts fiber to Ethernet, but it does not need to remain the home router.

When bridge mode is available, the cleaner model is:

```text
Residential Gateway = fiber / Ethernet bridge
Flint 2             = actual home router
```

This avoids:

- double NAT;
- two competing DHCP servers;
- unclear routing authority;
- split firewall policy between two devices;
- awkward interactions between Residential Gateway LAN and Flint LAN.

### Flint 2 as main router

The Flint 2 running OpenWrt should own:

- WAN connection;
- LAN interfaces;
- VLANs;
- Wi-Fi SSIDs;
- DHCP;
- DNS forwarding/resolution policy;
- firewall zones;
- VPN clients;
- policy-based routing.

This creates one coherent place to control traffic behavior.

### SSID/VLAN-based choice

Clients should not need to manually configure their default gateway.

Instead:

```text
Join direct SSID  -> direct internet
Join VPN SSID     -> VPN internet
Attach VM to direct bridge -> direct internet
Attach VM to VPN bridge    -> VPN internet
```

That is much easier to reason about, document, enforce, and debug.

---

## Logical Networks

Create at least two logical networks.

### 1. Direct network

Purpose:

- normal household internet access;
- no VPN tunnel by default;
- suitable for ordinary devices, latency-sensitive services, streaming devices, trusted local devices, and management clients if desired.

Current Platform values:

```text
Name:        direct
VLAN:        10
Subnet:      10.10.10.0/24
SSID:        home-direct
Gateway:     10.10.10.1
DHCP scope:  10.10.10.50-10.10.10.180
Internet:    WAN direct
DHCP:        enabled on Flint
DNS:         Flint or chosen upstream resolver
```

Routing behavior:

```text
source = 10.10.10.0/24
route  = WAN / ISP path
```

### 2. VPN network

Purpose:

- clients that should exit through a VPN;
- VMs that should have VPN egress;
- optional privacy / geolocation / testing network.

Proposed values:

```text
Name:        vpn
VLAN:        40, proposed
Subnet:      10.10.40.0/24, proposed
SSID:        home-vpn
Gateway:     10.10.40.1, proposed
Internet:    VPN tunnel only
DHCP:        enabled on Flint
DNS:         Flint, with upstream DNS routed through VPN
```

The VPN range is not active in the current inventory. Add it to the Platform
network model before deploying this part.

Routing behavior:

```text
source = 10.10.40.0/24
route  = selected VPN tunnel
```

Firewall behavior:

```text
VPN clients -> VPN tunnel: allowed
VPN clients -> direct WAN: blocked
VPN clients -> direct LAN: blocked by default, selectively allowed if needed
```

The VPN network should have a real kill switch. If the VPN tunnel is down, clients on this network should lose internet access rather than silently falling back to direct WAN.

### 3. Optional management network

Purpose:

- router administration;
- server administration;
- hypervisor administration;
- trusted admin devices;
- out-of-band or semi-out-of-band control.

Current Platform values:

```text
Name:        mgmt
VLAN:        20
Subnet:      10.10.20.0/24
SSID:        optional, or wired only
Gateway:     10.10.20.1
DHCP scope:  10.10.20.50-10.10.20.100
AP address:  10.10.20.2
Internet:    direct WAN, or restricted
DHCP:        optional
DNS:         Flint or chosen internal resolver
```

Recommended firewall posture:

```text
mgmt -> Flint admin UI / SSH: allowed
mgmt -> server/hypervisor admin: allowed
mgmt -> direct LAN: optional
mgmt -> VPN LAN: optional
other networks -> mgmt: blocked by default
```

### 4. Optional IoT / guest network

This is not required for the VPN/direct goal, but may be useful later.

```text
Name:        guest or iot
VLAN:        existing IoT zone has no household VLAN in this design yet
Subnet:      192.168.150.0/24 for the current Klokast IoT router zone
SSID:        home-guest or home-iot
Internet:    direct WAN or VPN, depending on preference
LAN access:  blocked by default
```

---

## Physical Topology

### Target topology

```text
Fiber
  |
Residential Gateway in bridge mode
  |
Flint WAN port
  |
Flint LAN/Wi-Fi side
  |
  +-- Direct SSID
  +-- VPN SSID
  +-- Optional management SSID
  +-- Server direct NIC
  +-- Server VPN NIC
  +-- Optional switch or VLAN trunk
```

### Recommended initial physical server wiring

Because the server has multiple available NICs, use separate NICs first:

```text
Flint direct-side port/network -> Server NIC A -> vmbr-direct
Flint VPN-side port/network    -> Server NIC B -> vmbr-vpn
```

This avoids needing to debug VLAN trunking on day one.

VM behavior:

```text
VM attached to vmbr-direct:
  gets DHCP from direct network
  exits through direct WAN

VM attached to vmbr-vpn:
  gets DHCP from VPN network
  exits through VPN tunnel only
```

### Later physical simplification with VLAN trunking

After the basic design is stable, a single trunk can replace multiple physical links:

```text
Flint trunk port
  |
Managed switch
  |
  +-- Server trunk NIC
  +-- Other wired devices
  +-- Additional APs if needed
```

The server would then expose multiple VLAN-backed bridges:

```text
Server trunk NIC
  |
  +-- VLAN 10 -> vmbr-direct
  +-- VLAN 40 -> vmbr-vpn, proposed
  +-- VLAN 20 -> vmbr-mgmt
```

---

## OpenWrt Interface Model

Use separate OpenWrt interfaces for each logical network.

Example model:

```text
wan
  role: upstream internet
  device: <WAN_INTERFACE>
  protocol: depends on ISP bridge mode

lan_direct
  role: direct household LAN
  device: br-lan.10, or the equivalent DSA VLAN device
  subnet: 10.10.10.0/24
  DHCP: enabled
  firewall zone: direct

lan_vpn
  role: VPN-only client LAN
  device: br-lan.40, or the equivalent DSA VLAN device, proposed
  subnet: 10.10.40.0/24, proposed
  DHCP: enabled
  firewall zone: vpn_clients

lan_mgmt
  role: administration network
  device: br-lan.20, or the equivalent DSA VLAN device
  subnet: 10.10.20.0/24
  DHCP: optional
  firewall zone: mgmt
```

If using OpenWrt DSA bridge VLAN filtering, the physical layout would conceptually be:

```text
br-lan
  VLAN 10 -> direct / household network
  VLAN 20 -> management network
  VLAN 40 -> VPN network, proposed
```

The exact device names depend on the Flint OpenWrt installation and should be confirmed with:

```sh
ip link
bridge vlan show
uci show network
```

---

## Wi-Fi / SSID Model

Create separate SSIDs and bind each SSID to the correct OpenWrt network.

```text
SSID: home-direct
  network: lan_direct
  internet: direct WAN

SSID: home-vpn
  network: lan_vpn
  internet: VPN tunnel only

SSID: home-mgmt
  network: lan_mgmt
  internet: optional
  access: admin devices only
```

Recommended Wi-Fi security posture:

```text
Use WPA2/WPA3 mixed or WPA3-only if all clients support it.
Use strong unique passphrases per SSID.
Do not reuse the VPN SSID password for guest or IoT networks.
Disable WPS.
Use client isolation on guest/IoT SSIDs, not necessarily on trusted direct/VPN SSIDs unless desired.
```

For the VPN SSID, the important point is not only Wi-Fi association but also routing and firewall enforcement:

```text
SSID home-vpn -> lan_vpn -> vpn_clients firewall zone -> VPN tunnel only
```

---

## Server / Hypervisor Model

The server should expose at least two VM bridges.

### Direct bridge

```text
Bridge name: vmbr-direct
Physical uplink: Server NIC A, or VLAN subinterface on trunk
Network: direct
DHCP source: Flint direct network
Default VM behavior: direct WAN
```

### VPN bridge

```text
Bridge name: vmbr-vpn
Physical uplink: Server NIC B, or VLAN subinterface on trunk
Network: vpn
DHCP source: Flint VPN network
Default VM behavior: VPN tunnel only
```

### Optional management bridge

```text
Bridge name: vmbr-mgmt
Physical uplink: Server NIC C, or VLAN subinterface on trunk
Network: mgmt
Purpose: hypervisor/admin access
```

### VM choice model

Do not make each VM manually choose a gateway unless needed.

Preferred model:

```text
VM requiring normal internet:
  attach NIC to vmbr-direct

VM requiring VPN egress:
  attach NIC to vmbr-vpn

VM requiring admin-only access:
  attach NIC to vmbr-mgmt
```

This makes VM behavior visible from the hypervisor configuration itself.

---

## VPN Backends

The routing design should support several possible VPN tunnel types.

### Commercial WireGuard subscription

Preferred first choice if the subscription provider offers WireGuard configuration.

Suggested OpenWrt interface name:

```text
wg_sub
```

Use case:

```text
lan_vpn -> wg_sub
```

Advantages:

- good performance;
- clean interface model;
- simple policy-based routing target;
- easier firewall kill-switch;
- easier future migration to a self-hosted WireGuard server.

### Commercial OpenVPN subscription

Use if the provider does not offer WireGuard.

Suggested OpenWrt interface name:

```text
ovpn_sub
```

Use case:

```text
lan_vpn -> ovpn_sub
```

OpenVPN is usually heavier on small routers, but it remains acceptable if performance is sufficient.

### Tailscale exit node

Tailscale can be added later as a second-stage backend.

Suggested interface name:

```text
tailscale0
```

Potential use:

```text
lan_vpn_test -> tailscale0 exit node
```

Caution:

Tailscale manages routes, DNS behavior, and exit-node selection. Do not let it unexpectedly replace the router's own default route. Treat it as a specific policy-routing target, not as the implicit default path for the entire router, unless that is deliberately intended.

### Future self-hosted WireGuard server abroad

Long-term clean option.

Suggested interface name:

```text
wg_abroad
```

Use case:

```text
lan_vpn -> wg_abroad
```

Advantages:

- controlled exit location;
- controlled logging policy;
- stable routing;
- simple OpenWrt integration;
- no dependency on a commercial VPN provider's client tooling.

---

## Policy-Based Routing Model

Install and use OpenWrt policy-based routing if not already available.

Conceptual policies:

```text
Policy: direct network via WAN
  source: 10.10.10.0/24
  target: wan

Policy: VPN network via selected VPN tunnel
  source: 10.10.40.0/24, proposed
  target: <VPN_INTERFACE>

Policy: management network via WAN or restricted route
  source: 10.10.20.0/24
  target: wan or specific rules
```

The most important policy is:

```text
source = VPN network
target = VPN interface
```

Avoid overcomplicating the first version with per-domain, per-device, or per-port routing.

Start with subnet-based routing:

```text
one source subnet = one intended egress path
```

That is easier to debug and safer for the household.

---

## Firewall Zone Model

Use distinct firewall zones.

```text
Zone: wan
  contains: WAN interface
  input: reject/drop
  output: accept
  forward: reject/drop
  masquerading: enabled as needed

Zone: direct
  contains: lan_direct
  input: accept or restricted
  output: accept
  forward: allow to wan

Zone: vpn_clients
  contains: lan_vpn
  input: limited; DHCP/DNS allowed to router
  output: accept
  forward: allow only to vpn_tunnel zone

Zone: vpn_tunnel
  contains: VPN interface
  input: reject/drop
  output: accept
  forward: reject/drop unless needed
  masquerading: enabled if required by tunnel design

Zone: mgmt
  contains: lan_mgmt
  input: accept to router admin services
  output: accept
  forward: selective
```

### Critical kill-switch rule

Do not allow VPN clients to forward to WAN.

```text
vpn_clients -> vpn_tunnel: allowed
vpn_clients -> wan: blocked
```

That way:

```text
VPN up   -> VPN clients have internet
VPN down -> VPN clients lose internet
```

They should not fall back to direct WAN.

### Optional local access rules

Decide explicitly whether VPN clients can access local services.

Default recommendation:

```text
vpn_clients -> direct LAN: blocked
vpn_clients -> mgmt: blocked
vpn_clients -> selected internal services: optional explicit allow
```

Examples of selected internal services that might be allowed:

```text
VPN clients -> internal DNS resolver
VPN clients -> selected NAS service
VPN clients -> selected printer
VPN clients -> selected server service
```

Do not allow broad lateral access by default.

---

## DNS Model

DNS must match the routing policy.

For the direct network:

```text
Direct clients -> Flint DNS
Flint DNS -> chosen upstream resolver via WAN
```

For the VPN network:

```text
VPN clients -> Flint DNS
Flint DNS for VPN clients -> resolver reachable through VPN
```

Avoid this bad pattern:

```text
VPN clients -> Residential Gateway DNS or ISP DNS directly
```

That can create DNS leaks even when the main traffic goes through the VPN.

Possible DNS approaches:

```text
Approach A:
  Flint dnsmasq handles all client DNS
  policy/firewall ensures DNS from VPN clients exits through VPN

Approach B:
  VPN clients receive a VPN-provider DNS server via DHCP
  firewall blocks other DNS destinations

Approach C:
  run internal encrypted DNS resolver
  route its upstream traffic according to policy
```

Recommended baseline:

```text
Clients use Flint as DNS.
Flint policy ensures VPN-network DNS queries do not leak through direct WAN.
Block direct outbound DNS from VPN clients except to Flint or approved DNS endpoint.
```

---

## IPv6 Model

Make an explicit IPv6 decision.

VPN leak problems often come from IPv6 being enabled on the direct WAN while the VPN only handles IPv4.

For the VPN network, choose one of:

```text
Option A: VPN supports IPv6 properly
  route IPv6 through VPN as well

Option B: VPN does not support IPv6
  disable IPv6 on the VPN client network
  block IPv6 forwarding from VPN clients to WAN
```

Do not leave IPv6 half-enabled on the VPN SSID.

For the direct network, IPv6 can remain enabled if the ISP and OpenWrt configuration are correct.

---

## Migration Plan

### Phase 0 — Inventory current state

Collect:

```sh
ip link
ip addr
ip route
bridge vlan show
uci show network
uci show wireless
uci show firewall
uci show dhcp
```

Also identify:

```text
Residential Gateway bridge-mode behavior
Flint WAN interface name
Flint LAN switch/DSA port names
Available server NIC names
Current hypervisor bridge names
VPN provider configuration format
Tailscale status, if already installed
```

### Phase 1 — Put Residential Gateway into bridge mode

Goal:

```text
Residential Gateway stops being the LAN router.
Flint receives the WAN uplink.
Flint becomes the routing authority.
```

Verify:

```text
Flint has WAN connectivity.
Flint firewall is active.
Only Flint provides DHCP on home LAN networks.
Residential Gateway no longer provides normal LAN DHCP.
```

Rollback requirement:

Document how to return the Residential Gateway to router mode before making major changes.

### Phase 2 — Establish direct LAN first

Before adding VPN complexity, make the direct network stable.

Create:

```text
lan_direct
SSID home-direct
DHCP on direct network
DNS on direct network
WAN routing via ISP
```

Verify from a direct Wi-Fi client and one wired/VM client:

```sh
ip route
nslookup example.com
curl https://ifconfig.me
ping known external host
```

### Phase 3 — Add VPN tunnel

Add the first VPN tunnel, preferably WireGuard if available.

Create interface:

```text
<VPN_INTERFACE>
```

Verify from the router itself:

```sh
ip addr show <VPN_INTERFACE>
ip route
ping through tunnel if applicable
curl --interface <VPN_INTERFACE> https://ifconfig.me
```

Exact commands depend on tunnel type.

### Phase 4 — Create VPN network

Create:

```text
lan_vpn
SSID home-vpn
DHCP on VPN network
firewall zone vpn_clients
```

Do not yet allow fallback to WAN.

Verify:

```text
Client on home-vpn receives address from VPN network.
Client can reach Flint for DHCP/DNS.
Client exits through VPN public endpoint.
Client loses internet if VPN tunnel is disabled.
Client does not fall back to direct WAN.
```

### Phase 5 — Add server VM bridges

Initial version with multiple NICs:

```text
Server NIC A -> direct network -> vmbr-direct
Server NIC B -> VPN network    -> vmbr-vpn
```

Create test VMs or temporarily attach existing VMs:

```text
VM on vmbr-direct -> direct WAN IP
VM on vmbr-vpn    -> VPN exit IP
```

Verify DNS and IPv6 behavior separately.

### Phase 6 — Add management network if desired

Create:

```text
lan_mgmt
vmbr-mgmt
optional admin SSID
```

Move admin interfaces carefully. Avoid locking yourself out of the router or hypervisor.

### Phase 7 — Optional: move to VLAN trunking

After stable operation, simplify cabling if desired:

```text
Flint trunk -> managed switch -> server trunk NIC
```

Then replace separate physical NIC mappings with VLAN subinterfaces on the server.

---

## Validation Checklist

### Direct network

```text
[ ] Client joins home-direct.
[ ] Client receives address from direct DHCP scope.
[ ] Client default route points to Flint's direct-network address.
[ ] Public IP equals ISP/direct public IP.
[ ] DNS works.
[ ] IPv6 behavior is intentional.
```

### VPN network

```text
[ ] Client joins home-vpn.
[ ] Client receives address from VPN DHCP scope.
[ ] Client default route points to Flint's VPN-network address.
[ ] Public IP equals VPN exit IP.
[ ] DNS does not leak through ISP.
[ ] IPv6 is either routed through VPN or disabled/blocked.
[ ] If VPN tunnel is stopped, internet access fails closed.
[ ] Client does not fall back to direct WAN.
```

### Server VMs

```text
[ ] VM on vmbr-direct exits through direct WAN.
[ ] VM on vmbr-vpn exits through VPN.
[ ] VM on vmbr-vpn fails closed when VPN is down.
[ ] VM DNS behavior matches its network.
[ ] VM IPv6 behavior is intentional.
```

### Firewall

```text
[ ] direct -> wan is allowed.
[ ] vpn_clients -> vpn_tunnel is allowed.
[ ] vpn_clients -> wan is blocked.
[ ] vpn_clients -> mgmt is blocked.
[ ] vpn_clients -> direct is blocked unless explicitly allowed.
[ ] mgmt -> router admin is allowed.
[ ] non-mgmt -> router admin is restricted as desired.
```

---

## Debugging Commands

Useful OpenWrt commands:

```sh
ip link
ip addr
ip route
ip rule
bridge vlan show
nft list ruleset
uci show network
uci show firewall
uci show dhcp
uci show wireless
logread -f
```

Useful client-side tests:

```sh
ip addr
ip route
nslookup example.com
curl https://ifconfig.me
curl https://ipinfo.io/ip
traceroute example.com
```

Useful VPN checks:

```sh
wg show
ip addr show <VPN_INTERFACE>
ip route show table all
```

For OpenVPN-based tunnels:

```sh
logread | grep -i openvpn
ip addr
ip route
```

For Tailscale:

```sh
tailscale status
tailscale netcheck
tailscale ip
tailscale debug prefs
```

---

## Important Design Constraints

### Avoid multiple DHCP servers on the same network

Only one DHCP server should serve a given L2 network/VLAN.

In the target design:

```text
Flint serves DHCP for all OpenWrt-controlled LAN/VLAN networks.
Residential Gateway does not serve DHCP when in bridge mode.
```

### Avoid default-gateway choice as the main mechanism

Manual gateway selection is acceptable for testing, but not preferred as a household design.

Preferred:

```text
Network membership determines egress path.
```

Not preferred:

```text
Same LAN, some clients manually use gateway A, others gateway B.
```

### Keep the first implementation simple

Start with:

```text
one direct network
one VPN network
one VPN tunnel
server using two NICs
```

Do not begin with:

```text
multiple VPN providers
per-domain routing
per-device routing
Tailscale exit-node routing for everyone
complex DNS split-horizon
full VLAN trunking everywhere
```

Add those only after the basic model is stable.

---

## Suggested Naming

Logical networks:

```text
direct
vpn
mgmt
guest
iot
```

OpenWrt interface names:

```text
lan_direct
lan_vpn
lan_mgmt
wan
wg_sub
ovpn_sub
tailscale0
wg_abroad
```

Firewall zones:

```text
wan
direct
vpn_clients
vpn_tunnel
mgmt
guest
iot
```

Wi-Fi SSIDs:

```text
home-direct
home-vpn
home-mgmt
home-guest
home-iot
```

Server bridges:

```text
vmbr-direct
vmbr-vpn
vmbr-mgmt
```

---

## Final Target Summary

The preferred target design is:

```text
Residential Gateway:
  bridge mode
  fiber-to-Ethernet conversion only

Flint 2 / OpenWrt:
  main router
  firewall
  DHCP/DNS authority
  Wi-Fi AP
  VLAN controller
  VPN client
  policy-based router

Direct network:
  own SSID/VLAN
  exits through WAN directly

VPN network:
  own SSID/VLAN
  exits through selected VPN tunnel only
  no fallback to WAN

Server:
  direct VM bridge connected to direct network
  VPN VM bridge connected to VPN network
  multiple physical NICs can be used first
  VLAN trunking can be introduced later
```

This gives each device a simple choice:

```text
Use direct network -> direct internet
Use VPN network    -> VPN internet
```

And each VM a similarly simple choice:

```text
Attach to direct bridge -> direct internet
Attach to VPN bridge    -> VPN internet
```

The design is compatible with:

```text
commercial WireGuard VPN
commercial OpenVPN VPN
Tailscale exit nodes
future self-hosted WireGuard server abroad
```

The recommended implementation order is:

```text
1. Residential Gateway bridge mode
2. Flint as main router
3. direct network stable
4. VPN tunnel stable
5. VPN SSID/VLAN with kill switch
6. server direct/VPN VM bridges using separate NICs
7. optional management network
8. optional VLAN trunking and additional VPN backends
```
