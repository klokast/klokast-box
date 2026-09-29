import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import router_initial_contact as contact


class RouterInitialContactTests(unittest.TestCase):
    def test_backend_address_is_a_single_ipv4_host(self):
        self.assertEqual(
            contact.first_contact_interfaces('192.0.2.8', 24),
            'auto lo\niface lo inet loopback\n\n'
            'auto eth0\niface eth0 inet dhcp\n\n'
            'auto eth3\niface eth3 inet static\n'
            '    address 192.0.2.8/24\n',
        )

    def test_backend_rejects_invalid_or_unbounded_prefix(self):
        for address, prefix in (('192.0.2.8', 0), ('192.0.2.8', 33),
                                ('192.0.2.0/24', 24), ('not-an-address', 24)):
            with self.subTest(address=address, prefix=prefix):
                with self.assertRaises(ValueError):
                    contact.first_contact_interfaces(address, prefix)

    def test_public_key_parser_rejects_malformed_key_material(self):
        for key in ('ssh-ed25519 YQ==', 'ssh-ed25519 !!!! comment',
                    'ssh-rsa YQ== comment\nssh-ed25519 YQ=='):
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    contact._validate_public_key(key)


if __name__ == '__main__':
    unittest.main()
