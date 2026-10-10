"""Qualify the pinned Mihomo binary with loopback-only synthetic relays.

Run on the controller with MIHOMO_TEST_BINARY pointing to a verified executable.
No subscription, real relay, or Internet destination is used by this test.
"""
import contextlib
import json
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
    def test_failure_selects_lowest_latency_without_scheduled_checks(self):
        with contextlib.ExitStack() as cleanup:
            relays, payload = {}, []
            for name, delay in [('primary', .2), ('slow', .4), ('fast', .02)]:
                server = Relay(('127.0.0.1', 0), RelayHandler)
                server.delay, server.fail = delay, False
                threading.Thread(target=server.serve_forever, daemon=True).start()
                cleanup.callback(server.server_close)
                cleanup.callback(server.shutdown)
                relays[name] = server
                payload.append({'name': name, 'type': 'http', 'server': '127.0.0.1',
                                'port': server.server_address[1]})
            directory = Path(cleanup.enter_context(tempfile.TemporaryDirectory(prefix='mihomo-failover-test-')))
            # Keep the production group/provider structure. Replace only transport
            # fixtures, listeners, DNS and routing so no external traffic is possible.
            config = vpn.render_config({'proxies': [{'name': 'fixture', 'type': 'vmess',
                'server': 'fixture.invalid', 'port': 443, 'uuid': 'fixture'}]},
                '127.0.0.1', ['127.0.0.1'], 'test-only-secret-with-no-authority')
            proxy_port, api_port = unused_port(), unused_port()
            config.update({'mixed-port': proxy_port, 'external-controller': f'127.0.0.1:{api_port}',
                           'dns': {'enable': False}, 'rule-providers': {}, 'rules': ['MATCH,VPN']})
            config['proxy-providers']['relays']['payload'] = payload
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
            def api(route):
                request = urllib.request.Request(f'http://127.0.0.1:{api_port}/{route}',
                    headers={'Authorization': 'Bearer ' + config['secret']})
                with direct.open(request, timeout=2) as response:
                    return json.load(response)
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

            def fail_relay(name, expected):
                relays[name].fail = True
                for _ in range(2):
                    with self.assertRaises((OSError, urllib.error.URLError)):
                        request()
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
