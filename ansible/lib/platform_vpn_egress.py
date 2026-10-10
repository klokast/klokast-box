"""Platform-owned shared VM web egress. No credentials or execution authority."""
import copy
import ipaddress
import re

# These are provider transport ports, not client destination ports.
RELAY_PORTS = [443, 16616, 16617, 16618, 16622, 16626, 16632, 16641, 16644, 16645, 16648]
RELAY_UDP_PORTS = [1443]
GFW_RULES_URL = 'https://raw.githubusercontent.com/Loyalsoldier/clash-rules/release/gfw.txt'
RELAY_TEST_URL = 'https://www.gstatic.com/generate_204'
PRIVATE4 = ['0.0.0.0/8', '10.0.0.0/8', '100.64.0.0/10', '127.0.0.0/8',
            '169.254.0.0/16', '172.16.0.0/12', '192.168.0.0/16',
            '198.18.0.0/15', '224.0.0.0/4', '240.0.0.0/4']
PRIVATE6 = ['::/128', '::1/128', '::ffff:0:0/96', 'fc00::/7', 'fe80::/10', 'ff00::/8']


def valid_subscription_ref(value):
    return isinstance(value, str) and re.fullmatch('[a-z][a-z0-9-]{0,62}', value) is not None


def clients(box, config, topology):
    value = config.get('vpn_egress')
    enabled = 'vpn-egress' in config.get('access', {}).get('enabled_capabilities', [])
    if value is not None and not enabled:
        raise ValueError('vpn-egress capability and clients must be declared together')
    if value is None:
        return []
    if not isinstance(value, dict) or set(value) - {'clients', 'subscription_ref'} or 'clients' not in value or not isinstance(value['clients'], list):
        raise ValueError('vpn-egress requires a clients list')
    if not valid_subscription_ref(value.get('subscription_ref', 'openclaw-vpn')):
        raise ValueError('invalid VPN subscription secret reference')
    result, seen = [], set()
    for name in value['clients']:
        if not isinstance(name, str) or name in seen:
            raise ValueError('VPN clients must be unique VM names')
        seen.add(name)
        roles = {box + '-' + role: role for role in ('ops', 'air', 'bak', 'dmz', 'iot')}
        if name not in roles:
            raise ValueError('VPN client must be a supported same-box VM')
        role = roles[name]
        allocation = topology['air'] if role == 'air' else None
        zone = topology['control_zones']['ops'] if role == 'ops' else topology['zones'][allocation['zone'] if allocation else role]
        result.append({'hostname': name, 'role': role, 'address': allocation['ipv4_address'] if allocation else zone['vm_ipv4_address'],
                       'interface': zone['router_interface']})
    return result


def compile_network(registry, configs, topology, *, bootstrap_boxes=()):
    allocation = topology['vpn_egress']
    zone = topology['zones'][allocation['zone']]
    address = allocation['ipv4_address']
    if address not in zone.get('reserved_ipv4_addresses', []):
        raise ValueError('VPN egress address must be reserved')
    guests, rules, vm_rules = [], [], []
    for box, config in sorted(configs.items()):
        selected = clients(box, config, topology)
        if 'vpn_egress' not in config:
            continue
        if any(c['role'] == 'air' and c['hostname'] not in registry.get('airunners', []) for c in selected):
            raise ValueError('VPN client air VM must be declared')
        guests.append({'node': box, 'app': 'platform', 'resource': 'vpn-egress',
                       'hostname': box + '-vpn-egress', 'tailnet_hostname': box + '-vpn-egress',
                       'inventory_hostname': box + '-vpn-egress', 'guest_name': 'vpn-egress',
                       'user_slug': '', 'tailscale_login': 'neo', 'site_role': 'active', 'runtime_state': 'running',
                       'zone': allocation['zone'], 'vm_ipv4_address': address,
                       'memory_mb': allocation['memory_mb'], 'vcpus': allocation['vcpus'], 'autostart': True,
                       'expected_tags': ['tag:vm', 'tag:vpn-egress'], 'tailnet_tag': 'tag:vpn-egress'})
        def add(resource, incoming, outgoing, source, destination, protocol, ports):
            rules.append({'node': box, 'app': 'platform', 'resource': 'vpn-egress-' + resource,
                          'in_interface': incoming, 'out_interface': outgoing, 'source': source,
                          'destination': destination, 'protocol': protocol, 'ports': ports,
                          'exclusive': True, 'comment': 'platform-vpn-egress-' + resource})
        wan = topology['realms']['wan']['router_interface']
        add('relay', zone['router_interface'], wan, address, '', 'tcp', RELAY_PORTS)
        add('http', zone['router_interface'], wan, address, '', 'tcp', [80])
        add('relay-quic', zone['router_interface'], wan, address, '', 'udp', RELAY_UDP_PORTS)
        for protocol in ('tcp', 'udp'):
            for resolver in ('1.1.1.1', '1.0.0.1'):
                add('dns', zone['router_interface'], wan, address, resolver, protocol, [53])
        add('tailscale', zone['router_interface'], wan, address, '', 'udp', [3478, 41641])
        ops = topology['control_zones']['ops']
        add('controller-transport', zone['router_interface'], ops['router_interface'], address, ops['vm_ipv4_address'], 'udp', [41641])
        add('gateway-transport', ops['router_interface'], zone['router_interface'], ops['vm_ipv4_address'], address, 'udp', [41641])
        vm_rules.append({'node': box, 'app': 'platform', 'resource': 'vpn-egress-controller-transport',
                         'target_role': 'ops', 'interface': ops['vm_interface'], 'source': address,
                         'destination': ops['vm_ipv4_address'], 'protocol': 'udp', 'ports': [41641],
                         'exclusive': True, 'comment': 'platform-vpn-egress-controller-transport'})
        for client in selected:
            if client['interface'] != zone['router_interface']:
                add('client-' + client['role'], client['interface'], zone['router_interface'], client['address'], address, 'tcp', [allocation['port']])
        if box in bootstrap_boxes:
            bak = topology['zones']['bak']
            add('bootstrap', bak['router_interface'], zone['router_interface'], bak['dom0_ipv4_address'], address, 'tcp', [22])
    if set(bootstrap_boxes) - {g['node'] for g in guests}:
        raise ValueError('VPN bootstrap requires a declared gateway')
    return guests, rules, vm_rules


def routing_rules():
    rules = ['DOMAIN-SUFFIX,' + domain + ',REJECT' for domain in ('localhost', 'local', 'lan', 'ts.net', 'tailscale.com')]
    # Omit no-resolve: domain destinations must also be checked after resolution.
    rules += ['IP-CIDR,' + network + ',REJECT' for network in PRIVATE4]
    rules += ['IP-CIDR6,' + network + ',REJECT' for network in PRIVATE6]
    rules += ['RULE-SET,gfw,VPN']
    return rules + ['MATCH,DIRECT']


def rule_providers():
    # External domain data can select the VPN; it cannot replace Platform rules.
    return {'gfw': {'type': 'http', 'behavior': 'domain', 'format': 'yaml',
                    'url': GFW_RULES_URL, 'path': './rules/gfw.yaml',
                    'interval': 86400, 'proxy': 'VPN', 'size-limit': 4 * 1024 * 1024}}


def relay_health_check():
    # enable controls the scheduler; explicit checks after dial failures still work.
    return {'enable': False, 'interval': 0, 'url': RELAY_TEST_URL,
            'timeout': 5000, 'expected-status': '204'}


def relay_group():
    # Use only an inline provider. With direct `proxies`, Mihomo replaces interval
    # zero with 300 seconds for url-test groups. Do not restore a manual selection:
    # that would override the lowest-latency result while that relay is alive.
    return {'name': 'VPN', 'type': 'url-test', 'use': ['relays'],
            'interval': 0, 'url': RELAY_TEST_URL, 'expected-status': '204',
            'max-failed-times': 2, 'timeout': 60000, 'tolerance': 0}


def render_config(subscription, address, client_addresses, secret):
    """Accept proxy records only; provider rules, URLs, listeners and routes have no authority."""
    proxies = subscription.get('proxies')
    if not isinstance(proxies, list) or not 1 <= len(proxies) <= 512:
        raise ValueError('subscription must contain 1 to 512 explicit proxies')
    safe = []
    common = {'name', 'type', 'server', 'port', 'password', 'cipher', 'uuid', 'alterId', 'tls',
              'servername', 'sni', 'network', 'ws-opts', 'grpc-opts', 'client-fingerprint',
              'fingerprint', 'alpn', 'skip-cert-verify', 'udp', 'up', 'down', 'ws-path', 'ws-headers'}
    names = set()
    for record in proxies:
        if not isinstance(record, dict) or record.get('type') not in ('ss', 'trojan', 'vmess', 'vless', 'hysteria2'):
            raise ValueError('unsupported VPN proxy protocol')
        if set(record) - common:
            raise ValueError('subscription proxy has unsupported fields')
        name = record.get('name')
        if not isinstance(name, str) or not name or name in names or name.upper() in ('DIRECT', 'REJECT', 'VPN') or ',' in name:
            raise ValueError('subscription proxy name is invalid or duplicated')
        names.add(name)
        if type(record.get('port')) is not int or record['port'] not in (RELAY_UDP_PORTS if record['type'] == 'hysteria2' else RELAY_PORTS):
            raise ValueError('subscription relay port is not authorized by Platform')
        server = record.get('server', '')
        if not isinstance(server, str) or not re.fullmatch(r'[a-zA-Z0-9.-]{1,253}', server):
            raise ValueError('subscription relay server is invalid')
        try:
            if not ipaddress.ip_address(server).is_global:
                raise ValueError('subscription relay must use a public address')
        except ValueError:
            if re.fullmatch(r'[0-9.]+', server):
                raise ValueError('subscription relay must use a public address')
        entry = copy.deepcopy(record)
        entry['udp'] = False
        safe.append(entry)
    if not isinstance(secret, str) or len(secret) < 24:
        raise ValueError('controller secret is too short')
    return {'mixed-port': 7890, 'allow-lan': True, 'bind-address': address,
            'lan-allowed-ips': [value + '/32' for value in client_addresses] or ['192.0.2.1/32'],
            'mode': 'rule', 'ipv6': False, 'log-level': 'warning', 'find-process-mode': 'off',
            'external-controller': '127.0.0.1:19090', 'secret': secret,
            'tun': {'enable': False}, 'profile': {'store-selected': False},
            # Tailscale owns system DNS. The confined proxy must use its declared
            # public DNS flow instead of the private MagicDNS address.
            'dns': {'enable': True, 'ipv6': False, 'enhanced-mode': 'redir-host',
                    'nameserver': ['1.1.1.1', '1.0.0.1'],
                    'proxy-server-nameserver': ['1.1.1.1', '1.0.0.1']},
            'proxy-providers': {'relays': {'type': 'inline', 'payload': safe,
                                         'health-check': relay_health_check()}},
            'proxy-groups': [relay_group()],
            'rule-providers': rule_providers(), 'rules': routing_rules()}
