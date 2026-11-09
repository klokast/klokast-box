"""Prepare infrastructure images only on the target box controller."""
import json
import hashlib
import os
from pathlib import Path
import socket
import re
import sys
import subprocess
import tempfile
import threading
import time
import vm_local_images
from platform_maintenance import progress
from platform_updates import no_application_release, digest


PROFILES = ('shared-alpine-v1', 'air-alpine-v1', 'ops-alpine-v1', 'vpn-egress-alpine-v1', 'router-alpine-v3')


def qualify_profile(profile, *, repo):
    """Bind infrastructure image reuse to the same public guest recipe."""
    recipe_files = (
        'vm-template-builder/files/vm-template-build-guest',
        'vm-template-builder/files/vm-template-build-dom0',
        'vm-template-builder/files/vm-template-smoke-guest',
        'vm-template-builder/files/vm-infrastructure-finalize',
        'vm-template-builder/files/vm_infrastructure_personalize.py',
        'vm-template-builder/files/vm_ops_data.py',
        'vm-template-builder/files/vm_controller_state.py',
        'vm-template-builder/files/ops_network.py',
        'vm-retained-data/files/retained_data.py',
        'vm-retained-data/files/retained_data_test.py',
        'vm-personalize/files/vm_personalize.py',
        'vm-personalize/files/vm_personalize_test.py',
    )
    if profile['profile'] in ('shared-alpine-v1', 'vpn-egress-alpine-v1', 'router-alpine-v3', 'ops-alpine-v1', 'air-alpine-v1'):
        recipe_files += ('vm-template-builder/files/vm-service-finalize',
                         'vm-template-builder/files/vm_guest_identity.py')
    if profile['profile'] in ('ops-alpine-v1', 'air-alpine-v1'):
        recipe_files += ('vm-template-builder/files/vm_infrastructure_storage.py',
                         'vm-template-builder/files/vm_control_personalize.py',
                         'vm-template-builder/files/vm_control_storage_test.py')
    if profile['profile'] == 'air-alpine-v1':
        recipe_files += ('vm-template-builder/files/guest_enrollment.py',
                         'vm-template-builder/files/guest-enrollment.openrc',
                         'podman-vm-firewall/templates/nftables.nft.j2',
                         '../inventory-policy/group_vars/air.yml',
                         '../lib/vm_template_inputs.py')
    if profile['profile'] == 'shared-alpine-v1':
        recipe_files += ('vm-template-builder/files/vm_dmz_personalize.py',
                         'vm-template-builder/files/guest_enrollment.py',
                         'vm-template-builder/files/guest-enrollment.openrc')
    if profile['profile'] == 'vpn-egress-alpine-v1':
        recipe_files += ('vm-template-builder/files/vm_vpn_reconstruct.py',
                         'vm-template-builder/files/vm_service_test.py',
                         'vpn-egress-runtime/templates/mihomo.openrc.j2')
    if profile['profile'] == 'router-alpine-v3':
        recipe_files += ('vm-template-builder/files/vm_router_state.py',
                         'vm-template-builder/files/vm_router_image_test.py',
                         'vm-template-builder/files/vm_router_image_test_dom0.py',
                         'router-alpine-rootfs/files/router-template-guest',
                         'router-alpine-rootfs/files/tailscale-openrc',
                         '../lib/router_personalize.py', '../lib/router_service_probe.py',
                         '../lib/router_template_inputs.py', '../lib/platform_lan_print.py',
                         'router/templates/interfaces.j2',
                         'router/templates/dhcpcd.conf.j2', 'router/templates/dnsmasq.conf.j2',
                         'router/templates/nftables.nft.j2')
    return dict(profile, recipe_sha256=digest({name: hashlib.sha256(
        (Path(repo) / 'ansible/roles' / name).read_bytes()).hexdigest() for name in recipe_files}))


def prepare_image(box, profile, *, repo):
    REPO = Path(repo)
    def command(argv):
        return subprocess.check_output([str(v) for v in argv], cwd=REPO, text=True, timeout=7200)
    if not vm_local_images.BOX.fullmatch(box) or profile not in ('air-alpine-v1', 'ops-alpine-v1', 'vpn-egress-alpine-v1', 'router-alpine-v3'):
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
    release = no_application_release(inputs, candidate, boot.get('openrc_test', {}), boot.get('personalized_test', {}), boot.get('maintenance_restore', {}), require_infrastructure_storage=True)
    if (inputs['profile'] != profile or candidate['box'] != box or
            json.loads((evidence / 'release-evidence.json').read_text()) != release):
        raise RuntimeError('qualified image evidence differs from requested infrastructure profile')
    return result['operation_id']



def streamed(argv, script, timeout):
    """Run image preparation and publish each platform-update stage while it runs."""
    reader, writer = os.pipe()
    lines = []
    def relay():
        with os.fdopen(reader, encoding='utf-8', errors='replace') as stream:
            for line in stream:
                lines.append(line)
                print(line, end='', file=sys.stderr, flush=True)
                if line.startswith('platform-update: '):
                    progress('image build: ' + line[len('platform-update: '):].split('; operation ')[0].strip())
    thread = threading.Thread(target=relay, daemon=True)
    thread.start()
    try:
        result = subprocess.run(argv, input=script, stdout=subprocess.PIPE, stderr=writer, text=True, timeout=timeout)
    finally:
        os.close(writer)
        # A killed local shell can leave a child holding the pipe; do not wait for it.
        thread.join(10)
    result.stderr = ''.join(lines)
    return result


def local_action(box, profile, revision, action, keep=None):
    if (not vm_local_images.BOX.fullmatch(box) or profile not in PROFILES or
            not re.fullmatch('[0-9a-f]{40}', revision) or
            action not in ('prepare', 'cleanup-before', 'cleanup', 'image-receipt') or
            (action != 'prepare' and not vm_local_images.OP.fullmatch(keep or ''))):
        raise RuntimeError('invalid local image operation')
    # Synchronize public source before local work; execute the requested frozen revision.
    script = '''set -eu
umask 077
box=$1
revision=$2
action=$3
keep=$4
profile=$5
/usr/local/sbin/klokast-controller-guard --require-local-image-box "$box" >/dev/null
source=/home/smith/src/klokast/klokast-box
if ! git -C "$source" diff --quiet || ! git -C "$source" diff --cached --quiet ||
   [ -n "$(git -C "$source" ls-files --others --exclude-standard)" ]; then
  echo "Controller $box-ops source has local changes; preserve them before updating." >&2
  exit 1
fi
if ! git -C "$source" merge-base --is-ancestor "$revision" HEAD 2>/dev/null; then
  echo "Controller $box-ops needs source revision $revision; trying a fast-forward update." >&2
  # Public source uses a declared same-box VPN first, then direct access.
  if ! timeout 150 "$source/ansible/bin/platform-vpn-egress" with-proxy --box "$box" -- git -C "$source" fetch --quiet origin "$revision" 2>/dev/null; then
    echo "Controller $box-ops source fetch through $box-vpn-egress is unavailable; trying direct access." >&2
    timeout 120 git -C "$source" fetch --quiet origin "$revision" || :
  fi
  if ! git -C "$source" cat-file -e "$revision^{commit}" 2>/dev/null ||
     ! git -C "$source" merge --ff-only "$revision" >&2; then
    echo "Controller $box-ops could not update source to $revision; check repository access or divergent commits." >&2
    exit 1
  fi
fi
work=$(mktemp -d /var/cache/klokast/updates/nightly-public.XXXXXXXX)
trap 'rm -rf "$work"' EXIT HUP INT TERM
git clone --quiet --no-hardlinks --no-checkout "$source" "$work/public"
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
    # Capture the single JSON stdout; stderr progress belongs in the private log.
    argv = ['sh', '-s', '--'] if socket.gethostname().split('.')[0] == box + '-ops' else ['tailscale', 'ssh', 'smith@' + box + '-ops', 'sh', '-s', '--']
    if action == 'prepare':
        result = streamed([*argv, box, revision, action, '-', profile], script, 7500)
    else:
        for attempt in range(3):
            result = subprocess.run([*argv, box, revision, action, keep or '-', profile], input=script, capture_output=True, text=True,
                                    timeout=4800)
            if not result.returncode or action != 'image-receipt' or 'dial failure:' not in result.stderr or attempt == 2:
                break
            print('Image receipt SSH dial failed for '+box+'-ops; retry '+str(attempt+2)+'/3',file=sys.stderr,flush=True)
            time.sleep(2)
        if result.stderr: print(result.stderr, file=sys.stderr, flush=True)
    if result.returncode:
        raise RuntimeError('local image ' + action + ' failed: ' + result.stderr.strip()[-1200:])
    return json.loads(result.stdout)
