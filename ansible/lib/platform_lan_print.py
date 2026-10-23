"""Platform-owned RG LAN print ingress; no app-supplied router code or ports."""
import ipaddress

INPUT = 'etc/klokast/app-resources/lan-print-input.nft'
NAT = 'etc/klokast/app-resources/lan-print-nat.nft'
CONFIG = 'etc/avahi/avahi-daemon.conf'
SERVICE = 'etc/avahi/services/klokast-printer.service'


def compile_service(box, config, topology):
    subnet = ipaddress.ip_network(config.get('rg_lan', {}).get('ipv4_cidr', ''))
    if subnet.version != 4 or not subnet.is_private or not 16 <= subnet.prefixlen <= 30:
        raise ValueError('LAN printing requires a declared private RG LAN IPv4 subnet')
    for zone in [*topology['zones'].values(), *topology.get('control_zones', {}).values()]:
        if subnet.overlaps(ipaddress.ip_network(zone['cidr'])):
            raise ValueError('RG LAN subnet overlaps a Platform workload zone')
    backend = topology['zones']['bak']
    interface = topology['realms']['wan']['router_interface']
    service = dict(node=box, app='print-server', hostname=box+'-bak-printer', queue='epson',
                   subnet=str(subnet), interface=interface, backend=backend['vm_ipv4_address'], port=18631)
    common = dict(node=box, app='print-server', resource='lan-printing', source=str(subnet),
                  destination=backend['vm_ipv4_address'], ports=[18631], comment='print-server-lan-printing')
    router = dict(common, in_interface=interface, out_interface=backend['router_interface'])
    vm = dict(common, target_role=backend['role'], interface=backend['vm_interface'])
    return service, router, vm


def router_files(compiled, box):
    services = [s for s in compiled.get('lan_print_services', []) if s['node'] == box]
    if len(services) > 1:
        raise ValueError('only one LAN print service per box is supported')
    files = {INPUT: '# LAN print discovery disabled.\n', NAT: '# LAN print forwarding disabled.\n'}
    if not services:
        return files
    s = services[0]
    files[INPUT] = (f'iifname "{s["interface"]}" ip saddr {s["subnet"]} '
                    'udp dport 5353 ip ttl 255 accept comment "klokast-lan-print-mdns"\n')
    files[NAT] = ('chain lan_print_prerouting {\n'
                  ' type nat hook prerouting priority dstnat; policy accept;\n'
                  f' iifname "{s["interface"]}" ip saddr {s["subnet"]} fib daddr type local '
                  f'tcp dport 631 dnat to {s["backend"]}:{s["port"]} comment "klokast-lan-print-ipp"\n'
                  '}\n')
    files[CONFIG] = f'''[server]
host-name={s['hostname']}
use-ipv4=yes
use-ipv6=no
allow-interfaces={s['interface']}
enable-dbus=no
allow-point-to-point=no
[publish]
publish-addresses=yes
publish-hinfo=no
publish-workstation=no
publish-domain=no
[wide-area]
enable-wide-area=no
[reflector]
enable-reflector=no
'''
    # The XP-55 queue reports PDF and these URF capabilities. The LAN probe
    # checks them against IPP. _cups makes macOS choose legacy shared drivers.
    files[SERVICE] = f'''<?xml version="1.0" standalone="no"?>
<!DOCTYPE service-group SYSTEM "avahi-service.dtd">
<service-group>
 <name>Epson XP-55 ({box})</name>
 <service protocol="ipv4">
  <type>_ipp._tcp</type><subtype>_universal._sub._ipp._tcp</subtype><port>631</port>
  <txt-record>txtvers=1</txt-record><txt-record>qtotal=1</txt-record>
  <txt-record>rp=printers/{s['queue']}</txt-record><txt-record>ty=Epson XP-55 Series</txt-record>
  <txt-record>pdl=application/pdf,image/urf</txt-record>
  <txt-record>URF=V1.4,CP1,W8,PQ3-4-5,SRGB24,RS360,DM1,FN3</txt-record>
 </service>
</service-group>
'''
    return files
