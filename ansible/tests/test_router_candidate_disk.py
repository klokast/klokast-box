"""Allocation and retirement must preserve exact ownership through interruptions."""
import copy
from contextlib import nullcontext
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import router_candidate_disk as c
from router_transaction import TransactionError


class DiskTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.operation = 'a'*24
        self.box = 'boxa'
        self.path, self.tag = c.selection(self.operation)
        self.row = {'lv_path':self.path, 'lv_uuid':'exact-uuid', 'lv_size':str(c.BYTES),
                    'origin':'', 'lv_attr':'-wi-a-----', 'lv_tags':self.tag}
        self.value = {'kind':'klokast.router-candidate-disk.v1', 'operation_id':self.operation,
                      'path':self.path, 'tag':self.tag, 'uuid':'exact-uuid', 'stage':'cloned',
                      'template_sha256':'b'*64}
        for name, value in (('ROOT_UID',os.geteuid()), ('parents',lambda path:None)):
            p=patch.object(c.records,name,value); p.start(); self.addCleanup(p.stop)
        p=patch.object(c.records,'BASE',self.work/'protected'); p.start(); self.addCleanup(p.stop)
        self.host=Mock()
        p=patch.object(c.native,'Native',return_value=self.host);p.start();self.addCleanup(p.stop)

    def store(self, **changes):
        c.records.write(self.work/'candidate-disk.json',{**self.value,**changes})

    def test_validation_rejects_foreign_disks_and_ownership(self):
        self.assertEqual(c.validate_row(self.row,self.operation,'exact-uuid')['path'],self.path)
        for field,value in (('lv_uuid','foreign'),('lv_path','/dev/vg0/lv_router'),
                            ('lv_tags','unowned'),('origin','snapshot-source'),('lv_size','1'),
                            ('lv_attr','swi-a-----')):
            with self.subTest(field=field),self.assertRaises(TransactionError):
                c.validate_row({**self.row,field:value},self.operation,'exact-uuid')
        for op in ('../escape', 'a'*23, None):
            with self.assertRaises(TransactionError): c.selection(op)

    def test_retirement_checks_backends_before_any_remove(self):
        self.store()
        self.host.wait_detached.side_effect=TransactionError('still attached')
        with patch.object(c,'observed',return_value=self.row),patch.object(c.native,'command') as command:
            with self.assertRaisesRegex(TransactionError,'still attached'):
                c.retire(self.work,self.operation,box=self.box)
            command.assert_not_called()
        self.assertEqual(c.record(self.work,self.operation)['stage'],'cloned')

    def test_retirement_uses_exact_identity_and_is_repeatable_after_removal(self):
        self.store()
        with patch.object(c,'observed',side_effect=[self.row,self.row,None]),patch.object(c.native,'command') as command:
            self.assertEqual(c.retire(self.work,self.operation,box=self.box),c.BYTES)
            self.assertEqual(command.call_args.args[0],['/sbin/lvremove','--yes',self.path])
        with patch.object(c,'observed',return_value=None),patch.object(c.native,'command') as command:
            self.assertEqual(c.retire(self.work,self.operation,box=self.box),0)
            command.assert_not_called()
        self.assertEqual(c.record(self.work,self.operation)['stage'],'retired')

    def test_missing_before_retirement_and_reappearing_after_retirement_fail(self):
        self.store()
        with patch.object(c,'observed',return_value=None),self.assertRaises(TransactionError):
            c.retire(self.work,self.operation,box=self.box)
        self.store(stage='retired')
        with patch.object(c,'observed',return_value=self.row),self.assertRaisesRegex(TransactionError,'reappeared'):
            c.retire(self.work,self.operation,box=self.box)
        self.store(stage='retiring')
        with patch.object(c,'observed',return_value=None):
            self.assertEqual(c.retire(self.work,self.operation,box=self.box),0)

    def test_renamed_uuid_is_not_absence_even_with_a_durable_retirement_intent(self):
        for stage in ('cloned', 'retiring', 'retired'):
            self.store(stage=stage)
            renamed = {**self.row, 'lv_path':'/dev/vg0/renamed-candidate'}
            with self.subTest(stage=stage), patch.object(c, 'inventory', return_value=[renamed]), \
                    patch.object(c.native, 'command') as command:
                with self.assertRaisesRegex(TransactionError, 'UUID moved'):
                    c.retire(self.work, self.operation, box=self.box)
                command.assert_not_called()
                self.assertEqual(c.record(self.work, self.operation)['stage'], stage)

    def test_interrupted_allocation_requires_explicit_observed_uuid(self):
        self.store(stage='planned',uuid=None)
        with patch.object(c,'observed',return_value=self.row),patch.object(c.native,'command') as command:
            with self.assertRaisesRegex(TransactionError,'explicitly inspected'):
                c.retire(self.work,self.operation,box=self.box)
            with self.assertRaises(TransactionError):
                c.retire(self.work,self.operation,box=self.box,inspected_uuid='foreign')
            command.assert_not_called()
        with patch.object(c,'observed',side_effect=[self.row,self.row,None]),patch.object(c.native,'command'):
            self.assertEqual(c.retire(self.work,self.operation,box=self.box,inspected_uuid='exact-uuid'),c.BYTES)

    def test_failed_lvcreate_with_exact_absence_records_aborted_operation(self):
        self.store(stage='planned',uuid=None)
        with patch.object(c,'observed',return_value=None),patch.object(c.native,'command') as command:
            self.assertEqual(c.retire(self.work,self.operation,box=self.box),0)
            self.assertEqual(c.retire(self.work,self.operation,box=self.box),0)
            command.assert_not_called()
        self.assertEqual(c.record(self.work,self.operation)['stage'],'aborted')
        with patch.object(c,'observed',return_value=self.row),self.assertRaisesRegex(TransactionError,'aborted'):
            c.retire(self.work,self.operation,box=self.box)

    def test_retirement_refuses_accepted_and_pending_references(self):
        self.store()
        protected=self.work/'protected'
        protected.mkdir()
        generation={'disk':{'path':self.path,'uuid':'exact-uuid'}}
        storage=Mock()
        storage.lock.return_value=nullcontext()
        storage.installation.return_value=None
        storage.accepted.return_value={'current_sha256':'b'*64,'previous_sha256':None}
        storage.generation.return_value=generation
        (protected/'accepted.json').write_text('{}')
        with patch.object(c.records,'Records',return_value=storage),patch.object(c,'observed',return_value=self.row), \
             patch.object(c.native,'command') as command:
            storage.pending.return_value=None
            with self.assertRaisesRegex(TransactionError,'referenced'):
                c.retire(self.work,self.operation,box=self.box)
            command.assert_not_called()
            (protected/'accepted.json').unlink()
            storage.pending.return_value={'request':{'operation_id':self.operation}}
            with self.assertRaisesRegex(TransactionError,'pending production'):
                c.retire(self.work,self.operation,box=self.box)
            command.assert_not_called()

    def test_retirement_preserves_each_recorded_initial_installation_stage(self):
        self.store()
        protected=self.work/'protected'
        protected.mkdir()
        storage=Mock()
        storage.lock.return_value=nullcontext()
        storage.pending.return_value=None
        with patch.object(c.records,'Records',return_value=storage),patch.object(c,'observed',return_value=self.row), \
             patch.object(c.native,'command') as command:
            for stage in ('allocated','prepared','enrolled','verified'):
                with self.subTest(stage=stage):
                    storage.installation.return_value={'operation_id':self.operation,
                        'disk':{'path':self.path,'uuid':'exact-uuid'}}
                    with self.assertRaisesRegex(TransactionError,'recorded first installation'):
                        c.retire(self.work,self.operation,box=self.box)
                    self.assertEqual(c.record(self.work,self.operation)['stage'],'cloned')
            command.assert_not_called()

    def test_retirement_refuses_reused_installation_disk_uuid(self):
        self.store()
        protected=self.work/'protected'
        protected.mkdir()
        storage=Mock()
        storage.lock.return_value=nullcontext()
        storage.installation.return_value={'operation_id':'b'*24,
            'disk':{'path':'/dev/vg0/routergen_'+'b'*24,'uuid':'exact-uuid'}}
        with patch.object(c.records,'Records',return_value=storage),patch.object(c,'observed',return_value=self.row), \
             patch.object(c.native,'command') as command:
            with self.assertRaisesRegex(TransactionError,'recorded first installation'):
                c.retire(self.work,self.operation,box=self.box)
            command.assert_not_called()

    def test_clone_refuses_an_existing_initial_installation_operation(self):
        protected=self.work/'protected'
        protected.mkdir()
        storage=Mock()
        storage.installation.return_value={'operation_id':self.operation,
            'disk':{'path':self.path,'uuid':'exact-uuid'}}
        with patch.object(c.records,'Records',return_value=storage),patch.object(c,'observed',return_value=None), \
             patch.object(c.native,'command') as command:
            with self.assertRaisesRegex(TransactionError,'recorded first installation'):
                c.create(self.work,self.operation,self.work/'template',
                         {'sha256':'b'*64,'bytes':c.BYTES},box=self.box)
            command.assert_not_called()
        self.assertFalse((self.work/'candidate-disk.json').exists())

    def test_changed_template_refuses_before_lvcreate(self):
        source=self.work/'template'
        with source.open('wb') as f: f.truncate(c.BYTES)
        with patch.object(c,'safe_file'),patch.object(c,'observed',return_value=None), \
             patch.object(c,'checksum',return_value='c'*64),patch.object(c.native,'command') as command:
            with self.assertRaisesRegex(TransactionError,'template bytes'):
                c.create(self.work,self.operation,source,{'sha256':'b'*64,'bytes':c.BYTES},box=self.box)
            command.assert_not_called()
        self.assertFalse((self.work/'candidate-disk.json').exists())

    def test_clone_failure_keeps_exact_allocation_and_never_reformats_on_retry(self):
        source=self.work/'template'
        with source.open('wb') as f: f.truncate(c.BYTES)
        def command(argv,*args,**kwargs):
            if argv[0]=='/bin/dd': raise TransactionError('interrupted opaque clone')
        with patch.object(c,'safe_file'),patch.object(c,'observed',side_effect=[None,self.row,self.row]), \
             patch.object(c,'checksum',return_value='b'*64),patch.object(c.native,'command',side_effect=command):
            with self.assertRaisesRegex(TransactionError,'interrupted opaque clone'):
                c.create(self.work,self.operation,source,{'sha256':'b'*64,'bytes':c.BYTES},box=self.box)
        self.assertEqual(c.record(self.work,self.operation)['uuid'],'exact-uuid')
        self.assertEqual(c.record(self.work,self.operation)['stage'],'allocated')
        with patch.object(c.native,'command') as command:
            with self.assertRaisesRegex(TransactionError,'already exists'):
                c.create(self.work,self.operation,source,{'sha256':'b'*64,'bytes':c.BYTES},box=self.box)
            command.assert_not_called()

    def test_clone_records_uuid_before_copy_and_checks_complete_bytes(self):
        source=self.work/'template'
        with source.open('wb') as f: f.truncate(c.BYTES)
        commands=[]
        def command(argv,*args,**kwargs):
            value=c.record(self.work,self.operation)
            commands.append(argv)
            if argv[0]=='/sbin/lvcreate':
                self.assertEqual(value['stage'],'planned')
                self.assertIsNone(value['uuid'])
                self.assertEqual(argv[-4:],['--wipesignatures','y','--yes','vg0'])
            else:
                self.assertEqual(value['stage'],'allocated')
                self.assertEqual(value['uuid'],'exact-uuid')
                self.assertIn('of='+self.path,argv)
        with patch.object(c,'safe_file'),patch.object(c,'observed',side_effect=[None,self.row,self.row,self.row]), \
             patch.object(c,'checksum',return_value='b'*64) as checksum, \
             patch.object(c.native,'command',side_effect=command):
            disk=c.create(self.work,self.operation,source,{'sha256':'b'*64,'bytes':c.BYTES},box=self.box)
            self.assertEqual(disk['uuid'],'exact-uuid')
            self.assertEqual(checksum.call_args.args,(Path(self.path),c.BYTES))
        self.assertEqual([v[0] for v in commands],['/sbin/lvcreate','/bin/dd'])
        self.assertEqual(c.record(self.work,self.operation)['stage'],'cloned')

    def initial_storage(self):
        base = self.work / 'protected'
        base.mkdir(mode=0o700)
        for name in ('records', 'generations', 'operations'):
            (base / name).mkdir(mode=0o700)
        storage = c.records.Records(self.box, base)
        storage.record_installation(c.generations.seal({
            'kind':'klokast.router-initial-installation.v1', 'box':self.box, 'role':'router',
            'operation_id':self.operation, 'engine_commit':'a'*40, 'selection_sha256':'c'*64,
            'release_sha256':'d'*64, 'stage':'planned',
            'disk':{'path':self.path, 'uuid':None, 'bytes':c.BYTES},
            'preparation_sha256':None, 'enrollment_sha256':None,
            'machine_id':None, 'generation_sha256':None}))
        return storage

    def test_initial_clone_records_uuid_before_copy_and_resumes_only_that_allocation(self):
        storage = self.initial_storage()
        source = self.work / 'template'
        with source.open('wb') as stream: stream.truncate(c.BYTES)
        expected = {'bytes':c.BYTES, 'sha256':'b'*64}
        def interrupted(argv, *args, **kwargs):
            if argv[0] == '/sbin/lvcreate':
                self.assertEqual(storage.installation()['stage'], 'planned')
            else:
                self.assertEqual(storage.installation()['stage'], 'allocated')
                self.assertEqual(storage.installation()['disk']['uuid'], 'exact-uuid')
                raise RuntimeError('opaque copy interrupted')
        with patch.object(c, 'safe_file'), patch.object(c, 'checksum', return_value='b'*64), \
                patch.object(c, 'observed', side_effect=[None, self.row, self.row]), \
                patch.object(c.native, 'command', side_effect=interrupted):
            with self.assertRaisesRegex(RuntimeError, 'opaque copy interrupted'):
                c.initial_clone(self.work, self.operation, source, expected, storage)
        self.assertEqual(c.record(self.work, self.operation)['stage'], 'allocated')
        with patch.object(c, 'safe_file'), patch.object(c, 'checksum', return_value='b'*64), \
                patch.object(c, 'observed', return_value=self.row), patch.object(c.native, 'command') as command:
            disk = c.initial_clone(self.work, self.operation, source, expected, storage)
            self.assertEqual(disk['uuid'], 'exact-uuid')
            self.assertEqual([call.args[0][0] for call in command.call_args_list], ['/bin/dd'])
            command.reset_mock()
            (self.work / 'preparation.json').write_text('preparation has started')
            self.assertEqual(c.initial_clone(self.work, self.operation, source, expected, storage), disk)
            command.assert_not_called()

    def test_initial_clone_never_adopts_an_unrecorded_native_uuid(self):
        storage = self.initial_storage()
        source = self.work / 'template'
        with source.open('wb') as stream: stream.truncate(c.BYTES)
        with patch.object(c, 'safe_file'), patch.object(c, 'checksum', return_value='b'*64), \
                patch.object(c, 'observed', return_value=self.row), patch.object(c.native, 'command') as command:
            with self.assertRaisesRegex(TransactionError, 'unrecorded disk'):
                c.initial_clone(self.work, self.operation, source,
                    {'bytes':c.BYTES, 'sha256':'b'*64}, storage)
            self.store(stage='planned', uuid=None)
            with self.assertRaisesRegex(TransactionError, 'incomplete or different allocation'):
                c.initial_clone(self.work, self.operation, source,
                    {'bytes':c.BYTES, 'sha256':'b'*64}, storage)
            command.assert_not_called()
        self.assertIsNone(storage.installation()['disk']['uuid'])

    def test_initial_clone_cannot_copy_over_a_preparation_that_has_started(self):
        storage = self.initial_storage()
        self.store(stage='allocated')
        (self.work / 'result.slot').write_bytes(b'partial preparation')
        source = self.work / 'template'
        with source.open('wb') as stream: stream.truncate(c.BYTES)
        with patch.object(c, 'safe_file'), patch.object(c, 'checksum', return_value='b'*64), \
                patch.object(c, 'observed', return_value=self.row), patch.object(c.native, 'command') as command:
            with self.assertRaisesRegex(TransactionError, 'preparation has started'):
                c.initial_clone(self.work, self.operation, source,
                    {'bytes':c.BYTES, 'sha256':'b'*64}, storage)
            command.assert_not_called()

    def test_interrupted_initial_plan_with_proven_absence_can_allocate_once(self):
        storage = self.initial_storage()
        self.store(stage='planned', uuid=None)
        source = self.work / 'template'
        with source.open('wb') as stream: stream.truncate(c.BYTES)
        with patch.object(c, 'safe_file'), patch.object(c, 'checksum', return_value='b'*64), \
                patch.object(c, 'observed', side_effect=[None, self.row, self.row, self.row]), \
                patch.object(c.native, 'command') as command:
            disk = c.initial_clone(self.work, self.operation, source,
                {'bytes':c.BYTES, 'sha256':'b'*64}, storage)
            self.assertEqual(disk, storage.installation()['disk'])
            self.assertEqual([call.args[0][0] for call in command.call_args_list], ['/sbin/lvcreate', '/bin/dd'])

    def test_replacement_clone_resumes_only_recorded_allocation_for_accepted_router(self):
        source = self.work / 'template'
        with source.open('wb') as stream: stream.truncate(c.BYTES)
        expected = {'bytes':c.BYTES, 'sha256':'b'*64}
        old = 'c'*64
        storage = Mock(box=self.box)
        storage.pending.return_value = None
        storage.accepted.return_value = {'current_sha256':old}
        storage.cold_test.return_value = None

        def interrupted(argv, *args, **kwargs):
            if argv[0] == '/bin/dd':
                raise RuntimeError('candidate copy interrupted')

        with patch.object(c,'safe_file'), patch.object(c,'checksum',return_value='b'*64), \
                patch.object(c,'observed',side_effect=[None,self.row,self.row]), \
                patch.object(c.native,'command',side_effect=interrupted):
            with self.assertRaisesRegex(RuntimeError,'copy interrupted'):
                c.replacement_clone(self.work,self.operation,source,expected,storage,old)
        self.assertEqual(c.record(self.work,self.operation)['stage'],'allocated')
        self.assertEqual(c.record(self.work,self.operation)['uuid'],'exact-uuid')
        with patch.object(c,'safe_file'), patch.object(c,'checksum',return_value='b'*64), \
                patch.object(c,'observed',return_value=self.row), \
                patch.object(c.native,'command') as command:
            disk = c.replacement_clone(self.work,self.operation,source,expected,storage,old)
            self.assertEqual(disk['uuid'],'exact-uuid')
            self.assertEqual([call.args[0][0] for call in command.call_args_list], ['/bin/dd'])
            command.reset_mock()
            self.assertEqual(c.replacement_clone(self.work,self.operation,source,expected,storage,old),disk)
            command.assert_not_called()

    def test_replacement_clone_refuses_changed_assignment_and_unrecorded_uuid(self):
        source = self.work / 'template'
        with source.open('wb') as stream: stream.truncate(c.BYTES)
        expected = {'bytes':c.BYTES, 'sha256':'b'*64}
        old = 'c'*64
        storage = Mock(box=self.box)
        storage.pending.return_value = None
        storage.accepted.return_value = {'current_sha256':'d'*64}
        storage.cold_test.return_value = None
        with patch.object(c.native,'command') as command:
            with self.assertRaisesRegex(TransactionError,'unchanged accepted router'):
                c.replacement_clone(self.work,self.operation,source,expected,storage,old)
            command.assert_not_called()

        storage.accepted.return_value = {'current_sha256':old}
        self.store(stage='planned',uuid=None)
        with patch.object(c,'safe_file'), patch.object(c,'checksum',return_value='b'*64), \
                patch.object(c,'observed',return_value=self.row), \
                patch.object(c.native,'command') as command:
            with self.assertRaisesRegex(TransactionError,'unrecorded LV identity'):
                c.replacement_clone(self.work,self.operation,source,expected,storage,old)
            command.assert_not_called()

    def test_replacement_clone_refuses_a_supervised_first_install_window(self):
        storage = Mock(box=self.box)
        storage.cold_test.return_value = {'phase': 'open'}
        with patch.object(c.native, 'command') as command:
            with self.assertRaisesRegex(TransactionError, 'supervised first-install test'):
                c.replacement_clone(self.work, self.operation, self.work / 'unused', {}, storage, 'c'*64)
            command.assert_not_called()


if __name__=='__main__': unittest.main()
