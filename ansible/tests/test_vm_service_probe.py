"""Retry a failed SSH dial without hiding a guest validation failure."""
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'lib'))
import vm_service_probe as probe
import infrastructure_images as images


class ProbeTransport(unittest.TestCase):
    def result(self, value=None, error=''):
        return subprocess.CompletedProcess([], 255 if error else 0,
            json.dumps(value) if value is not None else '', error)

    def root(self):
        return self.result({'identity': {'hostname': 'boxa-bak', 'tailscale_hostname': 'boxa-bak'},
            'retained_uuid': None, 'online': True})

    @patch.object(probe.time, 'sleep')
    @patch.object(probe.subprocess, 'run')
    def test_failed_dial_retries_the_same_probe_then_collects_workloads(self, run, sleep):
        run.side_effect = [self.result(error='dial failure: i/o timeout'), self.root(),
                           self.result({'running': []})]
        value = probe.probe('boxa', 'bak', repair=True)
        self.assertEqual(value['workloads'], {'running': []})
        self.assertEqual(run.call_count, 3)
        self.assertEqual(run.call_args_list[0], run.call_args_list[1])
        sleep.assert_called_once_with(2)

    @patch.object(probe.time, 'sleep')
    @patch.object(probe.subprocess, 'run')
    def test_failed_dial_has_a_fixed_attempt_limit(self, run, sleep):
        run.return_value = self.result(error='dial failure: i/o timeout')
        with self.assertRaisesRegex(RuntimeError, 'service probe failed'):
            probe.probe('boxa', 'bak', repair=True)
        self.assertEqual(run.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    @patch.object(probe.time, 'sleep')
    @patch.object(probe.subprocess, 'run')
    def test_guest_failure_stops_without_retry(self, run, sleep):
        run.return_value = self.result(error='shared guest subordinate numeric ownership differs')
        with self.assertRaisesRegex(RuntimeError, 'numeric ownership differs'):
            probe.probe('boxa', 'bak', repair=True)
        self.assertEqual(run.call_count, 1)
        sleep.assert_not_called()


class ReceiptTransport(unittest.TestCase):
    def setUp(self):
        self.failed = subprocess.CompletedProcess([], 255, '', 'dial failure: i/o timeout')
        for target, name, options in ((images.subprocess, 'run', {}), (images.time, 'sleep', {}),
                (images.socket, 'gethostname', {'return_value': 'boxb-ops'})):
            context = patch.object(target, name, **options)
            setattr(self, name, context.start()); self.addCleanup(context.stop)

    def receipt(self):
        return images.local_action('boxa', 'shared-alpine-v1', 'a'*40, 'image-receipt', 'b'*24)

    def test_receipt_dial_retry_keeps_the_same_frozen_source_and_request(self):
        self.run.side_effect = [self.failed, subprocess.CompletedProcess([], 0, '{"files":{}}', '')]
        self.assertEqual(self.receipt(), {'files': {}})
        self.assertEqual(self.run.call_args_list[0], self.run.call_args_list[1])
        self.sleep.assert_called_once_with(2)

    def test_receipt_dial_has_a_fixed_attempt_limit(self):
        self.run.return_value = self.failed
        with self.assertRaisesRegex(RuntimeError, 'image-receipt failed'):
            self.receipt()
        self.assertEqual(self.run.call_count, 3)
        self.assertEqual(self.sleep.call_count, 2)

    def test_mutating_image_actions_are_not_repeated_after_a_dial_failure(self):
        self.run.return_value = self.failed
        for action in ('prepare', 'cleanup-before', 'cleanup'):
            with self.subTest(action=action), self.assertRaises(RuntimeError):
                images.local_action('boxa', 'shared-alpine-v1', 'a'*40, action,
                                    None if action == 'prepare' else 'b'*24)
            self.assertEqual(self.run.call_count, 1); self.run.reset_mock()
        self.sleep.assert_not_called()

    def test_receipt_validation_failure_stops_without_retry(self):
        self.run.return_value = subprocess.CompletedProcess([], 2, '', 'image receipt differs from qualification')
        with self.assertRaises(RuntimeError):self.receipt()
        self.assertEqual(self.run.call_count, 1)
        self.sleep.assert_not_called()
