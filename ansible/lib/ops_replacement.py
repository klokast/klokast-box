"""Controller-side dispatch for the existing provision-ops-vm interface."""
import argparse
import json
import os
from pathlib import Path
import pwd
import re
import socket
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parents[2]


def command(argv, *, timeout=120, stdin=None):
    return subprocess.check_output([str(v) for v in argv], input=stdin, text=True, timeout=timeout, cwd=REPO)


def selection(box, image):
    import vm_local_images
    script = 'set -eu\ncd ~/src/klokast/klokast-box\nexec ansible/bin/platform-update image-receipt --box "$1" --operation-id "$2"\n'
    payload = command(['tailscale', 'ssh', 'smith@' + box + '-ops', 'sh', '-s', '--', box, image], stdin=script)
    if len(payload.encode()) > vm_local_images.MAX_RECEIPT:
        raise RuntimeError('image receipt exceeds the public receipt limit')
    receipt = json.loads(payload)
    vm_local_images.validate(box, image, receipt['files'])
    if receipt['files']['inputs.json'].get('engine_commit') != command(['git', 'rev-parse', 'HEAD']).strip():
        raise RuntimeError('selected image was built from different controller code; prepare a qualified image from this pushed revision before replacement')
    if receipt['files']['inputs.json']['profile'] != 'ops-alpine-v1':
        raise RuntimeError('selected image is not a qualified controller image')
    return receipt


def authority(box):
    import platform_source
    platform_source.require_controller()
    source = json.loads(command(['/usr/local/sbin/platform-source', 'controllers']))
    pair = source['controllers']
    local = socket.gethostname().split('.')[0]
    if (pwd.getpwuid(os.geteuid()).pw_name != 'smith' or
            pair['active']['hostname'] != local or pair.get('standby', {}).get('box') != box or
            local == box + '-ops'):
        raise RuntimeError('replace only the Instance standby from its active peer as smith; hand off authority first')
    return pair['active']['box']


def execute(args):
    import platform_resource_runtime as runtime
    import platform_resource_model as model
    with runtime.vm_update_installation_lock():
        active = authority(args.box)
        variables = {'ops_replace_box': args.box, 'ops_replace_active_box': active,
                     'ops_replace_action': args.action, 'ops_replace_image': args.image or '',
                     'ops_replace_operation': args.resume or args.rollback or '',
                     'ops_replace_expected_lv_uuid': args.expected_lv_uuid or '',
                     'ops_replace_expected_config_sha256': args.expected_config_sha256 or ''}
        if args.action == 'replace':
            variables['ops_replace_receipt'] = selection(args.box, args.image)
            topology = model.load_topology(repo_root=REPO)['control_zones']['ops']
            account_script = "import json,pwd; print(json.dumps({n: [pwd.getpwnam(n).pw_uid, pwd.getpwnam(n).pw_gid] for n in ('smith','minion')}))"
            accounts = json.loads(command(['tailscale', 'ssh', 'smith@' + args.box + '-ops', 'python3', '-'], stdin=account_script))
            variables['ops_replace_configuration'] = {
                'kind': 'klokast.infrastructure-config.v1', 'box': args.box, 'role': 'ops',
                'bridge': topology['bridge'], 'address': topology['vm_ipv4_address'] + '/' + str(topology['router_ipv4_prefix']),
                'gateway': topology['router_ipv4_address'], 'bootstrap_source': topology['dom0_ipv4_address'],
                'public_key': (Path.home() / '.ssh/github-klokast-codex.pub').read_text().strip(),
                'agent_uid': 1004, 'agent_gid': 1004, 'active_box': active, 'accounts': accounts,
                'engine_commit': command(['git', 'rev-parse', 'HEAD']).strip(),
                'instance_commit': command(['git', '-C', Path.home() / 'private/klokast/instance', 'rev-parse', 'HEAD']).strip()}
        variables['ops_instance_repo_url'] = command(['git', '-C', Path.home() / 'private/klokast/instance', 'remote', 'get-url', 'origin']).strip()
        with tempfile.TemporaryDirectory(prefix='ops-replacement-') as temporary:
            path = Path(temporary) / 'vars.json'; path.write_text(json.dumps(variables)); path.chmod(0o600)
            result = subprocess.run(['ansible-playbook', '-vv', '-i', str(REPO / 'ansible/execution-inventory/hosts'),
                                     str(REPO / 'ansible/playbooks/65-ops-replace.yml'),
                                     '--limit', args.box, '-e', '@' + str(path)], cwd=REPO, timeout=7200,
                                    env=dict(os.environ, ANSIBLE_CONFIG=str(REPO / 'ansible/ansible.cfg')))
            if result.returncode:
                raise RuntimeError('replacement stopped; read the protected log and resume the recorded dom0 operation')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--box', required=True)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument('--replace-existing', action='store_true')
    actions.add_argument('--resume')
    actions.add_argument('--rollback')
    actions.add_argument('--adopt-existing', action='store_true')
    parser.add_argument('--image')
    parser.add_argument('--expected-lv-uuid')
    parser.add_argument('--expected-config-sha256')
    parser.add_argument('--dry-run-plan', action='store_true')
    parser.add_argument('--controller-job', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not re.fullmatch('[a-z0-9][a-z0-9-]{0,30}', args.box): parser.error('--box must be a box DNS label')
    for value in (args.image, args.resume, args.rollback):
        if value is not None and not re.fullmatch('[0-9a-f]{24}', value): parser.error('image and operation IDs must contain 24 lowercase hex characters')
    if bool(args.image) != args.replace_existing: parser.error('--image is required only with --replace-existing')
    if args.adopt_existing:
        if not args.expected_lv_uuid or not re.fullmatch('[0-9a-f]{64}', args.expected_config_sha256 or ''):
            parser.error('adoption requires --expected-lv-uuid and --expected-config-sha256 from reviewed live inspection')
    elif args.expected_lv_uuid or args.expected_config_sha256:
        parser.error('exact legacy identity inputs are only valid for adoption')
    args.action = 'replace' if args.replace_existing else ('resume' if args.resume else ('rollback' if args.rollback else 'adopt'))
    if args.dry_run_plan:
        print(json.dumps({'action': args.action, 'box': args.box, 'image': args.image,
                          'operation': args.resume or args.rollback, 'execution': 'active peer as smith',
                          'image_preparation': 'separate; no build or download during replacement',
                          'record': '/mnt/dom0_data/klokast-infrastructure/ops/replacement.json',
                          'rollback': 'refuse after replacement boot unless newer private state is reconciled',
                          'runner': 'unchanged'}, indent=2))
        return 0
    try:
        authority(args.box)
        if args.controller_job:
            execute(args)
        else:
            directory = Path.home() / 'private/klokast/logs/provision-ops-vm'
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            descriptor, name = tempfile.mkstemp(prefix=args.box + '-replacement-', suffix='.log', dir=directory)
            with os.fdopen(descriptor, 'w') as log:
                child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()),
                                          *(sys.argv[1:] if argv is None else argv), '--controller-job'],
                                         cwd=REPO, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                         start_new_session=True, close_fds=True)
            print(json.dumps({'state': 'job-started', 'complete': False, 'pid': child.pid, 'log': name}))
        return 0
    except (RuntimeError, OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print('provision-ops-vm: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__': raise SystemExit(main())
