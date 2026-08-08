#!/usr/bin/env python3
import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest.mock import patch

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
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


class PlatformResourcesTest(unittest.TestCase):
    def setUp(self):
        self.mod = load_module()

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
            "node": "k001",
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
        ledger = self.mod.build_app_resource_ledger(router_rules, [])
        return {
            "boxes": ["k001", "k002"],
            "app_vm_specs": [],
            "apps": {"test": {"boxes": ["k001"]}},
            "app_resource_claims": ledger["claims"],
            "app_resource_effective_files": ledger["effective_files"],
            "app_resource_cleanup_scopes": [],
            "registry_sha256": "sha256-test",
        }

    def compiled_with_podman_resource_for_run(self):
        compiled = self.compiled_for_run()
        vm_rules = [
            {
                "node": "k001",
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
        ledger = self.mod.build_app_resource_ledger([self.run_router_rule()], vm_rules)
        compiled["app_resource_claims"] = ledger["claims"]
        compiled["app_resource_effective_files"] = ledger["effective_files"]
        return compiled

    def test_nextcloud_compiles_required_and_optional_resources(self):
        path = self.write_registry(
            {
                "schema_version": 1,
                "apps": {
                    "nextcloud": {
                        "enabled": True,
                        "placement": {"active_master": "k001", "passive_backup": "k002"},
                        "resources": {"cloudflare-tunnel-egress": True},
                    }
                },
            }
        )
        compiled = self.mod.compile_registry(path, ["nextcloud"])
        self.assert_no_raw_rule_arrays(compiled)
        comments = self.claim_comments(compiled)
        self.assertIn("app-nextcloud-backend-http-upstream-router", comments)
        self.assertIn("app-nextcloud-cloudflare-tunnel-egress-tcp", comments)
        self.assertIn("app-nextcloud-cloudflare-tunnel-egress-udp", comments)
        self.assertEqual(compiled["apps"]["nextcloud"]["boxes"], ["k001", "k002"])
        upstream_router_claims = self.claims_for_comment(
            compiled, "app-nextcloud-backend-http-upstream-router"
        )
        self.assertEqual(len(upstream_router_claims), 2)
        self.assertEqual(upstream_router_claims[0]["normalized"]["in_interface"], "eth2")
        self.assertEqual(upstream_router_claims[0]["normalized"]["out_interface"], "eth3")
        self.assertEqual(upstream_router_claims[0]["normalized"]["source"], "192.168.200.10")
        self.assertEqual(upstream_router_claims[0]["normalized"]["destination"], "192.168.100.10")
        self.assertEqual(upstream_router_claims[0]["normalized"]["ports"], [8080])
        upstream_vm_claims = self.claims_for_comment(
            compiled, "app-nextcloud-backend-http-upstream-vm-input"
        )
        self.assertEqual(len(upstream_vm_claims), 2)
        self.assertEqual(upstream_vm_claims[0]["host_role"], "backend")
        self.assertEqual(upstream_vm_claims[0]["normalized"]["interface"], "eth0")

    def test_disabled_nextcloud_compiles_cleanup_scopes_for_deprovision(self):
        path = self.write_registry(
            {
                "schema_version": 1,
                "apps": {
                    "nextcloud": {
                        "enabled": False,
                        "placement": {"active_master": "k001", "passive_backup": "k002"},
                        "resources": {"cloudflare-tunnel-egress": True},
                    }
                },
            }
        )
        compiled = self.mod.compile_registry(path, ["nextcloud"])
        self.assert_no_raw_rule_arrays(compiled)
        self.assertEqual(compiled["apps"]["nextcloud"]["boxes"], ["k001", "k002"])
        self.assertEqual(
            compiled["app_resource_cleanup_scopes"],
            [
                {
                    "schema_version": 1,
                    "node": "k001",
                    "app": "nextcloud",
                    "host_roles": ["router", "backend", "dmz", "iot"],
                    "reason": "disabled-app",
                },
                {
                    "schema_version": 1,
                    "node": "k002",
                    "app": "nextcloud",
                    "host_roles": ["router", "backend", "dmz", "iot"],
                    "reason": "disabled-app",
                },
            ],
        )

    def test_shared_guest_runtime_state_compiles_for_platform_map(self):
        path = self.write_registry(
            {
                "schema_version": 1,
                "apps": {},
                "boxes": {
                    "k001": {
                        "shared_guests": {"iot": {"runtime_state": "stopped"}}
                    }
                },
            }
        )
        compiled = self.mod.compile_registry(path, [])

        self.assertEqual(
            compiled["box_configs"]["k001"]["shared_guests"],
            {
                "bak": {"runtime_state": "running"},
                "dmz": {"runtime_state": "running"},
                "iot": {"runtime_state": "stopped"},
            },
        )
        mapped = {
            item["role"]: item
            for item in compiled["platform_map"]["shared_guests"]
            if item["node"] == "k001"
        }
        self.assertEqual(mapped["iot"]["runtime_state"], "stopped")
        self.assertFalse(mapped["iot"]["autostart"])

    def test_shared_guest_registry_rejects_unknown_role_and_state(self):
        for shared_guests, expected in (
            ({"router": {"runtime_state": "stopped"}}, "unsupported role"),
            ({"iot": {"runtime_state": "paused"}}, "must be one of"),
            ({"iot": {"runtime_state": "stopped", "extra": True}}, "unsupported field"),
        ):
            with self.subTest(shared_guests=shared_guests):
                path = self.write_registry(
                    {
                        "schema_version": 1,
                        "apps": {},
                        "boxes": {"k001": {"shared_guests": shared_guests}},
                    }
                )
                with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()) as stderr:
                    self.mod.compile_registry(path, [])
                self.assertIn(expected, stderr.getvalue())

    def test_running_app_cannot_target_stopped_shared_zone(self):
        path = self.write_registry(
            {
                "schema_version": 1,
                "apps": {
                    "nextcloud-v2": {
                        "enabled": True,
                        "placement": {
                            "active_master": "k001",
                            "passive_backup": "k002",
                        },
                    }
                },
                "boxes": {
                    "k001": {
                        "shared_guests": {"bak": {"runtime_state": "stopped"}}
                    }
                },
            }
        )
        with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()) as stderr:
            self.mod.compile_registry(path, [])
        self.assertIn("apps.nextcloud-v2 is running on k001", stderr.getvalue())

    def test_box_config_compile_tolerates_missing_manifest_for_stopped_app(self):
        path = self.write_registry(
            {
                "schema_version": 1,
                "apps": {
                    "private-missing-app": {
                        "enabled": True,
                        "runtime_state": "stopped",
                    }
                },
                "boxes": {
                    "k001": {
                        "shared_guests": {"iot": {"runtime_state": "stopped"}}
                    }
                },
            }
        )

        compiled = self.mod.compile_box_registry_plan(path)
        self.assertEqual(
            compiled["box_configs"]["k001"]["shared_guests"]["iot"][
                "runtime_state"
            ],
            "stopped",
        )

    def test_duplicate_shareable_claims_create_one_effective_resource_with_two_owners(self):
        path = self.write_registry(
            {
                "schema_version": 1,
                "apps": {
                    "nextcloud-v2": {
                        "enabled": True,
                        "placement": {"active_master": "k001", "passive_backup": "k002"},
                        "resources": {},
                    },
                    "nextcloud": {
                        "enabled": True,
                        "placement": {"active_master": "k001", "passive_backup": "k002"},
                        "resources": {},
                    },
                },
            }
        )
        compiled = self.mod.compile_registry(path, [])
        shared = [
            item
            for item in compiled["app_resource_effective_files"]
            if item["node"] == "k001"
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
                        "placement": {"active_master": "k001", "passive_backup": "k002"},
                        "resources": {},
                    },
                    "nextcloud": {
                        "enabled": True,
                        "placement": {"active_master": "k001", "passive_backup": "k002"},
                        "resources": {},
                    },
                },
            }
        )
        compiled = self.mod.compile_registry(path, [])
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
                        "placement": {"active_master": "k001", "passive_backup": "k002"},
                        "resources": {},
                    }
                },
            }
        )
        compiled = self.mod.compile_registry(path, [])
        self.assertEqual(compiled["app_resource_effective_files"], [])

    def test_exclusive_conflicts_fail_before_apply(self):
        rule = {
            "node": "k001",
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
                self.mod.build_app_resource_ledger([rule, other], [])

    def test_immich_compiles_private_ingress_resources(self):
        path = self.write_registry(
            {
                "schema_version": 1,
                "apps": {
                    "immich": {
                        "enabled": True,
                        "placement": {"active_master": "k001", "passive_backup": "k002"},
                        "resources": {},
                    }
                },
            }
        )
        compiled = self.mod.compile_registry(path, ["immich"])
        self.assert_no_raw_rule_arrays(compiled)
        comments = self.claim_comments(compiled)
        self.assertIn("app-immich-backend-http-upstream-router", comments)
        self.assertEqual(compiled["apps"]["immich"]["boxes"], ["k001", "k002"])
        upstream_router_claims = self.claims_for_comment(
            compiled, "app-immich-backend-http-upstream-router"
        )
        self.assertEqual(len(upstream_router_claims), 2)
        self.assertEqual(upstream_router_claims[0]["normalized"]["in_interface"], "eth2")
        self.assertEqual(upstream_router_claims[0]["normalized"]["out_interface"], "eth3")
        self.assertEqual(upstream_router_claims[0]["normalized"]["source"], "192.168.200.10")
        self.assertEqual(upstream_router_claims[0]["normalized"]["destination"], "192.168.100.10")
        self.assertEqual(upstream_router_claims[0]["normalized"]["ports"], [2283])
        upstream_vm_claims = self.claims_for_comment(
            compiled, "app-immich-backend-http-upstream-vm-input"
        )
        self.assertEqual(len(upstream_vm_claims), 2)
        self.assertEqual(upstream_vm_claims[0]["host_role"], "backend")
        self.assertEqual(upstream_vm_claims[0]["normalized"]["ports"], [2283])
        tailnet_resources = compiled["tailnet_resources"]
        self.assertEqual(tailnet_resources[0]["hostname"], "photos")
        self.assertEqual(tailnet_resources[0]["tag"], "tag:immich")

    def test_static_site_compiles_single_box_resources(self):
        path = self.write_registry(
            {
                "schema_version": 1,
                "apps": {
                    "static-site": {
                        "enabled": True,
                        "placement": {"active_master": "k001"},
                        "resources": {},
                    }
                },
            }
        )
        compiled = self.mod.compile_registry(path, ["static-site"])
        self.assertEqual(compiled["apps"]["static-site"]["boxes"], ["k001"])
        self.assertEqual(
            compiled["apps"]["static-site"]["resources"],
            ["github-ssh-egress", "cloudflare-tunnel-egress"],
        )
        self.assert_no_raw_rule_arrays(compiled)
        comments = self.claim_comments(compiled)
        self.assertIn("app-static-site-github-ssh-egress-tcp", comments)
        self.assertIn("app-static-site-cloudflare-tunnel-egress-tcp", comments)
        self.assertIn("app-static-site-cloudflare-tunnel-egress-udp", comments)
        github_egress_claims = self.claims_for_comment(
            compiled, "app-static-site-github-ssh-egress-tcp"
        )
        self.assertEqual(len(github_egress_claims), 1)
        self.assertEqual(github_egress_claims[0]["normalized"]["in_interface"], "eth2")
        self.assertEqual(github_egress_claims[0]["normalized"]["out_interface"], "eth0")
        self.assertEqual(github_egress_claims[0]["normalized"]["source"], "192.168.200.10")
        self.assertEqual(github_egress_claims[0]["normalized"]["destination"], "")
        self.assertEqual(github_egress_claims[0]["normalized"]["ports"], [443])
        self.assertEqual(self.mod.limit_for_resource_hosts(compiled), "k001-router")

    def test_music_compiles_managed_iot_device_resources(self):
        path = self.write_registry(
            {
                "schema_version": 1,
                "boxes": {
                    "k002": {
                        "dom0_bridge_ports": {
                            "iot": ["eth3"],
                        },
                    },
                },
                "apps": {
                    "music": {
                        "enabled": True,
                        "placement": {"boxes": ["k001", "k002"]},
                        "devices": {
                            "local-audio-endpoint": {
                                "k001": {
                                    "mac": "b8:27:eb:00:00:01",
                                    "ipv4_address": "192.168.150.60",
                                    "hostname": "k001-streamer",
                                },
                                "k002": {
                                    "mac": "b8:27:eb:00:00:02",
                                    "ipv4_address": "192.168.150.60",
                                    "hostname": "k002-streamer",
                                },
                            }
                        },
                    }
                },
            }
        )
        compiled = self.mod.compile_registry(path, ["music"])
        self.assertEqual(
            compiled["box_configs"]["k002"]["dom0_bridge_ports"],
            {"iot": ["eth3"]},
        )
        self.assertEqual(compiled["apps"]["music"]["boxes"], ["k001", "k002"])
        self.assertEqual(
            compiled["apps"]["music"]["resources"],
            ["snapcast-stream", "audio-endpoint-updates"],
        )
        self.assertEqual(len(compiled["managed_iot_devices"]), 2)
        self.assertEqual(compiled["managed_iot_devices"][0]["hostname"], "k001-streamer")
        self.assertEqual(compiled["managed_iot_devices"][0]["ipv4_address"], "192.168.150.60")
        self.assertEqual(compiled["managed_iot_devices"][0]["tailnet_tag"], "tag:streamer")
        self.assertEqual(compiled["managed_iot_devices"][1]["ipv4_address"], "192.168.150.60")
        self.assertEqual(compiled["managed_iot_devices"][1]["tailnet_tag"], "tag:streamer")
        self.assertEqual(
            self.mod.router_managed_dhcp_hosts(compiled),
            [
                {
                    "node": "k001",
                    "name": "k001-streamer",
                    "mac": "b8:27:eb:00:00:01",
                    "address": "192.168.150.60",
                    "app": "music",
                    "resource": "local-audio-endpoint",
                },
                {
                    "node": "k002",
                    "name": "k002-streamer",
                    "mac": "b8:27:eb:00:00:02",
                    "address": "192.168.150.60",
                    "app": "music",
                    "resource": "local-audio-endpoint",
                },
            ],
        )
        snap_router_claims = self.claims_for_comment(
            compiled, "app-music-snapcast-stream-router"
        )
        self.assertEqual(len(snap_router_claims), 2)
        self.assertEqual(snap_router_claims[0]["normalized"]["in_interface"], "eth4")
        self.assertEqual(snap_router_claims[0]["normalized"]["out_interface"], "eth3")
        self.assertEqual(snap_router_claims[0]["normalized"]["source"], "192.168.150.60")
        self.assertEqual(snap_router_claims[0]["normalized"]["destination"], "192.168.100.10")
        self.assertEqual(snap_router_claims[0]["normalized"]["ports"], [1704])
        snap_vm_claims = self.claims_for_comment(
            compiled, "app-music-snapcast-stream-vm-input"
        )
        self.assertEqual(len(snap_vm_claims), 2)
        self.assertEqual(snap_vm_claims[0]["host_role"], "backend")
        update_tcp_claims = self.claims_for_comment(
            compiled, "app-music-audio-endpoint-updates-tcp"
        )
        update_udp_claims = self.claims_for_comment(
            compiled, "app-music-audio-endpoint-updates-udp"
        )
        self.assertEqual(len(update_tcp_claims), 2)
        self.assertEqual(update_tcp_claims[0]["normalized"]["ports"], [80, 443])
        self.assertEqual(update_udp_claims[0]["normalized"]["ports"], [123, 41641])
        tailnet_resources = compiled["tailnet_resources"]
        self.assertEqual(
            [item["hostname"] for item in tailnet_resources],
            [
                "k001-music",
                "k002-music",
                "k001-music-upload",
                "k002-music-upload",
            ],
        )
        self.assertEqual(
            [item["tag"] for item in tailnet_resources],
            ["tag:music", "tag:music", "tag:music-upload", "tag:music-upload"],
        )

    def test_box_dhcp_reservation_compiles_without_apps(self):
        path = self.write_registry(
            {
                "schema_version": 1,
                "boxes": {
                    "k001": {
                        "dhcp_reservations": [
                            {
                                "hostname": "flint2",
                                "mac": "02:00:00:00:00:01",
                                "ipv4_address": "10.10.30.2",
                            }
                        ],
                    }
                },
            }
        )
        compiled = self.mod.compile_registry(path, [])
        self.assertEqual(compiled["boxes"], [])
        self.assertEqual(
            compiled["box_configs"]["k001"]["dhcp_reservations"],
            [
                {
                    "hostname": "flint2",
                    "mac": "02:00:00:00:00:01",
                    "ipv4_address": "10.10.30.2",
                }
            ],
        )
        self.assertEqual(
            self.mod.router_managed_dhcp_hosts(compiled),
            [
                {
                    "node": "k001",
                    "name": "flint2",
                    "mac": "02:00:00:00:00:01",
                    "address": "10.10.30.2",
                    "app": "",
                    "resource": "box-dhcp-reservation",
                }
            ],
        )
        self.assertEqual(self.mod.boxes_for_scope(compiled), ["k001"])

    def test_box_dhcp_reservation_rejects_invalid_identity_fields(self):
        cases = [
            ("hostname", "not_a_hostname"),
            ("mac", "not-a-mac"),
            ("ipv4_address", "not-an-ip"),
        ]
        for key, value in cases:
            reservation = {
                "hostname": "flint2",
                "mac": "02:00:00:00:00:01",
                "ipv4_address": "10.10.30.2",
            }
            reservation[key] = value
            path = self.write_registry(
                {
                    "schema_version": 1,
                    "boxes": {
                        "k001": {
                            "dhcp_reservations": [reservation],
                        }
                    },
                }
            )
            with self.subTest(key=key):
                with self.assertRaises(SystemExit):
                    with redirect_stderr(io.StringIO()):
                        self.mod.compile_registry(path, [])

    def test_local_ingress_compiles_realm_and_backend_resources(self):
        path = self.write_registry(
            {
                "schema_version": 1,
                "boxes": {
                    "k002": {
                        "access": {
                            "available_capabilities": ["overlay", "local-lan"],
                            "enabled_capabilities": ["overlay", "local-lan"],
                            "policy": {
                                "local-presence-control": "local-lan",
                                "private-service-ingress": "local-lan",
                                "file-upload": "overlay",
                            },
                        }
                    }
                },
                "apps": {
                    "local-ingress": {
                        "enabled": True,
                        "placement": {"active_master": "k002"},
                    }
                },
            }
        )
        compiled = self.mod.compile_registry(path, ["local-ingress"])
        comments = self.claim_comments(compiled)
        self.assertIn("app-local-ingress-household-https-router", comments)
        self.assertNotIn("app-local-ingress-admin-https-router", comments)
        self.assertIn("app-local-ingress-music-upstream-router", comments)
        household_router = self.claims_for_comment(
            compiled, "app-local-ingress-household-https-router"
        )[0]
        self.assertEqual(household_router["normalized"]["in_interface"], "eth1.10")
        self.assertEqual(household_router["normalized"]["out_interface"], "eth2")
        self.assertEqual(household_router["normalized"]["source"], "10.10.10.0/24")
        self.assertEqual(household_router["normalized"]["destination"], "192.168.200.10")
        self.assertEqual(household_router["normalized"]["ports"], [443])
        household_vm = self.claims_for_comment(
            compiled, "app-local-ingress-household-https-vm-input"
        )[0]
        self.assertEqual(household_vm["host_role"], "dmz")
        self.assertEqual(household_vm["normalized"]["source"], "10.10.10.0/24")
        music_upstream = self.claims_for_comment(
            compiled, "app-local-ingress-music-upstream-router"
        )[0]
        self.assertEqual(music_upstream["normalized"]["source"], "192.168.200.10")
        self.assertEqual(music_upstream["normalized"]["destination"], "192.168.100.10")
        self.assertEqual(music_upstream["normalized"]["ports"], [18082])

    def test_local_ingress_music_only_when_private_services_stay_overlay(self):
        path = self.write_registry(
            {
                "schema_version": 1,
                "boxes": {
                    "k002": {
                        "access": {
                            "available_capabilities": ["overlay", "local-lan"],
                            "enabled_capabilities": ["overlay", "local-lan"],
                            "policy": {
                                "local-presence-control": "local-lan",
                                "private-service-ingress": "overlay",
                                "file-upload": "overlay",
                            },
                        }
                    }
                },
                "apps": {
                    "local-ingress": {
                        "enabled": True,
                        "placement": {"active_master": "k002"},
                    }
                },
            }
        )
        compiled = self.mod.compile_registry(path, ["local-ingress"])
        self.assertEqual(
            compiled["apps"]["local-ingress"]["resources"],
            ["household-https", "music-upstream"],
        )
        comments = self.claim_comments(compiled)
        self.assertIn("app-local-ingress-household-https-router", comments)
        self.assertIn("app-local-ingress-music-upstream-router", comments)
        self.assertNotIn("app-local-ingress-nextcloud-upstream-router", comments)
        self.assertNotIn("app-local-ingress-immich-upstream-router", comments)

    def test_local_ingress_does_not_compile_when_local_lan_is_only_available(self):
        path = self.write_registry(
            {
                "schema_version": 1,
                "boxes": {
                    "k002": {
                        "access": {
                            "available_capabilities": ["overlay", "local-lan"],
                            "enabled_capabilities": ["overlay"],
                            "policy": {"local-presence-control": "overlay"},
                        }
                    }
                },
                "apps": {
                    "local-ingress": {
                        "enabled": True,
                        "placement": {"active_master": "k002"},
                    }
                },
            }
        )
        compiled = self.mod.compile_registry(path, ["local-ingress"])
        self.assertEqual(compiled["apps"]["local-ingress"]["resources"], [])
        self.assertEqual(compiled["app_resource_claims"], [])

    def test_music_local_lan_control_removes_overlay_ui_but_keeps_upload(self):
        path = self.write_registry(
            {
                "schema_version": 1,
                "boxes": {
                    "k002": {
                        "access": {
                            "available_capabilities": ["overlay", "local-lan"],
                            "enabled_capabilities": ["overlay", "local-lan"],
                            "policy": {
                                "local-presence-control": "local-lan",
                                "file-upload": "overlay",
                            },
                        }
                    }
                },
                "apps": {
                    "music": {
                        "enabled": True,
                        "placement": {"boxes": ["k002"]},
                        "devices": {
                            "local-audio-endpoint": {
                                "k002": {
                                    "mac": "b8:27:eb:00:00:02",
                                    "ipv4_address": "192.168.150.60",
                                    "hostname": "k002-streamer",
                                },
                            }
                        },
                    }
                },
            }
        )
        compiled = self.mod.compile_registry(path, ["music"])
        self.assertEqual(compiled["apps"]["music"]["tailnet_resources"], ["upload-ingress"])
        self.assertEqual(
            [item["hostname"] for item in compiled["tailnet_resources"]],
            ["k002-music-upload"],
        )

    def test_print_server_compiles_backend_to_iot_printer_resources(self):
        path = self.write_registry(
            {
                "schema_version": 1,
                "apps": {
                    "print-server": {
                        "enabled": True,
                        "placement": {"boxes": ["k002"]},
                        "devices": {
                            "printer": {
                                "k002": {
                                    "mac": "02:00:00:00:00:02",
                                    "ipv4_address": "192.168.150.78",
                                    "hostname": "k002-printer",
                                },
                            }
                        },
                        "resources": {},
                    }
                },
            }
        )
        compiled = self.mod.compile_registry(path, ["print-server"])
        self.assertEqual(compiled["apps"]["print-server"]["boxes"], ["k002"])
        self.assertEqual(compiled["apps"]["print-server"]["resources"], ["printer-ipp"])
        self.assertEqual(
            compiled["apps"]["print-server"]["tailnet_resources"],
            ["print-ingress"],
        )
        self.assertEqual(len(compiled["managed_iot_devices"]), 1)
        self.assertEqual(compiled["managed_iot_devices"][0]["hostname"], "k002-printer")
        self.assertEqual(
            compiled["managed_iot_devices"][0]["ipv4_address"],
            "192.168.150.78",
        )
        self.assertEqual(compiled["managed_iot_devices"][0]["tailnet_tag"], "tag:iot")
        self.assertEqual(
            self.mod.router_managed_dhcp_hosts(compiled),
            [
                {
                    "node": "k002",
                    "name": "k002-printer",
                    "mac": "02:00:00:00:00:02",
                    "address": "192.168.150.78",
                    "app": "print-server",
                    "resource": "printer",
                },
            ],
        )
        router_claims = self.claims_for_comment(
            compiled, "app-print-server-printer-ipp-router"
        )
        self.assertEqual(len(router_claims), 1)
        self.assertEqual(router_claims[0]["normalized"]["in_interface"], "eth3")
        self.assertEqual(router_claims[0]["normalized"]["out_interface"], "eth4")
        self.assertEqual(router_claims[0]["normalized"]["source"], "192.168.100.10")
        self.assertEqual(router_claims[0]["normalized"]["destination"], "192.168.150.78")
        self.assertEqual(router_claims[0]["normalized"]["ports"], [631])
        self.assertEqual(
            [(item["hostname"], item["tag"]) for item in compiled["tailnet_resources"]],
            [("k002-print", "tag:print")],
        )

    def test_box_access_rejects_enabled_unavailable_capability(self):
        path = self.write_registry(
            {
                "schema_version": 1,
                "boxes": {
                    "k002": {
                        "access": {
                            "available_capabilities": ["overlay"],
                            "enabled_capabilities": ["overlay", "local-lan"],
                        }
                    }
                },
                "apps": {},
            }
        )
        with self.assertRaises(SystemExit):
            with redirect_stderr(io.StringIO()):
                self.mod.compile_registry(path, [])

    def test_ap_uplink_box_config_selects_box_without_apps(self):
        path = self.write_registry(
            {
                "schema_version": 1,
                "boxes": {
                    "k001": {
                        "access": {
                            "available_capabilities": [
                                "overlay",
                                "ap-uplink",
                                "direct-egress",
                            ],
                            "enabled_capabilities": [
                                "overlay",
                                "ap-uplink",
                                "direct-egress",
                            ],
                            "policy": {
                                "household-wan-egress": "direct-egress",
                            },
                        },
                        "dom0_bridge_ports": {"lan": ["eth2"]},
                    }
                },
                "apps": {},
            }
        )
        compiled = self.mod.compile_registry(path, [])

        self.assertEqual(
            compiled["box_configs"]["k001"]["access"]["enabled_capabilities"],
            ["overlay", "ap-uplink", "direct-egress"],
        )
        self.assertEqual(
            compiled["box_configs"]["k001"]["access"]["policy"]["household-wan-egress"],
            "direct-egress",
        )
        self.assertEqual(
            compiled["box_configs"]["k001"]["dom0_bridge_ports"],
            {"lan": ["eth2"]},
        )
        self.assertEqual(self.mod.boxes_for_scope(compiled), ["k001"])

    def test_box_access_rejects_prohibited_available_capability(self):
        path = self.write_registry(
            {
                "schema_version": 1,
                "boxes": {
                    "k002": {
                        "access": {
                            "available_capabilities": ["overlay", "direct-egress"],
                            "enabled_capabilities": ["overlay"],
                            "prohibited_capabilities": ["direct-egress"],
                        }
                    }
                },
                "apps": {},
            }
        )
        with self.assertRaises(SystemExit):
            with redirect_stderr(io.StringIO()):
                self.mod.compile_registry(path, [])

    def test_box_access_rejects_prohibited_policy_capability(self):
        path = self.write_registry(
            {
                "schema_version": 1,
                "boxes": {
                    "k002": {
                        "access": {
                            "available_capabilities": ["overlay", "direct-ingress"],
                            "enabled_capabilities": ["overlay"],
                            "prohibited_capabilities": ["direct-ingress"],
                            "policy": {"public-ingress": "direct-ingress"},
                        }
                    }
                },
                "apps": {},
            }
        )
        with self.assertRaises(SystemExit):
            with redirect_stderr(io.StringIO()):
                self.mod.compile_registry(path, [])

    def test_music_requires_device_mac_for_enabled_box(self):
        path = self.write_registry(
            {
                "schema_version": 1,
                "apps": {
                    "music": {
                        "enabled": True,
                        "placement": {"boxes": ["k001"]},
                        "devices": {"local-audio-endpoint": {"k001": {}}},
                    }
                },
            }
        )
        with self.assertRaises(SystemExit):
            with redirect_stderr(io.StringIO()):
                self.mod.compile_registry(path, ["music"])

    def test_box_bridge_ports_reject_unknown_bridge_key(self):
        path = self.write_registry(
            {
                "schema_version": 1,
                "boxes": {
                    "k001": {
                        "dom0_bridge_ports": {
                            "unknown": ["eth3"],
                        },
                    },
                },
                "apps": {},
            }
        )
        with self.assertRaises(SystemExit):
            with redirect_stderr(io.StringIO()):
                self.mod.compile_registry(path, [])

    def test_static_site_rejects_passive_backup(self):
        path = self.write_registry(
            {
                "schema_version": 1,
                "apps": {
                    "static-site": {
                        "enabled": True,
                        "placement": {"active_master": "k001", "passive_backup": "k002"},
                        "resources": {},
                    }
                },
            }
        )
        with self.assertRaises(SystemExit):
            with redirect_stderr(io.StringIO()):
                self.mod.compile_registry(path, ["static-site"])

    def test_disabled_static_site_compiles_single_cleanup_scope(self):
        path = self.write_registry(
            {
                "schema_version": 1,
                "apps": {
                    "static-site": {
                        "enabled": False,
                        "placement": {"active_master": "k001"},
                        "resources": {},
                    }
                },
            }
        )
        compiled = self.mod.compile_registry(path, ["static-site"])
        self.assert_no_raw_rule_arrays(compiled)
        self.assertEqual(compiled["apps"]["static-site"]["boxes"], ["k001"])
        self.assertEqual(
            compiled["app_resource_cleanup_scopes"],
            [
                {
                    "schema_version": 1,
                    "node": "k001",
                    "app": "static-site",
                    "host_roles": ["router", "backend", "dmz", "iot"],
                    "reason": "disabled-app",
                }
            ],
        )
        self.assertEqual(
            self.mod.limit_for_resource_hosts(compiled),
            "k001-router,k001-bak,k001-dmz,k001-iot",
        )

    def test_apply_converges_router_topology_before_resources(self):
        compiled = self.compiled_for_run()
        with patch.object(self.mod.subprocess, "run") as run:
            self.mod.run_ansible("apply", compiled, "example.ts.net", "abc123")

        playbook_calls = [
            call.args[0]
            for call in run.call_args_list
            if call.args[0] and call.args[0][0] == "ansible-playbook"
        ]
        self.assertEqual(len(playbook_calls), 2)
        first_cmd = playbook_calls[0]
        second_cmd = playbook_calls[1]
        self.assertIn("31-vm-router.yml", " ".join(map(str, first_cmd)))
        self.assertEqual(first_cmd[first_cmd.index("--limit") + 1], "k001,k002")
        self.assertIn("80-platform-resources.yml", " ".join(map(str, second_cmd)))
        self.assertEqual(second_cmd[second_cmd.index("--limit") + 1], "k001-router")

    def test_verify_does_not_converge_router_topology(self):
        compiled = self.compiled_for_run()
        with patch.object(self.mod.subprocess, "run") as run:
            self.mod.run_ansible("verify", compiled, "example.ts.net")

        playbook_calls = [
            call.args[0]
            for call in run.call_args_list
            if call.args[0] and call.args[0][0] == "ansible-playbook"
        ]
        self.assertEqual(len(playbook_calls), 1)
        cmd = playbook_calls[0]
        self.assertNotIn("31-vm-router.yml", " ".join(map(str, cmd)))
        self.assertIn("81-platform-resources-verify.yml", " ".join(map(str, cmd)))
        self.assertEqual(cmd[cmd.index("--limit") + 1], "k001-router")

    def test_apply_uses_tailscale_ssh_for_podman_resource_hosts(self):
        compiled = self.compiled_with_podman_resource_for_run()
        with patch.object(self.mod.subprocess, "run") as run:
            self.mod.run_ansible("apply", compiled, "example.ts.net", "abc123")

        playbook_calls = [
            call.args[0]
            for call in run.call_args_list
            if call.args[0] and call.args[0][0] == "ansible-playbook"
        ]
        resource_cmd = [
            call
            for call in playbook_calls
            if "80-platform-resources.yml" in " ".join(map(str, call))
        ][0]
        self.assertEqual(resource_cmd[resource_cmd.index("--limit") + 1], "k001-router")

        tailscale_calls = [
            call.args[0]
            for call in run.call_args_list
            if call.args[0] and call.args[0][0] == "tailscale"
        ]
        self.assertTrue(tailscale_calls)
        self.assertTrue(all("neo@k001-bak" in call for call in tailscale_calls))
        joined_playbooks = "\n".join(" ".join(map(str, call)) for call in playbook_calls)
        self.assertNotIn("k001-bak", joined_playbooks)

        last_applied_uploads = [
            call
            for call in run.call_args_list
            if '"inventory_hostname": "k001-bak"' in (call.kwargs.get("input") or "")
        ]
        self.assertEqual(len(last_applied_uploads), 1)
        self.assertIn('"approved_commit": "abc123"', last_applied_uploads[0].kwargs["input"])
        self.assertIn('"registry_sha256": "sha256-test"', last_applied_uploads[0].kwargs["input"])

    def test_verify_uses_tailscale_ssh_without_last_applied_upload(self):
        compiled = self.compiled_with_podman_resource_for_run()
        with patch.object(self.mod.subprocess, "run") as run:
            self.mod.run_ansible("verify", compiled, "example.ts.net")

        playbook_calls = [
            call.args[0]
            for call in run.call_args_list
            if call.args[0] and call.args[0][0] == "ansible-playbook"
        ]
        resource_cmd = playbook_calls[0]
        self.assertEqual(resource_cmd[resource_cmd.index("--limit") + 1], "k001-router")

        tailscale_calls = [
            call
            for call in run.call_args_list
            if call.args[0] and call.args[0][0] == "tailscale"
        ]
        self.assertEqual(len(tailscale_calls), 3)
        self.assertTrue(all("neo@k001-bak" in call.args[0] for call in tailscale_calls))
        self.assertFalse(
            any('"inventory_hostname": "k001-bak"' in (call.kwargs.get("input") or "") for call in tailscale_calls)
        )

    def test_podman_remote_script_requires_firewall_baseline(self):
        script = self.mod.podman_resource_remote_script()
        self.assertIn("missing Podman VM firewall baseline", script)
        self.assertIn("/usr/sbin/nft -c -f /etc/nftables.nft", script)
        self.assertIn('if [ "$changed" = "1" ]; then', script)
        self.assertIn("/usr/sbin/nft -f /etc/nftables.nft", script)

    def test_app_scoped_apply_is_allowed(self):
        args = self.mod.argparse.Namespace(command="apply", app=["nextcloud-v2"])
        self.mod.assert_command_scope(args)

    def test_app_scoped_apply_skips_unrelated_app_vm_convergence(self):
        compiled = self.compiled_for_run()
        compiled["apps"] = {
            "static-site": {"boxes": ["k001"]},
            "user-shell": {"boxes": ["k001"]},
        }
        compiled["app_vm_specs"] = [
            {"app": "user-shell", "inventory_hostname": "k001-usr-alice"}
        ]

        with patch.object(self.mod.subprocess, "run") as run:
            self.mod.run_ansible(
                "apply",
                compiled,
                "example.ts.net",
                "abc123",
                scope_apps=["static-site"],
            )

        playbook_calls = [
            call.args[0]
            for call in run.call_args_list
            if call.args[0] and call.args[0][0] == "ansible-playbook"
        ]
        joined_calls = "\n".join(" ".join(map(str, call)) for call in playbook_calls)
        self.assertEqual(len(playbook_calls), 2)
        self.assertIn("31-vm-router.yml", joined_calls)
        self.assertIn("80-platform-resources.yml", joined_calls)
        self.assertNotIn("79-platform-app-vms.yml", joined_calls)
        self.assertNotIn("app-vms.yml", joined_calls)

    def test_resource_host_limit_targets_only_roles_with_rules(self):
        compiled = self.compiled_for_run()
        vm_rules = [
            {
                "node": "k001",
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
                "node": "k002",
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
        ledger = self.mod.build_app_resource_ledger([self.run_router_rule()], vm_rules)
        compiled["app_resource_claims"] = ledger["claims"]
        compiled["app_resource_effective_files"] = ledger["effective_files"]
        self.assertEqual(
            self.mod.limit_for_resource_hosts(compiled),
            "k001-router,k001-bak,k002-dmz",
        )

    def test_immich_grant_exports_only_app_approved_state(self):
        path = self.write_registry(
            {
                "schema_version": 1,
                "apps": {
                    "nextcloud": {
                        "enabled": True,
                        "placement": {"active_master": "k001", "passive_backup": "k002"},
                    },
                    "immich": {
                        "enabled": True,
                        "placement": {"active_master": "k001", "passive_backup": "k002"},
                        "resources": {},
                    },
                },
            }
        )
        compiled = self.mod.compile_registry(path, ["immich"])
        grant = self.mod.build_app_grant(compiled, "immich", "abc123")
        self.assertEqual(grant["kind"], "platform-resource-grant")
        self.assertEqual(grant["app"], "immich")
        self.assertTrue(grant["enabled"])
        self.assertEqual(grant["approved_commit"], "abc123")
        self.assertEqual(grant["boxes"], ["k001", "k002"])
        self.assertEqual(grant["placement"]["active_master"], "k001")
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
                        "placement": {"active_master": "k001", "passive_backup": "k002"},
                    }
                },
            }
        )
        compiled = self.mod.compile_registry(path, ["immich"])
        self.assert_no_raw_rule_arrays(compiled)
        self.assertEqual(compiled["apps"]["immich"]["boxes"], ["k001", "k002"])
        self.assertEqual(
            compiled["app_resource_cleanup_scopes"],
            [
                {
                    "schema_version": 1,
                    "node": "k001",
                    "app": "immich",
                    "host_roles": ["router", "backend", "dmz", "iot"],
                    "reason": "disabled-app",
                },
                {
                    "schema_version": 1,
                    "node": "k002",
                    "app": "immich",
                    "host_roles": ["router", "backend", "dmz", "iot"],
                    "reason": "disabled-app",
                },
            ],
        )

    def test_bootstrap_privileged_builder_requires_unexpired_approval(self):
        path = self.write_registry(
            {
                "schema_version": 1,
                "apps": {
                    "bootstrap-iso-debian": {
                        "enabled": True,
                        "placement": {"builder_box": "k001"},
                        "ephemeral": {
                            "privileged_approval": True,
                            "expires_at": "2099-01-01T00:00:00Z",
                            "cleanup_required": True,
                        },
                    }
                },
            }
        )
        compiled = self.mod.compile_registry(path, ["bootstrap-iso-debian"])
        self.assertEqual(compiled["apps"]["bootstrap-iso-debian"]["boxes"], ["k001"])

    def test_bootstrap_privileged_builder_rejects_missing_approval(self):
        path = self.write_registry(
            {
                "schema_version": 1,
                "apps": {
                    "bootstrap-iso-debian": {
                        "enabled": True,
                        "placement": {"builder_box": "k001"},
                    }
                },
            }
        )
        with self.assertRaises(SystemExit):
            with redirect_stderr(io.StringIO()):
                self.mod.compile_registry(path, ["bootstrap-iso-debian"])

    def test_per_user_app_vm_compiles_to_usr_zone(self):
        path = self.write_registry(
            {
                "schema_version": 1,
                "apps": {
                    "user-shell": {
                        "enabled": True,
                        "placement": {"active_master": "k001"},
                        "users": self.per_user_app_users()[:1],
                    }
                },
            }
        )

        with patch.object(self.mod, "app_manifest", return_value=self.per_user_app_manifest()):
            compiled = self.mod.compile_registry(path, ["user-shell"])

        specs = compiled["app_vm_specs"]
        self.assertEqual(len(specs), 1)
        spec = specs[0]
        self.assertEqual(spec["inventory_hostname"], "k001-usr-alice")
        self.assertEqual(spec["node_domain_role"], "usr")
        self.assertEqual(spec["zone"], "usr")
        self.assertEqual(spec["guest_spec"]["guest_name"], "usr-alice")
        self.assertEqual(spec["guest_spec"]["config_path"], "/etc/xen/usr-alice.cfg")
        self.assertEqual(spec["advertised_tags"], ["tag:vm", "tag:user-shell-alice"])
        bootstrap = self.claims_for_comment(
            compiled, "app-user-shell-app-vm-bootstrap-ssh-alice-usr-router"
        )
        self.assertEqual(bootstrap[0]["normalized"]["out_interface"], "eth5")
        self.assertEqual(bootstrap[0]["normalized"]["destination"], "192.168.175.20")
        egress = self.claims_for_comment(
            compiled, "app-user-shell-web-egress-alice-tcp"
        )
        self.assertEqual(egress[0]["normalized"]["source"], "192.168.175.20")
        policy = compiled["tailnet_policy_resources"][0]
        self.assertEqual(policy["hostname"], "k001-usr-alice")
        self.assertEqual(policy["tag"], "tag:user-shell-alice")
        self.assertEqual(policy["grants"][0]["ports"], [22])
        self.assertEqual(policy["ssh"][0]["users"], ["alice"])

    def test_legacy_per_user_zones_are_rejected(self):
        for zone in ("agt", "agent"):
            path = self.write_registry(
                {
                    "schema_version": 1,
                    "apps": {
                        "user-shell": {
                            "enabled": True,
                            "placement": {"active_master": "k001"},
                            "users": self.per_user_app_users()[:1],
                        }
                    },
                }
            )
            with patch.object(
                self.mod,
                "app_manifest",
                return_value=self.per_user_app_manifest(zone=zone),
            ):
                with self.assertRaises(SystemExit):
                    with redirect_stderr(io.StringIO()):
                        self.mod.compile_registry(path, ["user-shell"])

    def test_torrent_compiles_dedicated_alpine_app_vm(self):
        path = self.write_registry(
            {
                "schema_version": 1,
                "apps": {
                    "torrent": {
                        "enabled": True,
                        "placement": {"active_master": "k002"},
                        "app_vms": {
                            "torrent": {
                                "k002": {"vm_ipv4_address": "192.168.200.30"}
                            }
                        },
                    }
                },
            }
        )
        compiled = self.mod.compile_registry(path, ["torrent"])
        self.assertEqual(compiled["apps"]["torrent"]["boxes"], ["k002"])
        specs = compiled["app_vm_specs"]
        self.assertEqual(len(specs), 1)
        spec = specs[0]
        self.assertEqual(spec["inventory_hostname"], "k002-torrent")
        self.assertEqual(spec["node_domain_role"], "dmz")
        self.assertEqual(spec["advertised_tags"], ["tag:vm", "tag:torrent"])
        self.assertEqual(spec["guest_spec"]["guest_os"], "alpine")
        self.assertEqual(spec["guest_spec"]["container_runtime"], "none")
        self.assertEqual(spec["guest_spec"]["installed"]["required_lvs"], ["/dev/vg0/lv_torrent_torrent"])
        self.assertIn("address 192.168.200.30/", spec["guest_spec"]["network_interfaces"])

        egress_tcp = self.claims_for_comment(compiled, "app-torrent-vpn-egress-torrent-tcp")
        self.assertEqual(egress_tcp[0]["normalized"]["source"], "192.168.200.30")
        self.assertEqual(egress_tcp[0]["normalized"]["out_interface"], "eth0")
        bootstrap = self.claims_for_comment(
            compiled, "app-torrent-app-vm-bootstrap-ssh-torrent-dmz-router"
        )
        self.assertEqual(bootstrap[0]["normalized"]["destination"], "192.168.200.30")
        underlay_ops_to_app = self.claims_for_comment(
            compiled,
            "app-torrent-app-vm-tailscale-underlay-torrent-dmz-ops-to-app-router",
        )
        self.assertEqual(underlay_ops_to_app[0]["normalized"]["protocol"], "udp")
        self.assertEqual(underlay_ops_to_app[0]["normalized"]["source"], "192.168.125.10")
        self.assertEqual(underlay_ops_to_app[0]["normalized"]["destination"], "192.168.200.30")
        self.assertEqual(underlay_ops_to_app[0]["normalized"]["ports"], [41641])
        underlay_app_to_ops = self.claims_for_comment(
            compiled,
            "app-torrent-app-vm-tailscale-underlay-torrent-dmz-app-to-ops-router",
        )
        self.assertEqual(underlay_app_to_ops[0]["normalized"]["protocol"], "udp")
        self.assertEqual(underlay_app_to_ops[0]["normalized"]["source"], "192.168.200.30")
        self.assertEqual(underlay_app_to_ops[0]["normalized"]["destination"], "192.168.125.10")
        self.assertEqual(underlay_app_to_ops[0]["normalized"]["ports"], [41641])

        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "app-vms.yml"
            self.mod.render_app_vm_inventory(compiled, output, "example.ts.net")
            inventory = yaml.safe_load(output.read_text(encoding="utf-8"))
        hosts = inventory["all"]["hosts"]
        self.assertEqual(hosts["k002-torrent"]["ansible_become_method"], "doas")
        self.assertEqual(hosts["k002-torrent"]["platform_app_vm_guest_os"], "alpine")
        self.assertEqual(hosts["k002-torrent"]["platform_app_vm_zone"], "dmz")
        self.assertEqual(hosts["k002-torrent"]["platform_app_vm_interface"], "eth0")
        self.assertIn("vm_admin_authorized_key_file", hosts["k002-torrent"])
        self.assertIn("vm_bootstrap_private_key_file", hosts["k002-torrent"])
        self.assertIn("vm_bootstrap_known_hosts_file", hosts["k002-torrent"])
        self.assertEqual(hosts["k002-torrent"]["vm_local_users"][0]["name"], "neo")
        self.assertIn("k002-torrent", inventory["all"]["children"]["torrent_app_vms"]["hosts"])
        self.assertIn("k002-torrent", inventory["all"]["children"]["dmz_app_vms"]["hosts"])
        self.assertNotIn("dmz", inventory["all"]["children"])

    def test_household_vpn_compiles_dedicated_alpine_app_vm(self):
        path = self.write_registry(
            {
                "schema_version": 1,
                "boxes": {
                    "k002": {
                        "access": {
                            "available_capabilities": [
                                "overlay",
                                "local-lan",
                                "vpn-egress",
                            ],
                            "enabled_capabilities": [
                                "overlay",
                                "local-lan",
                                "vpn-egress",
                            ],
                            "policy": {
                                "local-presence-control": "local-lan",
                                "household-wan-egress": "vpn-egress",
                            },
                        }
                    }
                },
                "apps": {
                    "household-vpn": {
                        "enabled": True,
                        "placement": {"active_master": "k002"},
                        "app_vms": {
                            "gateway": {
                                "k002": {"vm_ipv4_address": "192.168.200.40"}
                            }
                        },
                    }
                },
            }
        )
        compiled = self.mod.compile_registry(path, ["household-vpn"])
        self.assertEqual(compiled["apps"]["household-vpn"]["boxes"], ["k002"])
        specs = compiled["app_vm_specs"]
        self.assertEqual(len(specs), 1)
        spec = specs[0]
        self.assertEqual(spec["inventory_hostname"], "k002-household-vpn")
        self.assertEqual(spec["node_domain_role"], "dmz")
        self.assertEqual(spec["advertised_tags"], ["tag:vm", "tag:household-vpn"])
        self.assertEqual(spec["guest_spec"]["guest_os"], "alpine")
        self.assertEqual(spec["guest_spec"]["container_runtime"], "none")
        self.assertEqual(
            spec["guest_spec"]["installed"]["required_lvs"],
            ["/dev/vg0/lv_household_vpn_household_vpn"],
        )
        self.assertIn("address 192.168.200.40/", spec["guest_spec"]["network_interfaces"])

        egress_tcp = self.claims_for_comment(
            compiled, "app-household-vpn-vpn-egress-gateway-tcp"
        )
        self.assertEqual(egress_tcp[0]["normalized"]["source"], "192.168.200.40")
        self.assertEqual(egress_tcp[0]["normalized"]["out_interface"], "eth0")
        bootstrap = self.claims_for_comment(
            compiled, "app-household-vpn-app-vm-bootstrap-ssh-gateway-dmz-router"
        )
        self.assertEqual(bootstrap[0]["normalized"]["destination"], "192.168.200.40")

        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "app-vms.yml"
            self.mod.render_app_vm_inventory(compiled, output, "example.ts.net")
            inventory = yaml.safe_load(output.read_text(encoding="utf-8"))
        self.assertIn(
            "k002-household-vpn",
            inventory["all"]["children"]["household_vpn_app_vms"]["hosts"],
        )
        self.assertIn(
            "k002-household-vpn",
            inventory["all"]["children"]["dmz_app_vms"]["hosts"],
        )

    def test_tailscale_ssh_quotes_remote_command_arguments(self):
        calls = []
        original_run = self.mod.subprocess.run

        def fake_run(argv, **kwargs):
            calls.append((argv, kwargs))

        self.mod.subprocess.run = fake_run
        try:
            self.mod.run_tailscale_ssh(
                "k002-bak.tail",
                ["sh", "-c", "umask 077 && cat > /tmp/a b"],
                input_text="payload",
            )
        finally:
            self.mod.subprocess.run = original_run

        self.assertEqual(
            calls[0][0],
            [
                "tailscale",
                "ssh",
                "neo@k002-bak.tail",
                "sh -c 'umask 077 && cat > /tmp/a b'",
            ],
        )
        self.assertEqual(calls[0][1]["input"], "payload")

    def test_platform_resources_inventory_does_not_override_dom0_remote_tmp(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "k001.yml"
            original_run = self.mod.subprocess.run

            def fake_run(argv, **_kwargs):
                rendered_output = Path(argv[argv.index("--output") + 1])
                rendered_output.write_text(
                    """---
all:
  children:
    k001_dom0:
      hosts:
        k001-dom0:
          node_name: k001
""",
                    encoding="utf-8",
                )

            self.mod.subprocess.run = fake_run
            try:
                self.mod.render_inventory("k001", output, "example.ts.net")
            finally:
                self.mod.subprocess.run = original_run

            inventory = yaml.safe_load(output.read_text(encoding="utf-8"))

        self.assertNotIn(
            "ansible_remote_tmp",
            inventory["all"]["children"]["k001_dom0"]["hosts"]["k001-dom0"],
        )

    def test_platform_resources_inventory_passes_dom0_bridge_ports(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "k002.yml"
            calls = []
            original_run = self.mod.subprocess.run

            def fake_run(argv, **_kwargs):
                calls.append(argv)
                rendered_output = Path(argv[argv.index("--output") + 1])
                rendered_output.write_text("---\nall: {}\n", encoding="utf-8")

            self.mod.subprocess.run = fake_run
            try:
                self.mod.render_inventory(
                    "k002",
                    output,
                    "example.ts.net",
                    {"k002": {"dom0_bridge_ports": {"iot": ["eth3"]}}},
                )
            finally:
                self.mod.subprocess.run = original_run

        self.assertIn("--dom0-bridge-port", calls[0])
        index = calls[0].index("--dom0-bridge-port")
        self.assertEqual(calls[0][index + 1], "iot=eth3")

    def test_app_vm_limit_includes_backend_builder_host(self):
        specs = [{"inventory_hostname": "k001-usr-alice"}]
        self.assertEqual(
            self.mod.limit_for_app_vms(["k001"], specs),
            "k001-bak,k001-dom0,k001-usr-alice",
        )

    def test_platform_resources_vars_marks_desired_json_unsafe(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "extra-vars.yml"
            self.mod.write_platform_resources_vars(
                output,
                {"plain": "value"},
                '{"config_path": "{{ xen_guest_config_dir }}/usr-alice.cfg"}',
            )
            text = output.read_text(encoding="utf-8")

        self.assertIn("plain: value\n", text)
        self.assertIn("platform_resources_desired_json: !unsafe |-\n", text)
        self.assertIn('  {"config_path": "{{ xen_guest_config_dir }}/usr-alice.cfg"}\n', text)

    def desired_for_rules(self, router_rules):
        ledger = self.mod.build_app_resource_ledger(router_rules, [])
        return {
            "schema_version": 1,
            "compiler": "platform-resources",
            "compiler_version": self.mod.COMPILER_VERSION,
            "registry_sha256": "test",
            "app_resource_effective_files": ledger["effective_files"],
        }

    def configure_reconciler_root(self, reconciler, root):
        reconciler.APP_RESOURCE_ROOT = root
        reconciler.KIND_DIRS = {
            "router-forward": root / "router-forward.d",
            "vm-input": root / "vm-input.d",
        }

    def test_app_scoped_reconcile_mutates_only_selected_resource_key_files(self):
        reconciler = load_reconcile_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "app-resources"
            self.configure_reconciler_root(reconciler, root)
            selected_rule = {
                "node": "k001",
                "app": "selected",
                "resource": "web",
                "in_interface": "eth2",
                "out_interface": "eth3",
                "source": "192.168.200.10",
                "destination": "192.168.100.10",
                "protocol": "tcp",
                "ports": [8080],
                "comment": "selected-web",
            }
            other_rule = dict(selected_rule)
            other_rule.update(
                {
                    "app": "other",
                    "resource": "admin",
                    "ports": [9443],
                    "comment": "other-admin",
                }
            )
            desired = self.desired_for_rules([selected_rule, other_rule])
            args = self.mod.argparse.Namespace(
                scope_app=[],
                node_name="k001",
                node_role="router",
            )
            with redirect_stdout(io.StringIO()):
                reconciler.apply_resources(desired, args)
            other_files = sorted((root / "router-forward.d").glob("*.nft"))
            other_content_before = {
                path.name: path.read_text(encoding="utf-8") for path in other_files
            }

            selected_rule_changed = dict(selected_rule)
            selected_rule_changed["ports"] = [8081]
            desired_changed = self.desired_for_rules([selected_rule_changed, other_rule])
            args.scope_app = ["selected"]
            with redirect_stdout(io.StringIO()):
                reconciler.apply_resources(desired_changed, args)

            other_after = {
                path.name: path.read_text(encoding="utf-8")
                for path in (root / "router-forward.d").glob("*.nft")
                if "other" in path.read_text(encoding="utf-8")
            }
            self.assertEqual(
                {
                    name: content
                    for name, content in other_content_before.items()
                    if "other" in content
                },
                other_after,
            )
            rendered = "\n".join(
                path.read_text(encoding="utf-8")
                for path in (root / "router-forward.d").glob("*.nft")
            )
            self.assertIn("8081", rendered)
            self.assertNotIn(" 8080 accept", rendered)

    def test_full_and_app_scoped_apply_converge_to_same_snippets(self):
        reconciler = load_reconcile_module()
        rule_a = {
            "node": "k001",
            "app": "app-a",
            "resource": "web",
            "in_interface": "eth2",
            "out_interface": "eth3",
            "source": "192.168.200.10",
            "destination": "192.168.100.10",
            "protocol": "tcp",
            "ports": [8080],
            "comment": "app-a-web",
        }
        rule_b = dict(rule_a)
        rule_b.update({"app": "app-b", "ports": [9443], "comment": "app-b-web"})
        desired = self.desired_for_rules([rule_a, rule_b])
        with tempfile.TemporaryDirectory() as full_tmp, tempfile.TemporaryDirectory() as scoped_tmp:
            full_root = Path(full_tmp) / "app-resources"
            scoped_root = Path(scoped_tmp) / "app-resources"
            args = self.mod.argparse.Namespace(scope_app=[], node_name="k001", node_role="router")

            self.configure_reconciler_root(reconciler, full_root)
            with redirect_stdout(io.StringIO()):
                reconciler.apply_resources(desired, args)
            full_files = {
                path.name: path.read_text(encoding="utf-8")
                for path in (full_root / "router-forward.d").glob("*.nft")
            }

            self.configure_reconciler_root(reconciler, scoped_root)
            args.scope_app = ["app-a"]
            with redirect_stdout(io.StringIO()):
                reconciler.apply_resources(desired, args)
            args.scope_app = ["app-b"]
            with redirect_stdout(io.StringIO()):
                reconciler.apply_resources(desired, args)
            scoped_files = {
                path.name: path.read_text(encoding="utf-8")
                for path in (scoped_root / "router-forward.d").glob("*.nft")
            }

            self.assertEqual(full_files, scoped_files)


    def test_legacy_raw_topology_fields_are_rejected(self):
        topology = self.mod.load_topology()
        resource = {
            "id": "bad-flow",
            "type": "interzone_tcp",
            "router": {"in_interface": "eth2"},
            "from_zone": "dmz",
            "to_zone": "bak",
            "ports": [443],
        }
        with self.assertRaises(SystemExit):
            with redirect_stderr(io.StringIO()):
                self.mod.validate_network_resource_shape("badapp", resource, topology)

    def test_app_manifest_cannot_declare_tailnet_tag_ownership(self):
        topology = self.mod.load_topology()
        manifest = {
            "_manifest_path": "test",
            "resources": {
                "tailnet": [
                    {
                        "id": "bad-ingress",
                        "tag_default": "tag:bad",
                        "tag_owners": ["tag:vm"],
                    }
                ]
            },
        }
        with self.assertRaises(SystemExit):
            with redirect_stderr(io.StringIO()):
                self.mod.validate_manifest_resources("badapp", manifest, topology)

    def test_app_manifest_cannot_use_reserved_control_tailnet_tag(self):
        topology = self.mod.load_topology()
        manifest = {
            "_manifest_path": "test",
            "resources": {
                "tailnet": [
                    {
                        "id": "bad-ingress",
                        "tag_default": "tag:infra",
                    }
                ]
            },
        }
        with self.assertRaises(SystemExit):
            with redirect_stderr(io.StringIO()):
                self.mod.validate_manifest_resources("badapp", manifest, topology)

    def test_app_manifest_cannot_default_app_vm_to_reserved_control_tailnet_tag(self):
        topology = self.mod.load_topology()
        manifest = {
            "_manifest_path": "test",
            "resources": {
                "compute": [
                    {
                        "id": "bad-vm",
                        "type": "app_vm",
                        "zone": "dmz",
                        "tailnet_tag_default": "tag:ops",
                    }
                ]
            },
        }
        with self.assertRaises(SystemExit):
            with redirect_stderr(io.StringIO()):
                self.mod.validate_manifest_resources("badapp", manifest, topology)

    def test_registry_cannot_assign_app_vm_reserved_control_tailnet_tag(self):
        manifest = {
            "schema_version": 1,
            "app": "badapp",
            "placement_mode": "single_box",
            "_manifest_path": "test://badapp/platform-resources.yml",
            "resources": {
                "compute": [
                    {
                        "id": "runtime",
                        "type": "app_vm",
                        "zone": "dmz",
                    }
                ]
            },
        }
        path = self.write_registry(
            {
                "schema_version": 1,
                "apps": {
                    "badapp": {
                        "enabled": True,
                        "placement": {"active_master": "k002"},
                        "app_vms": {
                            "runtime": {
                                "k002": {
                                    "vm_ipv4_address": "192.168.200.30",
                                    "tailnet_tag": "tag:ops",
                                }
                            }
                        },
                    }
                },
            }
        )
        with patch.object(self.mod, "app_manifest", return_value=manifest):
            with self.assertRaises(SystemExit):
                with redirect_stderr(io.StringIO()):
                    self.mod.compile_registry(path, [])

    def test_app_manifest_cannot_place_privileged_builder_directly(self):
        topology = self.mod.load_topology()
        manifest = {
            "_manifest_path": "test",
            "resources": {
                "compute": [
                    {
                        "id": "bad-builder",
                        "type": "ephemeral_privileged_builder",
                        "zone": "bak",
                        "builder_host": "k001-bak",
                    }
                ]
            },
        }
        with self.assertRaises(SystemExit):
            with redirect_stderr(io.StringIO()):
                self.mod.validate_manifest_resources("badapp", manifest, topology)

    def test_app_manifest_rejects_unknown_resource_section(self):
        topology = self.mod.load_topology()
        manifest = {
            "_manifest_path": "test",
            "resources": {
                "network": [],
                "sudoers": [{"id": "bad"}],
            },
        }
        with self.assertRaises(SystemExit):
            with redirect_stderr(io.StringIO()):
                self.mod.validate_manifest_resources("badapp", manifest, topology)

    def test_app_manifest_rejects_unknown_compute_field(self):
        topology = self.mod.load_topology()
        manifest = {
            "_manifest_path": "test",
            "resources": {
                "compute": [
                    {
                        "id": "runtime",
                        "type": "podman_workload",
                        "zone": "bak",
                        "shell_command": "doas nft flush ruleset",
                    }
                ]
            },
        }
        with self.assertRaises(SystemExit):
            with redirect_stderr(io.StringIO()):
                self.mod.validate_manifest_resources("badapp", manifest, topology)

    def test_app_manifest_rejects_unknown_tailnet_grant_field(self):
        topology = self.mod.load_topology()
        manifest = {
            "_manifest_path": "test",
            "resources": {
                "tailnet": [
                    {
                        "id": "ingress",
                        "tag_default": "tag:bad",
                        "grants": [
                            {
                                "src": "group:family",
                                "ports": [443],
                                "users": ["root"],
                            }
                        ],
                    }
                ]
            },
        }
        with self.assertRaises(SystemExit):
            with redirect_stderr(io.StringIO()):
                self.mod.validate_manifest_resources("badapp", manifest, topology)

    def test_unknown_zone_is_rejected(self):
        topology = self.mod.load_topology()
        resource = {
            "id": "bad-flow",
            "type": "interzone_tcp",
            "from_zone": "internet",
            "to_zone": "bak",
            "ports": [443],
        }
        with self.assertRaises(SystemExit):
            with redirect_stderr(io.StringIO()):
                self.mod.validate_network_resource_shape("badapp", resource, topology)

    def test_missing_platform_resources_manifest_is_rejected(self):
        path = self.write_registry(
            {
                "schema_version": 1,
                "apps": {
                    "missingapp": {
                        "enabled": True,
                        "placement": {"active_master": "k001", "passive_backup": "k002"},
                    }
                },
            }
        )
        with self.assertRaises(SystemExit):
            with redirect_stderr(io.StringIO()):
                self.mod.compile_registry(path, ["missingapp"])

    def test_app_scoped_verify_compiles_only_requested_app(self):
        path = self.write_registry({"schema_version": 1, "apps": {}})
        compile_calls = []

        def fake_compile(registry_path, app_filter):
            compile_calls.append((registry_path, list(app_filter)))
            return {"apps": {"immich": {}}}

        with patch.object(self.mod, "compile_registry", side_effect=fake_compile):
            with patch.object(self.mod, "run_ansible") as run_ansible:
                with patch.object(
                    self.mod.sys,
                    "argv",
                    [
                        "platform-resources",
                        "--registry",
                        str(path),
                        "--app",
                        "immich",
                        "verify",
                    ],
                ):
                    self.mod.main()

        self.assertEqual(compile_calls, [(path, ["immich"])])
        run_ansible.assert_called_once()

    def test_ops_role_hostname_is_not_accepted_as_box_name(self):
        with self.assertRaises(SystemExit):
            with redirect_stderr(io.StringIO()):
                self.mod.validate_box("k001-ops", "apps.example.placement.active_master")


if __name__ == "__main__":
    unittest.main()
