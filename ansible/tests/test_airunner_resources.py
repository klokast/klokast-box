import copy
import contextlib
import io
import unittest

from platform_resource_test_support import ResourceTestCase, REPO_ROOT, model, compiler


class RunnerResourcesTests(ResourceTestCase):
    def test_controller_transport_is_owned_by_platform_and_tracks_placement(self):
        for runners in (['boxa-air'], []):
            path = self.write_registry({'schema_version':1, 'boxes':{'boxa':{}},
                                        'airunners':runners, 'apps':{}})
            result = compiler.compile_registry(path, [], repo_root=REPO_ROOT)
            rules = [v for v in result['app_resource_effective_files'] if v['host_role']=='ops']
            self.assertEqual(len(rules), len(runners))
            if rules:
                self.assertEqual(rules[0]['owners'], ['platform'])
                self.assertIn('udp dport 41641', rules[0]['content'])
                self.assertIn('192.168.175.11', rules[0]['content'])

    def test_placement_and_exclusive_network_rules(self):
        topology = model.load_topology(repo_root=REPO_ROOT)
        registry = {'boxes': {'boxa': {}, 'boxb': {}},
                    'airunners': ['boxb-air', 'boxa-ops-airunner', 'vultr-ops']}
        guests, rules = compiler.compile_airunners(registry, topology)
        self.assertEqual([v['hostname'] for v in guests], ['boxb-air'])
        self.assertTrue(all(v['app'] == 'platform' and v['exclusive'] for v in rules))
        self.assertFalse(any(v['ports'] == [22] for v in rules))
        self.assertEqual(guests[0]['vm_ipv4_address'], '192.168.175.11')
        ledger = compiler.build_app_resource_ledger(rules, [])
        self.assertTrue(all(v['owners'] == ['platform'] for v in ledger['effective_files']))
        _, bootstrap = compiler.compile_airunners(registry, topology, bootstrap_boxes=['boxb'])
        self.assertEqual(sum(v['ports'] == [22] for v in bootstrap), 1)
        self.assertTrue(all(v['ports'] != [22] for v in rules))

    def test_unknown_duplicate_and_undeclared_bootstrap_refused(self):
        topology = model.load_topology(repo_root=REPO_ROOT)
        for runners, bootstrap in ((['missing-air'], []), (['boxa-air', 'boxa-air'], []), ([], ['boxa'])):
            with self.subTest(runners=runners), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                compiler.compile_airunners({'boxes': {'boxa': {}}, 'airunners': runners}, topology, bootstrap_boxes=bootstrap)

    def test_reserved_runner_address_cannot_be_a_user_vm(self):
        users = self.per_user_app_users()
        users[0]['vm_ipv4_address'] = '192.168.175.11'
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            model.selected_users('user-shell', self.per_user_app_manifest(),
                                 {'enabled': True, 'users': users}, model.load_topology(repo_root=REPO_ROOT))


if __name__ == '__main__':
    unittest.main()
