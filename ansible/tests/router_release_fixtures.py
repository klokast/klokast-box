"""Shared first-router release and input fixtures."""
import datetime as dt
import hashlib
import json
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'ansible/lib'))
import router_updates as r
from platform_updates import UpdateError, digest, timestamp

NOW = dt.datetime(2026, 9, 25, 12, tzinfo=dt.timezone.utc)
ENGINE = 'a' * 40
PROFILE = json.loads((REPO / 'ansible/update-profiles/router-alpine-v2.json').read_text())


def package(name, version='1-r0'):
    return {'name': name, 'version': version, 'origin': name, 'architecture': 'x86_64',
            'file': 'packages/' + name + '-' + version + '.apk', 'bytes': 100,
            'sha256': hashlib.sha256((name + version).encode()).hexdigest()}


def inputs(branch='v3.23'):
    return r.seal({'kind': 'klokast.vm-template-inputs.v1', 'profile': r.PROFILE,
                   'profile_sha256': digest(PROFILE), 'engine_commit': ENGINE,
                   'architecture': 'x86_64', 'branch': branch, 'world': sorted(PROFILE['packages']),
                   'repositories': [PROFILE['repository_origin'] + '/' + branch + '/' + name
                                    for name in PROFILE['repositories']],
                   'keys': {'alpine.pub': 'b' * 64},
                   'indexes': {'APKINDEX.111.tar.gz': 'c' * 64, 'APKINDEX.222.tar.gz': 'd' * 64},
                   'packages': sorted([package(name) for name in PROFILE['packages']], key=lambda p: p['name']),
                   'tailscale': {'kind': 'klokast.router-tailscale-input.v1', 'version': '1.102.4',
                       'file': 'components/tailscale_1.102.4_amd64.tgz', 'bytes': 100,
                       'openrc_file': 'components/tailscale-openrc',
                       'openrc_sha256': hashlib.sha256((Path(__file__).resolve().parents[1] /
                           'roles/router-alpine-rootfs/files/tailscale-openrc').read_bytes()).hexdigest(),
                       'sha256': 'e' * 64,
                       'tailscale_sha256': hashlib.sha256(b'\x7fELFtailscale').hexdigest(),
                       'tailscaled_sha256': hashlib.sha256(b'\x7fELFtailscaled').hexdigest(),
                       'metadata_sha256': 'f' * 64, 'verifier_source_tree': 'a' * 40,
                       'verifier_sha256': 'b' * 64, 'signature_verified': True}},
                  'inputs_sha256')


def reseal(value, field='receipt_sha256'):
    value.pop(field)
    value.update(r.seal(value, field))


def release():
    return r.seal({'kind': r.RELEASE, 'profile': r.PROFILE, 'engine_commit': ENGINE,
                   'inputs': inputs(), 'kernel_release': '6.12.1-virt',
                   'artifacts': {name: name[0] * 64 if name[0] in 'abcdef' else 'e' * 64
                                 for name in ('os', 'kernel', 'initramfs')},
                   'generic_tests': {name: True for name in ('identity_absent', 'exact_packages', 'upstream_tailscale', 'kernel_modules', 'openrc')},
                   'runtime_packages': {p['name']: p['version'] for p in inputs()['packages'] if p['name'] != 'openssh'},
                   'runtime_tests': dict.fromkeys(('frozen_packages', 'no_openssh_server', 'locked_root', 'pinned_world'), True)})


def branch(name, date='2026-01-01'):
    return {'rel_branch': name, 'git_branch': name[1:] + '-stable', 'branch_date': date,
            'eol_date': '2028-01-01', 'arches': ['x86_64'],
            'repos': [{'name': 'main', 'eol_date': '2028-01-01'},
                      {'name': 'community', 'eol_date': '2027-01-01'}],
            'releases': [{'version': name[1:] + '.0', 'date': date}]}


