"""Candidate reconciliation cannot claim or stop the running accepted router."""
import copy
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import router_generations as generations
import router_native as native
from router_transaction import TransactionError
from test_router_generations import generation, reseal
from test_router_native import domain


class CandidateRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.operation = 'b' * 24
        self.old, self.candidate = generation('legacy'), generation()
        self.candidate['xen']['vif'] = ['bridge=br-test,mac=00:16:3e:00:00:02']
        reseal(self.candidate)
        self.expected = native.literal_configuration(generations.configuration(self.candidate))
        self.expected['name'] = 'router-candidate-' + self.operation
        self.live = domain(self.candidate, domid=5)
        self.live['config']['c_info']['name'] = self.expected['name']
        self.old_live = domain(self.old, domid=4)
        self.host = native.Native()
        self.devices = {self.old['disk']['path']:1, self.candidate['disk']['path']:2,
                        '/dev/mapper/candidate-alias':2, '/dev/vg0/foreign':3}
        for target, name, kwargs in (
                (self.host, 'disk', {'return_value':2}),
                (self.host, 'device', {'side_effect':self.devices.__getitem__}),
                (self.host, 'inventory', {'return_value':[self.old_live,self.live]})):
            patch = mock.patch.object(target,name,**kwargs)
            patch.start(); self.addCleanup(patch.stop)

    def lookup(self):
        return self.host.candidate_guest(self.candidate['disk'],self.expected,
                                         self.operation,deadline=110)

    def test_candidate_and_accepted_router_can_run_together(self):
        self.live['config']['disks'][0]['pdev_path'] = '/dev/mapper/candidate-alias'
        self.assertEqual(self.lookup(),self.live)
        self.host.inventory.return_value = [self.old_live]
        self.assertIsNone(self.lookup())

    def test_candidate_lookup_cannot_select_the_canonical_router(self):
        for operation,name in ((self.operation,'router'),('c'*24,self.expected['name']),
                               ('../invalid',self.expected['name'])):
            expected = {**self.expected,'name':name}
            with self.subTest(operation=operation,name=name), self.assertRaises(TransactionError):
                self.host.candidate_guest(self.candidate['disk'],expected,operation,deadline=110)
        self.host.inventory.assert_not_called()

    def test_conflicting_name_uuid_disk_or_mac_refuses_lookup(self):
        mutations = (
            lambda v:v['config']['c_info'].update(uuid=self.old['xen']['uuid']),
            lambda v:v['config']['c_info'].update(name='foreign'),
            lambda v:v['config']['disks'][0].update(pdev_path='/dev/vg0/foreign'),
            lambda v:v['config']['b_info'].update(cmdline='different boot'),
            lambda v:v['config']['nics'][0].update(mac='00:16:3e:00:00:03'))
        for mutate in mutations:
            changed = copy.deepcopy(self.live); mutate(changed)
            self.host.inventory.return_value = [self.old_live,changed]
            with self.assertRaises(TransactionError):
                self.lookup()
        foreign = copy.deepcopy(self.old_live)
        foreign['config']['nics'][0]['mac'] = self.live['config']['nics'][0]['mac']
        self.host.inventory.return_value = [foreign,self.live]
        with self.assertRaises(TransactionError):
            self.lookup()

    def test_an_unknown_guest_holding_an_alias_is_not_adopted(self):
        foreign = copy.deepcopy(self.old_live)
        foreign['config']['disks'][0]['pdev_path'] = '/dev/mapper/candidate-alias'
        self.host.inventory.return_value = [foreign]
        with self.assertRaises(TransactionError):
            self.lookup()

    def test_stop_targets_only_candidate_uuid_and_proves_detachment(self):
        with mock.patch.object(self.host,'monotonic',return_value=100), \
             mock.patch.object(self.host,'wait_detached') as detached, \
             mock.patch.object(native,'command') as command:
            self.host.stop_candidate(self.candidate['disk'],self.expected,self.operation,deadline=110)
        self.assertEqual([call.args[0] for call in command.call_args_list],[
            ['/usr/sbin/xl','shutdown',self.candidate['xen']['uuid']],
            ['/usr/sbin/xl','destroy',self.candidate['xen']['uuid']]])
        detached.assert_called_once_with([self.candidate['disk']['path']],deadline=110)

    def test_stop_refuses_identity_change_before_force(self):
        foreign = copy.deepcopy(self.live)
        foreign['config']['c_info']['uuid'] = '9'*8+'-1111-4111-8111-111111111111'
        self.host.inventory.side_effect = [[self.old_live,self.live],[self.old_live,foreign]]
        with mock.patch.object(self.host,'monotonic',return_value=100), \
             mock.patch.object(self.host,'wait_detached') as detached, \
             mock.patch.object(native,'command') as command, self.assertRaises(TransactionError):
            self.host.stop_candidate(self.candidate['disk'],self.expected,self.operation,deadline=110)
        command.assert_called_once_with(
            ['/usr/sbin/xl','shutdown',self.candidate['xen']['uuid']],110)
        detached.assert_not_called()

    def test_stopped_candidate_retry_and_graceful_exit_leave_old_router_alone(self):
        for inventories,commands in (([[self.old_live]],0),
                ([[self.old_live,self.live],[self.old_live]],1)):
            self.host.inventory.side_effect = inventories
            with mock.patch.object(self.host,'monotonic',return_value=100), \
                 mock.patch.object(self.host,'wait_detached') as detached, \
                 mock.patch.object(native,'command') as command:
                self.host.stop_candidate(self.candidate['disk'],self.expected,self.operation,deadline=110)
            self.assertEqual(command.call_count,commands)
            if commands:
                command.assert_called_once_with(
                    ['/usr/sbin/xl','shutdown',self.candidate['xen']['uuid']],110)
            detached.assert_called_once_with([self.candidate['disk']['path']],deadline=110)

    def test_initial_lookup_still_refuses_an_existing_different_router(self):
        expected = {**self.expected,'name':'router'}
        self.host.inventory.return_value = [self.old_live]
        with self.assertRaises(TransactionError):
            self.host.initial_guest(self.candidate['disk'],expected,deadline=110)
        with self.assertRaises(TransactionError):
            self.host.initial_guest(self.candidate['disk'],self.expected,deadline=110)


if __name__ == '__main__':
    unittest.main()
