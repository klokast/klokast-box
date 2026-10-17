import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import air_maintenance as maintenance
import vm_control_probe
import vm_service_probe


class MaintenanceTest(unittest.TestCase):
    def test_last_runner_is_protected(self):
        with patch.object(maintenance.platform_source, 'snapshot', return_value={
                'instance': {'airunners': ['boxa-air'], 'boxes': {'boxa': {}}}}):
            with self.assertRaisesRegex(RuntimeError, 'last verified usable runner'):
                maintenance.usable_alternative('boxa')

    def test_reserved_peer_and_failed_dependency_block_stop(self):
        view = {'instance': {'airunners': ['boxa-air','boxb-air'], 'boxes': {'boxb': {'vpn-egress': {'clients': ['boxb-air']}}}}}
        with patch.object(maintenance.platform_source, 'snapshot', return_value=view), \
                patch.object(maintenance, 'remote', return_value={'available':True,'maintenance':True}), \
                patch.object(vm_control_probe, 'probe') as probe:
            with self.assertRaisesRegex(RuntimeError, 'last verified usable runner'):
                maintenance.usable_alternative('boxa')
            probe.assert_not_called()
        with patch.object(maintenance.platform_source, 'snapshot', return_value=view), \
                patch.object(maintenance, 'remote', return_value={'available':True,'maintenance':False}), \
                patch.object(vm_control_probe, 'probe'), patch.object(maintenance.subprocess, 'run') as router, \
                patch.object(vm_service_probe, 'probe', side_effect=RuntimeError('VPN unavailable')):
            router.return_value.returncode = 0
            with self.assertRaisesRegex(RuntimeError, 'VPN unavailable'):
                maintenance.usable_alternative('boxa')

    def test_final_recheck_precedes_pending(self):
        events=[]
        with patch.object(maintenance, 'usable_alternative', side_effect=lambda box: events.append('verify-peer')), \
                patch.object(maintenance, 'remote', side_effect=lambda *a,**kw: events.append(a[1])):
            maintenance.before_stop('boxa','operation')
        self.assertEqual(events,['verify-peer','begin'])

    def test_no_pending_on_failed_peer(self):
        with patch.object(maintenance, 'usable_alternative', side_effect=RuntimeError('no peer')), \
                patch.object(maintenance, 'remote') as remote:
            with self.assertRaises(RuntimeError): maintenance.before_stop('boxa','operation')
            remote.assert_not_called()


if __name__ == '__main__': unittest.main()
