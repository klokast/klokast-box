"""VPN artifacts, approved runtime variables and network orchestration."""
import gzip
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import urllib.request
import yaml
import platform_resource_compiler as compiler
import platform_vpn_egress as vpn
import routing_profile
REGISTRY = Path('/home/smith/private/klokast/platform-resources.yml')
PRIVATE = Path('/home/smith/private/klokast/openclaw-vpn.yml')
CACHE = Path('/home/smith/private/klokast/vpn-egress')


def command(argv, capture=True, *, repo):
    result = subprocess.run([str(a) for a in argv], cwd=repo, text=True,
                            stdout=subprocess.PIPE if capture else None, timeout=7200)
    if result.returncode:
        raise RuntimeError('command failed: ' + str(argv[0]) + '; inspect the controller log')
    return result.stdout


def ansible(name, hosts, variables, *, repo, inventory=None):
    with tempfile.TemporaryDirectory(prefix='vpn-egress-') as directory:
        path = Path(directory) / 'vars.json'
        path.write_text(json.dumps(variables)); path.chmod(0o600)
        command(['ansible-playbook', '-vv', '-i', inventory or repo / 'ansible/execution-inventory/hosts',
                 repo / 'ansible/playbooks' / name, '--limit', hosts, '-e', '@' + str(path)], capture=False, repo=repo)


def network(box, view, bootstrap=False, *, repo):
    compiled = compiler.compile_registry(REGISTRY, [], registry_input=view, repo_root=repo,
                                        vpn_bootstrap_boxes=[box] if bootstrap else [])
    ansible('69-air-network.yml', box + '-router,' + box + '-ops', {
        'platform_resources_desired_json': json.dumps(compiled),
        'platform_resources_registry_sha256': compiled['registry_sha256'],
        'platform_resources_compiler_version': compiled['compiler_version'],
        'airunner_controller_hostname': socket.gethostname().split('.')[0]}, repo=repo)


def private_config(refresh=False, subscription_ref='openclaw-vpn'):
    if not vpn.valid_subscription_ref(subscription_ref):
        raise RuntimeError('invalid VPN subscription secret reference')
    private = PRIVATE.with_name(subscription_ref + '.yml')
    cache = CACHE if subscription_ref == 'openclaw-vpn' else CACHE / subscription_ref
    if private.is_symlink() or not private.is_file() or private.stat().st_mode & 0o077:
        raise RuntimeError('VPN subscription record must be a protected regular file')
    value = yaml.safe_load(private.read_text())
    if value.get('schema_version') != 1 or value.get('enabled') is not True:
        raise RuntimeError('existing VPN subscription is not enabled')
    cache.mkdir(mode=0o700, parents=True, exist_ok=True)
    for name, section, url in [('mihomo.gz', 'mihomo', value['mihomo']['download_url']),
                               ('subscription.yml', 'subscription', value['subscription_url'])]:
        path = cache / name
        if not path.exists() or (refresh and section == 'subscription'):
            if not url.startswith('https://'):
                raise RuntimeError('VPN downloads require HTTPS')
            try:
                with urllib.request.urlopen(url, timeout=90) as response:
                    data = response.read(64 * 1024 * 1024 + 1)
                if len(data) > 64 * 1024 * 1024:
                    raise RuntimeError('VPN download exceeds size limit')
            except Exception:
                raise RuntimeError('VPN download failed; private URL was withheld') from None
            if section == 'mihomo' and hashlib.sha256(data).hexdigest() != value[section]['sha256']:
                raise RuntimeError('VPN binary checksum differs from the pinned private artifact')
            if section == 'subscription':
                vpn.validated_proxies(yaml.safe_load(data))
            temporary = cache / (name + '.new')
            temporary.write_bytes(data); temporary.chmod(0o600); temporary.replace(path)
        if section == 'mihomo' and hashlib.sha256(path.read_bytes()).hexdigest() != value[section]['sha256']:
            raise RuntimeError('VPN binary cache failed checksum verification')
        if section == 'subscription' and not refresh and hashlib.sha256(path.read_bytes()).hexdigest() != value[section]['sha256']:
            # A refreshed cache is validated structurally on each deployment.
            marker = cache / 'subscription.sha256'
            if not marker.exists() or marker.read_text().strip() != hashlib.sha256(path.read_bytes()).hexdigest():
                raise RuntimeError('VPN subscription cache failed checksum verification')
    if refresh:
        marker = cache / 'subscription.sha256'
        marker.write_text(hashlib.sha256((cache / 'subscription.yml').read_bytes()).hexdigest() + '\n'); marker.chmod(0o600)
    with gzip.open(cache / 'mihomo.gz', 'rb') as stream:
        binary = stream.read(128 * 1024 * 1024 + 1)
    if len(binary) > 128 * 1024 * 1024 or not binary.startswith(b'\x7fELF'):
        raise RuntimeError('VPN binary archive is not a bounded ELF executable')
    path = cache / 'mihomo'
    if not path.exists() or path.read_bytes() != binary:
        path.write_bytes(binary); path.chmod(0o600)
    return value, yaml.safe_load((cache / 'subscription.yml').read_text()), path


def variables(box, config, topology, profile=None):
    profile = routing_profile.validate(profile or config["routing_profile"])
    selected = vpn.clients(box, config, topology)
    allocation = topology['vpn_egress']
    proxy = 'http://' + allocation['ipv4_address'] + ':' + str(allocation['port'])
    bypass = 'localhost,127.0.0.1,::1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,100.64.0.0/10,.ts.net,.local,.lan'
    return {'infrastructure_box': box, 'vpn_egress_client_addresses': [v['address'] for v in selected],
            'vpn_egress_bootstrap_dns': profile['bootstrap-dns'],
            'vpn_egress_expected_vpn_dns': [v+'#VPN' for v in profile['vpn-dns']],
            'vpn_egress_dns_address': allocation['ipv4_address'],
            'vpn_egress_private4': vpn.PRIVATE4,
            'vpn_egress_expected_rules': vpn.routing_rules(profile),
            'vpn_egress_expected_rule_providers': vpn.rule_providers(profile),
            'vpn_egress_expected_relay_group': vpn.relay_group(),
            'vpn_egress_expected_relay_health_check': vpn.relay_health_check(),
            'vpn_egress_environment': {'http_proxy': proxy, 'https_proxy': proxy, 'HTTP_PROXY': proxy,
                                       'HTTPS_PROXY': proxy, 'no_proxy': bypass, 'NO_PROXY': bypass},
            'vpn_egress_controller_hostname': socket.gethostname().split('.')[0]}


def client_hosts(box, config, topology, *, repo):
    selected = {v['hostname'] for v in vpn.clients(box, config, topology)}
    status = json.loads(command(['tailscale', 'status', '--json'], repo=repo))
    peers = list(status.get('Peer', {}).values()) + [status.get('Self', {})]
    candidates = {box + '-' + role for role in ('ops', 'air', 'bak', 'dmz', 'iot')}
    # Converge all online supported VMs so removing a client also removes its setting.
    return sorted(selected | {v.get('HostName') for v in peers if v.get('Online') and v.get('HostName') in candidates})



def creation_config(box, topology, profile):
    """The public personalization request used for creation and reconstruction."""
    zone = topology['zones']['dmz']
    return {'kind': 'klokast.infrastructure-config.v1', 'box': box, 'role': 'vpn-egress',
            'bridge': zone['bridge'], 'address': topology['vpn_egress']['ipv4_address'] + '/' + str(zone['router_ipv4_prefix']),
            'gateway': zone['router_ipv4_address'], 'routing_profile': profile,
            'bootstrap_dns': profile['bootstrap-dns'], 'bootstrap_source': topology['zones']['bak']['dom0_ipv4_address'],
            'public_key': (Path.home()/'.ssh/github-klokast-codex.pub').read_text().strip(),
            'agent_uid': 1004, 'agent_gid': 1004}


def domain_list(profile):
    """Fetch and validate the selected domain provider before stopping a VM."""
    url = routing_profile.validate(profile)['domain-list-url']
    if url is None:
        return None
    try:
        with urllib.request.urlopen(url, timeout=90) as response:
            if not routing_profile.public_https(response.geturl()):
                raise RuntimeError('domain list redirected to an unsupported URL')
            data = response.read(4 * 1024 * 1024 + 1)
        validate_domain_list(data)
    except Exception:
        raise RuntimeError('required VPN domain list is unavailable or invalid') from None
    CACHE.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = CACHE / ('domains-' + hashlib.sha256(data).hexdigest() + '.yaml')
    if not path.exists():
        with path.open('xb') as stream:
            path.chmod(0o600); stream.write(data)
    if path.is_symlink() or path.read_bytes() != data:
        raise RuntimeError('domain list cache differs from the verified artifact')
    return path


def validate_domain_list(data):
    import re
    if len(data) > 4 * 1024 * 1024:
        raise ValueError('domain list exceeds its size limit')
    value = yaml.safe_load(data)
    if not isinstance(value, dict) or set(value) != {'payload'} or not isinstance(value['payload'], list) or not value['payload']:
        raise ValueError('domain list requires a nonempty payload')
    for item in value['payload']:
        if not isinstance(item, str) or len(item) > 255 or not re.fullmatch(r'(?:\+\.|\*\.|\.)?[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*', item):
            raise ValueError('domain provider contains an unsupported domain entry')
    return value


def prepare_configuration(box, config, topology, profile, *, refresh=False):
    try:
        private, subscription, binary = private_config(refresh, config['vpn_egress'].get('subscription_ref', 'openclaw-vpn'))
    except yaml.YAMLError:
        raise RuntimeError('private VPN inputs are invalid; parser details were withheld') from None
    values = variables(box, config, topology, profile)
    values.update(vpn_egress_binary=str(binary), vpn_egress_config=vpn.render_config(
        subscription, topology['vpn_egress']['ipv4_address'], values['vpn_egress_client_addresses'],
        private['controller']['secret'], profile=profile))
    selected = domain_list(profile)
    if selected:
        values['vpn_egress_domain_list'] = str(selected)
        values['vpn_egress_domain_path'] = next(iter(routing_profile.providers(profile).values()))['path'].removeprefix('./')
    return values


def configure(box, values, *, repo, inventory=None):
    return ansible('69-vpn-egress-runtime.yml', box+'-vpn-egress', values, repo=repo, inventory=inventory)
