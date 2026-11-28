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
import struct
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


def connect(port):
    """Connect to a Mihomo listener once it has started."""
    for _ in range(100):
        try:
            return socket.create_connection(('127.0.0.1', port), timeout=5)
        except ConnectionRefusedError:
            time.sleep(.1)
    raise AssertionError('Mihomo listener did not start on port ' + str(port))


class FakeDNS(socketserver.ThreadingUDPServer):
    """Answer every A query with one fixed address and record the names."""
    daemon_threads = True

    def __init__(self, answer):
        super().__init__(('127.0.0.1', 0), FakeDNSHandler)
        self.answer, self.names = answer, []


class FakeDNSHandler(socketserver.BaseRequestHandler):
    def handle(self):
        data, sock = self.request
        end, labels = 12, []
        while data[end]:
            labels.append(data[end + 1:end + 1 + data[end]].decode()); end += data[end] + 1
        qtype = struct.unpack('!H', data[end + 1:end + 3])[0]
        self.server.names.append('.'.join(labels))
        answer = (b'\xc0\x0c' + struct.pack('!HHIH', 1, 1, 60, 4) + socket.inet_aton(self.server.answer)
                  if qtype == 1 else b'')
        sock.sendto(data[:2] + struct.pack('!5H', 0x8180, 1, 1 if answer else 0, 0, 0) +
                    data[12:end + 5] + answer, self.client_address)


def resolve(port, name):
    """Return the first A record from the Mihomo DNS listener."""
    query = (struct.pack('!6H', 0x1234, 0x100, 1, 0, 0, 0) +
             b''.join(bytes([len(v)]) + v.encode() for v in name.split('.')) + b'\0' + struct.pack('!2H', 1, 1))
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(5)
        sock.sendto(query, ('127.0.0.1', port))
        reply = sock.recv(4096)
    assert struct.unpack('!H', reply[6:8])[0] > 0, 'Mihomo DNS gave no answer for ' + name
    return socket.inet_ntoa(reply[-4:])


def unused_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


@unittest.skipUnless(os.environ.get('MIHOMO_TEST_BINARY'), 'requires the verified controller Mihomo binary')
class MihomoFailoverTests(unittest.TestCase):
    def test_group_disables_client_udp_for_hysteria2(self):
        with tempfile.TemporaryDirectory(prefix='mihomo-udp-test-') as directory:
            config = vpn.render_config('https://subscription.invalid/token',
                '127.0.0.1', ['127.0.0.1'], 'test-only-secret-with-no-authority', profile=CN)
            port = unused_port()
            config.update({'mixed-port': unused_port(), 'external-controller': f'127.0.0.1:{port}',
                           'dns': {'enable': False}, 'rule-providers': {}, 'rules': ['MATCH,VPN']})
            provider = config['proxy-providers']['relays']
            provider.update(type='inline', payload=[{'name': 'fixture', 'type': 'hysteria2',
                'server': '127.0.0.1', 'port': unused_port(), 'password': 'fixture'}])
            path = Path(directory) / 'config.yml'
            path.write_text(yaml.safe_dump(config))
            with (Path(directory) / 'mihomo.log').open('w') as log:
                process = subprocess.Popen([os.environ['MIHOMO_TEST_BINARY'], '-d', directory, '-f', str(path)],
                                           stdout=log, stderr=subprocess.STDOUT)
                try:
                    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                    def api(name):
                        request = urllib.request.Request(f'http://127.0.0.1:{port}/proxies/{name}',
                            headers={'Authorization': 'Bearer ' + config['secret']})
                        with opener.open(request, timeout=1) as response:
                            return json.load(response)
                    for _ in range(50):
                        try:
                            group = api('VPN')
                            break
                        except OSError:
                            time.sleep(.1)
                    else:
                        self.fail('Mihomo did not start')
                    self.assertTrue(api('fixture')['udp'], 'Hysteria2 must exercise the ignored override')
                    self.assertIs(group['udp'], False, 'Group must block client UDP for Hysteria2')
                finally:
                    process.terminate()
                    process.wait(timeout=5)

    def test_initial_seed_uses_native_filters_and_rejects_empty_result(self):
        provider = vpn.relay_provider('https://subscription.invalid:5000/token')
        relay = {'name': 'fixture', 'type': 'ss', 'server': '127.0.0.1', 'port': 443,
                 'cipher': 'aes-128-gcm', 'password': 'fixture', 'udp': True, 'dialer-proxy': 'DIRECT'}
        data = yaml.safe_dump({'proxies': [relay, {'name': 'bypass', 'type': 'direct'}],
                               'rules': ['MATCH,DIRECT'], 'tun': {'enable': True}}).encode()
        provisioning.validate_subscription(data, Path(os.environ['MIHOMO_TEST_BINARY']), provider)
        for proxies in ([], [{'name': 'bypass', 'type': 'direct'}],
                        [dict(relay, name='VPN')],
                        [dict(relay, name='Hong-Kong')], [dict(relay, cipher='invalid')]):
            with self.subTest(proxies=proxies), self.assertRaisesRegex(RuntimeError, 'Mihomo validation'):
                provisioning.validate_subscription(yaml.safe_dump({'proxies': proxies}).encode(),
                                                   Path(os.environ['MIHOMO_TEST_BINARY']), provider)

    def mihomo(self, cleanup, config):
        """Start the pinned binary on a loopback-only configuration."""
        directory = Path(cleanup.enter_context(tempfile.TemporaryDirectory(prefix='mihomo-redirect-test-')))
        executable = directory / 'mihomo'
        executable.write_bytes(Path(os.environ['MIHOMO_TEST_BINARY']).read_bytes()); executable.chmod(0o700)
        path = directory / 'config.yml'
        path.write_text(yaml.safe_dump(config, allow_unicode=True))
        checked = subprocess.run([str(executable), '-t', '-d', str(directory), '-f', str(path)],
                                 capture_output=True, text=True, timeout=30)
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
        log = cleanup.enter_context((directory / 'mihomo.log').open('w+'))
        process = subprocess.Popen([str(executable), '-d', str(directory), '-f', str(path)],
                                   stdout=log, stderr=subprocess.STDOUT)
        def stop():
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=5)
        cleanup.callback(stop)
        return process

    def test_rendered_redirect_listener_and_rules_pass_native_validation(self):
        config = vpn.render_config('https://subscription.invalid/token', '127.0.0.1', ['127.0.0.1'],
                                   'test-only-secret-with-no-authority', profile=CN)
        self.assertEqual([v['type'] for v in config['listeners']], ['redir'])
        self.assertIn('AND,((IN-TYPE,REDIR),(DOMAIN-SUFFIX,tailscale.com)),DIRECT', config['rules'])
        redirect = config['listeners'][0]['port'] = unused_port()
        # Keep the production rules except the downloaded list; no network access.
        config.update({'mixed-port': unused_port(),
                       'external-controller': f'127.0.0.1:{unused_port()}', 'dns': {'enable': False},
                       'rule-providers': {}, 'rules': [r for r in config['rules'] if not r.startswith('RULE-SET,')]})
        config['proxy-providers']['relays'].update(type='inline')
        with contextlib.ExitStack() as cleanup:
            process = self.mihomo(cleanup, config)
            connect(redirect).close()
            self.assertIsNone(process.poll(), 'Mihomo stopped with the redirect listener')
            log = (Path(process.args[2]) / 'mihomo.log').read_text()
            # The redirect listener must not need a capability (no UDP transparent socket).
            self.assertNotIn('operation not permitted', log)
            self.assertNotIn('Redir UDP', log)

    def test_split_dns_sends_only_listed_names_to_vpn_dns(self):
        with contextlib.ExitStack() as cleanup:
            direct, tunnel = FakeDNS('192.0.2.1'), FakeDNS('192.0.2.2')
            for server in (direct, tunnel):
                threading.Thread(target=server.serve_forever, daemon=True).start()
                cleanup.callback(server.server_close); cleanup.callback(server.shutdown)
            # The VPN transport of DNS is checked live by verify. Here a local
            # server stands for each choice, so only the selection is tested.
            bootstrap = ['udp://127.0.0.1:' + str(direct.server_address[1])]
            through_vpn = ['udp://127.0.0.1:' + str(tunnel.server_address[1])]
            def local(servers):
                return through_vpn if all(v.endswith('#VPN') for v in servers) else bootstrap
            for selected, expected in ((CN, {'www.blocked.test': '192.0.2.2', 'pkgs.tailscale.com': '192.0.2.2',
                                             'plain.test': '192.0.2.1', 'subscription.invalid': '192.0.2.1'}),
                                       (dict(CN, **{'domain-list-url': None}),
                                        {'www.blocked.test': '192.0.2.2', 'plain.test': '192.0.2.2',
                                         'subscription.invalid': '192.0.2.1'})):
                with self.subTest(listed=selected['domain-list-url'] is not None), contextlib.ExitStack() as run:
                    direct.names.clear(); tunnel.names.clear()
                    config = vpn.render_config('https://subscription.invalid/token', '127.0.0.1', [],
                        'test-only-secret-with-no-authority', profile=selected, controller_tunnel=True)
                    dns, port = config['dns'], unused_port()
                    dns.update({'listen': '127.0.0.1:' + str(port), 'nameserver': local(dns['nameserver']),
                                'default-nameserver': bootstrap, 'proxy-server-nameserver': bootstrap,
                                'nameserver-policy': {k: local(v) for k, v in dns['nameserver-policy'].items()}})
                    config['rule-providers'] = {name: {'type': 'inline', 'behavior': 'domain', 'payload': ['+.blocked.test']}
                                                for name in config['rule-providers']}
                    config.update({'mixed-port': unused_port(), 'external-controller': f'127.0.0.1:{unused_port()}'})
                    config['proxy-providers']['relays'].update(type='inline')
                    process = self.mihomo(run, config)
                    deadline = time.monotonic() + 10
                    while True:
                        try:
                            resolve(port, 'start.test'); break
                        except OSError:
                            self.assertLess(time.monotonic(), deadline, 'Mihomo DNS did not start')
                            self.assertIsNone(process.poll(), 'Mihomo stopped')
                    for name, address in expected.items():
                        self.assertEqual(resolve(port, name), address, name)
                    if selected['domain-list-url']:
                        # Bootstrap resolvers never see a listed name.
                        self.assertNotIn('www.blocked.test', direct.names)
                        self.assertNotIn('pkgs.tailscale.com', direct.names)
                        self.assertNotIn('plain.test', tunnel.names)

    def test_sniffed_name_selects_the_rule_and_the_original_address_is_kept(self):
        import ssl
        class RecordingHandler(RelayHandler):
            def handle(self):
                line = self.rfile.readline()
                self.server.targets.append(line.split()[1].decode() if line.startswith(b'CONNECT ') else '')
                self.wfile.write(b'HTTP/1.1 200 Connection Established\r\n\r\n')
        with contextlib.ExitStack() as cleanup:
            relay = Relay(('127.0.0.1', 0), RecordingHandler)
            relay.targets = []
            threading.Thread(target=relay.serve_forever, daemon=True).start()
            cleanup.callback(relay.server_close); cleanup.callback(relay.shutdown)
            config = vpn.render_config('https://subscription.invalid/token', '127.0.0.1', ['127.0.0.1'],
                                       'test-only-secret-with-no-authority', profile=CN)
            tunnel = unused_port()
            # A tunnel listener gives Mihomo an address-only connection, as the
            # gateway redirect does, without root or nftables.
            config.update({'mixed-port': unused_port(),
                           'external-controller': f'127.0.0.1:{unused_port()}', 'dns': {'enable': False},
                           'rule-providers': {},
                           'listeners': [{'name': 'redirect-fixture', 'type': 'tunnel', 'port': tunnel,
                                          'listen': '127.0.0.1', 'network': ['tcp'], 'target': '127.0.0.2:443'}],
                           'rules': ['DOMAIN-SUFFIX,listed.example.test,VPN', 'MATCH,REJECT']})
            provider = config['proxy-providers']['relays']
            provider.update(type='inline', payload=[{'name': 'fixture', 'type': 'http', 'server': '127.0.0.1',
                                                     'port': relay.server_address[1]}])
            provider['exclude-type'] = provider['exclude-type'].replace('http|', '')
            self.assertEqual(config['sniffer']['override-destination'], False)
            process = self.mihomo(cleanup, config)
            for name in ('www.listed.example.test', 'unlisted.example.test'):
                outgoing = ssl.MemoryBIO()
                tls = ssl.create_default_context().wrap_bio(ssl.MemoryBIO(), outgoing, server_hostname=name)
                with contextlib.suppress(ssl.SSLWantReadError):
                    tls.do_handshake()
                with connect(tunnel) as connection:
                    connection.sendall(outgoing.read())
                    connection.settimeout(5)
                    with contextlib.suppress(OSError):
                        connection.recv(100)
            deadline = time.monotonic() + 5
            while not relay.targets and time.monotonic() < deadline:
                time.sleep(.1)
            self.assertIsNone(process.poll(), 'Mihomo stopped unexpectedly')
            # Only the listed name used the VPN, and the relay got the original address.
            self.assertEqual(relay.targets, ['127.0.0.2:443'])

    def test_failure_selects_available_relay_without_scheduled_checks(self):
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
            excluded = [dict(payload[-1], name=name) for name in
                        ('Hong Kong', 'Hong-Kong', 'Hong_Kong', 'HongKong',
                         '香港', '🇭🇰', 'HK01', '[hk] relay')]
            payload = excluded + payload
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
                    self.server.user_agent = self.headers.get('User-Agent')
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
            wait_for(lambda: api('proxies/VPN')['type'] == 'Fallback')
            time.sleep(2)
            self.assertTrue(all(not h for h in histories().values()), 'Unexpected startup tests')
            request()
            self.assertEqual(api('proxies/VPN')['now'], 'primary')
            self.assertTrue(all(not h for h in histories().values()), 'Healthy traffic caused probes')
            print('Native check: no startup or healthy-traffic probes.', flush=True)

            subscription.status = 200
            wait_for(lambda: subscription.requests >= 2)
            self.assertEqual(cache.read_bytes(), subscription.body)
            self.assertEqual(subscription.user_agent, vpn.SUBSCRIPTION_USER_AGENT)
            subscription.body = yaml.safe_dump({'proxies': payload, 'rules': ['MATCH,REJECT'], 'tun': {'enable': True}}).encode()
            api('providers/proxies/relays', 'PUT')
            self.assertEqual(cache.read_bytes(), subscription.body)
            self.assertTrue(all(not h for h in histories().values()), 'Subscription change caused probes')
            accepted = cache.read_bytes()
            for invalid in (b'proxies: []', b'proxies: [',
                            yaml.safe_dump({'proxies': excluded}).encode()):
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

            for startup_cache in (None, yaml.safe_dump({'proxies': excluded}).encode()):
                process.terminate(); process.wait(timeout=5)
                if startup_cache is None:
                    cache.unlink()
                else:
                    cache.write_bytes(startup_cache)
                subscription.status = 503
                process = subprocess.Popen([os.environ['MIHOMO_TEST_BINARY'], '-d', directory, '-f', str(path)],
                                           stdout=log, stderr=subprocess.STDOUT)
                wait_for(lambda: api('proxies/VPN')['now'] == 'SUBSCRIPTION-UNAVAILABLE')
                with self.assertRaises((OSError, urllib.error.URLError)):
                    proxied.open(f'http://127.0.0.1:{subscription.server_port}/must-not-arrive', timeout=3)
                self.assertEqual(subscription.leaks, 0, 'Unavailable subscription caused direct fallback')
                subscription.status = 200
                wait_for(lambda: set(histories()) == set(relays) and api('proxies/VPN')['now'] == 'primary')
                self.assertEqual(set(api('proxies/VPN')['all']), set(relays))
                self.assertTrue(all(not h for h in histories().values()), 'Recovery caused relay probes')
            print('Native check: missing and all-excluded caches fail closed and recover without relay probes.', flush=True)

            def fail_relay(name, expected):
                relays[name].fail = True
                for _ in range(2):
                    try:
                        request()
                    except (OSError, urllib.error.URLError):
                        pass  # Mihomo may retry setup internally before the client sees failure.
                self.assertGreater(relays[name].failures, 0, 'No real relay failure occurred')
                wait_for(lambda: all(histories().values()) and api('proxies/VPN')['now'] == expected)
                self.assertFalse(api('proxies/VPN')['fixed'], 'Manual selection overrides automatic selection')
                request()
            fail_relay('primary', 'slow')
            baseline = histories()
            print('Native check: real request failures selected the first available relay.', flush=True)

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
            # A subscription update replaces relay objects. Selection must use their
            # new state; v1.19.25 url-test kept a stale dead relay in this case.
            subscription.body = accepted + b'# changed relay list\n'
            api('providers/proxies/relays', 'PUT')
            self.assertTrue(all(not h for h in histories().values()), 'Subscription update kept old relay state')
            relays['slow'].fail = True
            for _ in range(2):
                try:
                    request()
                except (OSError, urllib.error.URLError):
                    pass
            wait_for(lambda: all(histories().values()) and api('proxies/VPN')['now'] == 'fast')
            request()
            print('Native check: after a subscription update, failures selected the remaining healthy relay.', flush=True)


if __name__ == '__main__':
    unittest.main(verbosity=2)
