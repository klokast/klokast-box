"""Box access policy, capabilities, shared guests and box-level resources."""
from platform_resource_test_support import ResourceTestCase, REPO_ROOT
import io
import unittest
from contextlib import redirect_stderr
import platform_resource_model as model
import platform_resource_compiler as compiler


class BoxAccessTest(ResourceTestCase):
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
