"""Platform-owned shared VM web egress. No credentials or execution authority."""
import hashlib
from urllib.parse import urlsplit
import re
from platform_resource_model import bootstrap_dns
import routing_profile

# These are provider transport ports, not client destination ports.
RELAY_PORTS = [443, 16616, 16617, 16618, 16622, 16626, 16632, 16641, 16644, 16645, 16648]
RELAY_UDP_PORTS = [1443]
SUBSCRIPTION_PORTS = [443, 5000]
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


def compile_network(registry, configs, topology, *, bootstrap_boxes=(), installed_profiles=None):
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
        add('subscription', zone['router_interface'], wan, address, '', 'tcp',
            [port for port in SUBSCRIPTION_PORTS if port not in RELAY_PORTS])
        add('http', zone['router_interface'], wan, address, '', 'tcp', [80])
        add('relay-quic', zone['router_interface'], wan, address, '', 'udp', RELAY_UDP_PORTS)
        for protocol in ('tcp', 'udp'):
            for resolver in (routing_profile.validate(installed_profiles[box])['bootstrap-dns']
                             if installed_profiles and box in installed_profiles else bootstrap_dns(config)):
                add('dns', zone['router_interface'], wan, address, resolver, protocol, [53])
        add('tailscale', zone['router_interface'], wan, address, '', 'udp', [3478, 41641])
        for client in selected:
            if client['role'] == 'ops':
                continue  # Explicit downloads use the existing Tailnet SSH path.
            if client['interface'] != zone['router_interface']:
                add('client-' + client['role'], client['interface'], zone['router_interface'], client['address'], address, 'tcp', [allocation['port']])
                for protocol in ('tcp', 'udp'):
                    add('client-dns-' + client['role'], client['interface'], zone['router_interface'], client['address'], address, protocol, [53])
        if box in bootstrap_boxes:
            bak = topology['zones']['bak']
            add('bootstrap', bak['router_interface'], zone['router_interface'], bak['dom0_ipv4_address'], address, 'tcp', [22])
    if set(bootstrap_boxes) - {g['node'] for g in guests}:
        raise ValueError('VPN bootstrap requires a declared gateway')
    return guests, rules, vm_rules


def routing_rules(profile):
    rules = ['DOMAIN-SUFFIX,' + domain + ',REJECT' for domain in ('localhost', 'local', 'lan', 'ts.net', 'tailscale.com')]
    # Omit no-resolve: domain destinations must also be checked after resolution.
    rules += ['IP-CIDR,' + network + ',REJECT' for network in PRIVATE4]
    rules += ['IP-CIDR6,' + network + ',REJECT' for network in PRIVATE6]
    if routing_profile.validate(profile)['domain-list-url'] is not None:
        rules += ['RULE-SET,selected,VPN']
    return rules + ['MATCH,DIRECT']


def rule_providers(profile):
    return routing_profile.providers(profile)


def relay_health_check():
    # enable controls the scheduler; explicit checks after dial failures still work.
    return {'enable': False, 'interval': 0, 'url': RELAY_TEST_URL,
            'timeout': 5000, 'expected-status': '204'}


def relay_group():
    # Use only provider members. With direct `proxies`, Mihomo replaces interval
    # zero with 300 seconds for url-test groups. Do not restore a manual selection:
    # that would override the lowest-latency result while that relay is alive.
    return {'name': 'VPN', 'type': 'url-test', 'use': ['relays'],
            'interval': 0, 'url': RELAY_TEST_URL, 'expected-status': '204',
            'max-failed-times': 2, 'timeout': 60000, 'tolerance': 0,
            'disable-udp': True}


# All other protocols accepted by the pinned Mihomo v1.19.25 parser.
# Review this list when qualifying a new binary. Unsupported future types fail parsing.
RELAY_EXCLUDED_TYPES = ('ssr|socks5|http|snell|hysteria|wireguard|tuic|gost-relay|'
                        'direct|dns|reject|ssh|mieru|anytls|sudoku|masque|trusttunnel|openvpn|tailscale')
SUBSCRIPTION_SIZE_LIMIT = 4 * 1024 * 1024
SUBSCRIPTION_USER_AGENT = 'clash.meta'


def relay_provider(url):
    try:
        parsed = urlsplit(url)
        valid = (parsed.port in (None, *SUBSCRIPTION_PORTS) and parsed.username is None and
                 parsed.password is None and routing_profile.public_https(parsed._replace(netloc=parsed.hostname or '').geturl()))
    except (ValueError, TypeError, AttributeError):
        valid = False
    if not valid:
        raise ValueError('VPN subscription requires a public HTTPS URL on a Platform subscription port')
    return {'type': 'http', 'url': url,
            'path': './proxies/' + hashlib.sha256(url.encode()).hexdigest() + '.yaml',
            'interval': 86400, 'proxy': 'DIRECT', 'size-limit': SUBSCRIPTION_SIZE_LIMIT,
            'header': {'User-Agent': [SUBSCRIPTION_USER_AGENT]},
            'exclude-type': RELAY_EXCLUDED_TYPES,
            'exclude-filter': '(?i)^(DIRECT|REJECT|VPN|COMPATIBLE|GLOBAL)$',
            'override': {'udp': False, 'dialer-proxy': '', 'interface-name': '', 'routing-mark': 0},
            # Prevent native COMPATIBLE/DIRECT fallback if startup cannot load a list.
            # The service firewall rejects this loopback target. Native data replaces it.
            'payload': [{'name': 'SUBSCRIPTION-UNAVAILABLE', 'type': 'ss',
                         'server': '127.0.0.1', 'port': 1, 'cipher': 'aes-128-gcm',
                         'password': 'unavailable', 'udp': False}],
            'health-check': relay_health_check()}


def render_config(subscription_url, address, client_addresses, secret, *, profile, controller_tunnel=False):
    provider = relay_provider(subscription_url)
    if not isinstance(secret, str) or len(secret) < 24:
        raise ValueError('controller secret is too short')
    profile = routing_profile.validate(profile)
    resolvers = profile['bootstrap-dns']
    return {'mixed-port': 7890, 'allow-lan': True, 'bind-address': address,
            'lan-allowed-ips': ([value + '/32' for value in client_addresses] +
                                ([address + '/32'] if controller_tunnel else [])) or ['192.0.2.1/32'],
            'mode': 'rule', 'ipv6': False, 'log-level': 'warning', 'find-process-mode': 'off',
            'external-controller': '127.0.0.1:19090', 'secret': secret,
            'tun': {'enable': False}, 'profile': {'store-selected': False},
            # Force ordinary queries through VPN. Relay bootstrap is independent
            # of the tunnel and cannot recurse through the gateway's system DNS.
            'dns': {'enable': True, 'ipv6': False, 'enhanced-mode': 'redir-host',
                    'listen': address + ':1053', 'use-system-hosts': False,
                    'default-nameserver': resolvers,
                    'nameserver': [v+'#VPN' for v in profile['vpn-dns']],
                    'proxy-server-nameserver': resolvers,
                    'nameserver-policy': {urlsplit(subscription_url).hostname: resolvers}},
            'proxy-providers': {'relays': provider},
            'proxy-groups': [relay_group()],
            'rule-providers': rule_providers(profile), 'rules': routing_rules(profile)}
