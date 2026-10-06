"""The broker must never confuse two offline or online same-name devices."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
WRAPPER = ROOT / 'klokast-ops/tailscale/bin/ts-device-delete-stale'


@unittest.skipUnless(shutil.which('jq'), 'requires jq')
class DeviceDeleteTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        scripts = self.work / 'bin'
        scripts.mkdir()
        (self.work / 'secrets').write_text('TAILNET_ID=test\nTS_OAUTH_CLIENT_ID=test\nTS_OAUTH_CLIENT_SECRET=test\n')
        self.api = {'devices': [
            {'id': '111', 'nodeId': 'old-device', 'hostname': 'boxa-router',
             'addresses': ['100.64.0.1']},
            {'id': '222', 'nodeId': 'new-device', 'hostname': 'boxa-router',
             'addresses': ['100.64.0.2']}]}
        self.detail = {**self.api['devices'][1], 'tags': ['tag:vm']}
        self.status = {'Self': {'ID': 'controller'}, 'Peer': {
            'old': {'ID': 'old-device', 'HostName': 'boxa-router', 'Online': True,
                    'TailscaleIPs': ['100.64.0.1']},
            'test': {'ID': 'new-device', 'HostName': 'boxa-router', 'Online': False,
                     'TailscaleIPs': ['100.64.0.2']}}}
        (scripts / 'curl').write_text('''#!/bin/sh
case " $* " in
  *" -X DELETE "*) : > "$TEST_DELETE_MARKER" ;;
  *"/oauth/token"*) printf '{"access_token":"test-token"}\\n' ;;
  *"/device/222?fields=all"*) cat "$TEST_DEVICE_JSON" ;;
  *"/tailnet/test/devices"*) cat "$TEST_LIST_JSON" ;;
  *) exit 2 ;;
esac
''')
        (scripts / 'guard').write_text('#!/bin/sh\nexit 0\n')
        (scripts / 'tailscale').write_text('#!/bin/sh\ncat "$TEST_STATUS_JSON"\n')
        for path in scripts.iterdir():
            path.chmod(0o755)
        self.env = dict(os.environ, PATH=str(scripts) + ':' + os.environ['PATH'],
            KLOKAST_CONTROLLER_GUARD=str(scripts / 'guard'),
            TS_DEVICES_SECRET_FILE=str(self.work / 'secrets'),
            TEST_DEVICE_JSON=str(self.work / 'device.json'),
            TEST_LIST_JSON=str(self.work / 'list.json'),
            TEST_STATUS_JSON=str(self.work / 'status.json'),
            TEST_DELETE_MARKER=str(self.work / 'deleted'))

    def invoke(self, machine_id='new-device'):
        for name, value in (('device.json', self.detail), ('list.json', self.api),
                            ('status.json', self.status)):
            (self.work / name).write_text(json.dumps(value))
        return subprocess.run([str(WRAPPER), '--id', '222', '--hostname', 'boxa-router',
            '--tag', 'tag:vm', '--machine-id', machine_id], env=self.env,
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)

    def test_exact_offline_node_with_detail_tags_can_be_deleted(self):
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.work / 'deleted').exists())

    def test_missing_guard_prevents_provider_access(self):
        self.env['KLOKAST_CONTROLLER_GUARD'] = str(self.work / 'missing-guard')
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('guard is missing', result.stderr)
        self.assertFalse((self.work / 'deleted').exists())

    def test_wrong_node_or_changed_address_never_reaches_delete(self):
        result = self.invoke('old-device')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.work / 'deleted').exists())
        self.detail['addresses'] = ['100.64.0.9']
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.work / 'deleted').exists())


if __name__ == '__main__':
    unittest.main()
