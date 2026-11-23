"""Resource ownership, cleanup scopes, grants and host limits."""
from platform_resource_test_support import ResourceTestCase, REPO_ROOT
import io
import unittest
from contextlib import redirect_stderr
import yaml
import platform_resource_compiler as compiler


class ResourceOwnershipTest(ResourceTestCase):
    def test_disabled_nextcloud_compiles_cleanup_scopes_for_deprovision(self):
        path = self.write_registry(
            {
                "schema_version": 1,
                "boxes": {
                    "boxa": {
                        "access": {
                            "available_capabilities": ["overlay", "edge-ingress"],
                            "enabled_capabilities": ["overlay", "edge-ingress"],
                        }
                    }
                },
                "apps": {
                    "nextcloud": {
                        "enabled": False,
                        "placement": {"active_master": "boxa", "passive_backup": "boxb"},
                        "resources": {"cloudflare-tunnel-egress": True},
                    }
                },
            }
        )
        compiled = compiler.compile_registry(path, ["nextcloud"], repo_root=REPO_ROOT)
        self.assert_no_raw_rule_arrays(compiled)
        self.assertEqual(compiled["apps"]["nextcloud"]["boxes"], ["boxa", "boxb"])
        self.assertEqual(
            compiled["app_resource_cleanup_scopes"],
            [
                {
                    "schema_version": 1,
                    "node": "boxa",
                    "app": "nextcloud",
                    "host_roles": ["router", "backend", "dmz", "iot"],
                    "reason": "disabled-app",
                },
                {
                    "schema_version": 1,
                    "node": "boxb",
                    "app": "nextcloud",
                    "host_roles": ["router", "backend", "dmz", "iot"],
                    "reason": "disabled-app",
                },
            ],
        )

    def test_disabled_legacy_app_without_manifest_compiles_cleanup_only(self):
        path = self.write_registry(
            {
                "schema_version": 1,
                "apps": {
                    "retained-legacy-app": {
                        "enabled": False,
                        "placement": {
                            "active_master": "boxa",
                            "passive_backup": "boxb",
                        },
                        "resources": {"retained-resource": True},
                        "cleanup": {"required": True},
                    }
                },
            }
        )

        compiled = compiler.compile_registry(path, [], repo_root=REPO_ROOT)

        self.assertNotIn("retained-legacy-app", compiled["manifest_paths"])
        self.assertEqual(
            compiled["apps"]["retained-legacy-app"],
            {
                "enabled": False,
                "runtime_state": "stopped",
                "boxes": ["boxa", "boxb"],
                "placement": {
                    "active_master": "boxa",
                    "passive_backup": "boxb",
                },
                "resources": [],
                "tailnet_resources": [],
                "isolation": "",
                "resource_flags": {"retained-resource": True},
                "controls": {},
                "users": [],
                "app_vms": [],
                "managed_iot_devices": [],
                "containers": [],
            },
        )
        self.assertEqual(len(compiled["app_resource_cleanup_scopes"]), 2)
        self.assertEqual(compiled["app_resource_claims"], [])
        self.assertEqual(compiled["app_resource_effective_files"], [])
        self.assertEqual(compiled["tailnet_resources"], [])
        self.assertEqual(compiled["tailnet_policy_resources"], [])
        self.assertEqual(compiled["app_vm_specs"], [])
        self.assertEqual(compiled["managed_iot_devices"], [])

    def test_optional_false_and_omission_are_the_same_disabled_state(self):
        compiled_results = []
        for resources in ({}, {"cloudflare-tunnel-egress": False}):
            path = self.write_registry(
                {
                    "schema_version": 1,
                    "apps": {
                        "nextcloud": {
                            "enabled": True,
                            "placement": {"active_master": "boxa", "passive_backup": "boxb"},
                            "resources": resources,
                        }
                    },
                }
            )
            compiled_results.append(compiler.compile_registry(path, ["nextcloud"], repo_root=REPO_ROOT))
        self.assertEqual(
            compiled_results[0]["apps"]["nextcloud"]["resources"],
            compiled_results[1]["apps"]["nextcloud"]["resources"],
        )
        self.assertNotIn(
            "app-nextcloud-cloudflare-tunnel-egress-tcp",
            self.claim_comments(compiled_results[0]),
        )

    def test_duplicate_shareable_claims_create_one_effective_resource_with_two_owners(self):
        path = self.write_registry(
            {
                "schema_version": 1,
                "apps": {
                    "nextcloud-v2": {
                        "enabled": True,
                        "placement": {"active_master": "boxa", "passive_backup": "boxb"},
                        "resources": {},
                    },
                    "nextcloud": {
                        "enabled": True,
                        "placement": {"active_master": "boxa", "passive_backup": "boxb"},
                        "resources": {},
                    },
                },
            }
        )
        compiled = compiler.compile_registry(path, [], repo_root=REPO_ROOT)
        shared = [
            item
            for item in compiled["app_resource_effective_files"]
            if item["node"] == "boxa"
            and item["kind"] == "router-forward"
            and item["owners"] == ["nextcloud", "nextcloud-v2"]
        ]
        self.assertEqual(len(shared), 1)
        self.assertIn("klokast-router-forward-", shared[0]["rendered_rule_identity"])

    def test_uninstalling_one_owner_keeps_shared_effective_resource(self):
        path = self.write_registry(
            {
                "schema_version": 1,
                "apps": {
                    "nextcloud-v2": {
                        "enabled": False,
                        "placement": {"active_master": "boxa", "passive_backup": "boxb"},
                        "resources": {},
                    },
                    "nextcloud": {
                        "enabled": True,
                        "placement": {"active_master": "boxa", "passive_backup": "boxb"},
                        "resources": {},
                    },
                },
            }
        )
        compiled = compiler.compile_registry(path, [], repo_root=REPO_ROOT)
        owners = [item["owners"] for item in compiled["app_resource_effective_files"]]
        self.assertIn(["nextcloud"], owners)
        self.assertNotIn(["nextcloud", "nextcloud-v2"], owners)

    def test_uninstalling_last_owner_removes_effective_resource(self):
        path = self.write_registry(
            {
                "schema_version": 1,
                "apps": {
                    "nextcloud-v2": {
                        "enabled": False,
                        "placement": {"active_master": "boxa", "passive_backup": "boxb"},
                        "resources": {},
                    }
                },
            }
        )
        compiled = compiler.compile_registry(path, [], repo_root=REPO_ROOT)
        self.assertEqual(compiled["app_resource_effective_files"], [])

    def test_exclusive_conflicts_fail_before_apply(self):
        rule = {
            "node": "boxa",
            "app": "app-a",
            "resource": "shared",
            "in_interface": "eth2",
            "out_interface": "eth3",
            "source": "192.168.200.10",
            "destination": "192.168.100.10",
            "protocol": "tcp",
            "ports": [8080],
            "exclusive": True,
            "comment": "app-a-shared",
        }
        other = dict(rule)
        other["app"] = "app-b"
        other["comment"] = "app-b-shared"
        with self.assertRaises(SystemExit):
            with redirect_stderr(io.StringIO()):
                compiler.build_app_resource_ledger([rule, other], [])

    def test_disabled_static_site_compiles_single_cleanup_scope(self):
        path = self.write_registry(
            {
                "schema_version": 1,
                "apps": {
                    "static-site": {
                        "enabled": False,
                        "placement": {"active_master": "boxa"},
                        "resources": {},
                    }
                },
            }
        )
        compiled = compiler.compile_registry(path, ["static-site"], repo_root=REPO_ROOT)
        self.assert_no_raw_rule_arrays(compiled)
        self.assertEqual(compiled["apps"]["static-site"]["boxes"], ["boxa"])
        self.assertEqual(
            compiled["app_resource_cleanup_scopes"],
            [
                {
                    "schema_version": 1,
                    "node": "boxa",
                    "app": "static-site",
                    "host_roles": ["router", "backend", "dmz", "iot"],
                    "reason": "disabled-app",
                }
            ],
        )
        self.assertEqual(
            ",".join(compiler.resource_hosts_for_scope(compiled)),
            "boxa-router,boxa-bak,boxa-dmz,boxa-iot",
        )

    def test_resource_host_limit_targets_only_roles_with_rules(self):
        compiled = self.compiled_for_run()
        vm_rules = [
            {
                "node": "boxa",
                "app": "test",
                "resource": "backend",
                "target_role": "backend",
                "interface": "eth0",
                "source": "192.168.200.10",
                "destination": "192.168.100.10",
                "ports": [2283],
                "comment": "app-test-backend-vm-input",
            },
            {
                "node": "boxb",
                "app": "test",
                "resource": "dmz",
                "target_role": "dmz",
                "interface": "eth0",
                "source": "192.168.100.10",
                "destination": "192.168.200.10",
                "ports": [8080],
                "comment": "app-test-dmz-vm-input",
            },
        ]
        ledger = compiler.build_app_resource_ledger([self.run_router_rule()], vm_rules)
        compiled["app_resource_claims"] = ledger["claims"]
        compiled["app_resource_effective_files"] = ledger["effective_files"]
        self.assertEqual(
            ",".join(compiler.resource_hosts_for_scope(compiled)),
            "boxa-router,boxa-bak,boxb-dmz",
        )

    def test_immich_grant_exports_only_app_approved_state(self):
        path = self.write_registry(
            {
                "schema_version": 1,
                "apps": {
                    "nextcloud": {
                        "enabled": True,
                        "placement": {"active_master": "boxa", "passive_backup": "boxb"},
                    },
                    "immich": {
                        "enabled": True,
                        "placement": {"active_master": "boxa", "passive_backup": "boxb"},
                        "resources": {},
                    },
                },
            }
        )
        compiled = compiler.compile_registry(path, ["immich"], repo_root=REPO_ROOT)
        grant = compiler.build_app_grant(compiled, "immich", "abc123")
        self.assertEqual(grant["kind"], "platform-resource-grant")
        self.assertEqual(grant["app"], "immich")
        self.assertTrue(grant["enabled"])
        self.assertEqual(grant["approved_commit"], "abc123")
        self.assertEqual(grant["boxes"], ["boxa", "boxb"])
        self.assertEqual(grant["placement"]["active_master"], "boxa")
        self.assertEqual(grant["resources"], ["backend-http-upstream"])
        self.assertEqual(grant["tailnet_resources"][0]["tag"], "tag:immich")
        self.assertIn("app_resource_effective_files", grant)
        self.assertGreater(len(grant["app_resource_effective_files"]), 0)
        self.assertNotIn("content", grant["app_resource_effective_files"][0])
        self.assertNotIn("apps", grant)
        self.assertNotIn("registry_path", grant)
        self.assertNotIn("manifest_paths", grant)
        self.assertNotIn("users", grant)
        self.assertNotIn("app_resources_router_forward_rules", grant)
        self.assertNotIn("app_resources_vm_input_tcp_rules", grant)
        self.assertNotIn("app_resources_absent_comment_prefixes", grant)
        serialized = yaml.safe_dump(grant)
        self.assertNotIn("nextcloud", serialized)

    def test_disabled_immich_compiles_cleanup_scopes_for_deprovision(self):
        path = self.write_registry(
            {
                "schema_version": 1,
                "apps": {
                    "immich": {
                        "enabled": False,
                        "placement": {"active_master": "boxa", "passive_backup": "boxb"},
                    }
                },
            }
        )
        compiled = compiler.compile_registry(path, ["immich"], repo_root=REPO_ROOT)
        self.assert_no_raw_rule_arrays(compiled)
        self.assertEqual(compiled["apps"]["immich"]["boxes"], ["boxa", "boxb"])
        self.assertEqual(
            compiled["app_resource_cleanup_scopes"],
            [
                {
                    "schema_version": 1,
                    "node": "boxa",
                    "app": "immich",
                    "host_roles": ["router", "backend", "dmz", "iot"],
                    "reason": "disabled-app",
                },
                {
                    "schema_version": 1,
                    "node": "boxb",
                    "app": "immich",
                    "host_roles": ["router", "backend", "dmz", "iot"],
                    "reason": "disabled-app",
                },
            ],
        )

    def test_app_vm_limit_includes_backend_builder_host(self):
        specs = [{"inventory_hostname": "boxa-usr-alice"}]
        self.assertEqual(
            compiler.limit_for_app_vms(["boxa"], specs),
            "boxa-bak,boxa-dom0,boxa-usr-alice",
        )


if __name__ == "__main__":
    unittest.main()
