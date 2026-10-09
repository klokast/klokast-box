"""Prepare infrastructure images only on the target box controller."""
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import vm_local_images
from platform_updates import no_application_release


def prepare_image(box, profile, *, repo):
    REPO = Path(repo)
    def command(argv):
        return subprocess.check_output([str(v) for v in argv], cwd=REPO, text=True, timeout=7200)
    if not vm_local_images.BOX.fullmatch(box) or profile not in ('air-alpine-v1', 'ops-alpine-v1', 'vpn-egress-alpine-v1'):
        raise RuntimeError('unsupported infrastructure image box or profile')
    if socket.gethostname().split('.')[0] != box + '-ops':
        # Start only on the existing local controller. There is no download VM,
        # active-controller build fallback, or cross-box disk transfer.
        def remote(arguments):
            script = 'set -eu\ncd /home/smith/src/klokast/klokast-box\nexec ansible/bin/platform-update "$@"\n'
            with tempfile.TemporaryFile(mode='w+') as output:
                process = subprocess.run(['tailscale', 'ssh', 'smith@' + box + '-ops', 'sh', '-s', '--', *arguments],
                                         input=script, text=True, stdout=output, timeout=7200, check=False)
                output.seek(0)
                payload = output.read(vm_local_images.MAX_RECEIPT + 1)
            if process.returncode or len(payload.encode()) > vm_local_images.MAX_RECEIPT:
                raise RuntimeError('local image preparation on ' + box + '-ops failed; inspect its build log; no fallback was used')
            return json.loads(payload)
        result = remote(['prepare', '--box', box, '--profile', profile])
        operation = result.get('operation_id', '')
        if result.get('state') not in ('candidate-built', 'candidate-reused') or not vm_local_images.OP.fullmatch(operation):
            raise RuntimeError('local controller did not return a qualified image')
        receipt = remote(['image-receipt', '--box', box, '--operation-id', operation])
        if receipt['files']['inputs.json']['profile'] != profile:
            raise RuntimeError('local controller returned a different image profile')
        import fcntl
        state = Path('/var/lib/klokast/updates/discovery')
        with (state / 'build.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            imported = vm_local_images.import_receipt(state / 'builds', box, receipt)
        if imported != operation:
            raise RuntimeError('local controller returned a different build operation')
        return operation
    result = json.loads(command([REPO / 'ansible/bin/platform-update', 'prepare', '--box', box, '--profile', profile]))
    if result.get('state') not in ('candidate-built', 'candidate-reused'):
        raise RuntimeError('image preparation did not produce a qualified image')
    evidence = Path(result.get('image_result_directory', result['result_directory']))
    inputs = json.loads((evidence / 'inputs.json').read_text())
    candidate = json.loads((evidence / 'candidate.json').read_text())
    boot = candidate['boot_test']
    release = no_application_release(inputs, candidate, boot['openrc_test'], boot['personalized_test'], boot['maintenance_restore'])
    if (inputs['profile'] != profile or candidate['box'] != box or
            json.loads((evidence / 'release-evidence.json').read_text()) != release):
        raise RuntimeError('qualified image evidence differs from requested infrastructure profile')
    return result['operation_id']

