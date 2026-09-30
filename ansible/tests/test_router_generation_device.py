"""Retained router devices must remain distinct across A/B generations."""
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'lib'))
import router_generation_device as devices
import router_records as records
from router_transaction import TransactionError


class GenerationDeviceTests(unittest.TestCase):
    def setUp(self):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        base = Path(root.name)
        (base/'records').mkdir(mode=0o700)
        self.storage = SimpleNamespace(box='boxa',base=base,
            generation=lambda checksum:{'generation_id':
                'a'*24 if checksum == '4'*64 else 'b'*24 if checksum == '6'*64 else 'c'*24})
        for name,value in (('ROOT_UID',os.geteuid()),('parents',lambda path:None)):
            patch = mock.patch.object(records,name,value)
            patch.start();self.addCleanup(patch.stop)

    def test_one_machine_id_is_immutable_for_each_generation(self):
        generation = '1'*64
        first = devices.remember(self.storage,generation,'nOldRouter','boxa-router','2'*64)
        self.assertEqual(devices.read(self.storage,generation),first)
        self.assertEqual(devices.remember(self.storage,generation,'nOldRouter',
            'boxa-router','3'*64),first)
        with self.assertRaises(TransactionError):
            devices.remember(self.storage,generation,'nOtherRouter','boxa-router','3'*64)
        next_generation = '4'*64
        second = devices.remember(self.storage,next_generation,'nNewRouter',
            'boxa-router-'+'a'*24,'5'*64)
        self.assertNotEqual(first['machine_id'],second['machine_id'])
        with self.assertRaises(TransactionError):
            devices.remember(self.storage,'6'*64,'nOldRouter',
                'boxa-router-'+'b'*24,'7'*64)

    def test_malformed_name_or_changed_record_refuses(self):
        generation = '1'*64
        with self.assertRaises(TransactionError):
            devices.remember(self.storage,generation,'nNewRouter',
                'boxa-router-'+'x'*24,'2'*64)
        devices.remember(self.storage,generation,'nOldRouter','boxa-router','2'*64)
        target = devices.path(self.storage,generation)
        records.write(target,{**records.read(target),'machine_id':'nChanged'})
        with self.assertRaises(RuntimeError):
            devices.read(self.storage,generation)


if __name__ == '__main__':
    unittest.main()
