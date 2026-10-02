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
    def test_only_first_old_hold_allows_existing_source_wan_cache(self):
        path = Path(__file__).resolve().parents[1] / 'roles/router-state-copy/files/router-compatibility-guest'
        required = runpy.run_path(str(path))['fresh_wan_required']
        self.assertFalse(required('hold-old', 'old'))
        for phase, previous in (('hold-old', 'hold-new'), ('hold-new', 'hold-old'),
                                ('new', 'seed'), ('old', 'new')):
            self.assertTrue(required(phase, previous))

    def test_guest_initializes_phase_before_hold_decision(self):
        path = Path(__file__).resolve().parents[1] / 'roles/router-state-copy/files/router-compatibility-guest'
        main = runpy.run_path(str(path))['main']
        called = []
        with tempfile.TemporaryDirectory() as root:
            slot = Path(root) / 'result.slot'
            slot.write_bytes(b'\0' * 4096)
            def action(argv, *args, **kwargs):
                if argv == ['poweroff', '-f']:
                    raise SystemExit(0)
            def identify():
                called.append('guard')
                return 'seed', {'operation_id':'a'*24, 'inputs_sha256':'b'*64}
            with patch.dict(main.__globals__, {'run':action, 'guard':identify,
                    'execute':lambda phase, request: {'production_identity':False},
                    'Path':lambda name: slot}), \
                    patch.object(os, 'getpid', return_value=1), \
                    patch.object(os.path, 'ismount', return_value=True), \
                    patch.object(os, 'sync'):
                with self.assertRaises(SystemExit):
                    main()
        self.assertEqual(called, ['guard'])

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
            self.assertEqual(router_compatibility.validate_copy(
                expected, 'new', require_fresh_wan_cache=False)['lan_leases'], current['lan_leases'])
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

    def test_controller_accepts_every_native_phase_in_order(self):
        cli = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'bin/platform-router-update'))
        self.assertEqual(cli['COMPATIBILITY_PHASES'],
                         list(self.host.PHASES + self.host.HOLD_PHASES))

    def test_isolated_fence_targets_verified_xen_id(self):
        with tempfile.TemporaryDirectory() as root:
            value = {'box': 'k001', 'operation_id': self.operation, 'engine_commit': 'b' * 40}
            identity = '11111111-1111-4111-8111-111111111111'
            adapter = self.host.IsolatedRollback(Path(root), value, {'path': '/dev/vg0/test'},
                                                 {'hold-old': identity, 'hold-new': identity}, {})
            adapter.running = 'old'
            live = {'domid': 413, 'config': {'c_info': {'uuid': identity}}}
            with patch.object(self.host, 'domain', side_effect=[live, None]), \
                    patch.object(self.host, 'run') as run:
                adapter.stop('old', deadline=adapter.monotonic() + 30)
            run.assert_called_once_with(['xl', 'destroy', '413'], timeout=30)
            self.assertIsNone(adapter.running)

            adapter.running = 'old'
            wrong = {'domid': 413, 'config': {'c_info': {'uuid': 'other'}}}
            with patch.object(self.host, 'domain', return_value=wrong), \
                    patch.object(self.host, 'run') as run, self.assertRaisesRegex(RuntimeError, 'UUID changed'):
                adapter.stop('old', deadline=adapter.monotonic() + 30)
            run.assert_not_called()

    def test_isolated_boot_waits_for_complete_result_slot(self):
        with tempfile.TemporaryDirectory() as root:
            work = Path(root)
            for name in ('result', 'previous', 'old'):
                (work / (name + '.slot')).write_bytes(b'\0' * 4096)
            identity = '11111111-1111-4111-8111-111111111111'
            value = {'box': 'k001', 'operation_id': self.operation,
                     'inputs_sha256': 'b' * 64, 'engine_commit': 'c' * 40, 'guest': {}}
            phases = {}
            adapter = self.host.IsolatedRollback(work, value, {'path': '/dev/vg0/test'},
                                                 {'hold-old': identity, 'hold-new': identity}, phases)
            live = {'domid': 413, 'config': {'c_info': {'uuid': identity}}}
            complete = {'phase': 'hold-old', 'operation_id': self.operation, 'success': True}
            with patch.object(self.host, 'attach_loop', side_effect=['/dev/loop1', '/dev/loop2', '/dev/loop3']), \
                    patch.object(self.host, 'domain', return_value=live), \
                    patch.object(self.host, 'run') as run, \
                    patch.object(self.host, 'completion_ready', side_effect=[False, True]) as ready, \
                    patch.object(self.host, 'read_slot', return_value=complete) as read, \
                    patch.object(self.host, 'validate_phase'), \
                    patch.object(self.host.time, 'sleep'):
                adapter.boot('old', {'phase': 'old', 'success': True}, deadline=adapter.monotonic() + 30)
            self.assertEqual(ready.call_count, 2)
            read.assert_called_once_with(work / 'result.slot')
            self.assertIn('hold-old', phases)
            self.assertEqual(adapter.running, 'old')
            self.assertIn((['xl', 'unpause', '413'],), [call.args for call in run.call_args_list])

    def test_result_slot_is_allocated_only_after_candidate_clone(self):
        with tempfile.TemporaryDirectory() as root:
            work = Path(root)
            template = work / 'template'
            template.mkdir()
            lifecycle = {'slots': {}, 'operation_id':self.operation}
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

    def test_snapshot_lost_remove_reply_reconciles_with_its_durable_uuid(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)/self.operation; root.mkdir()
            record = {'operation_id':self.operation,'stage':'created','uuid':'snapshot',
                'origin_uuid':'source','path':'/dev/vg0/routercompat_'+self.operation,
                'tag':'routercompat_'+self.operation}
            (root/'snapshot.json').write_text(json.dumps(record))
            row = {'lv_uuid':'snapshot','origin_uuid':'source','lv_path':record['path'],
                'lv_tags':record['tag'],'lv_attr':'sri-a-s---',
                'lv_size':str(1024*self.host.MIB),'data_percent':'0.1'}
            rows = [row]
            def remove(argv):
                self.assertEqual(json.loads((root/'snapshot.json').read_text())['stage'],'retiring')
                rows.clear()
                raise RuntimeError('lost removal reply')
            with patch.object(self.host,'safe_file'), \
                    patch.object(self.host,'snapshot_info',side_effect=lambda *a,**kw: rows[0] if rows else None), \
                    patch.object(self.host.router_native,'Native') as native, \
                    patch.object(self.host,'run',side_effect=remove) as run, \
                    patch.object(self.host,'source_identity'):
                with self.assertRaisesRegex(RuntimeError,'lost removal reply'):
                    self.host.retire_snapshot(root)
                native.return_value.wait_detached.assert_called_once()
                self.host.reconcile_snapshot(root,{'source':{'disk':{'uuid':'source'}}},'snapshot')
                self.host.retire_snapshot(root)
                self.assertEqual(run.call_count,1)
                self.assertEqual(json.loads((root/'snapshot.json').read_text())['stage'],'retired')
                rows.append(row)
                with self.assertRaisesRegex(RuntimeError,'reappeared'):
                    self.host.retire_snapshot(root)
                self.assertEqual(run.call_count,1)

    def test_snapshot_absence_without_intent_and_failed_detachment_preserve_record(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)/self.operation; root.mkdir()
            record = {'operation_id':self.operation,'stage':'created','uuid':'snapshot',
                'origin_uuid':'source','path':'/dev/vg0/routercompat_'+self.operation,
                'tag':'routercompat_'+self.operation}
            (root/'snapshot.json').write_text(json.dumps(record))
            row = {'lv_uuid':'snapshot','origin_uuid':'source','lv_path':record['path'],
                'lv_tags':record['tag'],'lv_attr':'sri-a-s---',
                'lv_size':str(1024*self.host.MIB),'data_percent':'0.1'}
            with patch.object(self.host,'safe_file'),patch.object(self.host,'run') as run, \
                    patch.object(self.host.router_native,'Native') as native:
                with patch.object(self.host,'snapshot_info',return_value=None), \
                        self.assertRaisesRegex(RuntimeError,'without its removal intent'):
                    self.host.retire_snapshot(root)
                native.return_value.wait_detached.side_effect = RuntimeError('backend still attached')
                with patch.object(self.host,'snapshot_info',return_value=row), \
                        self.assertRaisesRegex(RuntimeError,'backend still attached'):
                    self.host.retire_snapshot(root)
                run.assert_not_called()
                self.assertEqual(json.loads((root/'snapshot.json').read_text()),record)

    def test_snapshot_inventory_refuses_renamed_uuid_and_ambiguous_rows(self):
        path='/dev/vg0/routercompat_'+self.operation
        row={'lv_uuid':'snapshot','origin_uuid':'source','lv_path':path,
             'lv_tags':'routercompat_'+self.operation,'lv_attr':'sri-a-s---',
             'lv_size':str(1024*self.host.MIB),'data_percent':'0.1'}
        for rows in ([{**row,'lv_path':'/dev/vg0/renamed'}],[row,row],[{**row,'lv_tags':None}]):
            with patch.object(self.host,'run',return_value=SimpleNamespace(stdout=json.dumps({'report':[{'lv':rows}]}))), \
                    self.assertRaises(RuntimeError):
                self.host.snapshot_info(path,identity='snapshot')
        with patch.object(self.host,'run',return_value=SimpleNamespace(stdout=json.dumps({'report':[{'lv':[]}]}))):
            self.assertIsNone(self.host.snapshot_info(path,identity='snapshot'))
        with patch.object(self.host,'run',return_value=SimpleNamespace(stdout=json.dumps({'report':[]}))), \
                self.assertRaisesRegex(RuntimeError,'inventory is incomplete'):
            self.host.snapshot_info(path,identity='snapshot')


class PartialCleanupTests(unittest.TestCase):
    def setUp(self):
        self.host=module('router-compatibility-dom0')
        self.operation='a'*24
        temporary=tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.work=Path(temporary.name)/self.operation; self.work.mkdir(mode=0o700)
        previous=os.umask(0o077); self.addCleanup(os.umask,previous)
        self.record={'operation_id':self.operation,'stage':'allocating',
            'uuids':{phase:'11111111-1111-4111-8111-111111111111' for phase in self.host.PHASES+self.host.HOLD_PHASES},
            'slots':{}}
        self.host.write(self.work/'lifecycle.json',self.record)
        def ref(data):
            return {'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
        self.value={'source':{'disk':{'uuid':'source'},'boot_artifacts':{
                'kernel':{'path':'/protected/kernel',**ref(b'old')},
                'ramdisk':{'path':'/protected/initramfs',**ref(b'oldram')}}},
            'engine_commit':'b'*40,'box':'boxa','operation_id':self.operation,'inputs_sha256':'d'*64,
            'bootstrap':{'kernel':ref(b'ker'),'initramfs':ref(b'ram')},
            'template':{'operation':'c'*24,'sha256':None}}
        for name,data in (('kernel',b'ker'),('initramfs',b'ram')):
            (self.work/name).write_bytes(data)
        self.boot_bytes=6
        self.templates=self.work.parent/'templates'; template=self.templates/('c'*24)
        template.mkdir(parents=True,mode=0o700)
        candidate={'kind':'klokast.router-template-candidate.v1','box':'boxa','role':'router',
            'operation_id':'c'*24,'inputs_sha256':'d'*64,'artifacts':{'kernel':ref(b'new'),'initramfs':ref(b'newram')}}
        self.host.write(template/'candidate.json',candidate)
        self.value['template']['sha256']=hashlib.sha256((template/'candidate.json').read_bytes()).hexdigest()
        self.native=unittest.mock.Mock()
        self.native.inventory.return_value=[{'domid':0,'config':{'c_info':{'uuid':'dom0'}}}]
        for owner,name,value in ((self.host,'TEMPLATES',self.templates),(self.host,'safe_file',unittest.mock.Mock()),
                (self.host,'request',unittest.mock.Mock(return_value=self.value)),
                (self.host,'source_identity',unittest.mock.Mock()),
                (self.host,'domain',unittest.mock.Mock(return_value=None)),
                (self.host,'loop_devices',unittest.mock.Mock(return_value=[])),
                (self.host,'snapshot_info',unittest.mock.Mock(return_value=None)),
                (self.host.router_native,'Native',unittest.mock.Mock(return_value=self.native)),
                (self.host.router_candidate_disk,'observed',unittest.mock.Mock(return_value=None)),
                (self.host.router_candidate_disk,'refuse_referenced_disk',unittest.mock.Mock())):
            selected=patch.object(owner,name,value); selected.start(); self.addCleanup(selected.stop)

    def cleanup(self):
        return self.host.cleanup(self.work,self.operation,'boxa')

    def allocate(self,name='copy'):
        self.host.allocate_slot(self.work,name,self.record)
        return self.work/(name+'.slot')

    def test_partial_allocation_failure_and_completed_subset_are_retired(self):
        first=self.allocate()
        def interrupted(descriptor,offset,size):
            os.ftruncate(descriptor,73)
            raise OSError('allocation interrupted')
        with patch.object(self.host.os,'posix_fallocate',side_effect=interrupted),self.assertRaises(OSError):
            self.allocate('previous')
        pending=self.work/'previous.slot'
        intent=json.loads((self.work/'slot-previous.json').read_text())
        self.assertEqual(intent['stage'],'allocating')
        self.assertEqual(intent['identity']['inode'],pending.stat().st_ino)
        amount=self.cleanup()
        self.assertEqual(amount,self.host.MIB+73+self.boot_bytes)
        self.assertFalse(first.exists()); self.assertFalse(pending.exists())
        self.assertEqual(self.cleanup(),amount)
        self.assertEqual(json.loads((self.work/'lifecycle.json').read_text())['stage'],'cleaned')
        result=json.loads((self.work/'cleanup-complete.json').read_text())
        self.assertEqual(result['status'],'unused-disks-retired')
        self.assertEqual(result['bytes_reclaimed'],amount-self.boot_bytes)
        complete=json.loads((self.work/'cleanup-complete.v2.json').read_text())
        self.assertEqual(complete['status'],'unused-resources-retired')
        self.assertEqual(complete['bytes_reclaimed'],amount)
        self.assertFalse((self.work/'kernel').exists())

    def test_created_file_before_inode_reply_is_bound_from_its_explicit_intent(self):
        target=self.work/'copy.slot'; target.write_bytes(b'raw')
        self.host.write(self.work/'slot-copy.json',{'kind':'klokast.router-compatibility-slot.v1',
            'operation_id':self.operation,'slot':'copy','maximum':self.host.MIB,'stage':'planned','identity':None})
        self.assertEqual(self.cleanup(),3+self.boot_bytes)
        self.assertFalse(target.exists())

    def test_allocated_intent_survives_loss_of_lifecycle_write(self):
        original=self.host.write
        def fail(path,value):
            if path.name=='lifecycle.json':
                raise RuntimeError('lifecycle write lost')
            return original(path,value)
        with patch.object(self.host,'write',side_effect=fail),self.assertRaisesRegex(RuntimeError,'write lost'):
            self.allocate()
        self.assertEqual(json.loads((self.work/'lifecycle.json').read_text())['slots'],{})
        self.assertEqual(self.cleanup(),self.host.MIB+self.boot_bytes)

    def test_lost_unlink_reply_retries_exact_intent_and_reappearance_refuses(self):
        target=self.allocate(); unlink=Path.unlink
        def lost(path,*args,**kwargs):
            unlink(path,*args,**kwargs)
            if path==target:
                raise RuntimeError('unlink reply lost')
        with patch.object(Path,'unlink',lost),self.assertRaisesRegex(RuntimeError,'reply lost'):
            self.cleanup()
        self.assertEqual(json.loads((self.work/'cleanup-progress.json').read_text())['inflight'],'copy')
        self.assertEqual(self.cleanup(),self.host.MIB+self.boot_bytes)
        target.write_bytes(b'reappeared')
        with self.assertRaisesRegex(RuntimeError,'reappeared'):
            self.cleanup()

    def test_unknown_missing_changed_and_attached_files_preserve_the_barrier(self):
        target=self.allocate()
        original=target.read_bytes()
        self.host.loop_devices.return_value=['/dev/loop7']
        with self.assertRaisesRegex(RuntimeError,'attachment'):
            self.cleanup()
        self.host.loop_devices.return_value=[]
        target.replace(self.work/'retained-original')
        with self.assertRaisesRegex(RuntimeError,'without removal intent'):
            self.cleanup()
        target.write_bytes(original)
        with self.assertRaisesRegex(RuntimeError,'recorded allocation'):
            self.cleanup()
        target.unlink()
        self.host.write(self.work/'lifecycle.json',{**self.record,'slots':{}})
        (self.work/'slot-copy.json').unlink()
        target.write_bytes(b'unrecorded')
        with self.assertRaisesRegex(RuntimeError,'no recorded allocation intent'):
            self.cleanup()

    def test_live_renamed_helper_and_unrecorded_lv_prevent_deletion(self):
        target=self.allocate()
        self.native.inventory.return_value=[{'domid':3,'config':{'c_info':{
            'uuid':self.record['uuids']['prepare'],'name':'renamed-helper'}}}]
        with self.assertRaisesRegex(RuntimeError,'helper UUID remains'):
            self.cleanup()
        self.native.inventory.return_value=[]
        self.host.router_candidate_disk.observed.return_value={'lv_uuid':'unrecorded'}
        with self.assertRaisesRegex(RuntimeError,'no allocation record'):
            self.cleanup()
        self.assertTrue(target.exists())

    def test_empty_partial_operation_and_planned_absent_snapshot_cleanup(self):
        self.host.write(self.work/'snapshot.json',{'operation_id':self.operation,'stage':'planned',
            'path':'/dev/vg0/routercompat_'+self.operation,'tag':'routercompat_'+self.operation,'origin_uuid':'source'})
        self.assertEqual(self.cleanup(),self.boot_bytes)
        self.assertEqual(json.loads((self.work/'snapshot.json').read_text())['stage'],'aborted')
        self.assertEqual(self.cleanup(),self.boot_bytes)
        self.host.snapshot_info.return_value={'lv_uuid':'reappeared'}
        with self.assertRaisesRegex(RuntimeError,'reappeared'):
            self.cleanup()

    def test_removed_slot_loop_and_unplanned_slot_reappearance_refuse_retry(self):
        self.allocate(); self.cleanup()
        self.host.loop_devices.return_value=['/dev/loop7']
        with self.assertRaisesRegex(RuntimeError,'loop attachment'):
            self.cleanup()
        self.host.loop_devices.return_value=[]
        (self.work/'previous.slot').write_bytes(b'new')
        with self.assertRaisesRegex(RuntimeError,'outside its cleanup plan'):
            self.cleanup()

    def test_runtime_loop_inventory_detects_a_deleted_backing_file(self):
        import xen_build_runtime as runtime
        directory=self.work/'sys-block'; backing=directory/'loop7/loop/backing_file'
        backing.parent.mkdir(parents=True)
        selected=self.work/'removed.slot'
        backing.write_text(str(selected)+' (deleted)\n')
        with patch.object(runtime,'Path',return_value=directory):
            self.assertEqual(runtime.loop_devices(selected),['/dev/loop7'])

    def test_runtime_journal_stale_temporary_does_not_block_retry(self):
        import xen_build_runtime as runtime
        target=self.work/'record.json'; stale=self.work/'record.new'
        stale.write_bytes(b'old interrupted output')
        runtime.write(target,{'phase':'new'})
        self.assertEqual(json.loads(target.read_text()),{'phase':'new'})
        self.assertEqual(stale.read_bytes(),b'old interrupted output')
        with patch.object(runtime.os,'replace',side_effect=OSError('rename lost')),self.assertRaises(OSError):
            runtime.write(target,{'phase':'failed'})
        self.assertEqual(list(self.work.glob('.record.json-*')),[])
        self.assertEqual(json.loads(target.read_text()),{'phase':'new'})


if __name__ == '__main__':
    unittest.main()


class HistoricalArtifactTests(unittest.TestCase):
    def setUp(self):
        import test_router_records as record_tests
        self.case=PartialCleanupTests(); self.case.setUp(); self.addCleanup(self.case.doCleanups)
        self.host=self.case.host; self.work=self.case.work; self.operation=self.case.operation
        self.storage_case=record_tests.RecordsTests(); self.storage_case.setUp()
        self.addCleanup(self.storage_case.doCleanups)
        selected=patch.object(self.host.router_candidate_disk.records,'BASE',self.storage_case.base)
        selected.start(); self.addCleanup(selected.stop)
        self.record={'operation_id':self.operation,'stage':'cleaned',
            'uuids':{phase:'11111111-1111-4111-8111-111111111111' for phase in self.host.PHASES},
            'slots':{name:{'device':1,'inode':i+1,'bytes':size} for i,(name,size) in enumerate(self.host.SLOTS.items())}}
        self.host.write(self.work/'lifecycle.json',self.record)
        self.snapshot={'operation_id':self.operation,'path':'/dev/vg0/routercompat_'+self.operation,
            'tag':'routercompat_'+self.operation,'origin_uuid':'source','uuid':'snapshot','stage':'retired'}
        self.host.write(self.work/'snapshot.json',self.snapshot)

    def retire(self):
        return self.host.retire_historical_artifacts(self.work,self.operation,'boxa')

    def test_historical_file_disk_layout_retires_only_boots_and_preserves_records(self):
        result=self.retire()
        self.assertEqual(result['status'],'historical-artifacts-retired')
        self.assertEqual(result['bytes_reclaimed'],self.case.boot_bytes)
        retry=self.retire()
        self.assertEqual({k:v for k,v in retry.items() if k!='removed_now'},
                         {k:v for k,v in result.items() if k!='removed_now'})
        self.assertEqual(retry['removed_now'],[])
        self.assertFalse((self.work/'kernel').exists())
        self.assertTrue((self.work/'lifecycle.json').exists())
        self.assertFalse((self.work/'cleanup-complete.v2.json').exists())
        self.host.source_identity.assert_not_called()
        self.host.router_candidate_disk.refuse_referenced_disk.assert_called()

    def test_historical_lvm_layout_requires_retired_exact_allocation(self):
        self.record['slots'].pop('new'); self.host.write(self.work/'lifecycle.json',self.record)
        with self.assertRaisesRegex(RuntimeError,'candidate layout'):
            self.retire()
        candidate={'kind':'klokast.router-candidate-disk.v1','operation_id':self.operation,
            'path':'/dev/vg0/routergen_'+self.operation,'tag':'routergen_'+self.operation,
            'uuid':'candidate','stage':'retired','template_sha256':'e'*64}
        self.host.write(self.work/'candidate-disk.json',candidate)
        self.assertEqual(self.retire()['status'],'historical-artifacts-retired')
        candidate['stage']='cloned'; self.host.write(self.work/'candidate-disk.json',candidate)
        with self.assertRaisesRegex(RuntimeError,'candidate layout'):
            self.retire()

    def test_reappeared_slot_lv_or_renamed_helper_refuses_before_boot_unlink(self):
        (self.work/'new.slot').write_bytes(b'wrong')
        with self.assertRaisesRegex(RuntimeError,'slot or loop'):
            self.retire()
        (self.work/'new.slot').unlink()
        self.host.snapshot_info.return_value={'lv_uuid':'snapshot'}
        with self.assertRaisesRegex(RuntimeError,'LV reappeared'):
            self.retire()
        self.host.snapshot_info.return_value=None
        self.case.native.inventory.return_value=[{'domid':7,'config':{'c_info':{
            'uuid':next(iter(self.record['uuids'].values())),'name':'renamed'}}}]
        with self.assertRaisesRegex(RuntimeError,'helper remains'):
            self.retire()
        self.assertTrue((self.work/'kernel').exists())
        self.assertFalse((self.work/'historical-cleanup-complete.json').exists())

    def test_changed_or_reappeared_boot_and_missing_retirement_intent_refuse(self):
        (self.work/'kernel').write_bytes(b'bad')
        with self.assertRaisesRegex(RuntimeError,'declared source'):
            self.retire()
        (self.work/'kernel').write_bytes(b'ker')
        (self.work/'initramfs').unlink()
        with self.assertRaisesRegex(RuntimeError,'disappeared'):
            self.retire()
        (self.work/'initramfs').write_bytes(b'ram')
        self.retire(); (self.work/'kernel').write_bytes(b'ker')
        with self.assertRaisesRegex(RuntimeError,'reappeared'):
            self.retire()

    def test_lost_unlink_reply_resumes_exact_plan(self):
        unlink=Path.unlink; lost=False
        def removing(path,*args,**kwargs):
            nonlocal lost
            unlink(path,*args,**kwargs)
            if path==self.work/'kernel' and not lost:
                lost=True; raise OSError('lost unlink reply')
        with patch.object(Path,'unlink',removing),self.assertRaisesRegex(OSError,'lost unlink reply'):
            self.retire()
        self.assertEqual(self.retire()['bytes_reclaimed'],self.case.boot_bytes)

    def test_legacy_request_is_cleanup_only_and_keeps_matching_guest_contract(self):
        host=module('router-compatibility-dom0')
        selected=patch.object(host,'safe_file'); selected.start(); self.addCleanup(selected.stop)
        value=copy.deepcopy(self.case.value)
        value.update(kind='klokast.router-compatibility-host.v1',role='router')
        value['guest']={'kind':'klokast.router-compatibility-request.v1','operation_id':self.operation,
            'inputs_sha256':value['inputs_sha256'],'box':'boxa','source_packages':{},'source_files':{},
            'source_kernel_release':'test','fixture':{},'manifest':{'inputs_sha256':value['inputs_sha256']},
            'runtime_packages':{},'kernel_release':'test'}
        host.write(self.work/'request.json',value)
        with self.assertRaisesRegex(RuntimeError,'invalid target'):
            host.request(self.work,'boxa',self.operation,verify_boot=False)
        self.assertEqual(host.request(self.work,'boxa',self.operation,verify_boot=False,historical=True),value)
        value['guest']['kind']='klokast.router-compatibility-request.v2'; host.write(self.work/'request.json',value)
        with self.assertRaisesRegex(RuntimeError,'guest contract'):
            host.request(self.work,'boxa',self.operation,verify_boot=False,historical=True)


    def test_original_legacy_hash_only_boot_records_resume_after_unlink(self):
        for name,field,data in (('old-kernel','kernel',b'old'),('old-initramfs','ramdisk',b'oldram')):
            item=self.case.value['source']['boot_artifacts'][field]
            item.pop('bytes'); item['path']='/mnt/dom0_data/xen_images/router-'+('kernel' if field=='kernel' else 'initramfs')
            (self.work/name).write_bytes(data)
        result=self.retire()
        self.assertEqual(result['bytes_reclaimed'],self.case.boot_bytes+9)
        self.assertEqual(self.retire()['removed_now'],[])

    def test_legacy_hash_only_boot_records_refuse_unknown_sources_or_changed_bytes(self):
        item=self.case.value['source']['boot_artifacts']['kernel']; item.pop('bytes')
        (self.work/'old-kernel').write_bytes(b'bad')
        with self.assertRaisesRegex(RuntimeError,'unknown identity'):
            self.retire()
        item['path']='/mnt/dom0_data/xen_images/router-kernel'
        with self.assertRaisesRegex(RuntimeError,'declared source'):
            self.retire()
        self.assertTrue((self.work/'kernel').exists())


    def test_explicit_historical_absence_reconciles_without_claiming_removed_bytes(self):
        for name in ('kernel','initramfs'):
            (self.work/name).unlink()
        with self.assertRaisesRegex(RuntimeError,'disappeared'):
            self.retire()
        result=self.host.retire_historical_artifacts(self.work,self.operation,'boxa',absent_only=True)
        self.assertEqual(result['status'],'historical-artifacts-absent')
        self.assertEqual(result['bytes_reclaimed'],0)
        self.assertEqual(result['removed_now'],[])
        self.assertTrue((self.work/'artifact-cleanup-progress.json').exists())
        self.assertEqual(self.host.retire_historical_artifacts(self.work,self.operation,'boxa',absent_only=True),result)

    def test_historical_absence_refuses_any_existing_boot_copy(self):
        with self.assertRaisesRegex(RuntimeError,'every boot copy'):
            self.host.retire_historical_artifacts(self.work,self.operation,'boxa',absent_only=True)
        self.assertTrue((self.work/'kernel').exists())
        self.assertFalse((self.work/'artifact-cleanup-plan.json').exists())
