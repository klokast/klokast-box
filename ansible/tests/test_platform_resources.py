"""Resource compiler tests for each application's declared resources."""
from platform_resource_test_support import ResourceTestCase, REPO_ROOT
import io
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch
import yaml
import platform_resource_model as model
import platform_resource_compiler as compiler
import platform_resource_runtime as runtime
from pathlib import Path


class ResourceCompilerTest(ResourceTestCase):
    def test_lan_printing_requires_capability_and_compiles_reconstructable_files(self):
        registry = {'schema_version': 1, 'boxes': {'boxa': {
            'rg_lan': {'ipv4_cidr': '192.168.1.0/24'},
            'access': {'available_capabilities': ['overlay', 'rg-lan'], 'enabled_capabilities': ['overlay', 'rg-lan']}}},
            'apps': {'print-server': {'enabled': True, 'placement': {'boxes': ['boxa']},
                'app_vms': {'cups': {'boxa': {'vm_ipv4_address': '192.168.100.30'}}},
                'resources': {'lan-printing': True}, 'devices': {'printer': {'boxa': {
                    'hostname': 'boxa-iot-printer', 'ipv4_address': '192.168.150.78', 'mac': '02:00:00:00:00:78'}}}}}}
        registry['boxes']['boxb'] = {}
        path = self.write_registry(registry)
        compiled = compiler.compile_registry(path, [], repo_root=REPO_ROOT)
        self.assertEqual(compiled['lan_print_services'][0]['hostname'], 'boxa-print')
        self.assertFalse(any(r.get('resource') == 'app-vm-bootstrap-ssh' for r in compiled['app_resource_claims']))
        self.assertEqual(compiled['app_vm_specs'][0]['advertised_tags'], ['tag:print'])
        self.assertEqual(compiled['app_vm_specs'][0]['user']['system_user'], '')
        output = Path(path).parent / 'print-management.yml'
        runtime.render_app_vm_inventory(compiled, output, 'example.ts.net')
        self.assertEqual(yaml.safe_load(output.read_text())['all']['hosts'], {})
        self.assertEqual(set(compiled['lan_print_router_files']), {'boxa', 'boxb'})
        self.assertNotIn('etc/avahi/services/klokast-printer.service', compiled['lan_print_router_files']['boxb'])
        self.assertEqual(compiled['tailnet_resources'][0]['hostname'], 'boxa-print')
        transport = [c for c in compiled['app_resource_claims'] if c['resource'] == 'print-transport']
        self.assertTrue(transport)
        for claim in transport:
            self.assertIn('192.168.0.0/16', claim['normalized']['destination_exclusions'])
        files = compiled['lan_print_router_files']['boxa']
        advertisement = files['etc/avahi/services/klokast-printer.service']
        self.assertIn('_universal._sub._ipp._tcp', advertisement)
        self.assertNotIn('_cups._sub._ipp._tcp', advertisement)
        self.assertIn('pdl=application/pdf,image/urf', advertisement)
        self.assertIn('URF=V1.4,CP1,W8,PQ3-4-5,SRGB24,RS360,DM1,FN3', advertisement)
        self.assertIn('dnat to 192.168.100.30:631', files['etc/klokast/app-resources/lan-print-nat.nft'])
        self.assertTrue(any(row['normalized']['ports'] == [631] for row in compiled['app_resource_claims']))
        registry['apps']['print-server']['resources']['lan-printing'] = False
        path = self.write_registry(registry)
        disabled = compiler.compile_registry(path, [], repo_root=REPO_ROOT)
        self.assertEqual(disabled['lan_print_services'], [])
        self.assertNotIn('etc/avahi/services/klokast-printer.service', disabled['lan_print_router_files']['boxa'])
        registry['apps']['print-server']['resources']['lan-printing'] = True
        registry['boxes']['boxa']['access']['enabled_capabilities'] = ['overlay']
        path = self.write_registry(registry)
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            compiler.compile_registry(path, [], repo_root=REPO_ROOT)

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
        self.assertEqual(",".join(compiler.resource_hosts_for_scope(compiled)), "boxa-router")

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

    def test_print_server_compiles_backend_to_iot_printer_resources(self):
        path = self.write_registry(
            {
                "schema_version": 1,
                "apps": {
                    "print-server": {
                        "enabled": True,
                        "placement": {"boxes": ["boxb"]},
                        "app_vms": {"cups": {"boxb": {"vm_ipv4_address": "192.168.100.30"}}},
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
        self.assertEqual(compiled["apps"]["print-server"]["resources"], ["print-transport", "printer-ipp"])
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
            compiled, "app-print-server-printer-ipp-cups-router"
        )
        self.assertEqual(len(router_claims), 1)
        self.assertEqual(router_claims[0]["normalized"]["in_interface"], "eth3")
        self.assertEqual(router_claims[0]["normalized"]["out_interface"], "eth4")
        self.assertEqual(router_claims[0]["normalized"]["source"], "192.168.100.30")
        self.assertEqual(router_claims[0]["normalized"]["destination"], "192.168.150.78")
        self.assertEqual(router_claims[0]["normalized"]["ports"], [631])
        self.assertEqual(
            [(item["hostname"], item["tag"]) for item in compiled["tailnet_resources"]],
            [("boxb-print", "tag:print")],
        )

    def test_exit_node_compiles_public_only_dmz_web_egress(self):
        path = self.write_registry(
            {
                "schema_version": 1,
                "apps": {
                    "exit-node": {
                        "enabled": True,
                        "placement": {"boxes": ["boxb"]},
                        "app_vms": {"exit": {"boxb": {"vm_ipv4_address": "192.168.200.50"}}},
                        "resources": {},
                    }
                },
            }
        )
        compiled = compiler.compile_registry(path, ["exit-node"], repo_root=REPO_ROOT)
        self.assertEqual(compiled["apps"]["exit-node"]["resources"], ["exit-egress"])
        specs = [spec for spec in compiled["app_vm_specs"] if spec["app"] == "exit-node"]
        self.assertEqual([(spec["node_hostname"], spec["node_domain_role"]) for spec in specs],
                         [("boxb-exit", "dmz")])
        # A headless appliance: fixed image workflow, no guest account or tag:vm.
        self.assertEqual(specs[0]["advertised_tags"], ["tag:exit"])
        self.assertEqual(specs[0]["user"]["system_user"], "")
        self.assertIn(" ro ", specs[0]["guest_spec"]["installed"]["extra"] + " ")
        claims = [claim for claim in compiled["app_resource_claims"]
                  if claim["app"] == "exit-node" and claim["resource"] == "exit-egress"]
        self.assertEqual(sorted((c["normalized"]["protocol"], tuple(c["normalized"]["ports"])) for c in claims),
                         [("tcp", (80, 443)), ("udp", (443, 3478, 41641))])
        for claim in claims:
            self.assertEqual(claim["normalized"]["source"], "192.168.200.50")
            self.assertEqual(claim["normalized"]["destination_exclusions"],
                             list(compiler.NON_PUBLIC_DESTINATIONS))
        self.assertEqual(
            [(item["hostname"], item["tag"]) for item in compiled["tailnet_resources"]],
            [("boxb-exit", "tag:exit")],
        )

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


class DeclaredContainersTest(unittest.TestCase):
    def test_a_workload_lists_distinct_container_names_in_its_zone(self):
        topology = model.load_topology(repo_root=REPO_ROOT)
        base = {"id": "web", "type": "podman_workload", "zone": "dmz", "privilege": "rootless", "lifecycle": "steady"}
        model.validate_compute_resource_shape("demo", dict(base, containers=["demo-web", "demo-db"]), topology)
        for containers in (["demo-web", "demo-web"], ["Demo_web"], "demo-web", ["demo web"]):
            with self.subTest(containers=containers), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                model.validate_compute_resource_shape("demo", dict(base, containers=containers), topology)
        without_zone = {k: v for k, v in base.items() if k != "zone"}
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            model.validate_compute_resource_shape("demo", dict(without_zone, containers=["demo-web"]), topology)

    def test_the_catalog_lists_the_containers_of_each_fixed_container_application(self):
        for app in ("immich", "music", "nextcloud", "static-site"):
            manifest = model.app_manifest(app, repo_root=REPO_ROOT)
            names = [n for r in manifest["resources"]["compute"] for n in r.get("containers", [])]
            with self.subTest(app=app):
                self.assertTrue(names)
                self.assertTrue(all(n.startswith(app + "-") for n in names))


if __name__ == "__main__":
    unittest.main()
