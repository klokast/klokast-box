"""Real SQLite fixtures for selected-discussion transfer and recovery."""
import base64
import copy
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'lib'))
import codex_discussion as storage
import codex_handoff as handoff

SESSION='11111111-1111-4111-8111-111111111111'
CHILD='22222222-2222-4222-8222-222222222222'
OTHER='33333333-3333-4333-8333-333333333333'
OP='a'*24


def initialize(home):
    home.mkdir(parents=True)
    store=storage.Store(home)
    for name,format in store.formats.items():
        migrations=[dict(version=r['version'],description='fixture',installed_on='2026-01-01',
            success=1,checksum={'sqlite_blob':r['checksum']},execution_time=1) for r in format['migrations']]
        store.create(name,migrations).close()
    (home/'auth.json').write_text('fixture authentication: never transfer')
    return store


def insert(store,name,table,values):
    c=store.connect(name)
    store.insert(c,table,[values]);c.commit();c.close()


def discussion(store,sid=SESSION,mode='legacy',attachment=False):
    relative='.codex/sessions/2026/10/17/rollout-'+sid+'.jsonl'
    p=store.path(relative);p.parent.mkdir(parents=True,exist_ok=True)
    content=[{'type':'session_meta','payload':{'id':sid,'cwd':'/home/agent/src/klokast/klokast-box'}},
             {'type':'response_item','payload':{'type':'message','role':'user','content':[{'type':'input_text','text':'fixture '+sid}]}}]
    if attachment:
        asset=store.path('.codex/attachments/image.png');asset.parent.mkdir(exist_ok=True);asset.write_bytes(b'fixture image')
        content.append({'type':'response_item','payload':{'type':'local_image','path':'/home/agent/.codex/attachments/image.png'}})
    p.write_text(''.join(json.dumps(v)+'\n' for v in content))
    insert(store,'state_5.sqlite','threads',dict(id=sid,rollout_path='/home/agent/'+relative,
        created_at=1,updated_at=2,source='cli',model_provider='openai',cwd='/home/agent/src/klokast/klokast-box',
        title='fixture',sandbox_policy='{}',approval_mode='never',history_mode=mode))
    if mode=='paginated':
        insert(store,'thread_history_1.sqlite','thread_history_projection_state',dict(thread_id=sid,next_rollout_byte_offset=p.stat().st_size,next_rollout_ordinal=2))
        for turn in range(3):
            insert(store,'thread_history_1.sqlite','thread_turns',dict(thread_id=sid,turn_id=str(turn),rollout_ordinal=turn,status='completed'))
            for item in range(7):
                insert(store,'thread_history_1.sqlite','thread_items',dict(thread_id=sid,turn_id=str(turn),item_id=str(item),
                    rollout_ordinal=turn*7+item,created_at_ms=1,item_json=json.dumps({'type':'agentMessage','text':f'page {turn} item {item}'})))
    return relative


class Transfer(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup);self.root=Path(temporary.name)
        self.source=initialize(self.root/'source/.codex');self.dest=initialize(self.root/'dest/.codex')
        self.receipt=self.root/'receipt.json'

    def test_legacy_and_paginated_keep_other_threads_auth_and_attachments(self):
        for mode in ('legacy','paginated'):
            with self.subTest(mode=mode):
                a=initialize(self.root/mode/'source/.codex');b=initialize(self.root/mode/'dest/.codex')
                discussion(a,mode=mode,attachment=True);discussion(a,OTHER);discussion(b,OTHER)
                unrelated=b.snapshot(OTHER);authentication=(b.home/'auth.json').read_bytes()
                bundle=a.snapshot(SESSION);storage.validate_bundle(bundle)
                result=b.install(bundle,self.root/mode/'receipt.json')
                self.assertEqual(result['phase'],'imported')
                self.assertEqual(b.snapshot(SESSION),bundle)
                self.assertEqual(b.snapshot(OTHER),unrelated)
                self.assertEqual((b.home/'auth.json').read_bytes(),authentication)
                if mode=='paginated':self.assertEqual(len(bundle['databases']['thread_history_1.sqlite']['thread_items']),21)
                a.remove(bundle)
                with self.assertRaisesRegex(RuntimeError,'absent'):a.snapshot(SESSION)
                self.assertEqual(a.snapshot(OTHER)['session'],OTHER)
                self.assertTrue(a.path('.codex/attachments/image.png').is_file())
                self.assertTrue((a.home/'auth.json').is_file())

    def test_only_selected_children_are_exported(self):
        for sid in (SESSION,CHILD,OTHER):discussion(self.source,sid)
        insert(self.source,'state_5.sqlite','thread_spawn_edges',dict(parent_thread_id=SESSION,child_thread_id=CHILD,status='completed'))
        bundle=self.source.snapshot(SESSION);storage.validate_bundle(bundle)
        self.assertEqual(bundle['threads'],[SESSION,CHILD])
        self.dest.install(bundle,self.receipt)
        self.assertEqual(self.dest.snapshot(SESSION),bundle)

    def test_persisted_images_transfer_and_unknown_attachment_payloads_refuse_export(self):
        discussion(self.source,attachment=True)
        insert(self.source,'state_5.sqlite','thread_attachments',dict(id='asset',thread_id=SESSION,
            attachment_type='localImage',identity_key='fixture-image',created_at=1,
            payload=json.dumps({'path':'/home/agent/.codex/attachments/image.png'})))
        bundle=self.source.snapshot(SESSION)
        self.dest.install(bundle,self.receipt)
        self.assertEqual(self.dest.snapshot(SESSION),bundle)
        c=self.source.connect('state_5.sqlite')
        c.execute('UPDATE thread_attachments SET attachment_type=? WHERE id=?',('opaque.future','asset'))
        c.commit();c.close()
        with self.assertRaisesRegex(RuntimeError,'unsupported persisted discussion attachment'):
            self.source.snapshot(SESSION)

    def test_queue_triggers_preserve_selected_input_and_remove_source_counters(self):
        discussion(self.source)
        insert(self.source,'queue_1.sqlite','queued_items',dict(id='queued',thread_id=SESSION,
            payload_json=json.dumps({'type':'text','text':'continue fixture'}),queue_order=1,created_at_ms=1,updated_at_ms=1))
        bundle=self.source.snapshot(SESSION)
        self.dest.install(bundle,self.receipt)
        self.assertEqual(self.dest.snapshot(SESSION)['databases'],bundle['databases'])
        self.source.remove(bundle)
        c=self.source.connect('queue_1.sqlite')
        self.assertEqual(c.execute('select count(*) from queued_thread_revisions where thread_id=?',(SESSION,)).fetchone()[0],0)
        c.close()

    def test_unknown_native_trigger_is_rejected(self):
        c=self.source.connect('state_5.sqlite')
        c.execute('CREATE TRIGGER surprise AFTER INSERT ON threads BEGIN SELECT 1; END')
        with self.assertRaisesRegex(RuntimeError,'unsupported Codex storage trigger'):
            self.source.validate(c,'state_5.sqlite')
        c.close()

    def test_missing_projection_rollout_or_attachment_refuses_export(self):
        for kind in ('projection','rollout','attachment'):
            with self.subTest(kind=kind):
                store=initialize(self.root/kind/'.codex');rollout=discussion(store,mode='paginated',attachment=True)
                if kind=='projection':
                    c=store.connect('thread_history_1.sqlite');c.execute('delete from thread_history_projection_state');c.commit();c.close()
                else:store.path(rollout if kind=='rollout' else '.codex/attachments/image.png').unlink()
                with self.assertRaises((RuntimeError,OSError)):store.snapshot(SESSION)

    def test_unknown_schema_and_migration_refuse_before_import(self):
        discussion(self.source);bundle=self.source.snapshot(SESSION)
        for change in ('create table future (id text)','update _sqlx_migrations set checksum=x\'00\' where version=(select min(version) from _sqlx_migrations)'):
            with self.subTest(change=change):
                c=self.dest.connect('state_5.sqlite');c.execute('BEGIN');c.execute(change)
                with self.assertRaisesRegex(RuntimeError,'unsupported'):self.dest.validate(c,'state_5.sqlite')
                c.rollback();c.close()
        broken=copy.deepcopy(bundle);broken['adapter']='future'
        with self.assertRaisesRegex(RuntimeError,'unsupported'):self.dest.install(broken,self.receipt)
        self.assertFalse(self.receipt.exists())

    def test_incomplete_or_unrelated_payload_is_rejected_before_mutation(self):
        discussion(self.source,mode='paginated',attachment=True);discussion(self.source,OTHER)
        bundle=self.source.snapshot(SESSION)
        for edit in ('table','file','item','unrelated'):
            broken=copy.deepcopy(bundle)
            if edit=='table':del broken['databases']['state_5.sqlite']['thread_attachments']
            elif edit=='file':del broken['files']['.codex/attachments/image.png']
            elif edit=='item':broken['databases']['thread_history_1.sqlite']['thread_items'][0]['turn_id']='absent'
            else:broken['databases']['state_5.sqlite']['threads']+=self.source.snapshot(OTHER)['databases']['state_5.sqlite']['threads']
            with self.subTest(edit=edit),self.assertRaises((RuntimeError,OSError)):
                self.dest.install(broken,self.receipt)
            self.assertFalse(self.receipt.exists())

    def test_existing_destination_is_not_overwritten(self):
        discussion(self.source);discussion(self.dest)
        before=self.dest.snapshot(SESSION)
        with self.assertRaisesRegex(RuntimeError,'already has'):self.dest.install(self.source.snapshot(SESSION),self.receipt)
        self.assertEqual(self.dest.snapshot(SESSION),before)

    def test_interrupted_database_commits_and_receipts_resume_idempotently(self):
        discussion(self.source,mode='paginated',attachment=True);bundle=self.source.snapshot(SESSION)
        original=storage.atomic
        def interrupt(path,value):
            if value.get('completed_databases') and value.get('phase')=='importing':raise RuntimeError('lost commit acknowledgement')
            original(path,value)
        with patch.object(storage,'atomic',side_effect=interrupt),self.assertRaisesRegex(RuntimeError,'lost commit'):
            self.dest.install(bundle,self.receipt)
        self.dest.install(bundle,self.receipt)
        self.assertEqual(self.dest.snapshot(SESSION),bundle)
        c=self.dest.connect('state_5.sqlite');c.execute('update threads set title=? where id=?',('newer content',SESSION));c.commit();c.close()
        self.dest.install(bundle,self.receipt)
        self.assertEqual(self.dest.snapshot(SESSION)['databases']['state_5.sqlite']['threads'][0]['title'],'newer content')

    def test_interrupted_import_never_overwrites_changed_rows(self):
        discussion(self.source);bundle=self.source.snapshot(SESSION)
        with patch.object(self.dest,'snapshot',side_effect=RuntimeError('interrupted after import')):
            with self.assertRaises(RuntimeError):self.dest.install(bundle,self.receipt)
        c=self.dest.connect('state_5.sqlite');c.execute('update threads set title=? where id=?',('newer',SESSION));c.commit();c.close()
        with self.assertRaisesRegex(RuntimeError,'changed'):self.dest.install(bundle,self.receipt)

    def test_source_removal_rejects_newer_data_and_retry_is_safe(self):
        discussion(self.source);bundle=self.source.snapshot(SESSION)
        c=self.source.connect('state_5.sqlite');c.execute('update threads set title=? where id=?',('newer',SESSION));c.commit();c.close()
        with self.assertRaisesRegex(RuntimeError,'source discussion changed'):self.source.remove(bundle)
        bundle=self.source.snapshot(SESSION);self.source.remove(bundle);self.source.remove(bundle)

    def test_local_asset_collision_and_path_traversal_preserve_destination(self):
        discussion(self.source,attachment=True);bundle=self.source.snapshot(SESSION)
        asset=self.dest.path('.codex/attachments/image.png');asset.parent.mkdir();asset.write_bytes(b'destination asset')
        with self.assertRaisesRegex(RuntimeError,'changed'):self.dest.install(bundle,self.receipt)
        self.assertEqual(asset.read_bytes(),b'destination asset')
        for path in ('../escape','/etc/passwd','.ssh/key','.codex/auth.json'):
            with self.subTest(path=path),self.assertRaises(RuntimeError):self.dest.path(path)

    def test_writer_lock_blocks_snapshot_and_does_not_unlink_native_lock(self):
        with storage.writers(self.source.home,[SESSION]):
            with self.assertRaisesRegex(RuntimeError,'live writer'):
                with storage.writers(self.source.home,[SESSION]):pass
        self.assertTrue((self.source.home/'thread-writer-locks'/f'{SESSION}.lock').exists())
        with storage.writers(self.source.home,[SESSION]):pass

    def test_open_writer_is_never_archived_or_interrupted(self):
        with patch.object(handoff,'HOME',self.source.home),patch.object(handoff,'RPC') as rpc:
            with storage.writers(self.source.home,[SESSION]):
                with self.assertRaisesRegex(RuntimeError,'close its Codex client'):handoff.quiesce([SESSION],0)
            rpc.assert_not_called()

    def test_uncertain_start_does_not_create_another_window(self):
        root=self.root/'handoffs'
        storage.atomic(root/'threads'/f'{SESSION}.json',{'operation':OP,'phase':'here'})
        storage.atomic(root/OP/'destination.json',{'session':SESSION,'phase':'starting'})
        with patch.object(handoff,'ROOT',root),patch.object(handoff.subprocess,'run') as run:
            run.return_value.stdout='unrelated-window\n'
            with self.assertRaisesRegex(RuntimeError,'uncertain'):handoff.start(SESSION,OP)
            self.assertEqual(run.call_count,1)
            self.assertEqual(run.call_args.args[0][1],'list-windows')

    def test_handoff_writes_serialize_with_replacement_and_reservations(self):
        root=self.root/'workspace';root.mkdir();(root/'workspace.lock').touch()
        with patch.object(handoff,'WORKSPACE',root):
            storage.atomic(root/'maintenance.json',{'phase':'planned','expires_at':99999999999})
            with handoff.workspace_access('export'):pass
            with self.assertRaisesRegex(RuntimeError,'reserved'):
                with handoff.workspace_access('import'):pass
            storage.atomic(root/'maintenance.json',{'phase':'pending','expires_at':0})
            with self.assertRaisesRegex(RuntimeError,'pending'):
                with handoff.workspace_access('export'):pass
            (root/'maintenance.json').unlink()
            with handoff.workspace_access('import'):
                import fcntl
                with (root/'workspace.lock').open('r') as stop:
                    with self.assertRaises(BlockingIOError):fcntl.flock(stop,fcntl.LOCK_EX|fcntl.LOCK_NB)

    def test_round_trip_and_changed_source_backup_conflict(self):
        discussion(self.source,mode='paginated');source_root=self.root/'source/handoffs';source_root.mkdir()
        dest_root=self.root/'dest/handoffs';dest_root.mkdir()
        with patch.object(handoff,'HOME',self.source.home),patch.object(handoff,'ROOT',source_root):
            bundle=handoff.export(SESSION,OP,0)
            self.assertEqual(handoff.export(SESSION,OP,0),bundle)
        with patch.object(handoff,'HOME',self.dest.home),patch.object(handoff,'ROOT',dest_root):
            handoff.receive(bundle,OP);handoff.receive(bundle,OP)
            c=self.dest.connect('state_5.sqlite');c.execute('update threads set title=? where id=?',('continued',SESSION));c.commit();c.close()
            back=handoff.export(SESSION,'b'*24,0)
            with self.assertRaisesRegex(RuntimeError,'later handoff'):handoff.receive(bundle,OP)
        with patch.object(handoff,'HOME',self.source.home),patch.object(handoff,'ROOT',source_root):
            p=source_root/OP/'export.json';original=p.read_bytes();changed=json.loads(original);changed['adapter']='changed';p.write_text(json.dumps(changed))
            with self.assertRaisesRegex(RuntimeError,'earlier source copy changed'):handoff.receive(back,'b'*24)
            p.write_bytes(original);handoff.receive(back,'b'*24)
            self.assertEqual(self.source.snapshot(SESSION)['databases']['state_5.sqlite']['threads'][0]['title'],'continued')
            with self.assertRaisesRegex(RuntimeError,'later handoff'):handoff.export(SESSION,OP,0)


if __name__=='__main__':unittest.main()
