"""New package accounts must not take retained router service IDs."""
import tempfile
import sys
from pathlib import Path
import unittest
from unittest.mock import patch
from test_router_template import module


class BonjourAccounts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root/'etc').mkdir()
        self.guest = module('router-template-guest')
        (self.root/'etc/passwd').write_text('root:x:0:0:root:/root:/bin/ash\nmessagebus:x:103:103:messagebus:/dev/null:/sbin/nologin\ndnsmasq:x:104:104:dnsmasq:/dev/null:/sbin/nologin\n')
        (self.root/'etc/group').write_text('root:x:0:\nmessagebus:x:103:\ndnsmasq:x:104:\n')

    def test_reserves_package_identity_and_is_repeatable(self):
        with patch.object(self.guest.os,'chown'):
            self.guest.stable_messagebus_account(self.root)
            before = (self.root/'etc/passwd').read_text()
            self.guest.stable_messagebus_account(self.root)
        self.assertEqual((self.root/'etc/passwd').read_text(), before)
        self.assertIn('messagebus:x:81:81:', before)
        self.assertIn('dnsmasq:x:104:104:', before)
        self.assertIn('messagebus:x:81:', (self.root/'etc/group').read_text())

    def test_unrelated_account_conflict_is_refused_without_rewriting_tables(self):
        path = self.root/'etc/group'
        path.write_text(path.read_text()+'other:x:81:\n')
        before = path.read_text()
        with self.assertRaisesRegex(RuntimeError,'conflicts'):
            self.guest.stable_messagebus_account(self.root)
        self.assertEqual(path.read_text(), before)

    def test_common_image_entrypoint_applies_account_and_service_configuration(self):
        from test_vm_template_inputs import module as load
        repo = Path(__file__).resolve().parents[2]
        common = load('common_router_accounts', repo/'ansible/roles/vm-template-builder/files/vm-template-build-guest')
        inputs = self.root/'inputs'
        inputs.mkdir()
        (self.root/'etc/shadow').write_text('root::0:0:99999:7:::\n')
        for name in ('vm_router_state.py','router_personalize.py','router_service_probe.py','vm_router_image_test.py',
                     'router-fixture.json','service-finalize.py','smoke.py','retained_data.py','retained_data_test.py',
                     'vm_app_compatibility.py','static_site_test.py','vm_personalize.py','vm_personalize_test.py',
                     'personalization-config.json'):
            (inputs/name).write_text('# synthetic input\n')
        manifest = {'profile':'router-alpine-v3','packages':[{'name':'avahi','version':'1'}],
                    'world':['avahi'],'repositories':[],'engine_commit':'a'*40,'inputs_sha256':'b'*64}
        with patch.object(common,'ROOT',self.root),patch.object(common,'INPUT',inputs), \
                patch.dict(sys.modules,router_template_guest=self.guest), \
                patch.object(self.guest,'install_tailscale'),patch.object(self.guest,'verify_tailscale'), \
                patch.object(self.guest.os,'chown'):
            common.baseline(manifest)
        self.assertIn('messagebus:x:81:81:', (self.root/'etc/passwd').read_text())
        service = (self.root/'etc/init.d/avahi-daemon').read_text()
        self.assertIn('Starting the LAN Bonjour publisher', service)
        self.assertNotIn('need dbus', service)
