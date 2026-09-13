"""Run controller entry points in a disposable, relocated test checkout.

Source readers, compilers, wrapper CLI dispatch, variable generation, and
inventory parsing are real. Broker, approval, builder, and remote-execution
responses are explicit fixtures. This is dependency evidence, not live health.
No helper from this module is installed as a controller authority.
"""

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor


APPS = ('household-vpn', 'local-ingress', 'music', 'print-server', 'torrent',
        'nextcloud', 'nextcloud-v2', 'static-site', 'immich')
CONTRACT = 'controller-wrapper-commands-v1'


def stable(value, view):
    if isinstance(value, str):
        value = value.replace(str(view), '<view>')
        # Only test-created per-command temporary directories are normalized.
        value = re.sub(r'<view>/runtime/[^/\s"\']+', '<view>/runtime/<temporary>', value)
        # The real compiler stages requests in mkdtemp directories. Retain the
        # operation prefix; normalize only its generated random suffix.
        for root in ('<view>/.run/platform-resources/', '/home/neo/.cache/klokast-platform-resources/'):
            value = re.sub(re.escape(root) + r'((?:(?:shared-guests|box-access)-)?(?:apply|verify)-)[a-z0-9_]{8}',
                           lambda match: root + match[1] + '<temporary>', value)
        return value
    if isinstance(value, list):
        return [stable(v, view) for v in value]
    if isinstance(value, dict):
        return {k: stable(v, view) for k, v in value.items()}
    return value


def write_program(path, text):
    path.write_text(text)
    path.chmod(0o700)


def prepare(view, registry, inventory, controller_pair, tailnet):
    """Relocate fixed installation paths, without changing source decisions."""
    bindir = view / 'fixture-bin'
    bindir.mkdir()
    (view / 'runtime').mkdir()
    replacements = {
        '/home/smith/private/klokast': str(view / 'private'),
        '~/private/klokast': str(view / 'private'),
        '/home/smith/src/klokast/klokast-box': str(view),
        '/usr/local/sbin/platform-registry': str(view / 'ansible/bin/platform-registry'),
        '/usr/local/sbin/platform-inventory': str(view / 'ansible/bin/platform-inventory'),
        '/usr/local/sbin/klokast-controller-guard': str(bindir / 'klokast-controller-guard'),
        '/usr/bin/doas': str(bindir / 'doas'),
        '/var/lib/klokast/approved-state': str(view / 'approved-state'),
    }
    for directory in (view / 'ansible/bin', view / 'ansible/execution-inventory', view / 'apps'):
        for path in directory.rglob('*'):
            if path.is_symlink() or not path.is_file():
                continue
            try:
                original = path.read_text()
            except UnicodeError:
                continue
            changed = original
            for old, new in replacements.items():
                changed = changed.replace(old, new)
            if changed != original:
                path.write_text(changed)
    status = dict(schema_version=1, source='instance_specification_v1',
                  authority_state_sha256='a' * 64, engine_commit=registry['engine']['commit'])
    fixtures = {
        'registry-source-status': dict(status, kind='klokast.registry-source-status.v1', rendered=registry),
        'inventory-source-status': dict(status, kind='klokast.inventory-source-status.v1',
            rendered=dict(valid=True, kind='klokast.inventory.v1', projection=inventory)),
        'controller-identity-status': dict(status, kind='klokast.controller-identity-status.v1', controllers=controller_pair),
    }
    (view / 'broker-fixtures.json').write_text(json.dumps(fixtures))
    # A closed PATH excludes real SSH, privilege, provider, and build commands.
    for name in ('bash', 'sh', 'python3', 'cat', 'dirname', 'mkdir', 'mktemp', 'rm',
                 'chmod', 'install', 'touch', 'date', 'uname', 'env', 'sed', 'awk',
                 'tr', 'head', 'tail', 'sort', 'wc', 'basename', 'readlink'):
        executable = shutil.which(name)
        if executable:
            (bindir / name).symlink_to(executable)
    real_inventory = shutil.which('ansible-inventory')
    if not real_inventory:
        raise ValueError('actual wrapper absence tests require ansible-inventory')
    (bindir / 'ansible-inventory').symlink_to(real_inventory)
    dispatch = Path(__file__).with_name('consumer_absence_dispatch.py')
    for name in ('doas', 'sudo', 'git', 'hostname', 'tailscale', 'ansible-playbook',
                 'klokast-controller-guard', 'platform-image-build'):
        write_program(bindir / name, '#!' + sys.executable + '\n'
            'import runpy\nrunpy.run_path(' + repr(str(dispatch)) + ', init_globals={"PROGRAM":' + repr(name) + '}, run_name="__main__")\n')
    (bindir / 'platform-registry').symlink_to(view / 'ansible/bin/platform-registry')
    # Image production is an explicit leaf fixture, never a nested live build.
    write_program(view / 'ansible/bin/platform-image-build', '#!' + sys.executable + '\n'
        'import runpy\nrunpy.run_path(' + repr(str(dispatch)) + ', init_globals={"PROGRAM":"platform-image-build"}, run_name="__main__")\n')
    (view / '.klokast-approved-commit').write_text(registry['engine']['commit'] + '\n')
    members = {}
    for group in tailnet['groups']:
        role = {'operators': 'operator', 'family': 'family'}[group['name']]
        for member in group['members']:
            members.setdefault(member, {'roles': []})['roles'].append(role)
    instance = {'schema-version': 1, 'tailscale': {
        'tailnet-dns-name': tailnet['magicdns_suffix'], 'members': members}}
    (view / 'private/instance').mkdir(exist_ok=True)
    (view / 'private/instance/klokast-instance.json').write_text(json.dumps(instance))
    # Harmless auxiliary inputs exercise wrapper file handling, not credentials.
    (view / 'private/test-vpn.yml').write_text('{}\n')
    (view / 'private/test-cert.pem').write_text('TEST ONLY\n')
    (view / 'private/test-key.pem').write_text('TEST ONLY\n')
    environment = {
        'PATH': str(bindir), 'LANG': 'C.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1',
        'TMPDIR': str(view / 'runtime'), 'KLOKAST_ABSENCE_VIEW': str(view),
        'KLOKAST_ABSENCE_INVENTORY': real_inventory,
        'KLOKAST_MAGICDNS_SUFFIX': tailnet['magicdns_suffix'],
        'ANSIBLE_CONFIG': str(view / 'ansible/ansible.cfg'),
        'ANSIBLE_CACHE_PLUGIN': 'memory',
        'ANSIBLE_LOCAL_TEMP': str(view / 'runtime/ansible-local'),
        'ANSIBLE_INVENTORY_UNPARSED_IS_FAILED': 'true',
        'ANSIBLE_INVENTORY_ANY_UNPARSED_IS_FAILED': 'true',
        'NEXTCLOUD_V2CTL_DRY_RUN': 'true',
    }
    # Install tests get fixture values only; no inherited controller secrets.
    for name in ('NEXTCLOUD_ADMIN_PASSWORD', 'NEXTCLOUD_POSTGRES_PASSWORD',
                 'NEXTCLOUD_RESTIC_PASSWORD', 'NEXTCLOUD_RESTIC_REPOSITORY',
                 'NEXTCLOUD_CLOUDFLARED_TOKEN_ACTIVE', 'NEXTCLOUD_CLOUDFLARED_TOKEN_PASSIVE',
                 'IMMICH_POSTGRES_PASSWORD', 'IMMICH_RESTIC_PASSWORD', 'IMMICH_RESTIC_REPOSITORY',
                 'STATIC_SITE_CLOUDFLARED_TOKEN'):
        environment[name] = 'isolated-test-only'
    return environment


def run_commands(view, registry, inventory, controller_pair, tailnet):
    env = prepare(view, registry, inventory, controller_pair, tailnet)
    def private_snapshot():
        return {str(p.relative_to(view / 'private')): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mode & 0o777)
                for p in (view / 'private').rglob('*') if p.is_file()}
    private_before = private_snapshot()
    records = {}
    traces = view / 'command-traces'
    traces.mkdir()
    apps = registry['projection']['registry'].get('apps', {})
    boxes = inventory['boxes']

    def invoke(label, command, expected='success'):
        trace = traces / (hashlib.sha256(label.encode()).hexdigest() + '.jsonl')
        trace.write_text('')
        command_env = dict(env, KLOKAST_ABSENCE_TRACE=str(trace))
        result = subprocess.run([str(x) for x in command], cwd=view, env=command_env,
                                text=True, capture_output=True, stdin=subprocess.DEVNULL, timeout=120)
        log = result.stdout + result.stderr
        if expected == 'success' and result.returncode:
            raise ValueError(f'{label}: wrapper failed ({result.returncode}): {log[-4000:]}')
        if expected == 'write-refusal':
            if not result.returncode or 'publish instance intent' not in log:
                raise ValueError(f'{label}: legacy write did not refuse before file access: {log[-2000:]}')
        if expected == 'undeclared':
            if not result.returncode or not any(message in log for message in (
                    'registry has no app entry', 'must be enabled', 'grant is missing')):
                raise ValueError(f'{label}: missing app did not produce a source-aware refusal: {log[-2000:]}')
        events = [json.loads(line) for line in trace.read_text().splitlines()]
        if expected == 'write-refusal' and any(e['boundary'] != 'source-broker' for e in events):
            raise ValueError(f'{label}: legacy write reached a runtime boundary')
        records[label] = stable(dict(command=[str(x) for x in command], result=expected,
                                    stdout=result.stdout.strip(), events=events), view)
        return result

    compiler = view / 'ansible/bin/platform-resources'
    def app_commands(app):
        entry = apps.get(app) or {}
        placement = entry.get('placement') or {}
        app_boxes = placement.get('boxes') or [placement.get('active_master') or boxes[0]]
        box = app_boxes[0]
        active = placement.get('active_master') or box
        passive = placement.get('passive_backup') or next(b for b in boxes if b != active)
        pair = ['--active-master', active, '--passive-backup', passive]
        ctl = view / f'apps/{app}/bin/{app}ctl'
        common = ['--box', box]
        if app in ('nextcloud', 'immich', 'nextcloud-v2'):
            common = pair
        if app in ('local-ingress',):
            common += ['--local-domain', 'home.example.test']
        if app in ('nextcloud', 'static-site'):
            common += ['--domain', 'app.example.test']
        enabled = bool(entry.get('enabled'))
        expectation = 'success' if enabled else 'undeclared'
        if app == 'nextcloud-v2':
            # The normal registry-side wrapper must run, even for absent apps.
            invoke(app + '/infra-prepare', [ctl, 'infra-prepare', *pair], expectation)
            if not enabled:
                return
            common += ['--resource-grant', view / 'approved-state/apps/nextcloud-v2/grant.json']
        for operation in ('verify', 'install'):
            args = list(common)
            if operation == 'install' and app in ('torrent', 'household-vpn'):
                args += ['--vpn-config', view / 'private/test-vpn.yml']
            if app == 'household-vpn' and operation == 'install':
                args += ['--local-domain', 'home.example.test']
            if app == 'local-ingress' and operation == 'install':
                args += ['--tls-cert', view / 'private/test-cert.pem', '--tls-key', view / 'private/test-key.pem']
            invoke(app + '/' + operation, [ctl, operation, *args], expectation)
        if app in ('household-vpn', 'local-ingress', 'torrent'):
            invoke(app + '/status', [ctl, 'status', *common], expectation)
        if app in ('music', 'print-server'):
            invoke(app + '/preflight', [ctl, 'preflight', *common], expectation)

    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(app_commands, APPS))
    appctl = view / 'ansible/bin/platform-app'
    guest = view / 'ansible/bin/platform-guest'
    invoke('platform-app/list', [appctl, 'list'])
    for app in apps:
        invoke('platform-app/status/' + app, [appctl, 'status', app])
    for operation in ('start', 'stop', 'restart', 'remove', 'destroy'):
        args = [appctl, operation, 'nextcloud-v2']
        if operation == 'destroy':
            args += ['--yes', '--wipe-data']
        invoke('platform-app/' + operation, args, 'write-refusal')
    def box_commands(box):
        for operation in ('list', 'verify', 'apply'):
            invoke('platform-guest/' + box + '/' + operation, [guest, operation, '--box', box])
        for operation in ('start', 'stop'):
            invoke('platform-guest/' + box + '/' + operation,
                   [guest, operation, '--box', box, '--role', 'bak'], 'write-refusal')
        for target in ('dom0', 'router', 'podman', 'ops', 'resources'):
            invoke('platform-check/' + box + '/' + target,
                   [view / 'ansible/bin/platform-check', '--box', box, '--target', target])
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(box_commands, boxes))
    # The legacy Immich destroy path must refuse before reading or changing files.
    invoke('immich/destroy', [view / 'apps/immich/bin/immichctl', 'destroy',
           '--active-master', boxes[0], '--passive-backup', boxes[1], '--yes', '--wipe-data'], 'write-refusal')
    alternate = view / 'private/alternate.yml'
    alternate.write_text('schema_version: 1\napps: {}\n')
    alternate_env = dict(env, KLOKAST_ABSENCE_TRACE=str(traces / 'alternate.jsonl'))
    result = subprocess.run([str(view / 'ansible/bin/platform-registry'), 'read', '--registry', str(alternate)],
                            cwd=view, env=alternate_env, stdin=subprocess.DEVNULL, text=True, capture_output=True)
    if not result.returncode or 'registry overrides cannot replace the adopted private-instance source' not in result.stderr:
        raise ValueError('normal source reader did not refuse an alternate registry')
    records['registry/alternate-refusal'] = {'result': 'adopted-source-refusal'}
    invoke('compiler/explicit-compatibility', [compiler, '--registry', alternate, '--compatibility-registry', 'show'])
    alternate.unlink()
    invoke('tailnet/render', [view / 'ansible/bin/render-tailscale-policy', '--instance',
           view / 'private/instance/klokast-instance.json', '--output', view / 'policy.hujson'])
    records['tailnet/render']['policy_sha256'] = hashlib.sha256((view / 'policy.hujson').read_bytes()).hexdigest()
    if private_snapshot() != private_before:
        raise ValueError('controller command changed private input bytes, modes, or file set')
    return dict(contract=CONTRACT, commands=records,
                simulated_boundaries=['source-broker', 'approval', 'builder', 'runtime-dispatch'],
                live_execution_authority=False)
