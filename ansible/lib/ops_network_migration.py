"""Controller-side orchestration for the explicit ops network migration."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'ansible/roles/vm-template-builder/files'))
import ops_network


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--box', required=True)
    parser.add_argument('--network-only', action='store_true')
    choice = parser.add_mutually_exclusive_group()
    choice.add_argument('--status', action='store_true')
    choice.add_argument('--rollback', action='store_true')
    choice.add_argument('--resume', action='store_true')
    parser.add_argument('--dry-run-plan', action='store_true')
    args = parser.parse_args(argv)
    import platform_source as source
    import platform_resource_model as model
    import platform_resource_runtime as runtime
    source.require_controller()
    runtime.require_active_controller()
    view = source.snapshot()
    if args.box not in {v['box'] for v in view['instance']['controllers'].values() if isinstance(v, dict)}:
        # Instance controllers can also use their canonical string representation.
        pair = json.loads(subprocess.check_output(['/usr/local/sbin/platform-source', 'controllers'], text=True))['controllers']
        if args.box not in {v['box'] for v in pair.values() if isinstance(v, dict)}:
            raise RuntimeError('network migration requires a declared controller box')
    config = view['rendered']['projection']['registry']['boxes'][args.box]
    value = {'box': args.box, 'role': 'ops', 'bridge': 'br-ops', 'address': '192.168.125.10/24',
             'bootstrap_source': '192.168.125.1', 'bootstrap_dns': model.bootstrap_dns(config),
             'uplink': ops_network.uplink(args.box)}
    action = 'status' if args.status else 'rollback' if args.rollback else 'apply'
    variables = {'ops_network_box': args.box, 'ops_network_configuration': value, 'ops_network_action': action}
    if args.dry_run_plan:
        print(json.dumps(variables, sort_keys=True)); return 0
    with runtime.vm_update_installation_lock(), tempfile.TemporaryDirectory(prefix='ops-network-') as temporary:
        path = Path(temporary) / 'vars.json'; path.write_text(json.dumps(variables)); path.chmod(0o600)
        playbooks = ['67-ops-network.yml'] + (['67-ops-network-router.yml'] if action == 'apply' else [])
        for playbook in playbooks:
            subprocess.run(['ansible-playbook', '-vv', '-i', str(REPO / 'ansible/execution-inventory/hosts'),
                        str(REPO / 'ansible/playbooks' / playbook), '-e', '@' + str(path)],
                       env=dict(os.environ, ANSIBLE_CONFIG=str(REPO / 'ansible/ansible.cfg')), cwd=REPO, check=True)
    return 0


if __name__ == '__main__':
    try: sys.exit(main())
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        print('ops-network: ' + str(error) + '; inspect the recorded phase and use --resume or --rollback.', file=sys.stderr)
        sys.exit(1)
