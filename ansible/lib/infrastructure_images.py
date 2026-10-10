"""Prepare infrastructure images only on the target box controller."""
import json
import hashlib
from pathlib import Path
import socket
import re
import sys
import subprocess
import tempfile
import vm_local_images
from platform_updates import no_application_release, digest


PROFILES = ('shared-alpine-v1', 'air-alpine-v1', 'ops-alpine-v1', 'vpn-egress-alpine-v1')


def qualify_profile(profile, *, repo):
    """Bind infrastructure image reuse to the same public guest recipe."""
    recipe_files = (
        'vm-template-builder/files/vm-template-build-guest',
        'vm-template-builder/files/vm-template-smoke-guest',
        'vm-template-builder/files/vm-infrastructure-finalize',
        'vm-retained-data/files/retained_data.py',
        'vm-retained-data/files/retained_data_test.py',
        'vm-personalize/files/vm_personalize.py',
        'vm-personalize/files/vm_personalize_test.py',
    )
    return dict(profile, recipe_sha256=digest({name: hashlib.sha256(
        (Path(repo) / 'ansible/roles' / name).read_bytes()).hexdigest() for name in recipe_files}))


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



def local_action(box, profile, revision, action, keep=None):
    if (not vm_local_images.BOX.fullmatch(box) or profile not in PROFILES or
            not re.fullmatch('[0-9a-f]{40}', revision) or
            action not in ('prepare', 'cleanup-before', 'cleanup', 'image-receipt') or
            (action != 'prepare' and not vm_local_images.OP.fullmatch(keep or ''))):
        raise RuntimeError('invalid local image operation')
    # Public code only. This controller keeps its installed checkout unchanged.
    script = '''set -eu
umask 077
box=$1
revision=$2
action=$3
keep=$4
profile=$5
/usr/local/sbin/klokast-controller-guard --require-local-image-box "$box" >/dev/null
work=$(mktemp -d /var/cache/klokast/updates/nightly-public.XXXXXXXX)
trap 'rm -rf "$work"' EXIT HUP INT TERM
git clone --quiet --no-hardlinks --no-checkout /home/smith/src/klokast/klokast-box "$work/public"
git -C "$work/public" remote set-url origin https://github.com/klokast/klokast-box.git
if ! git -C "$work/public" cat-file -e "$revision^{commit}"; then
  timeout 120 git -C "$work/public" fetch --quiet origin "$revision"
fi
git -C "$work/public" checkout --quiet --detach "$revision"
test "$(git -C "$work/public" rev-parse HEAD)" = "$revision"
cd "$work/public"
if [ "$action" = prepare ]; then
  timeout 7200 ansible/bin/platform-update prepare --box "$box" --profile "$profile"
elif [ "$action" = image-receipt ]; then
  ansible/bin/platform-update image-receipt --box "$box" --operation-id "$keep"
else
  set -- ansible/bin/platform-update cleanup --box "$box" --profile "$profile" --keep-image "$keep"
  if [ "$action" = cleanup-before ]; then set -- "$@" --preserve-qualified; fi
  timeout 4500 "$@"
fi
'''
    # Capture the single JSON stdout; progress belongs in the private log.
    result = subprocess.run(['tailscale', 'ssh', 'smith@' + box + '-ops', 'sh', '-s', '--', box,
                             revision, action, keep or '-', profile], input=script, capture_output=True, text=True,
                            timeout=7500 if action == 'prepare' else 4800)
    if result.stderr: print(result.stderr, file=sys.stderr, flush=True)
    if result.returncode:
        raise RuntimeError('local image ' + action + ' failed; inspect its local protected build/cleanup log')
    return json.loads(result.stdout)

