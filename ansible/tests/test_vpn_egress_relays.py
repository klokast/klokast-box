"""The gateway relay check tests one relay at a time and renews only a due list; see doc/vpn-egress.md#relay-check."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

import yaml

from test_infrastructure_guest import load, ROOT
import platform_vpn_egress as vpn

SCRIPT = ROOT / 'ansible/roles/vpn-egress-runtime/files/vpn-egress-relays'
NOW = 1_800_000_000
DAY = 86400


def stamp(seconds):
    import time
    return time.strftime('%Y-%m-%dT%H:%M:%S.123456789Z', time.gmtime(seconds))


class FakeMihomo:
    """The loopback API of a fallback group, as v1.19.25 answers it.

    `now` is the first relay that is not dead, else the first relay. A delay
    test stores the result as the alive state, as URLTest does.
    """

    def __init__(self, relays, age=DAY, renewed=None):
        self.load(relays, age)
        self.renewed = renewed  # the list that a PUT loads, or None for a failed download
        self.calls, self.in_flight, self.most_in_flight = [], 0, 0

    def load(self, relays, age):
        # A changed list has new relay objects without test results.
        self.relays = [{'name': name, 'alive': True, 'history': [], 'works': works} for name, works in relays]
        self.updated = NOW - age

    def now(self):
        live = [r for r in self.relays if not (r['history'] and not r['alive'])]
        return (live or self.relays)[0]['name']

    def __call__(self, path, method='GET', timeout=10):
        self.calls.append((method, path))
        self.in_flight += 1; self.most_in_flight = max(self.most_in_flight, self.in_flight)
        try:
            if method == 'PUT':
                if self.renewed is None: raise urllib.error.HTTPError(path, 503, 'https://private.invalid/token', {}, io.BytesIO())
                self.load(self.renewed, 0)
                return None
            if path == 'providers/proxies/relays':
                return {'updatedAt': stamp(self.updated),
                        'proxies': [{k: r[k] for k in ('name', 'alive', 'history')} for r in self.relays]}
            if path == 'proxies/VPN': return {'type': 'Fallback', 'now': self.now()}
            name, query = path.split('/', 1)[1].split('/delay?')
            self.query = query
            relay = next(r for r in self.relays if __import__('urllib.parse').parse.quote(r['name'], safe='') == name)
            relay['alive'] = relay['works']; relay['history'].append({'delay': 9 if relay['works'] else 0})
            if not relay['works']: raise urllib.error.HTTPError(path, 504, 'Timeout', {}, io.BytesIO())
            return {'delay': 9}
        finally:
            self.in_flight -= 1

    def tests(self):
        return [path.split('/')[1] for method, path in self.calls if '/delay?' in path]


class Clock:
    def __init__(self): self.value = 0.0
    def __call__(self): self.value += 5; return self.value  # each reading costs about one test


class RelayCheckTests(unittest.TestCase):
    def setUp(self):
        self.m = load('vpn_egress_relays_test', SCRIPT)
        self.config = vpn.render_config('https://subscription.invalid/token', '192.168.200.41', [], 'x' * 32,
                                        profile=__import__('test_routing_profile').CN)
        self.test = self.m.test_settings(self.config)

    def run_main(self, api, action='check'):
        directory = tempfile.TemporaryDirectory(); self.addCleanup(directory.cleanup)
        path = Path(directory.name) / 'config.yml'; path.write_text(yaml.safe_dump(self.config))
        out, err = io.StringIO(), io.StringIO()
        with patch.object(self.m, 'CONFIG', str(path)), patch.object(self.m, 'LOCK', str(Path(directory.name) / 'lock')), \
                patch.object(self.m, 'Api', return_value=api), patch.object(self.m.time, 'sleep'), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = self.m.main([action], clock=Clock(), now=lambda: NOW)
        return code, (json.loads(out.getvalue()) if out.getvalue() else None), err.getvalue()

    def test_the_test_is_the_one_the_group_reads(self):
        self.assertEqual(self.test, {'url': vpn.RELAY_TEST_URL, 'expected': '204', 'timeout': 5000})

    def test_a_working_relay_in_use_costs_one_test(self):
        api = FakeMihomo([('JP', True), ('SG', True)])
        code, value, _ = self.run_main(api)
        self.assertEqual((code, api.tests(), value['selected_server']), (0, ['JP'], 'JP'))
        self.assertEqual(value['check'], {'tests': 1, 'working': True, 'renewal': None, 'renewed': None})
        self.assertIn('url=https%3A%2F%2Fwww.gstatic.com%2Fgenerate_204', api.query)
        self.assertNotIn(('PUT', 'providers/proxies/relays'), api.calls)

    def test_dead_relays_are_left_one_at_a_time_up_to_the_first_that_works(self):
        api = FakeMihomo([('剩余流量：1519.48 GB', False), ('套餐到期：长期有效', False), ('JP 01', False), ('JP 02', True), ('SG', True), ('US', True), ('DE', True)])
        code, value, _ = self.run_main(api)
        self.assertEqual(code, 0)
        self.assertEqual(len(api.tests()), 4)
        self.assertEqual(value['selected_server'], 'JP 02')
        self.assertEqual(api.most_in_flight, 1)  # never two requests at once
        self.assertFalse(any(r['history'] for r in api.relays if r['name'] == 'SG'))

    def test_a_check_stops_when_no_relay_is_left_or_its_time_ends(self):
        self.assertEqual(self.m.check(FakeMihomo([('A', False), ('B', False)]), self.test, Clock()),
                         {'tests': 2, 'working': False})
        many = FakeMihomo([(str(n), False) for n in range(30)])
        result = self.m.check(many, self.test, Clock())
        self.assertFalse(result['working']); self.assertLessEqual(result['tests'], self.m.CHECK_TIME // 5)

    def test_a_list_is_renewed_at_six_days_and_then_checked_again(self):
        api = FakeMihomo([('A', True)], age=6 * DAY, renewed=[('B', False), ('C', True)])
        code, value, _ = self.run_main(api)
        self.assertEqual(code, 0)
        self.assertEqual(value['check'], {'tests': 2, 'working': True, 'renewal': 'the list is at least 6 days old', 'renewed': True})
        self.assertEqual(value['selected_server'], 'C')
        api = FakeMihomo([('A', True)], age=6 * DAY - 60)
        self.assertIsNone(self.run_main(api)[1]['check']['renewal'])

    def test_failures_renew_a_list_earlier_but_not_within_an_hour_of_an_update(self):
        relays = [('A', False), ('B', False), ('C', True), ('D', True)]
        tested = FakeMihomo(relays, age=2 * DAY)
        for relay in tested.relays[2:]: relay.update(alive=False, history=[{'delay': 0}])  # a native round
        # Half of the relays failed their last test.
        self.assertEqual(self.m.renewal(self.m.status(tested), {'working': True}, NOW), '2 of 4 relays failed their last test')
        self.assertEqual(self.m.renewal(self.m.status(FakeMihomo(relays, age=2 * DAY)), {'working': False}, NOW), 'no relay works')
        self.assertIsNone(self.m.renewal(self.m.status(FakeMihomo(relays, age=1800)), {'working': False}, NOW))
        self.assertEqual(self.m.renewal(self.m.status(FakeMihomo([('SUBSCRIPTION-UNAVAILABLE', False)], age=60)),
                                        {'working': False}, NOW), 'no list is loaded')
        # One dead relay of four is normal: no renewal.
        self.assertIsNone(self.m.renewal(self.m.status(FakeMihomo(relays, age=2 * DAY)) | {'health': [
            {'name': 'A', 'alive': False, 'history': [{}]}] + [{'name': n, 'alive': True, 'history': []} for n in 'BCD']},
            {'working': True}, NOW))

    def test_a_failed_renewal_keeps_the_list_and_withholds_the_url(self):
        api = FakeMihomo([('A', False)], age=7 * DAY, renewed=None)
        code, value, err = self.run_main(api)
        self.assertEqual((code, value['check']['renewed'], value['server_count']), (1, False, 1))
        code, value, err = self.run_main(FakeMihomo([('A', True)], renewed=None), 'refresh')
        self.assertEqual((code, value), (2, None))
        self.assertIn('HTTPError', err); self.assertNotIn('private.invalid', err)

    def test_refresh_renews_then_checks_and_status_only_reads(self):
        api = FakeMihomo([('A', True)], renewed=[('B', True)])
        code, value, _ = self.run_main(api, 'refresh')
        self.assertEqual((code, value['selected_server'], value['check']['renewal']), (0, 'B', 'requested'))
        api = FakeMihomo([('A', False)])
        code, value, _ = self.run_main(api, 'status')
        self.assertEqual((code, api.tests(), value['renewal_age']), (0, [], 6 * DAY))
        self.assertNotIn('check', value)
        self.assertEqual(self.m.main(['other']), 2)

    def test_time_stamps_with_nanoseconds_and_offsets(self):
        self.assertEqual(self.m.parse_time(stamp(NOW)), NOW)
        self.assertEqual(self.m.parse_time('2027-01-15T16:00:00.5+08:00'), self.m.parse_time('2027-01-15T08:00:00Z'))
        self.assertIsNone(self.m.parse_time(None))


if __name__ == '__main__':
    unittest.main()
