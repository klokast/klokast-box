import contextlib
import io
import json
import unittest
from unittest.mock import patch
from test_development_source import wrapper


class PlatformInventoryTest(unittest.TestCase):
    def test_inventory_reads_only_validated_instance(self):
        module=wrapper('platform-inventory')
        result={'valid':True,'kind':'klokast.inventory.v1','projection':{'inventory':{'_meta':{'hostvars':{'site-a-dom0':{'ansible_user':'neo'}}}}}}
        output=io.StringIO()
        with patch.object(module.source,'require_controller'),patch.object(module.source,'as_controller',return_value=json.dumps(result)),contextlib.redirect_stdout(output):
            self.assertEqual(module.main(['--list']),0)
        self.assertEqual(json.loads(output.getvalue()),result['projection']['inventory'])

    def test_invalid_projection_has_no_legacy_fallback(self):
        module=wrapper('platform-inventory')
        with patch.object(module.source,'require_controller'),patch.object(module.source,'as_controller',return_value='{"valid":false}'),contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(module.main(['--list']),1)
