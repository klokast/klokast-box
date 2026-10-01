"""Scheduled router starts must preserve both policy timing and local recovery."""
import copy
import datetime as dt
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import router_updates as router
from platform_updates import UpdateError


class RouterWindowTests(unittest.TestCase):
    def setUp(self):
        self.policy = {'enabled': True, 'targets': {'boxa': ['router']},
            'exclusions': [], 'replacement-minutes': 15, 'recovery-minutes': 15,
            'maintenance-window': {'start': '02:00', 'last-start': '03:00', 'end': '04:00'}}
        self.request = {'box': 'boxa', 'role': 'router',
                        'cutover_seconds': 900, 'recovery_seconds': 900}

    def clock(self, hour, minute=0, second=0):
        return dt.datetime(2026, 10, 1, hour, minute, second, tzinfo=dt.timezone.utc)

    def check(self, now, policy=None, request=None):
        return router.require_cutover_window(self.policy if policy is None else policy,
            'boxa', now, self.request if request is None else request)

    def test_start_and_last_start_are_inclusive_but_next_second_is_closed(self):
        for now in (self.clock(2), self.clock(3)):
            self.assertEqual(self.check(now), int(self.clock(3).timestamp()) + 1)
        for now in (self.clock(1, 59, 59), self.clock(3, 0, 1), self.clock(4)):
            with self.subTest(now=now), self.assertRaisesRegex(UpdateError, 'outside'):
                self.check(now)

    def test_actual_transaction_reserve_cannot_be_smaller_than_policy_reserve(self):
        policy = copy.deepcopy(self.policy)
        policy.update({'replacement-minutes': 5, 'recovery-minutes': 5})
        policy['maintenance-window']['last-start'] = '03:30'
        request = {**self.request, 'cutover_seconds': 1800, 'recovery_seconds': 1800}
        self.assertEqual(self.check(self.clock(3), policy, request),
                         int(self.clock(3).timestamp()) + 1)
        with self.assertRaisesRegex(UpdateError, 'recovery reserve'):
            self.check(self.clock(3, 0, 1), policy, request)

    def test_disabled_excluded_other_role_and_other_box_cannot_start(self):
        for change in ({'enabled': False}, {'enabled': 1}, {'targets': {'boxa': ['dmz']}},
                {'targets': {'boxb': ['router']}}, {'targets': {'boxa': None}},
                {'exclusions': [{'box': 'boxa', 'role': 'router', 'reason': 'maintenance'}]}):
            with self.subTest(change=change), self.assertRaisesRegex(UpdateError, 'enabled target'):
                self.check(self.clock(2), {**self.policy, **change})
        with self.assertRaises(UpdateError):
            self.check(self.clock(2), request={**self.request, 'role': 'dmz'})

    def test_real_exclusion_contract_refuses_cutover_and_malformed_rows(self):
        row = {'box': 'boxa', 'role': 'router', 'reason': 'manual recovery'}
        for rows in ([row], [row, row], [{'box': 'boxa', 'role': 'router'}],
                     [{**row, 'reason': ' '}], [{**row, 'role': []}]):
            with self.subTest(rows=rows), self.assertRaises(UpdateError):
                self.check(self.clock(2), {**self.policy, 'exclusions': rows})

    def test_non_utc_naive_and_invalid_budgets_refuse(self):
        for now in (self.clock(2).replace(tzinfo=None),
                    self.clock(2).astimezone(dt.timezone(dt.timedelta(hours=2)))):
            with self.subTest(now=now), self.assertRaises(UpdateError):
                self.check(now)
        for key in ('cutover_seconds', 'recovery_seconds'):
            for value in (True, 0, -1, 3601, '900'):
                with self.subTest(key=key, value=value), self.assertRaises(UpdateError):
                    self.check(self.clock(2), request={**self.request, key: value})

    def test_invalid_window_cannot_fall_back_to_default_timing(self):
        for window in ({'start': '02:00', 'last-start': '03:00'},
                {'start': '2', 'last-start': '03:00', 'end': '04:00'},
                {'start': '23:00', 'last-start': '23:30', 'end': '01:00'},
                {'start': '02:00+00:00', 'last-start': '03:00', 'end': '04:00'}):
            with self.subTest(window=window), self.assertRaisesRegex(UpdateError, 'invalid maintenance'):
                self.check(self.clock(2), {**self.policy, 'maintenance-window': window})


if __name__ == '__main__':
    unittest.main()
