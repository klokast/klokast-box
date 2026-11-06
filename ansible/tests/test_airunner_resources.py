import copy
import contextlib
import io
import unittest

from platform_resource_test_support import ResourceTestCase, REPO_ROOT, model, compiler


class RunnerResourcesTests(ResourceTestCase):
    def test_permanent_router_rules_track_runner_placement(self):
        for runners in (['boxa-air'], []):
            path = self.write_registry({'schema_version':1, 'boxes':{'boxa':{}},
                                        'airunners':runners, 'apps':{}})
            result = compiler.compile_registry(path, [], repo_root=REPO_ROOT)
            self.assertFalse(any(v['host_role']=='ops' for v in result['app_resource_effective_files']))
            rules = [v for v in result['app_resource_effective_files'] if v['host_role']=='router']
            self.assertEqual(bool(rules), bool(runners))
            if rules:
                self.assertTrue(all(v['owners']==['platform'] for v in rules))
                content='\n'.join(v['content'] for v in rules)
                self.assertIn('udp dport { 3478, 41641 }', content)
                self.assertIn('192.168.175.11', content)
                self.assertNotIn('tcp dport 22', content)

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
        self.assertTrue(all(v['ports'] != [22] for v in rules))

    def test_unknown_and_duplicate_runners_refused(self):
        topology = model.load_topology(repo_root=REPO_ROOT)
        for runners in (['missing-air'], ['boxa-air', 'boxa-air']):
            with self.subTest(runners=runners), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                compiler.compile_airunners({'boxes': {'boxa': {}}, 'airunners': runners}, topology)

    def test_reserved_runner_address_cannot_be_a_user_vm(self):
        users = self.per_user_app_users()
        users[0]['vm_ipv4_address'] = '192.168.175.11'
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            model.selected_users('user-shell', self.per_user_app_manifest(),
                                 {'enabled': True, 'users': users}, model.load_topology(repo_root=REPO_ROOT))


if __name__ == '__main__':
    unittest.main()
