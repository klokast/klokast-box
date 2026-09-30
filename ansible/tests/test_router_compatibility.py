"""Legacy copies must be fenced, sanitized, and bound to exact source evidence."""
import ast
import copy
import hashlib
import json
import os
from pathlib import Path
import runpy
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import router_fixture as fixture
import router_compatibility
from test_router_copy_qualification import module


class FixtureTests(unittest.TestCase):
    def test_compatibility_initial_candidate_has_only_synthetic_first_contact(self):
        path = Path(__file__).resolve().parents[1] / 'roles/router-state-copy/files/router-compatibility-guest'
        job = runpy.run_path(str(path))['candidate_job']
        request = {'fixture': {'box': 'boxa'}, 'operation_id': 'a' * 24,
                   'manifest': {'engine_commit': 'b' * 40}, 'inputs_sha256': 'c' * 64,
                   'kernel_release': '6.18.54-virt', 'runtime_packages': {}}
        initial = job(request, 'initial')
        replacement = job(request, 'candidate')
        self.assertEqual(initial['mode'], 'initial-install')
        self.assertEqual(replacement['mode'], 'replacement')
        self.assertEqual(set(initial['first_contact']), {'key', 'backend_address', 'backend_prefix',
                                                         'backend_source_address'})
        self.assertNotIn('first_contact', replacement)

    def test_guest_preparation_stage_hides_source_value_errors(self):
        path = Path(__file__).resolve().parents[1] / 'roles/router-state-copy/files/router-compatibility-guest'
        stage = runpy.run_path(str(path))['preparation_step']
        def invalid_source():
            raise ValueError('private source bytes')
        with self.assertRaisesRegex(RuntimeError, 'router compatibility legacy sanitize rejected fixture values') as caught:
            stage('legacy sanitize', invalid_source)
        self.assertNotIn('private source bytes', str(caught.exception))

    def test_dhcp_copy_accepts_distinct_identity_and_rejects_replacement_or_stale_wan_cache(self):
        from test_router_state import CopyTests
        case = CopyTests()
        self.addCleanup(case.doCleanups)
        case.setUp()
        for root, generation in ((case.source, 'legacy'), (case.target, 'candidate')):
            for relative in router_compatibility.router_state.IDENTITY:
                case.put(relative, (generation + ':' + relative).encode(), root=root)
        accounts = dict(dnsmasq_uid=0, dnsmasq_gid=0, tailscale_gid=0)
        with patch.object(fixture, 'accounts', return_value=accounts), \
                patch.object(router_compatibility, 'Path', side_effect=lambda name: case.source / name.lstrip('/')):
            expected = router_compatibility.state('seed')
        case.copy()
        with patch.object(fixture, 'accounts', return_value=accounts), \
                patch.object(router_compatibility, 'Path', side_effect=lambda name: case.target / name.lstrip('/')):
            current = router_compatibility.validate_copy(expected, 'new')
            self.assertNotEqual(current['generation_identity'], expected['generation_identity'])
            relative = 'var/lib/tailscale/tailscaled.state'
            case.put(relative, ('legacy:' + relative).encode(), root=case.target)
            with self.assertRaisesRegex(RuntimeError, 'generation-local identity'):
                router_compatibility.validate_copy(expected, 'new')
            case.put(relative, ('candidate:' + relative).encode(), root=case.target)
            case.put('var/lib/dhcpcd/eth0.lease', b'stale', root=case.target)
            with self.assertRaisesRegex(RuntimeError, 'WAN lease cache'):
                router_compatibility.validate_copy(expected, 'new')

    def test_probe_requires_complete_modules_before_writing(self):
        source = Path(__file__)
        modules = {name:source for name in fixture.PROBE_MODULES}
        for wrong in ({}, {k:v for k,v in modules.items() if k != 'router_candidate.py'},
                      {**modules, 'extra.py':source}):
            with self.assertRaisesRegex(ValueError, 'complete fixed set'):
                fixture.install_probe(self.root, {}, wrong, source)
            self.assertFalse((self.root/'usr/local/lib/klokast/router-probe').exists())
        fixture.install_probe(self.root, {}, modules, source)
        self.assertEqual({p.name for p in (self.root/'usr/local/lib/klokast/router-probe').iterdir()},
                         set(fixture.PROBE_MODULES))

    def test_identity_fixture_uses_service_directory_and_rejects_links(self):
        service = self.root / 'var/lib/tailscale'
        service.chmod(0o700)
        with patch.object(fixture.personalizer, 'service_directory', return_value=service):
            state = 'var/lib/tailscale/tailscaled.state'
            fixture.put_identity_fixture(self.root, state, 'opaque test state')
            self.assertEqual((service / 'tailscaled.state').read_text(), 'opaque test state')
            self.assertEqual((service / 'tailscaled.state').stat().st_mode & 0o777, 0o600)
            key = 'var/lib/tailscale/ssh/ssh_host_ed25519_key'
            fixture.put_identity_fixture(self.root, key, 'opaque test key')
            self.assertEqual((service / 'ssh/ssh_host_ed25519_key').read_text(), 'opaque test key')
            self.assertEqual((service / 'ssh').stat().st_uid, os.geteuid())
            self.assertEqual((service / 'ssh').stat().st_gid, os.getegid())
            (service / 'ssh').chmod(0o755)
            with self.assertRaisesRegex(ValueError, 'directory is unsafe'):
                fixture.put_identity_fixture(self.root, 'var/lib/tailscale/ssh/ssh_host_rsa_key', 'x')
            (service / 'ssh').chmod(0o700)
            with self.assertRaisesRegex(ValueError, 'already exists'):
                fixture.put_identity_fixture(self.root, key, 'replacement')
            (service / 'ssh/ssh_host_ed25519_key').unlink()
            (service / 'ssh/ssh_host_ed25519_key').symlink_to(self.root / 'outside')
            with self.assertRaisesRegex(ValueError, 'already exists'):
                fixture.put_identity_fixture(self.root, key, 'replacement')
            with self.assertRaisesRegex(ValueError, 'path or content is invalid'):
                fixture.put_identity_fixture(self.root, 'var/lib/tailscale/unknown', 'x')

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.service = self.root / 'var/lib/tailscale'
        self.service.mkdir(parents=True)

    def test_clear_refuses_links_without_partial_deletion(self):
        good = self.service / 'tailscaled.state'; good.write_text('synthetic only')
        outside = self.root / 'outside'; outside.write_text('retained')
        for kind in ('symlink', 'hardlink', 'writable', 'fifo'):
            with self.subTest(kind=kind):
                bad = self.service / 'bad'
                if kind == 'symlink':
                    bad.symlink_to(outside)
                elif kind == 'hardlink':
                    os.link(outside, bad)
                elif kind == 'fifo':
                    os.mkfifo(bad)
                else:
                    bad.write_text('unsafe'); bad.chmod(0o666)
                with self.assertRaises(ValueError):
                    fixture.clear_directory(self.root, 'var/lib/tailscale')
                self.assertTrue(good.exists())
                self.assertEqual(outside.read_text(), 'retained')
                bad.unlink()
        fixture.clear_directory(self.root, 'var/lib/tailscale')
        self.assertEqual(list(self.service.iterdir()), [])
        self.assertTrue(self.service.is_dir())

    def test_unknown_paths_and_linked_parent_fail(self):
        for name in ('etc', '../escape', '/var/lib/tailscale'):
            with self.assertRaises(ValueError):
                fixture.clear_directory(self.root, name)
            with self.assertRaises(ValueError):
                fixture.remove(self.root, name)
        outside = self.root / 'outside'; outside.mkdir()
        self.service.rmdir(); self.service.symlink_to(outside)
        with self.assertRaises(ValueError):
            fixture.clear_directory(self.root, 'var/lib/tailscale')

    def test_lease_bound_is_distinct_from_small_identity_files(self):
        path = self.root / 'var/lib/misc/dnsmasq.leases'
        path.parent.mkdir()
        path.write_bytes(b'x' * 20000)
        fixture.remove(self.root, 'var/lib/misc/dnsmasq.leases', owner=os.geteuid())
        self.assertFalse(path.exists())
        path.write_bytes(b'x' * (4 * 1024 * 1024 + 1))
        with self.assertRaises(ValueError):
            fixture.remove(self.root, 'var/lib/misc/dnsmasq.leases', owner=os.geteuid())
        self.assertTrue(path.exists())

    def test_account_selector_rejects_ambiguous_or_out_of_range_ids(self):
        etc = self.root / 'etc'; etc.mkdir()
        (etc / 'group').write_text('tailscale:x:103:\n')
        for line in ('dnsmasq:x:0:65:x:/var/lib:/sbin/nologin\n',
                     'dnsmasq:x:65:65:x:/var/lib:/sbin/nologin\n' * 2,
                     'dnsmasq:x:999999:65:x:/var/lib:/sbin/nologin\n'):
            (etc / 'passwd').write_text(line)
            with self.assertRaises(ValueError):
                fixture.accounts(self.root)
        (etc / 'passwd').write_text('dnsmasq:x:65:65:x:/var/lib:/sbin/nologin\n')
        self.assertEqual(fixture.accounts(self.root), {'dnsmasq_uid': 65, 'dnsmasq_gid': 65, 'tailscale_gid': 103})


class HostTests(unittest.TestCase):
    def setUp(self):
        self.host = module('router-compatibility-dom0')
        self.operation = 'a' * 24

    def test_result_slot_is_allocated_only_after_candidate_clone(self):
        with tempfile.TemporaryDirectory() as root:
            work = Path(root)
            template = work / 'template'
            template.mkdir()
            lifecycle = {'slots': {}}
            candidate = {'artifacts': {'os': {'sha256': 'b' * 64}}}
            disk = {'path': '/dev/vg0/routergen_' + self.operation}

            def clone(*args, **kwargs):
                self.assertFalse((work / 'result.slot').exists())
                self.assertEqual(kwargs['box'], 'k001')
                return disk

            with patch.object(self.host.router_candidate_disk, 'create', side_effect=clone) as create:
                self.assertEqual(
                    self.host.clone_candidate_then_allocate_result(
                        work, self.operation, template, candidate, 'k001', lifecycle),
                    disk,
                )
                self.assertEqual(create.call_count, 1)
                self.assertEqual((work / 'result.slot').stat().st_size, self.host.SLOTS['result'])
                self.assertIn('result', lifecycle['slots'])
                with self.assertRaisesRegex(RuntimeError, 'exists before candidate cloning'):
                    self.host.clone_candidate_then_allocate_result(
                        work, self.operation, template, candidate, 'k001', lifecycle)
                self.assertEqual(create.call_count, 1)

    def test_success_flag_without_native_evidence_cannot_pass(self):
        value = {'operation_id': self.operation, 'inputs_sha256': 'b' * 64,
                 'guest': {'source_packages': {'tailscale': 'old'}, 'runtime_packages': {'tailscale': 'new'}}}
        record = {'kind': 'klokast.router-compatibility-phase.v2', 'operation_id': self.operation,
                  'inputs_sha256': 'b' * 64, 'success': True, 'production_identity': False, 'seconds': 1}
        for phase in self.host.PHASES:
            with self.subTest(phase=phase), self.assertRaises(RuntimeError):
                self.host.validate_phase({**record, 'phase': phase}, value, phase)
        self.host.validate_phase({**record, 'phase': 'forward', 'copy_complete': True,
            'copy_receipt_sha256':'a'*64}, value, 'forward')
        with self.assertRaises(RuntimeError):
            self.host.validate_phase({**record, 'phase': 'forward', 'copy_complete': True,
                'copy_receipt_sha256':'wrong'}, value, 'forward')
        with self.assertRaises(RuntimeError):
            self.host.validate_phase({**record, 'phase': 'forward', 'copy_complete': True,
                'copy_receipt_sha256':'a'*64, 'production_identity': True}, value, 'forward')

    def test_prepare_binds_common_recipe_and_cannot_substitute_success(self):
        accounts = {'dnsmasq_uid':65, 'dnsmasq_gid':65, 'tailscale_gid':103}
        component = {'version':'1.2.3', 'sha256':'a'*64, 'tailscale_sha256':'b'*64,
                     'tailscaled_sha256':'c'*64, 'openrc_sha256':'d'*64}
        value = {'operation_id':self.operation, 'inputs_sha256':'b'*64,
                 'guest':{'source_packages':{'tailscale':'old'}, 'runtime_packages':{'tailscale':'new'},
                          'manifest':{'engine_commit':'c'*40, 'tailscale':component},
                          'fixture':{'box':'boxa', 'files':{'etc/hostname':'boxa-router\n'},
                                     'packages':{'tailscale':'new', 'openssh':'bootstrap'}}}}
        prepared = {'kind':'klokast.router-candidate-files.v1', 'mode':'replacement', 'box':'boxa', 'role':'router',
                    'operation_id':self.operation, 'inputs_sha256':'b'*64, 'engine_commit':'c'*40,
                    'packages':{'tailscale':'new'}, 'accounts':accounts, 'tailscale':component,
                    'configuration_files':{'etc/hostname':hashlib.sha256(b'boxa-router\n').hexdigest()},
                    'identity_absent':True, 'service_syntax':True, 'replacement_authorized':False}
        record = {'kind':'klokast.router-compatibility-phase.v2', 'operation_id':self.operation,
                  'inputs_sha256':'b'*64, 'success':True, 'production_identity':False, 'seconds':1,
                  'phase':'prepare', 'prepared':True, 'fixtures':{
                      'legacy':{'packages':{'tailscale':'old'}, 'production_state_removed':True},
                      'candidate':{'packages':{'tailscale':'new'}, 'production_state_removed':True,
                                   'accounts':accounts, 'candidate_preparation':prepared}}}
        initial = copy.deepcopy(record['fixtures']['candidate'])
        initial['packages'] = value['guest']['fixture']['packages']
        initial['candidate_preparation'].update(mode='initial-install', packages=initial['packages'])
        record['fixtures']['initial'] = initial
        self.host.validate_phase(record, value, 'prepare')
        for field, wrong in (('mode','initial-install'), ('box','boxb'), ('role','dmz'),
                             ('engine_commit','d'*40), ('configuration_files',{}),
                             ('identity_absent',False), ('service_syntax',False),
                             ('replacement_authorized',True)):
            changed = copy.deepcopy(record)
            changed['fixtures']['candidate']['candidate_preparation'][field] = wrong
            with self.subTest(field=field), self.assertRaisesRegex(RuntimeError, 'common preparation'):
                self.host.validate_phase(changed, value, 'prepare')
        for name in ('candidate', 'initial'):
            for field in component:
                changed = copy.deepcopy(record)
                changed['fixtures'][name]['candidate_preparation']['tailscale'][field] = 'different'
                with self.subTest(fixture=name, component=field), self.assertRaisesRegex(RuntimeError, 'common preparation'):
                    self.host.validate_phase(changed, value, 'prepare')
            changed = copy.deepcopy(record)
            del changed['fixtures'][name]['candidate_preparation']['tailscale']
            with self.subTest(fixture=name, component='absent'), self.assertRaisesRegex(RuntimeError, 'common preparation'):
                self.host.validate_phase(changed, value, 'prepare')

    def test_common_preparer_output_passes_host_qualification_for_both_modes(self):
        from test_router_candidate import CandidateTests
        fixtures = {'legacy': {'packages': {'tailscale':'old'}, 'production_state_removed':True}}
        for name, mode in (('candidate', 'replacement'), ('initial', 'initial-install')):
            case = CandidateTests()
            self.addCleanup(case.doCleanups)
            case.setUp()
            case.job['mode'] = mode
            if mode == 'initial-install':
                case.job['first_contact'] = {'key':'ssh-ed25519 YQ==', 'backend_address':'192.0.2.2',
                                            'backend_prefix':24, 'backend_source_address':'192.0.2.1'}
            prepared = case.prepare()
            fixtures[name] = {'packages':prepared['packages'], 'accounts':prepared['accounts'],
                              'production_state_removed':True, 'candidate_preparation':prepared}
        value = {'operation_id':case.job['operation_id'], 'inputs_sha256':case.job['inputs_sha256'],
                 'guest':{'source_packages':fixtures['legacy']['packages'],
                          'runtime_packages':case.job['runtime_packages'],
                          'manifest':case.manifest, 'fixture':case.request}}
        record = {'kind':'klokast.router-compatibility-phase.v2', 'operation_id':value['operation_id'],
                  'inputs_sha256':value['inputs_sha256'], 'success':True, 'production_identity':False,
                  'seconds':1, 'phase':'prepare', 'prepared':True, 'fixtures':fixtures}
        self.host.validate_phase(record, value, 'prepare')

    def test_boots_have_no_production_disk_or_network_and_copies_are_readonly(self):
        loops = {name: '/dev/loop' + str(index) for index, name in enumerate(self.host.SLOTS)}
        value = {'operation_id': self.operation, 'inputs_sha256': 'b' * 64, 'guest': {}}
        for phase in self.host.PHASES:
            text = self.host.configuration(Path('/operation'), value, phase, 'uuid', loops)
            parsed = {node.targets[0].id: ast.literal_eval(node.value) for node in ast.parse(text).body}
            self.assertEqual(parsed['vif'], [])
            self.assertNotEqual(parsed['name'], 'router')
            self.assertNotIn('/dev/vg', text)
            self.assertIn('klokast_job=', parsed['extra'])
            if phase in ('forward', 'reverse'):
                name = 'new' if phase == 'reverse' else 'old'
                self.assertEqual(parsed['disk'][0], 'phy:' + loops[name] + ',xvda,r')
            if phase in ('new', 'old'):
                self.assertIn('phy:' + loops['previous'] + ',xvdf,r', parsed['disk'])
                self.assertTrue(parsed['kernel'].endswith('/' + phase + '-kernel'))

    def test_isolated_ab_guests_use_disjoint_networkless_disks(self):
        loops = {'old':'/dev/loop10', 'new':'/dev/vg0/routergen_' + self.operation,
                 'result':'/dev/loop11', 'previous':'/dev/loop12'}
        value = {'operation_id':self.operation, 'inputs_sha256':'b'*64, 'guest':{}}
        for phase, selected in (('hold-old','old'), ('hold-new','new')):
            config = self.host.configuration(Path('/private-test'), value, phase, 'test-uuid', loops)
            parsed = {node.targets[0].id: ast.literal_eval(node.value)
                      for node in ast.parse(config).body}
            self.assertEqual(parsed['vif'], [])
            self.assertNotEqual(parsed['name'], 'router')
            self.assertEqual(parsed['disk'][0], 'phy:' + loops[selected] + ',xvda,w')
            self.assertIn('phy:/dev/loop12,xvdf,r', parsed['disk'])
            self.assertTrue(parsed['kernel'].endswith('/' + selected + '-kernel'))
            self.assertIn('init=/usr/local/libexec/router-compatibility-guest', parsed['extra'])

    def test_isolated_ab_result_requires_ordered_rollback_and_running_old(self):
        with tempfile.TemporaryDirectory() as root:
            work = Path(root)
            operation = self.operation
            value = {'box':'k001', 'operation_id':operation, 'engine_commit':'a'*40}
            identities = dict.fromkeys(self.host.HOLD_PHASES, 'test-uuid')
            adapter = self.host.IsolatedRollback(work, value, {'path':'/dev/vg0/test'}, identities, {})
            adapter.running = 'old'
            adapter.events = ['stopped-old', 'copied-forward', 'started-candidate',
                              'controller-signal-absent', 'stopped-candidate',
                              'copied-reverse', 'started-old']
            with patch.object(self.host, 'domain', side_effect=lambda name: {} if name in (
                    adapter.guest_name('hold-old'), 'router') else None), \
                    patch.object(self.host, 'source_identity'), \
                    patch.object(self.host.router_candidate_disk, 'verify'):
                adapter.finish('rolled-back')
                self.assertEqual(json.loads((work/'ab-result.json').read_text())['outcome'],'rolled-back')
                adapter.events[4],adapter.events[5] = adapter.events[5],adapter.events[4]
                with self.assertRaisesRegex(RuntimeError, 'required Xen and copy order'):
                    adapter.finish('rolled-back')

    def test_snapshot_checks_uuid_origin_tag_permissions_capacity(self):
        record = {'uuid': 'snapshot', 'origin_uuid': 'source', 'path': '/dev/vg0/test', 'tag': 'tag'}
        row = {'lv_uuid': 'snapshot', 'origin_uuid': 'source', 'lv_path': '/dev/vg0/test',
               'lv_tags': 'tag', 'lv_attr': 'sri-a-s---', 'lv_size': str(1024 * self.host.MIB), 'data_percent': '0.1'}
        self.host.validate_snapshot(row, record)
        for field, wrong in (('lv_uuid', 'foreign'), ('origin_uuid', 'other'), ('lv_tags', 'other'),
                              ('lv_attr', 'swi-a-s---'), ('lv_attr', 'sri-I-s---'), ('data_percent', '95')):
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                self.host.validate_snapshot({**row, field: wrong}, record)

    def test_cleanup_rejects_same_size_replacement_and_live_guests(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            disk = root / 'old.slot'
            with disk.open('wb') as stream:
                stream.truncate(self.host.SLOTS['old'])
            record = {'operation_id': self.operation, 'stage': 'detached',
                      'uuids': dict.fromkeys(self.host.PHASES, '11111111-1111-4111-8111-111111111111'),
                      'slots': {name: self.host.file_identity(disk) for name in self.host.SLOTS if name != 'new'}}
            (root / 'lifecycle.json').write_text(json.dumps(record))
            (root / 'snapshot.json').write_text(json.dumps({'stage': 'retired'}))
            for live, attached in (({}, []), (None, ['/dev/loop7'])):
                with patch.object(self.host, 'safe_file'), patch.object(self.host, 'domain', return_value=live), \
                     patch.object(self.host, 'loop_devices', return_value=attached), self.assertRaises(RuntimeError):
                    self.host.cleanup(root, self.operation, 'boxa')
                self.assertTrue(disk.exists())
            record['slots']['old']['inode'] += 1
            (root / 'lifecycle.json').write_text(json.dumps(record))
            with patch.object(self.host, 'safe_file'), patch.object(self.host, 'domain', return_value=None), \
                 patch.object(self.host, 'loop_devices', return_value=[]), self.assertRaises(RuntimeError):
                self.host.cleanup(root, self.operation, 'boxa')
            self.assertTrue(disk.exists())

    def test_changed_live_identity_refuses_before_snapshot_command(self):
        source = {'configuration_sha256': 'c' * 64, 'xen_runtime': {'uuid': 'old'},
                  'boot_artifacts': {}, 'disk': {'path': '/dev/vg0/lv_router', 'uuid': 'exact', 'bytes': 2147483648},
                  'accepted': None}
        current = {**source, 'accepted_record_present': False, 'pending_record_present': False,
                   'xen_runtime_matches': True, 'xen': {'disk': ['phy:/dev/vg0/lv_router,xvda,w']},
                   'logical_volumes': {'report': [{'lv': [{'lv_path': '/dev/vg0/lv_router', 'lv_uuid': 'exact',
                     'lv_size': '2147483648', 'origin': ''}]}]}}
        for field, wrong in (('xen_runtime', {'uuid': 'new'}), ('pending_record_present', True),
                              ('accepted_record_present', True), ('configuration_sha256', 'changed')):
            changed = {**current, field: wrong}
            with patch.object(self.host.runpy, 'run_path', return_value={'inspect_dom0': lambda _: changed}), \
                 patch.object(self.host, 'run') as run, self.assertRaises(RuntimeError):
                self.host.source_identity(Path('/operation'), {'box': 'boxa', 'source': source})
            run.assert_not_called()

    def test_adopted_source_must_match_native_protected_reader(self):
        disk = {'path': '/dev/vg0/lv_router', 'uuid': 'exact', 'bytes': 2147483648}
        accepted = {'kind': 'klokast.router-accepted-source.v1', 'box': 'boxa',
                    'assignment': {'current_sha256': 'a' * 64},
                    'generation': {'origin': 'legacy', 'record_sha256': 'a' * 64,
                                   'disk':disk, 'packages':{'dhcpcd':'1'},
                                   'kernel_release':'test-kernel', 'configuration_files':{}}}
        source = {'configuration_sha256': 'c' * 64, 'xen_runtime': {'uuid': 'old'},
                  'boot_artifacts': {}, 'disk': disk, 'accepted': accepted}
        current = {**source, 'accepted_record_present': True, 'pending_record_present': False,
                   'xen_runtime_matches': True, 'xen': {'disk': ['phy:/dev/vg0/lv_router,xvda,w']},
                   'logical_volumes': {'report': [{'lv': [{'lv_path': '/dev/vg0/lv_router',
                     'lv_uuid': 'exact', 'lv_size': '2147483648', 'origin': ''}]}]}}
        wrapper = {'kind': 'klokast.router-command-result.v1', 'box': 'boxa',
                   'action': 'accepted-source', 'engine_commit': 'b' * 40, 'result': accepted}
        with patch.object(self.host.runpy, 'run_path', return_value={'inspect_dom0': lambda _: current}), \
                patch.object(self.host, 'run', return_value=SimpleNamespace(stdout=json.dumps(wrapper))) as run:
            self.assertEqual(self.host.source_identity(Path('/operation'), {'box': 'boxa',
                             'engine_commit': 'b' * 40, 'source': source,
                             'guest':{'source_packages':{'dhcpcd':'1'},
                                      'source_kernel_release':'test-kernel','source_files':{}}}), current)
        run.assert_called_once_with(['/usr/local/sbin/router-update-transaction', 'accepted-source', '--box', 'boxa'])
        with patch.object(self.host.runpy, 'run_path', return_value={'inspect_dom0': lambda _: current}), \
                patch.object(self.host, 'run', return_value=SimpleNamespace(stdout=json.dumps({**wrapper, 'result': {}}))):
            with self.assertRaisesRegex(RuntimeError, 'protected accepted router source changed'):
                self.host.source_identity(Path('/operation'), {'box': 'boxa',
                                          'engine_commit': 'b' * 40, 'source': source,
                                          'guest':{'source_packages':{'dhcpcd':'1'},
                                                   'source_kernel_release':'test-kernel','source_files':{}}})

    def test_template_source_requires_its_own_lv_and_recorded_software(self):
        generation_id = 'a' * 24
        disk = {'path':'/dev/vg0/routergen_' + generation_id, 'uuid':'exact', 'bytes':2147483648}
        generation = {'origin':'template', 'generation_id':generation_id, 'record_sha256':'a'*64,
                      'disk':disk, 'packages':{'dhcpcd':'1'}, 'kernel_release':'test-kernel',
                      'configuration_files':{'etc/dhcpcd.conf':'c'*64}}
        accepted = {'kind':'klokast.router-accepted-source.v1', 'box':'boxa',
                    'assignment':{'current_sha256':'a'*64}, 'generation':generation}
        source = {'configuration_sha256':'c'*64, 'xen_runtime':{'uuid':'old'},
                  'boot_artifacts':{}, 'disk':disk, 'accepted':accepted}
        current = {**source, 'accepted_record_present':True, 'pending_record_present':False,
                   'xen_runtime_matches':True, 'xen':{'disk':['phy:' + disk['path'] + ',xvda,w']},
                   'logical_volumes':{'report':[{'lv':[{'lv_path':disk['path'], 'lv_uuid':'exact',
                       'lv_size':'2147483648', 'origin':''}]}]}}
        value = {'box':'boxa', 'engine_commit':'b'*40, 'source':source,
                 'guest':{'source_packages':{'dhcpcd':'1'}, 'source_kernel_release':'test-kernel',
                          'source_files':{'etc/dhcpcd.conf':'c'*64}}}
        wrapper = {'kind':'klokast.router-command-result.v1','box':'boxa','action':'accepted-source',
                   'engine_commit':'b'*40,'result':accepted}
        with patch.object(self.host.runpy, 'run_path', return_value={'inspect_dom0':lambda _:current}), \
                patch.object(self.host, 'run', return_value=SimpleNamespace(stdout=json.dumps(wrapper))):
            self.assertEqual(self.host.source_identity(Path('/operation'), value), current)
            with self.assertRaisesRegex(RuntimeError, 'differs from its accepted generation'):
                self.host.source_identity(Path('/operation'), {**value, 'guest':{
                    **value['guest'], 'source_files':{'etc/dhcpcd.conf':'d'*64}}})
            with self.assertRaisesRegex(RuntimeError, 'differs from its accepted generation'):
                self.host.source_identity(Path('/operation'), {**value, 'source':{
                    **source, 'disk':{'path':'/dev/vg0/lv_router','uuid':'exact','bytes':2147483648}}})

    def test_snapshot_reconciliation_needs_the_explicit_uuid_and_origin(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / self.operation
            root.mkdir()
            planned = {'operation_id': self.operation, 'stage': 'planned',
                       'path': '/dev/vg0/routercompat_' + self.operation,
                       'tag': 'routercompat_' + self.operation, 'origin_uuid': 'source'}
            (root / 'snapshot.json').write_text(json.dumps(planned))
            row = {'lv_uuid': 'snapshot', 'origin_uuid': 'source', 'lv_path': planned['path'],
                   'lv_tags': planned['tag'], 'lv_attr': 'sri-a-s---',
                   'lv_size': str(1024 * self.host.MIB), 'data_percent': '0.1'}
            for change in ({'lv_uuid': 'foreign'}, {'origin_uuid': 'foreign'}, {'lv_tags': 'foreign'}):
                with patch.object(self.host, 'safe_file'), patch.object(self.host, 'source_identity'), \
                     patch.object(self.host, 'snapshot_info', return_value={**row, **change}), \
                     patch.object(self.host, 'run') as run, self.assertRaises(RuntimeError):
                    self.host.reconcile_snapshot(root, {'source': {'disk': {'uuid': 'source'}}}, 'snapshot')
                run.assert_not_called()
                self.assertEqual(json.loads((root / 'snapshot.json').read_text()), planned)


if __name__ == '__main__':
    unittest.main()
