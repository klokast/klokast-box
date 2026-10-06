"""Resource compiler regression tests."""
from platform_resource_test_support import ResourceTestCase, REPO_ROOT
import io
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch
import yaml
import platform_resource_model as model
import platform_resource_compiler as compiler


class ResourceCompilerTest(ResourceTestCase):
    def test_reserved_application_name_is_rejected(self):
        for reserved in ('platform', 'doctor'):
            for enabled in (True, False):
                registry = {'schema_version': 1, 'boxes': {},
                            'apps': {reserved: {'enabled': enabled}}}
                for operation in ('resources', 'filtered-resources', 'boxes'):
                    with self.subTest(reserved=reserved, enabled=enabled, operation=operation):
                        stderr = io.StringIO()
                        with redirect_stderr(stderr), self.assertRaises(SystemExit):
                            if operation == 'boxes':
                                compiler.compile_box_registry_plan('/unused', registry_input={'registry': registry}, repo_root=REPO_ROOT)
                            else:
                                selected = ['music'] if operation == 'filtered-resources' else []
                                compiler.compile_registry('/unused', selected, registry_input={'registry': registry}, repo_root=REPO_ROOT)
                        self.assertIn(f'application name {reserved} is reserved for kk {reserved}', stderr.getvalue())

    def test_usr_network_requires_a_dedicated_source(self):
        topology = model.load_topology(repo_root=REPO_ROOT)
        resource = {'id': 'web', 'type': 'wan_egress', 'from_zone': 'usr', 'tcp_ports': [443]}
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            compiler.compile_resource_for_box('user-shell', 'boxa', resource, [], [], topology)
        rules = []
        compiler.compile_resource_for_box('user-shell', 'boxa', resource, rules, [], topology,
                                         users=[{'slug': 'alice', 'vm_ipv4_address': '192.168.175.20'}])
        self.assertEqual(rules[0]['source'], '192.168.175.20')

    def test_usr_shared_destination_is_rejected(self):
        resource = {'id': 'ingress', 'type': 'realm_to_zone_tcp', 'from_realm': 'household', 'to_zone': 'usr', 'ports': [443]}
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            compiler.compile_resource_for_box('user-shell', 'boxa', resource, [], [], model.load_topology(repo_root=REPO_ROOT))

    def test_nextcloud_compiles_required_and_optional_resources(self):
        path = self.write_registry(
            {
                "schema_version": 1,
                "boxes": {
                    box: {
                        "access": {
                            "available_capabilities": ["overlay", "edge-ingress"],
                            "enabled_capabilities": ["overlay", "edge-ingress"],
                        }
                    }
                    for box in ("boxa", "boxb")
                },
                "apps": {
                    "nextcloud": {
                        "enabled": True,
                        "placement": {"active_master": "boxa", "passive_backup": "boxb"},
                        "resources": {"cloudflare-tunnel-egress": True},
                    }
                },
            }
        )
        compiled = compiler.compile_registry(path, ["nextcloud"], repo_root=REPO_ROOT)
        self.assert_no_raw_rule_arrays(compiled)
        comments = self.claim_comments(compiled)
        self.assertIn("app-nextcloud-backend-http-upstream-router", comments)
        self.assertIn("app-nextcloud-cloudflare-tunnel-egress-tcp", comments)
        self.assertIn("app-nextcloud-cloudflare-tunnel-egress-udp", comments)
        self.assertEqual(compiled["apps"]["nextcloud"]["boxes"], ["boxa", "boxb"])
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

    def test_selected_capability_is_required_on_every_placement_box(self):
        path = self.write_registry(
            {
                "schema_version": 1,
                "boxes": {
                    "boxa": {
                        "access": {
                            "available_capabilities": ["overlay", "edge-ingress"],
                            "enabled_capabilities": ["overlay", "edge-ingress"],
                        }
                    },
                    "boxb": {
                        "access": {
                            "available_capabilities": ["overlay"],
                            "enabled_capabilities": ["overlay"],
                        }
                    },
                },
                "apps": {
                    "nextcloud": {
                        "enabled": True,
                        "placement": {"active_master": "boxa", "passive_backup": "boxb"},
                        "resources": {"cloudflare-tunnel-egress": True},
                    }
                },
            }
        )
        with self.assertRaises(SystemExit):
            with redirect_stderr(io.StringIO()):
                compiler.compile_registry(path, ["nextcloud"], repo_root=REPO_ROOT)

    def test_required_capability_fails_closed(self):
        path = self.write_registry(
            {
                "schema_version": 1,
                "apps": {
                    "static-site": {
                        "enabled": True,
                        "placement": {"active_master": "boxa"},
                        "resources": {},
                    }
                },
            }
        )
        with self.assertRaises(SystemExit):
            with redirect_stderr(io.StringIO()):
                compiler.compile_registry(path, ["static-site"], repo_root=REPO_ROOT)

    def test_shared_guest_runtime_state_compiles_for_platform_map(self):
        path = self.write_registry(
            {
                "schema_version": 1,
                "apps": {},
                "boxes": {
                    "boxa": {
                        "shared_guests": {"iot": {"runtime_state": "stopped"}}
                    }
                },
            }
        )
        compiled = compiler.compile_registry(path, [], repo_root=REPO_ROOT)

        self.assertEqual(
            compiled["box_configs"]["boxa"]["shared_guests"],
            {
                "bak": {"runtime_state": "running"},
                "dmz": {"runtime_state": "running"},
                "iot": {"runtime_state": "stopped"},
            },
        )
        mapped = {
            item["role"]: item
            for item in compiled["platform_map"]["shared_guests"]
            if item["node"] == "boxa"
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
                        "boxes": {"boxa": {"shared_guests": shared_guests}},
                    }
                )
                with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()) as stderr:
                    compiler.compile_registry(path, [], repo_root=REPO_ROOT)
                self.assertIn(expected, stderr.getvalue())

    def test_running_app_cannot_target_stopped_shared_zone(self):
        path = self.write_registry(
            {
                "schema_version": 1,
                "apps": {
                    "nextcloud-v2": {
                        "enabled": True,
                        "placement": {
                            "active_master": "boxa",
                            "passive_backup": "boxb",
                        },
                    }
                },
                "boxes": {
                    "boxa": {
                        "shared_guests": {"bak": {"runtime_state": "stopped"}}
                    }
                },
            }
        )
        with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()) as stderr:
            compiler.compile_registry(path, [], repo_root=REPO_ROOT)
        self.assertIn("apps.nextcloud-v2 is running on boxa", stderr.getvalue())

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
                    "boxa": {
                        "shared_guests": {"iot": {"runtime_state": "stopped"}}
                    }
                },
            }
        )

        compiled = compiler.compile_box_registry_plan(path, repo_root=REPO_ROOT)
        self.assertEqual(
            compiled["box_configs"]["boxa"]["shared_guests"]["iot"][
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

    def test_immich_compiles_private_ingress_resources(self):
        path = self.write_registry(
            {
                "schema_version": 1,
                "apps": {
                    "immich": {
                        "enabled": True,
                        "placement": {"active_master": "boxa", "passive_backup": "boxb"},
                        "resources": {},
                    }
                },
            }
        )
        compiled = compiler.compile_registry(path, ["immich"], repo_root=REPO_ROOT)
        self.assert_no_raw_rule_arrays(compiled)
        comments = self.claim_comments(compiled)
        self.assertIn("app-immich-backend-http-upstream-router", comments)
        self.assertEqual(compiled["apps"]["immich"]["boxes"], ["boxa", "boxb"])
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
                "boxes": {
                    "boxa": {
                        "access": {
                            "available_capabilities": ["overlay", "edge-ingress"],
                            "enabled_capabilities": ["overlay", "edge-ingress"],
                        }
                    }
                },
                "apps": {
                    "static-site": {
                        "enabled": True,
                        "placement": {"active_master": "boxa"},
                        "resources": {},
                    }
                },
            }
        )
        compiled = compiler.compile_registry(path, ["static-site"], repo_root=REPO_ROOT)
        self.assertEqual(compiled["apps"]["static-site"]["boxes"], ["boxa"])
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
        self.assertEqual(compiler.limit_for_resource_hosts(compiled), "boxa-router")

    def test_music_compiles_managed_iot_device_resources(self):
        path = self.write_registry(
            {
                "schema_version": 1,
                "boxes": {
                    "boxb": {
                        "dom0_bridge_ports": {
                            "iot": ["eth3"],
                        },
                    },
                },
                "apps": {
                    "music": {
                        "enabled": True,
                        "placement": {"boxes": ["boxa", "boxb"]},
                        "devices": {
                            "local-audio-endpoint": {
                                "boxa": {
                                    "mac": "b8:27:eb:00:00:01",
                                    "ipv4_address": "192.168.150.60",
                                    "hostname": "boxa-streamer",
                                },
                                "boxb": {
                                    "mac": "b8:27:eb:00:00:02",
                                    "ipv4_address": "192.168.150.60",
                                    "hostname": "boxb-streamer",
                                },
                            }
                        },
                    }
                },
            }
        )
        compiled = compiler.compile_registry(path, ["music"], repo_root=REPO_ROOT)
        self.assertEqual(
            compiled["box_configs"]["boxb"]["dom0_bridge_ports"],
            {"iot": ["eth3"]},
        )
        self.assertEqual(compiled["apps"]["music"]["boxes"], ["boxa", "boxb"])
        self.assertEqual(
            compiled["apps"]["music"]["resources"],
            ["snapcast-stream", "audio-endpoint-updates"],
        )
        self.assertEqual(len(compiled["managed_iot_devices"]), 2)
        self.assertEqual(compiled["managed_iot_devices"][0]["hostname"], "boxa-streamer")
        self.assertEqual(compiled["managed_iot_devices"][0]["ipv4_address"], "192.168.150.60")
        self.assertEqual(compiled["managed_iot_devices"][0]["tailnet_tag"], "tag:streamer")
        self.assertEqual(compiled["managed_iot_devices"][1]["ipv4_address"], "192.168.150.60")
        self.assertEqual(compiled["managed_iot_devices"][1]["tailnet_tag"], "tag:streamer")
        self.assertEqual(
            compiler.router_managed_dhcp_hosts(compiled),
            [
                {
                    "node": "boxa",
                    "name": "boxa-streamer",
                    "mac": "b8:27:eb:00:00:01",
                    "address": "192.168.150.60",
                    "app": "music",
                    "resource": "local-audio-endpoint",
                },
                {
                    "node": "boxb",
                    "name": "boxb-streamer",
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
                "boxa-music",
                "boxb-music",
                "boxa-music-upload",
                "boxb-music-upload",
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
                    "boxa": {
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
        compiled = compiler.compile_registry(path, [], repo_root=REPO_ROOT)
        self.assertEqual(compiled["boxes"], [])
        self.assertEqual(
            compiled["box_configs"]["boxa"]["dhcp_reservations"],
            [
                {
                    "hostname": "flint2",
                    "mac": "02:00:00:00:00:01",
                    "ipv4_address": "10.10.30.2",
                }
            ],
        )
        self.assertEqual(
            compiler.router_managed_dhcp_hosts(compiled),
            [
                {
                    "node": "boxa",
                    "name": "flint2",
                    "mac": "02:00:00:00:00:01",
                    "address": "10.10.30.2",
                    "app": "",
                    "resource": "box-dhcp-reservation",
                }
            ],
        )
        self.assertEqual(compiler.boxes_for_scope(compiled), ["boxa"])

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
                        "boxa": {
                            "dhcp_reservations": [reservation],
                        }
                    },
                }
            )
            with self.subTest(key=key):
                with self.assertRaises(SystemExit):
                    with redirect_stderr(io.StringIO()):
                        compiler.compile_registry(path, [], repo_root=REPO_ROOT)

    def test_local_ingress_compiles_realm_and_backend_resources(self):
        path = self.write_registry(
            {
                "schema_version": 1,
                "boxes": {
                    "boxb": {
                        "access": {
                            "available_capabilities": ["overlay", "local-lan"],
                            "enabled_capabilities": ["overlay", "local-lan"],
                        }
                    }
                },
                "apps": {
                    "local-ingress": {
                        "enabled": True,
                        "placement": {"active_master": "boxb"},
                    }
                },
            }
        )
        compiled = compiler.compile_registry(path, ["local-ingress"], repo_root=REPO_ROOT)
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

    def test_local_ingress_capability_enables_all_required_local_lan_resources(self):
        path = self.write_registry(
            {
                "schema_version": 1,
                "boxes": {
                    "boxb": {
                        "access": {
                            "available_capabilities": ["overlay", "local-lan"],
                            "enabled_capabilities": ["overlay", "local-lan"],
                        }
                    }
                },
                "apps": {
                    "local-ingress": {
                        "enabled": True,
                        "placement": {"active_master": "boxb"},
                    }
                },
            }
        )
        compiled = compiler.compile_registry(path, ["local-ingress"], repo_root=REPO_ROOT)
        self.assertEqual(
            compiled["apps"]["local-ingress"]["resources"],
            ["household-https", "music-upstream", "nextcloud-upstream", "immich-upstream"],
        )
        comments = self.claim_comments(compiled)
        self.assertIn("app-local-ingress-household-https-router", comments)
        self.assertIn("app-local-ingress-music-upstream-router", comments)
        self.assertIn("app-local-ingress-nextcloud-upstream-router", comments)
        self.assertIn("app-local-ingress-immich-upstream-router", comments)

    def test_local_ingress_does_not_compile_when_local_lan_is_only_available(self):
        path = self.write_registry(
            {
                "schema_version": 1,
                "boxes": {
                    "boxb": {
                        "access": {
                            "available_capabilities": ["overlay", "local-lan"],
                            "enabled_capabilities": ["overlay"],
                        }
                    }
                },
                "apps": {
                    "local-ingress": {
                        "enabled": True,
                        "placement": {"active_master": "boxb"},
                    }
                },
            }
        )
        with self.assertRaises(SystemExit):
            with redirect_stderr(io.StringIO()):
                compiler.compile_registry(path, ["local-ingress"], repo_root=REPO_ROOT)

    def test_music_capabilities_do_not_select_box_wide_access_policy(self):
        path = self.write_registry(
            {
                "schema_version": 1,
                "boxes": {
                    "boxb": {
                        "access": {
                            "available_capabilities": ["overlay", "local-lan"],
                            "enabled_capabilities": ["overlay", "local-lan"],
                        }
                    }
                },
                "apps": {
                    "music": {
                        "enabled": True,
                        "placement": {"boxes": ["boxb"]},
                        "devices": {
                            "local-audio-endpoint": {
                                "boxb": {
                                    "mac": "b8:27:eb:00:00:02",
                                    "ipv4_address": "192.168.150.60",
                                    "hostname": "boxb-streamer",
                                },
                            }
                        },
                    }
                },
            }
        )
        compiled = compiler.compile_registry(path, ["music"], repo_root=REPO_ROOT)
        self.assertEqual(compiled["apps"]["music"]["tailnet_resources"], ["private-ui", "upload-ingress"])
        self.assertEqual(
            [item["hostname"] for item in compiled["tailnet_resources"]],
            ["boxb-music", "boxb-music-upload"],
        )

    def test_print_server_compiles_backend_to_iot_printer_resources(self):
        path = self.write_registry(
            {
                "schema_version": 1,
                "apps": {
                    "print-server": {
                        "enabled": True,
                        "placement": {"boxes": ["boxb"]},
                        "devices": {
                            "printer": {
                                "boxb": {
                                    "mac": "02:00:00:00:00:02",
                                    "ipv4_address": "192.168.150.78",
                                    "hostname": "boxb-printer",
                                },
                            }
                        },
                        "resources": {},
                    }
                },
            }
        )
        compiled = compiler.compile_registry(path, ["print-server"], repo_root=REPO_ROOT)
        self.assertEqual(compiled["apps"]["print-server"]["boxes"], ["boxb"])
        self.assertEqual(compiled["apps"]["print-server"]["resources"], ["printer-ipp"])
        self.assertEqual(
            compiled["apps"]["print-server"]["tailnet_resources"],
            ["print-ingress"],
        )
        self.assertEqual(len(compiled["managed_iot_devices"]), 1)
        self.assertEqual(compiled["managed_iot_devices"][0]["hostname"], "boxb-printer")
        self.assertEqual(
            compiled["managed_iot_devices"][0]["ipv4_address"],
            "192.168.150.78",
        )
        self.assertEqual(compiled["managed_iot_devices"][0]["tailnet_tag"], "tag:iot")
        self.assertEqual(
            compiler.router_managed_dhcp_hosts(compiled),
            [
                {
                    "node": "boxb",
                    "name": "boxb-printer",
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
            [("boxb-print", "tag:print")],
        )

    def test_box_access_rejects_enabled_unavailable_capability(self):
        path = self.write_registry(
            {
                "schema_version": 1,
                "boxes": {
                    "boxb": {
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
                compiler.compile_registry(path, [], repo_root=REPO_ROOT)

    def test_ap_uplink_box_config_selects_box_without_apps(self):
        path = self.write_registry(
            {
                "schema_version": 1,
                "boxes": {
                    "boxa": {
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
                        },
                        "dom0_bridge_ports": {"lan": ["eth2"]},
                    }
                },
                "apps": {},
            }
        )
        compiled = compiler.compile_registry(path, [], repo_root=REPO_ROOT)

        self.assertEqual(
            compiled["box_configs"]["boxa"]["access"]["enabled_capabilities"],
            ["overlay", "ap-uplink", "direct-egress"],
        )
        self.assertNotIn("policy", compiled["box_configs"]["boxa"]["access"])
        self.assertEqual(
            compiled["box_configs"]["boxa"]["dom0_bridge_ports"],
            {"lan": ["eth2"]},
        )
        self.assertEqual(compiler.boxes_for_scope(compiled), ["boxa"])

    def test_box_access_rejects_prohibited_available_capability(self):
        path = self.write_registry(
            {
                "schema_version": 1,
                "boxes": {
                    "boxb": {
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
                compiler.compile_registry(path, [], repo_root=REPO_ROOT)

    def test_box_access_rejects_removed_policy_field(self):
        path = self.write_registry(
            {
                "schema_version": 1,
                "boxes": {
                    "boxb": {
                        "access": {
                            "available_capabilities": ["overlay"],
                            "enabled_capabilities": ["overlay"],
                            "policy": {"public-ingress": "direct-ingress"},
                        }
                    }
                },
                "apps": {},
            }
        )
        with self.assertRaises(SystemExit):
            with redirect_stderr(io.StringIO()):
                compiler.compile_registry(path, [], repo_root=REPO_ROOT)

    def test_music_requires_device_mac_for_enabled_box(self):
        path = self.write_registry(
            {
                "schema_version": 1,
                "apps": {
                    "music": {
                        "enabled": True,
                        "placement": {"boxes": ["boxa"]},
                        "devices": {"local-audio-endpoint": {"boxa": {}}},
                    }
                },
            }
        )
        with self.assertRaises(SystemExit):
            with redirect_stderr(io.StringIO()):
                compiler.compile_registry(path, ["music"], repo_root=REPO_ROOT)

    def test_box_bridge_ports_reject_unknown_bridge_key(self):
        path = self.write_registry(
            {
                "schema_version": 1,
                "boxes": {
                    "boxa": {
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
                compiler.compile_registry(path, [], repo_root=REPO_ROOT)

    def test_static_site_rejects_passive_backup(self):
        path = self.write_registry(
            {
                "schema_version": 1,
                "apps": {
                    "static-site": {
                        "enabled": True,
                        "placement": {"active_master": "boxa", "passive_backup": "boxb"},
                        "resources": {},
                    }
                },
            }
        )
        with self.assertRaises(SystemExit):
            with redirect_stderr(io.StringIO()):
                compiler.compile_registry(path, ["static-site"], repo_root=REPO_ROOT)

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
            compiler.limit_for_resource_hosts(compiled),
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
            compiler.limit_for_resource_hosts(compiled),
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

    def test_bootstrap_privileged_builder_requires_unexpired_approval(self):
        path = self.write_registry(
            {
                "schema_version": 1,
                "apps": {
                    "bootstrap-iso-debian": {
                        "enabled": True,
                        "placement": {"builder_box": "boxa"},
                        "ephemeral": {
                            "privileged_approval": True,
                            "expires_at": "2099-01-01T00:00:00Z",
                            "cleanup_required": True,
                        },
                    }
                },
            }
        )
        compiled = compiler.compile_registry(path, ["bootstrap-iso-debian"], repo_root=REPO_ROOT)
        self.assertEqual(compiled["apps"]["bootstrap-iso-debian"]["boxes"], ["boxa"])

    def test_bootstrap_privileged_builder_rejects_missing_approval(self):
        path = self.write_registry(
            {
                "schema_version": 1,
                "apps": {
                    "bootstrap-iso-debian": {
                        "enabled": True,
                        "placement": {"builder_box": "boxa"},
                    }
                },
            }
        )
        with self.assertRaises(SystemExit):
            with redirect_stderr(io.StringIO()):
                compiler.compile_registry(path, ["bootstrap-iso-debian"], repo_root=REPO_ROOT)

    def test_per_user_app_vm_compiles_to_usr_zone(self):
        path = self.write_registry(
            {
                "schema_version": 1,
                "apps": {
                    "user-shell": {
                        "enabled": True,
                        "placement": {"active_master": "boxa"},
                        "users": self.per_user_app_users()[:1],
                    }
                },
            }
        )

        with patch.object(model, "app_manifest", return_value=self.per_user_app_manifest()):
            compiled = compiler.compile_registry(path, ["user-shell"], repo_root=REPO_ROOT)

        specs = compiled["app_vm_specs"]
        self.assertEqual(len(specs), 1)
        spec = specs[0]
        self.assertEqual(spec["inventory_hostname"], "boxa-usr-alice")
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
        self.assertEqual(policy["hostname"], "boxa-usr-alice")
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
                            "placement": {"active_master": "boxa"},
                            "users": self.per_user_app_users()[:1],
                        }
                    },
                }
            )
            with patch.object(
                model,
                "app_manifest",
                return_value=self.per_user_app_manifest(zone=zone),
            ):
                with self.assertRaises(SystemExit):
                    with redirect_stderr(io.StringIO()):
                        compiler.compile_registry(path, ["user-shell"], repo_root=REPO_ROOT)

    def test_app_vm_limit_includes_backend_builder_host(self):
        specs = [{"inventory_hostname": "boxa-usr-alice"}]
        self.assertEqual(
            compiler.limit_for_app_vms(["boxa"], specs),
            "boxa-bak,boxa-dom0,boxa-usr-alice",
        )

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
                        "placement": {"active_master": "boxb"},
                        "app_vms": {
                            "runtime": {
                                "boxb": {
                                    "vm_ipv4_address": "192.168.200.30",
                                    "tailnet_tag": "tag:ops",
                                }
                            }
                        },
                    }
                },
            }
        )
        with patch.object(model, "app_manifest", return_value=manifest):
            with self.assertRaises(SystemExit):
                with redirect_stderr(io.StringIO()):
                    compiler.compile_registry(path, [], repo_root=REPO_ROOT)

    def test_missing_platform_resources_manifest_is_rejected(self):
        path = self.write_registry(
            {
                "schema_version": 1,
                "apps": {
                    "missingapp": {
                        "enabled": True,
                        "placement": {"active_master": "boxa", "passive_backup": "boxb"},
                    }
                },
            }
        )
        with self.assertRaises(SystemExit):
            with redirect_stderr(io.StringIO()):
                compiler.compile_registry(path, ["missingapp"], repo_root=REPO_ROOT)

    def test_box_access_requires_one_exact_configured_box(self):
        compiled = {"box_configs": {"boxa": {}}}
        self.assertEqual(
            compiler.selected_box_access_box(compiled, ["boxa"]), "boxa"
        )
        for requested in ([], ["boxa", "boxb"], ["boxb"]):
            with self.subTest(requested=requested):
                with self.assertRaises(SystemExit):
                    with redirect_stderr(io.StringIO()):
                        compiler.selected_box_access_box(compiled, requested)

    def test_box_access_router_vars_contain_only_selected_router_inputs(self):
        compiled = {
            "compiler_version": model.COMPILER_VERSION,
            "registry_sha256": "a" * 64,
            "managed_iot_devices": [],
            "box_configs": {
                "boxa": {
                    "access": {
                        "available_capabilities": ["overlay"],
                        "enabled_capabilities": ["overlay"],
                        "prohibited_capabilities": ["local-lan"],
                    },
                    "dhcp_reservations": [
                        {
                            "hostname": "device-a",
                            "mac": "02:00:00:00:00:01",
                            "ipv4_address": "192.0.2.1",
                        }
                    ],
                },
                "boxb": {
                    "access": model.default_box_access(),
                    "dhcp_reservations": [],
                },
            },
        }
        value = compiler.box_access_router_vars(compiled, "boxa")
        self.assertEqual(set(value), {
            "platform_resources_box_access", "router_managed_dhcp_hosts",
        })
        self.assertEqual(set(value["platform_resources_box_access"]), {"boxa"})
        self.assertEqual(
            {item["node"] for item in value["router_managed_dhcp_hosts"]},
            {"boxa"},
        )


if __name__ == "__main__":
    unittest.main()
