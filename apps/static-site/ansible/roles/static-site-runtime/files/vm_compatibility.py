"""Static Site web component test on synthetic data in a networkless Xen VM.

No publisher, tunnel, private repository, credential, or production data is
attached. Host networking refers only to the disposable VM's loopback device.
This is compatibility evidence, never permission to reconstruct a deployment.
"""
import hashlib
import http.client
import json
import os
from pathlib import Path
import shlex
import time

from vm_app_compatibility import TESTS, verify_result


def test(run, directory, selection):
    # Defense in depth: this adapter cannot run on a production guest or dom0.
    if (os.getpid() != 1 or os.geteuid() != 0 or
            Path('/sys/hypervisor/type').read_text().strip() != 'xen' or
            Path('/sys/hypervisor/uuid').read_text().strip() == '00000000-0000-0000-0000-000000000000' or
            {p.name for p in Path('/sys/class/net').iterdir()} != {'lo'}):
        raise RuntimeError('application test requires PID 1 in its networkless Xen guest')
    # The base smoke test already created this throwaway unprivileged account.
    prefix = ['podman', '--storage-driver=vfs', '--events-backend=file', '--cgroup-manager=cgroupfs']

    def podman(*arguments):
        command = 'export XDG_RUNTIME_DIR=/run/user/2000; exec ' + shlex.join(prefix + list(arguments))
        return run(['su', '-s', '/bin/sh', 'template-test', '-c', command], timeout=60)

    run(['ip', 'link', 'set', 'lo', 'up'])
    podman('load', '--input', str(directory / 'image.oci'))
    image = json.loads(podman('image', 'inspect', selection['image_id']))
    if (len(image) != 1 or image[0].get('Id', '').removeprefix('sha256:') != selection['image_id'] or
            image[0].get('Architecture') != 'amd64' or image[0].get('Os') != 'linux' or
            image[0].get('Digest') != 'sha256:' + selection['manifest_sha256']):
        raise RuntimeError('loaded application image differs from the fixed catalog image')
    if json.loads(podman('info', '--format=json')).get('host', {}).get('security', {}).get('rootless') is not True:
        raise RuntimeError('application compatibility test is not rootless')
    public = directory / 'public'
    (public / 'sample').mkdir(parents=True)
    pages = {'index.html': b'klokast synthetic root\n', 'sample/index.html': b'klokast synthetic page\n'}
    for name, content in pages.items():
        (public / name).write_bytes(content)
    expected = {name: hashlib.sha256(value).hexdigest() for name, value in pages.items()}
    config_before = (directory / 'config.toml').read_bytes()
    # Same server settings and mounts as static-site-web-deploy.sh. Isolation
    # differs deliberately: networkless VM loopback and no boot-time cgroups.
    arguments = ['run', '-d', '--name', 'static-site-test', '--pull=never', '--network=host',
                 '--cgroups=disabled', '--log-driver=none', '--read-only', '--cap-drop=all',
                 '--security-opt=no-new-privileges', '--tmpfs', '/tmp:rw,noexec,nosuid,nodev,size=16m',
                 '-e', 'SERVER_HOST=127.0.0.1', '-e', 'SERVER_PORT=18081', '-e', 'SERVER_ROOT=/public',
                 '-e', 'SERVER_CONFIG_FILE=/etc/sws/config.toml', '-e', 'SERVER_DIRECTORY_LISTING=false',
                 '-e', 'SERVER_SECURITY_HEADERS=true', '-e', 'SERVER_LOG_LEVEL=info',
                 '-v', str(public) + ':/public:ro', '-v', str(directory / 'config.toml') + ':/etc/sws/config.toml:ro',
                 selection['image_id']]

    def response(path):
        connection = http.client.HTTPConnection('127.0.0.1', 18081, timeout=2)
        try:
            connection.request('GET', path)
            reply = connection.getresponse()
            return reply.status, dict(reply.getheaders()), reply.read(65537)
        finally:
            connection.close()

    def check_http():
        deadline = time.monotonic() + 20
        while True:
            try:
                status, _, content = response('/')
                if status == 200 and content == pages['index.html']:
                    break
            except OSError:
                pass
            if time.monotonic() >= deadline:
                raise RuntimeError('static web server failed its bounded startup check')
            time.sleep(0.2)
        status, _, content = response('/sample/')
        if status != 200 or content != pages['sample/index.html']:
            raise RuntimeError('static web server returned wrong synthetic content')
        status, headers, _ = response('/sample')
        if status not in (301, 308) or headers.get('Location', headers.get('location')) != '/sample/':
            raise RuntimeError('static web server directory redirect failed')
        if response('/missing-page')[0] != 404:
            raise RuntimeError('static web server missing-page check failed')

    podman(*arguments)
    try:
        inspect = json.loads(podman('container', 'inspect', 'static-site-test'))[0]
        if (inspect.get('Image', '').removeprefix('sha256:') != selection['image_id'] or
                inspect.get('HostConfig', {}).get('ReadonlyRootfs') is not True or
                {m['Destination']: m['RW'] for m in inspect.get('Mounts', []) if m['Destination'] in
                 ('/public', '/etc/sws/config.toml')} != {'/public': False, '/etc/sws/config.toml': False}):
            raise RuntimeError('static web server identity or read-only mounts differ')
        check_http()
        podman('stop', '--time', '10', 'static-site-test')
        podman('start', 'static-site-test')
        check_http()
        if ({name: hashlib.sha256((public / name).read_bytes()).hexdigest() for name in pages} != expected or
                (directory / 'config.toml').read_bytes() != config_before):
            raise RuntimeError('application test changed synthetic data or configuration')
    finally:
        podman('rm', '--force', 'static-site-test')
    result = {'kind': 'klokast.vm-app-test-result.v1', 'selection': selection,
              'tests': {name: True for name in TESTS}, 'production_qualified': False}
    verify_result(result, selection)
    return result
