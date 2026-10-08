"""Historical failure is closed only by a matching later reconciliation."""
import copy
import hashlib
import json
import unittest
from test_development_source import wrapper


class MigrationPreflightTests(unittest.TestCase):
    def setUp(self):
        self.cli=wrapper('development-migration-check')
        self.failed={'schema_version':8,'kind':'klokast.apply-execution.v8','action':'repair_overlay_ipv6_direct',
                     'executor':'overlay_ipv6_direct_repair_v2','active_controller_box':'boxa','peer_box':'boxb',
                     'freebox_gateway_id_sha256':'a'*64,'delegation_slot':1,
                     'result':'failed','recovery_result':'recovery_required','finished_at':'2026-09-01T00:00:00Z'}
        self.success={**self.failed,'result':'success','recovery_result':'not_needed','finished_at':'2026-09-02T00:00:00Z'}

    def seal(self, value):
        value=copy.deepcopy(value); value.pop('receipt_sha256',None)
        value['receipt_sha256']=hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
        return value

    def test_later_matching_success_closes_failure_without_rewriting_history(self):
        records=[self.seal(self.failed),self.seal(self.success)]; before=copy.deepcopy(records)
        self.cli.completed_apply_receipts(records)
        self.assertEqual(records,before)

    def test_older_unrelated_failed_or_corrupt_success_does_not_close_failure(self):
        for changes in ({'peer_box':'boxc'},{'delegation_slot':2},{'finished_at':'2026-08-31T00:00:00Z'},
                        {'result':'failed'},{'action':'verify_instance_authority'}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):
                self.cli.completed_apply_receipts([self.seal(self.failed),self.seal({**self.success,**changes})])
        bad=self.seal(self.success); bad['receipt_sha256']='0'*64
        with self.assertRaises(ValueError): self.cli.completed_apply_receipts([self.seal(self.failed),bad])

    def test_restored_failure_is_terminal_but_unknown_state_is_not(self):
        self.cli.completed_apply_receipts([self.seal({**self.failed,'recovery_result':'restored'})])
        with self.assertRaises(ValueError):
            self.cli.completed_apply_receipts([self.seal({**self.failed,'result':'running'})])


if __name__=='__main__': unittest.main()
