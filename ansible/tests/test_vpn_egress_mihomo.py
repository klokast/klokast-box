"""Qualify the pinned Mihomo binary with loopback-only synthetic relays.

Run on the controller with MIHOMO_TEST_BINARY pointing to a verified executable.
No subscription, real relay, or Internet destination is used by this test.
"""
import contextlib
import json
import http.server
import os
from pathlib import Path
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import platform_vpn_egress as vpn
import vpn_egress_provisioning as provisioning
from test_routing_profile import CN


class Relay(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class RelayHandler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(5)
        try:
            line = self.rfile.readline()
            while self.rfile.readline() not in (b'\r\n', b'\n', b''):
                pass
            if self.server.fail:
                self.server.failures += 1
                self.wfile.write(b'HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n')
                return
            if line.startswith(b'CONNECT '):
                self.wfile.write(b'HTTP/1.1 200 Connection Established\r\n\r\n')
                self.wfile.flush()
                line = self.rfile.readline()
                while self.rfile.readline() not in (b'\r\n', b'\n', b''):
                    pass
            time.sleep(self.server.delay)
            self.wfile.write(b'HTTP/1.1 204 No Content\r\nConnection: close\r\n\r\n')
        except (OSError, TimeoutError):
            pass


def unused_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


@unittest.skipUnless(os.environ.get('MIHOMO_TEST_BINARY'), 'requires the verified controller Mihomo binary')
class MihomoFailoverTests(unittest.TestCase):
    def test_initial_seed_uses_native_filters_and_rejects_empty_result(self):
        provider = vpn.relay_provider('https://subscription.invalid:5000/token')
        relay = {'name': 'fixture', 'type': 'ss', 'server': '127.0.0.1', 'port': 443,
                 'cipher': 'aes-128-gcm', 'password': 'fixture', 'udp': True, 'dialer-proxy': 'DIRECT'}
        data = yaml.safe_dump({'proxies': [relay, {'name': 'bypass', 'type': 'direct'}],
                               'rules': ['MATCH,DIRECT'], 'tun': {'enable': True}}).encode()
        provisioning.validate_subscription(data, Path(os.environ['MIHOMO_TEST_BINARY']), provider)
        for proxies in ([], [{'name': 'bypass', 'type': 'direct'}],
                        [dict(relay, name='VPN')], [dict(relay, cipher='invalid')]):
            with self.subTest(proxies=proxies), self.assertRaisesRegex(RuntimeError, 'Mihomo validation'):
                provisioning.validate_subscription(yaml.safe_dump({'proxies': proxies}).encode(),
                                                   Path(os.environ['MIHOMO_TEST_BINARY']), provider)

    def test_failure_selects_lowest_latency_without_scheduled_checks(self):
        with contextlib.ExitStack() as cleanup:
            relays, payload = {}, []
            for name, delay in [('primary', .2), ('slow', .4), ('fast', .02)]:
                server = Relay(('127.0.0.1', 0), RelayHandler)
                server.delay, server.fail, server.failures = delay, False, 0
                threading.Thread(target=server.serve_forever, daemon=True).start()
                cleanup.callback(server.server_close)
                cleanup.callback(server.shutdown)
                relays[name] = server
                payload.append({'name': name, 'type': 'http', 'server': '127.0.0.1',
                                'port': server.server_address[1]})
            directory = Path(cleanup.enter_context(tempfile.TemporaryDirectory(prefix='mihomo-failover-test-')))
            # Keep the production group/provider structure. Replace only transport
            # fixtures, listeners, DNS and routing so no external traffic is possible.
            config = vpn.render_config('https://subscription.invalid/token',
                '127.0.0.1', ['127.0.0.1'], 'test-only-secret-with-no-authority', profile=CN)
            proxy_port, api_port = unused_port(), unused_port()
            config.update({'mixed-port': proxy_port, 'external-controller': f'127.0.0.1:{api_port}',
                           'dns': {'enable': False}, 'rule-providers': {}, 'rules': ['MATCH,VPN']})
            class SubscriptionHandler(http.server.BaseHTTPRequestHandler):
                def do_GET(self):
                    self.server.requests += 1
                    if self.path == '/must-not-arrive':
                        self.server.leaks += 1
                    self.send_response(self.server.status)
                    self.end_headers()
                    self.wfile.write(self.server.body)
                def log_message(self, *args):
                    pass
            subscription = http.server.ThreadingHTTPServer(('127.0.0.1', 0), SubscriptionHandler)
            subscription.requests, subscription.status, subscription.leaks = 0, 503, 0
            subscription.body = yaml.safe_dump({'proxies': payload}).encode()
            threading.Thread(target=subscription.serve_forever, daemon=True).start()
            cleanup.callback(subscription.server_close); cleanup.callback(subscription.shutdown)
            provider = config['proxy-providers']['relays']
            provider.update(url=f'http://127.0.0.1:{subscription.server_port}/subscription', interval=2)
            # HTTP relay fixtures are local; production excludes this protocol.
            provider['exclude-type'] = provider['exclude-type'].replace('http|', '')
            cache = directory / provider['path']
            cache.parent.mkdir(); cache.write_bytes(subscription.body)
            health_url = 'http://health.invalid/health'
            config['proxy-providers']['relays']['health-check']['url'] = health_url
            config['proxy-groups'][0]['url'] = health_url
            path = directory / 'config.yml'
            path.write_text(yaml.safe_dump(config))
            log = cleanup.enter_context((directory / 'mihomo.log').open('w+'))
            process = subprocess.Popen([os.environ['MIHOMO_TEST_BINARY'], '-d', str(directory), '-f', str(path)],
                                       stdout=log, stderr=subprocess.STDOUT)
            def stop():
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            cleanup.callback(stop)
            direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            proxied = urllib.request.build_opener(urllib.request.ProxyHandler({'http': f'http://127.0.0.1:{proxy_port}'}))
            def api(route, method='GET'):
                request = urllib.request.Request(f'http://127.0.0.1:{api_port}/{route}',
                    method=method, headers={'Authorization': 'Bearer ' + config['secret']})
                with direct.open(request, timeout=2) as response:
                    data = response.read()
                    return json.loads(data) if data else None
            def histories():
                proxies = api('providers/proxies')['providers']['relays']['proxies']
                return {p['name']: p['history'] for p in proxies}
            def request():
                with proxied.open('http://request.invalid/work', timeout=3) as response:
                    self.assertEqual(response.status, 204)
            def wait_for(predicate):
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    self.assertIsNone(process.poll(), 'Mihomo stopped unexpectedly')
                    try:
                        if predicate():
                            return
                    except (OSError, urllib.error.URLError):
                        pass
                    time.sleep(.1)
                log.flush()
                log.seek(0)
                self.fail('Timed out waiting for native failover:\n' + log.read())
            wait_for(lambda: api('proxies/VPN')['type'] == 'URLTest')
            time.sleep(2)
            self.assertTrue(all(not h for h in histories().values()), 'Unexpected startup tests')
            request()
            self.assertEqual(api('proxies/VPN')['now'], 'primary')
            self.assertTrue(all(not h for h in histories().values()), 'Healthy traffic caused probes')
            print('Native check: no startup or healthy-traffic probes.', flush=True)

            subscription.status = 200
            wait_for(lambda: subscription.requests >= 2)
            self.assertEqual(cache.read_bytes(), subscription.body)
            subscription.body = yaml.safe_dump({'proxies': payload, 'rules': ['MATCH,REJECT'], 'tun': {'enable': True}}).encode()
            api('providers/proxies/relays', 'PUT')
            self.assertEqual(cache.read_bytes(), subscription.body)
            self.assertTrue(all(not h for h in histories().values()), 'Subscription change caused probes')
            accepted = cache.read_bytes()
            for invalid in (b'proxies: []', b'proxies: ['):
                subscription.body = invalid
                with self.assertRaises(urllib.error.HTTPError):
                    api('providers/proxies/relays', 'PUT')
                self.assertEqual(cache.read_bytes(), accepted)
                self.assertEqual(set(histories()), set(relays))
            subscription.status = 503
            count = subscription.requests
            wait_for(lambda: subscription.requests > count)
            self.assertEqual(cache.read_bytes(), accepted)
            subscription.status, subscription.body = 200, accepted
            wait_for(lambda: subscription.requests > count + 1)
            self.assertTrue(all(not h for h in histories().values()), 'Retry caused relay tests')
            print('Native check: automatic updates, changed data, malformed data and retry preserve health policy.', flush=True)

            process.terminate(); process.wait(timeout=5)
            cache.unlink()
            subscription.status = 503
            process = subprocess.Popen([os.environ['MIHOMO_TEST_BINARY'], '-d', str(directory), '-f', str(path)],
                                       stdout=log, stderr=subprocess.STDOUT)
            wait_for(lambda: api('proxies/VPN')['now'] == 'SUBSCRIPTION-UNAVAILABLE')
            with self.assertRaises((OSError, urllib.error.URLError)):
                proxied.open(f'http://127.0.0.1:{subscription.server_port}/must-not-arrive', timeout=3)
            self.assertEqual(subscription.leaks, 0, 'Missing subscription caused direct fallback')
            subscription.status = 200
            wait_for(lambda: set(histories()) == set(relays) and api('proxies/VPN')['now'] == 'primary')
            self.assertTrue(all(not h for h in histories().values()), 'Recovery caused relay probes')
            print('Native check: cached startup and missing-cache recovery fail closed without relay probes.', flush=True)

            def fail_relay(name, expected):
                relays[name].fail = True
                for _ in range(2):
                    try:
                        request()
                    except (OSError, urllib.error.URLError):
                        pass  # Mihomo may retry setup internally before the client sees failure.
                self.assertGreater(relays[name].failures, 0, 'No real relay failure occurred')
                wait_for(lambda: all(histories().values()) and api('proxies/VPN')['now'] == expected)
                self.assertFalse(api('proxies/VPN')['fixed'], 'Manual selection overrides latency')
                request()
            fail_relay('primary', 'fast')
            baseline = histories()
            self.assertLess(baseline['fast'][-1]['delay'], baseline['slow'][-1]['delay'])
            print('Native check: real request failures selected the lowest-latency relay.', flush=True)

            # Keep traffic active past the implicit 300-second default. An idle-only
            # test would miss a lazy scheduler. Compare timestamps as well as counts.
            deadline = time.monotonic() + 310
            report = time.monotonic() + 30
            while time.monotonic() < deadline:
                request()
                self.assertEqual(histories(), baseline, 'Scheduled relay check detected')
                if time.monotonic() >= report:
                    print('Native check: healthy traffic continues without new relay tests.', flush=True)
                    report += 30
                time.sleep(5)
            fail_relay('fast', 'slow')
            print('Native check: second real failure selected the remaining healthy relay.', flush=True)


if __name__ == '__main__':
    unittest.main(verbosity=2)
