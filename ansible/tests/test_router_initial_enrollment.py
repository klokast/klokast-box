"""One recorded first Tailnet attempt can resume but cannot mint twice."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import router_initial_enrollment as enrollment
import router_records as records
from router_transaction import TransactionError
import test_router_initial_boot as boot_fixture
from router_release_fixtures import ENGINE


class InitialEnrollmentTests(unittest.TestCase):
    def setUp(self):
        fixture = boot_fixture.InitialBootTests('test_intent_precedes_xl_create_and_retry_reconciles_exact_guest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.booted = fixture.execute()
        fixture.host.initial_guest.side_effect = None
        fixture.host.initial_guest.return_value = {'domid':7}
        self.storage, self.work, self.operation = fixture.storage, fixture.work, fixture.operation
        self.grant = {'kind':'klokast.router-initial-enrollment-grant.v1',
            'box':'boxa','operation_id':self.operation,'engine_commit':ENGINE,
            'boot_request_sha256':self.booted['request_sha256'],
            'boot_intent_sha256':self.booted['intent_sha256'],
            'granted_at':1000,'expires_at':1300}
        records.write(self.work / 'initial-enrollment-grant.json',self.grant)
        self.time_patch = patch.object(enrollment.time,'time',return_value=1001)
        self.time_patch.start(); self.addCleanup(self.time_patch.stop)

    def test_begin_records_one_attempt_and_retry_never_mints_again(self):
        first = enrollment.begin(self.storage,self.operation,ENGINE)
        self.assertTrue(first['mint_permitted'])
        self.assertEqual(self.storage.installation()['stage'],'prepared')
        self.assertEqual(records.read(self.work / 'initial-enrollment-intent.json')['disk'],self.fixture.disk)
        repeated = enrollment.begin(self.storage,self.operation,ENGINE)
        self.assertFalse(repeated['mint_permitted'])
        self.assertEqual(repeated['attempt'],first['attempt'])
        self.assertEqual(repeated['intent_sha256'],first['intent_sha256'])

    def test_finish_binds_one_machine_id_and_retry_keeps_it(self):
        begun = enrollment.begin(self.storage,self.operation,ENGINE)
        guest = {'kind':'klokast.router-initial-enrollment-guest.v1','box':'boxa',
            'operation_id':self.operation,'attempt':begun['attempt'],
            'machine_id':'nExactMachine','hostname':'boxa-router',
            'tags':['tag:vm'],'ssh':True,'state_sha256':'4'*64}
        records.write(self.work / 'initial-enrollment-guest.json',guest)
        finish = {'kind':'klokast.router-initial-enrollment-finish-grant.v1',
            'box':'boxa','operation_id':self.operation,'engine_commit':ENGINE,
            'intent_sha256':begun['intent_sha256'],
            'guest_sha256':enrollment.generations.digest(guest),
            'granted_at':1000,'expires_at':1300}
        records.write(self.work / 'initial-enrollment-finish-grant.json',finish)
        result = enrollment.finish(self.storage,self.operation,ENGINE)
        self.assertEqual(result['machine_id'],'nExactMachine')
        self.assertEqual(result['installation']['stage'],'enrolled')
        self.assertEqual(self.storage.installation()['machine_id'],'nExactMachine')
        self.assertFalse(enrollment.begin(self.storage,self.operation,ENGINE)['mint_permitted'])
        self.assertEqual(enrollment.finish(self.storage,self.operation,ENGINE),result)
        changed = {**guest,'machine_id':'nAnotherMachine'}
        records.write(self.work / 'initial-enrollment-guest.json',changed)
        records.write(self.work / 'initial-enrollment-finish-grant.json',
                      {**finish,'guest_sha256':enrollment.generations.digest(changed)})
        with self.assertRaisesRegex(TransactionError,'retry differs'):
            enrollment.finish(self.storage,self.operation,ENGINE)
        self.assertEqual(self.storage.installation()['machine_id'],'nExactMachine')

    def test_missing_running_guest_or_changed_intent_blocks_key_mint(self):
        self.fixture.host.initial_guest.return_value = None
        with self.assertRaisesRegex(TransactionError,'exact running router'):
            enrollment.begin(self.storage,self.operation,ENGINE)
        self.assertFalse((self.work / 'initial-enrollment-intent.json').exists())
        self.fixture.host.initial_guest.return_value = {'domid':7}
        enrollment.begin(self.storage,self.operation,ENGINE)
        intent = records.read(self.work / 'initial-enrollment-intent.json')
        records.write(self.work / 'initial-enrollment-intent.json',
                      {**intent,'xen_uuid':'f'*8+'-'+'f'*4+'-'+'f'*4+'-'+'f'*4+'-'+'f'*12})
        with self.assertRaisesRegex(TransactionError,'intent changed'):
            enrollment.begin(self.storage,self.operation,ENGINE)

    def test_stale_enrollment_grant_never_writes_intent(self):
        records.write(self.work / 'initial-enrollment-grant.json',
                      {**self.grant,'boot_intent_sha256':'0'*64})
        with self.assertRaisesRegex(TransactionError,'grant is stale'):
            enrollment.begin(self.storage,self.operation,ENGINE)
        self.assertFalse((self.work / 'initial-enrollment-intent.json').exists())


if __name__ == '__main__':
    unittest.main()
