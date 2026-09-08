import copy
import importlib.util
from pathlib import Path
import unittest


SPEC = importlib.util.spec_from_file_location("platform_inventory", Path(__file__).resolve().parents[1] / "lib/platform_inventory.py")
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


class InventoryComparisonTests(unittest.TestCase):
    def graph(self):
        return {"all": {"children": ["ops"]}, "ops": {"hosts": ["boxa-ops"]},
                "_meta": {"hostvars": {"boxa-ops": {"ansible_host": "boxa-ops.example.ts.net", "ops_airunner_enabled": True}}}}

    def test_equal_inventory_excludes_only_unused_legacy_hosts(self):
        effective = self.graph()
        old = copy.deepcopy(effective)
        old["ops"]["hosts"].append("old-example-ops")
        old["_meta"]["hostvars"]["old-example-ops"] = {"ansible_host": "example.invalid"}
        old["_meta"]["hostvars"]["boxa-ops"]["inventory_file"] = "/legacy/file"
        result = MOD.compare_inventories(old, effective, {"inventory": effective})
        self.assertEqual(result["excluded_legacy_hosts"], ["old-example-ops"])
        self.assertEqual(result["old_inventory_sha256"], result["effective_inventory_sha256"])

    def test_ansible_omits_empty_group_objects_but_sealed_graph_must_be_complete(self):
        graph = self.graph()
        omitted = copy.deepcopy(graph)
        omitted["all"]["children"] += ["ungrouped", "usr"]
        self.assertEqual(MOD.normalized_inventory(graph, ["boxa-ops"]), MOD.normalized_inventory(omitted, ["boxa-ops"]))
        with self.assertRaises(ValueError): MOD.static_inventory(omitted)

    def test_refuses_connection_runner_membership_and_host_changes(self):
        for change in ("address", "runner", "role", "missing", "extra"):
            with self.subTest(change=change):
                old = self.graph()
                effective = copy.deepcopy(old)
                if change == "address": effective["_meta"]["hostvars"]["boxa-ops"]["ansible_host"] = "other.example.ts.net"
                elif change == "runner": effective["_meta"]["hostvars"]["boxa-ops"]["ops_airunner_enabled"] = False
                elif change == "role": effective["ops"]["hosts"] = []
                elif change == "missing": effective["_meta"]["hostvars"] = {}
                else: effective["_meta"]["hostvars"]["other-ops"] = {}
                with self.assertRaises(ValueError): MOD.compare_inventories(old, effective, {"inventory": old})

    def test_static_graph_has_full_variables_and_rejects_bad_graphs(self):
        graph = self.graph()
        result = MOD.static_inventory(graph)
        self.assertEqual(result["all"]["children"]["ops"]["hosts"], graph["_meta"]["hostvars"])
        for change in ("cycle", "unknown", "unassigned"):
            bad = copy.deepcopy(graph)
            if change == "cycle": bad["ops"]["children"] = ["all"]
            elif change == "unknown": bad["all"]["children"].append("missing")
            else: bad["_meta"]["hostvars"]["other"] = {}
            with self.assertRaises(ValueError): MOD.static_inventory(bad)


if __name__ == "__main__": unittest.main()
