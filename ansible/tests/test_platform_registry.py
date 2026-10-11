import contextlib
import io
import json
import unittest
from unittest.mock import patch
from test_development_source import wrapper


class PlatformRegistryTest(unittest.TestCase):
    def test_registry_alias_reads_only_instance_projection(self):
        module=wrapper('platform-registry')
        view={'rendered':{'projection':{'registry':{'schema_version':1,'boxes':{},'apps':{}},'registry_sha256':'a'*64}}}
        output=io.StringIO()
        with patch.object(module.source,'snapshot',return_value=view),contextlib.redirect_stdout(output):
            self.assertEqual(module.main(['read','--json']),0)
        result=json.loads(output.getvalue())
        self.assertEqual(result['source'],'instance')
        self.assertEqual(result['registry_path'],str(module.INSTANCE))

    def test_arbitrary_registry_override_is_refused_before_source_read(self):
        module=wrapper('platform-registry')
        with patch.object(module.source,'snapshot') as read,contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(module.main(['read','--registry','/tmp/other.yml']),1)
            read.assert_not_called()

    def test_legacy_write_command_is_not_an_interface(self):
        module=wrapper('platform-registry')
        with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
            module.main(['assert-writable'])

    def test_resource_consumer_accepts_registry_output_with_installed_metadata(self):
        from types import SimpleNamespace
        module=wrapper('platform-registry')
        consumer=wrapper('platform-resources')
        view={'rendered':{'projection':{'registry':{'schema_version':1,'boxes':{},'apps':{}},'registry_sha256':'a'*64}}}
        output=io.StringIO()
        with patch.object(module.source,'snapshot',return_value=view),contextlib.redirect_stdout(output):
            self.assertEqual(module.main(['read','--json']),0)
        with patch.object(consumer.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=output.getvalue())):
            selected=consumer.registry_source_input(SimpleNamespace(registry=module.REGISTRY))
        self.assertEqual(selected['installed_routing_profiles'],{})
        self.assertEqual(selected['installed_air_bootstrap'],{})
