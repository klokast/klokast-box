"""Exercise the wrapper's LAN context without reaching Platform hosts."""
import argparse
from pathlib import Path
import runpy
import unittest
from unittest.mock import Mock, patch

module = runpy.run_path(str(Path(__file__).resolve().parents[2] / 'apps/print-server/bin/print-serverctl'))


class PrintContextTest(unittest.TestCase):
    def test_context_uses_selected_box_lan_binding(self):
        context = module['command_context']
        service = {'node': 'boxa', 'subnet': '192.168.1.0/24', 'queue': 'epson',
                   'backend': '192.168.100.10', 'port': 18631}
        for services, expected in [([], None), ([dict(service, node='boxb'), service], service)]:
            with self.subTest(services=services):
                write = Mock(return_value=Path('/unused/vars.yml'))
                load = Mock(return_value=({'lan_print_services': services}, {}, {}))
                args = argparse.Namespace(box='boxa', resources_registry='/unused', queue_name='epson', printer_uri='')
                with patch.dict(context.__globals__, load_resources=load, render_box_inventory=Mock(), write_vars=write):
                    directory, _, _ = context(args)
                    directory.cleanup()
                self.assertEqual(write.call_args.args[-1], expected)
