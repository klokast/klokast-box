import copy
import importlib.util
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path


class RegistryCandidateTest(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).resolve().parents[2] / "klokast-dev/bin/prepare-registry-source-candidate"
        loader = SourceFileLoader("registry_candidate_test", str(path))
        spec = importlib.util.spec_from_loader(loader.name,loader)
        self.m = importlib.util.module_from_spec(spec)
        loader.exec_module(self.m)
        self.instance = {"boxes":{"boxa":{},"boxb":{}},"apps":{"music":{"desired-state":"absent","data":{"library":{"box":"boxb","retention":"preserve"}}}}}
        self.legacy = {"schema_version":1,"boxes":{"boxa":{"access":{},"dom0_bridge_ports":{"lan":["eth2"]}},"boxb":{"access":{}}},"apps":{"unavailable":{"enabled":False,"placement":{"builder_box":""},"resources":{"saved":False},"ephemeral":{"expires_at":"","privileged_approval":False}}}}

    def test_conversion_preserves_absent_data_empty_targets_and_defaults(self):
        before = copy.deepcopy(self.instance)
        candidate = self.m.convert(self.instance,self.legacy)
        self.assertEqual(candidate["apps"],self.instance["apps"])
        self.assertEqual(self.instance,before)
        self.assertEqual(candidate["boxes"]["boxb"]["substrate"],{})
        app = candidate["inactive-apps"]["unavailable"]
        self.assertEqual(app["placement"],{"builder":""})
        self.assertEqual(app["resources"],{"saved":False})
        self.assertEqual(app["ephemeral"],{"expires-at":"","privileged-approval":False})
        self.assertEqual(self.m.convert(candidate,self.legacy),candidate)

    def test_unknown_fields_enabled_apps_and_conflicting_instance_values_refuse(self):
        for change in (
            lambda old: old.update(extra={}),
            lambda old: old["boxes"]["boxa"].update(unknown={}),
            lambda old: old["apps"]["unavailable"].update(command="id"),
            lambda old: old["apps"]["unavailable"].update(enabled=True),
            lambda old: old["apps"]["unavailable"].update(enabled=0),
        ):
            old = copy.deepcopy(self.legacy)
            change(old)
            with self.assertRaises(ValueError): self.m.convert(self.instance,old)
        candidate = self.m.convert(self.instance,self.legacy)
        candidate["inactive-apps"]["unavailable"]["placement"]["builder"] = "boxb"
        with self.assertRaises(ValueError): self.m.convert(candidate,self.legacy)


if __name__ == "__main__": unittest.main()
