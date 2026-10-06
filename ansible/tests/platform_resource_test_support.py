#!/usr/bin/env python3
import importlib.util
import sys
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest.mock import patch

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "ansible/lib"))
import platform_resource_model as model
import platform_resource_compiler as compiler
import platform_resource_runtime as runtime

SCRIPT = REPO_ROOT / "ansible" / "bin" / "platform-resources"
RECONCILE_SCRIPT = (
    REPO_ROOT
    / "ansible"
    / "roles"
    / "app-resources"
    / "files"
    / "reconcile-app-resources.py"
)


def load_module():
    loader = SourceFileLoader("platform_resources", str(SCRIPT))
    spec = importlib.util.spec_from_loader("platform_resources", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def load_reconcile_module():
    loader = SourceFileLoader("reconcile_app_resources", str(RECONCILE_SCRIPT))
    spec = importlib.util.spec_from_loader("reconcile_app_resources", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class ResourceTestCase(unittest.TestCase):
    def setUp(self):
        self.cli = load_module()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        runtime_root = patch.object(runtime, "RUN_ROOT", Path(temporary.name))
        runtime_root.start()
        self.addCleanup(runtime_root.stop)

    def per_user_app_users(self):
        return [
            {
                "slug": "alice",
                "tailscale_login": "alice@example.com",
                "system_user": "alice",
                "vm_ipv4_address": "192.168.175.20",
            },
            {
                "slug": "bob",
                "tailscale_login": "bob@example.com",
                "system_user": "bob",
                "vm_ipv4_address": "192.168.175.21",
            },
        ]

    def per_user_app_manifest(self, zone="usr"):
        return {
            "schema_version": 1,
            "app": "user-shell",
            "default_isolation": "per_user_pvh_vm",
            "_manifest_path": "test://user-shell/platform-resources.yml",
            "resources": {
                "compute": [
                    {
                        "id": "runtime",
                        "type": "per_user_app_vm",
                        "zone": zone,
                        "tailnet_tag_prefix": "user-shell",
                    }
                ],
                "network": [
                    {
                        "id": "web-egress",
                        "type": "wan_egress",
                        "required": True,
                        "from_zone": zone,
                        "tcp_ports": [443],
                        "udp_ports": [41641],
                    }
                ],
                "tailnet": [
                    {
                        "id": "private-ingress",
                        "required": True,
                        "hostname_default": "user-shell",
                        "tag_default": "tag:user-shell",
                        "grants": [
                            {
                                "src": "exact_user_login",
                                "tcp_ports": [22],
                            }
                        ],
                    }
                ],
            },
        }

    def write_registry(self, data):
        handle = tempfile.NamedTemporaryFile("w", delete=False, suffix=".yml")
        with handle:
            yaml.safe_dump(data, handle, sort_keys=False)
        return Path(handle.name)

    def claim_comments(self, compiled):
        return {claim["claim_comment"] for claim in compiled["app_resource_claims"]}

    def claims_for_comment(self, compiled, comment):
        return [
            claim
            for claim in compiled["app_resource_claims"]
            if claim["claim_comment"] == comment
        ]

    def assert_no_raw_rule_arrays(self, compiled):
        self.assertNotIn("app_resources_router_forward_rules", compiled)
        self.assertNotIn("app_resources_vm_input_tcp_rules", compiled)
        self.assertNotIn("app_resources_absent_comment_prefixes", compiled)

    def run_router_rule(self):
        return {
            "node": "boxa",
            "app": "test",
            "resource": "backend-http-upstream",
            "comment": "app-test-router",
            "in_interface": "eth2",
            "out_interface": "eth3",
            "source": "192.168.200.10",
            "destination": "192.168.100.10",
            "protocol": "tcp",
            "ports": [2283],
        }

    def compiled_for_run(self):
        router_rules = [self.run_router_rule()]
        ledger = compiler.build_app_resource_ledger(router_rules, [])
        return {
            "boxes": ["boxa", "boxb"],
            "app_vm_specs": [],
            "apps": {"test": {"boxes": ["boxa"]}},
            "app_resource_claims": ledger["claims"],
            "app_resource_effective_files": ledger["effective_files"],
            "app_resource_cleanup_scopes": [],
            "registry_sha256": "sha256-test",
        }

    def compiled_with_podman_resource_for_run(self):
        compiled = self.compiled_for_run()
        vm_rules = [
            {
                "node": "boxa",
                "app": "test",
                "resource": "backend",
                "target_role": "backend",
                "host_role": "backend",
                "interface": "eth0",
                "source": "192.168.200.10",
                "destination": "192.168.100.10",
                "ports": [2283],
                "comment": "app-test-backend-vm-input",
            }
        ]
        ledger = compiler.build_app_resource_ledger([self.run_router_rule()], vm_rules)
        compiled["app_resource_claims"] = ledger["claims"]
        compiled["app_resource_effective_files"] = ledger["effective_files"]
        return compiled

    def desired_for_rules(self, router_rules):
        ledger = compiler.build_app_resource_ledger(router_rules, [])
        return {
            "schema_version": 1,
            "compiler": "platform-resources",
            "compiler_version": model.COMPILER_VERSION,
            "registry_sha256": "test",
            "app_resource_effective_files": ledger["effective_files"],
        }

    def configure_reconciler_root(self, reconciler, root):
        reconciler.APP_RESOURCE_ROOT = root
        reconciler.KIND_DIRS = {
            "router-forward": root / "router-forward.d",
            "vm-input": root / "vm-input.d",
        }

