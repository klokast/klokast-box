#!/usr/bin/env python3
import importlib.util
import hashlib
import json
import os
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "ansible" / "bin" / "platform-map"


def load_module():
    loader = SourceFileLoader("platform_map", str(SCRIPT))
    spec = importlib.util.spec_from_loader("platform_map", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class PlatformMapTest(unittest.TestCase):
    def setUp(self):
        self.mod = load_module()

    def peer(self, hostname, tags=None, online=True):
        return {
            "name": hostname,
            "hostname": hostname,
            "dns_name": f"{hostname}.example.ts.net",
            "names": [hostname, f"{hostname}.example.ts.net"],
            "online": online,
            "tags": sorted(tags or []),
            "tailscale_ips": [],
        }

    def observation_source(self):
        peers = [
            self.peer("k001-router", ["tag:vm"]),
            self.peer("k001-dom0", ["tag:dom0"]),
        ]
        return {
            "schema_version": 1,
            "generated_at": "2026-08-10T12:00:00Z",
            "source_host": "k001-ops",
            "deployment_server": {
                "hostname": "k001-ops",
                "user": "private-user",
                "provider": {"public_ipv4": "192.0.2.10", "location": {"city": "Private"}},
            },
            "tailnet": {
                "available": True,
                "magicdns_suffix": "private.example.ts.net",
                "peers": peers,
            },
            "boxes": {
                "k001": {
                    "name": "k001",
                    "overrides": {"site": "private-site"},
                    "podman": {"bak": {"containers": [{"name": "private-container"}]}},
                    "dom0": {
                        "reachable": True,
                        "storage": {"logical_volumes": [{"name": "private-volume"}]},
                        "xen": {
                            "available": True,
                            "domains": [
                                {"name": "Domain-0", "id": 0, "memory_mib": 1024, "vcpus": 2, "state": "r-----", "time_s": "1.0"},
                                {"name": "router", "id": 1, "memory_mib": 512, "vcpus": 1, "state": "-b----", "time_s": "1.0"},
                            ],
                            "config_files": ["/etc/xen/router.cfg", "/etc/xen/bak.cfg"],
                            "autostart_files": ["/etc/xen/auto/router.cfg"],
                            "expected_guests": {"router": {"private": "desired-state"}},
                        },
                    },
                    "findings": [{"message": "private log-like detail"}],
                }
            },
            "findings": [],
            "warnings": ["private warning"],
            "artifact_dir": "/private/path",
        }

    def write_source(self, value):
        directory = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(directory))
        path = directory / "current.json"
        path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return path

    def test_observation_export_is_deterministic_redacted_and_separates_xen_state(self):
        source = self.observation_source()
        path = self.write_source(source)
        first = self.mod.export_observation(path)
        second = self.mod.export_observation(path)
        self.assertEqual(first, second)
        self.assertEqual(first["source_map_sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(first["boxes"][0]["configured_guests"], ["bak", "router"])
        self.assertEqual(first["boxes"][0]["autostart_guests"], ["router"])
        self.assertEqual(first["boxes"][0]["running_guests"], ["router"])
        encoded = json.dumps(first, sort_keys=True)
        for sensitive in ("private-user", "192.0.2.10", "Private", "private-container", "private-volume", "desired-state", "/private/path"):
            self.assertNotIn(sensitive, encoded)
        unhashed = dict(first)
        generation = unhashed.pop("generation_sha256")
        self.assertEqual(generation, self.mod.canonical_observation_hash(unhashed))

    def test_observation_export_rejects_duplicate_tailnet_identity(self):
        source = self.observation_source()
        source["tailnet"]["peers"].append(self.peer("k001-router", ["tag:vm"]))
        with self.assertRaises(SystemExit):
            self.mod.export_observation(self.write_source(source))

    def test_observation_export_rejects_ambiguous_controller_and_bad_xen_path(self):
        source = self.observation_source()
        source["deployment_server"]["hostname"] = "k002-ops"
        with self.assertRaises(SystemExit):
            self.mod.export_observation(self.write_source(source))
        source = self.observation_source()
        source["boxes"]["k001"]["dom0"]["xen"]["autostart_files"] = ["/etc/xen/router.cfg"]
        with self.assertRaises(SystemExit):
            self.mod.export_observation(self.write_source(source))

    def test_observation_export_rejects_source_symlink(self):
        target = self.write_source(self.observation_source())
        link = target.parent / "link.json"
        link.symlink_to(target)
        with self.assertRaises(SystemExit):
            self.mod.export_observation(link)

    def tailnet_index(
        self,
        include_app_vm=True,
        app_vm_tags=None,
        include_usr=True,
        include_ops=True,
        include_oob=False,
        extra_peers=None,
    ):
        peers = [
            self.peer("k001-dom0", ["tag:dom0"]),
            self.peer("k001-router", ["tag:vm"]),
            self.peer("k001-bak", ["tag:vm"]),
            self.peer("k001-dmz", ["tag:vm"]),
            self.peer("k001-iot", ["tag:vm"]),
        ]
        if include_usr:
            peers.append(self.peer("k001-usr", ["tag:vm"]))
        if include_ops:
            peers.append(self.peer("k001-ops", ["tag:ops"]))
        if include_oob:
            peers.append(self.peer("oob", ["tag:oob"]))
        if include_app_vm:
            peers.append(
                self.peer(
                    "k001-usr-alice",
                    app_vm_tags or ["tag:user-shell-alice", "tag:vm"],
                )
            )
        peers.extend(extra_peers or [])
        return self.mod.peer_index({"peers": peers})

    def dom0_fact(self, include_usr=True, include_ops=True, include_app_domain=True):
        expected_guests = {
            "router": {"guest_name": "router", "memory_mb": 512, "vcpus": 1, "autostart": True},
            "bak": {"guest_name": "bak", "memory_mb": 1024, "vcpus": 1, "autostart": True},
            "dmz": {"guest_name": "dmz", "memory_mb": 1024, "vcpus": 1, "autostart": True},
            "iot": {"guest_name": "iot", "memory_mb": 1024, "vcpus": 1, "autostart": True},
        }
        live_domains = [
            "Name                                        ID   Mem VCPUs      State   Time(s)",
            "Domain-0                                     0  1024     2     r-----      10.0",
            "router                                       1   512     1     -b----       1.0",
            "bak                                          2  1024     1     -b----       1.0",
            "dmz                                          3  1024     1     -b----       1.0",
            "iot                                          4  1024     1     -b----       1.0",
        ]
        if include_usr:
            expected_guests["usr"] = {"guest_name": "usr", "memory_mb": 1024, "vcpus": 1, "autostart": True}
            live_domains.append("usr                                        5  1024     1     -b----       1.0")
        if include_ops:
            expected_guests["ops"] = {"guest_name": "ops", "memory_mb": 4096, "vcpus": 2, "autostart": True}
            live_domains.append("ops                                          6  4096     2     -b----       1.0")
        if include_app_domain:
            live_domains.append("usr-alice                                    7  4096     4     -b----       1.0")
        return {
            "inventory_hostname": "k001-dom0",
            "xen": {
                "expected_guests": expected_guests,
                "xl_info_rc": "0",
                "xl_info_stdout": "total_memory           : 32768\nfree_memory            : 8192\n",
                "xl_list_stdout": "\n".join(live_domains),
            },
        }

    def expected_app_vms(self):
        return {
            "k001-usr-alice": {
                "node": "k001",
                "app": "user-shell",
                "resource": "user-runtime",
                "hostname": "k001-usr-alice",
                "tailnet_hostname": "k001-usr-alice",
                "guest_name": "usr-alice",
                "user_slug": "alice",
                "tailscale_login": "alice@example.com",
                "site_role": "active",
                "zone": "usr",
                "vm_ipv4_address": "192.168.175.20",
                "expected_tags": ["tag:user-shell-alice", "tag:vm"],
                "memory_mb": 4096,
                "vcpus": 4,
                "autostart": True,
            }
        }

    def summarize(
        self,
        expected_app_vms=None,
        expected_tailnet_hostnames=None,
        app_vm_tags=None,
        include_app_vm=True,
        include_usr=True,
        include_ops=True,
        include_oob=False,
        extra_peers=None,
    ):
        return self.mod.summarize_box(
            "k001",
            tailnet_index=self.tailnet_index(
                include_app_vm=include_app_vm,
                app_vm_tags=app_vm_tags,
                include_usr=include_usr,
                include_ops=include_ops,
                include_oob=include_oob,
                extra_peers=extra_peers,
            ),
            remote_facts={"k001-dom0": self.dom0_fact(include_usr=include_usr, include_ops=include_ops)},
            overrides={},
            expected_app_vms=expected_app_vms or {},
            expected_tailnet_hostnames=expected_tailnet_hostnames or set(),
        )

    def test_expected_app_vm_xen_domain_validates_cleanly(self):
        summary = self.summarize(expected_app_vms=self.expected_app_vms())
        codes = [item["code"] for item in summary["findings"]]
        self.assertNotIn("unmanaged_xen_domain", codes)
        self.assertNotIn("unexpected_tailscale_machine", codes)
        self.assertTrue(summary["app_vms"]["k001-usr-alice"]["tailscale"]["tags_ok"])
        self.assertTrue(summary["app_vms"]["k001-usr-alice"]["xen"]["running"])

    def test_stopped_expected_app_vm_can_be_offline_and_absent(self):
        expected = self.expected_app_vms()
        expected["k001-usr-alice"]["runtime_state"] = "stopped"
        expected["k001-usr-alice"]["autostart"] = False
        summary = self.mod.summarize_box(
            "k001",
            tailnet_index=self.tailnet_index(include_app_vm=False),
            remote_facts={
                "k001-dom0": self.dom0_fact(include_app_domain=False),
            },
            overrides={},
            expected_app_vms=expected,
            expected_tailnet_hostnames=set(),
        )
        codes = [item["code"] for item in summary["findings"]]
        self.assertNotIn("missing_tailscale_machine", codes)
        self.assertNotIn("offline_tailscale_machine", codes)
        self.assertNotIn("missing_xen_domain", codes)
        self.assertFalse(summary["app_vms"]["k001-usr-alice"]["xen"]["running"])

    def test_stopped_shared_guest_can_be_offline_and_absent(self):
        fact = self.dom0_fact(include_app_domain=False)
        fact["xen"]["xl_list_stdout"] = "\n".join(
            line
            for line in fact["xen"]["xl_list_stdout"].splitlines()
            if not line.startswith("iot ")
        )
        index = self.tailnet_index(include_app_vm=False)
        index = {
            key: value
            for key, value in index.items()
            if "k001-iot" not in key
        }
        summary = self.mod.summarize_box(
            "k001",
            tailnet_index=index,
            remote_facts={"k001-dom0": fact},
            overrides={},
            expected_app_vms={},
            expected_tailnet_hostnames=set(),
            expected_shared_guests={
                "iot": {"runtime_state": "stopped", "autostart": False}
            },
        )
        codes = [item["code"] for item in summary["findings"]]
        self.assertNotIn("missing_tailscale_machine", codes)
        self.assertNotIn("offline_tailscale_machine", codes)
        self.assertNotIn("missing_xen_domain", codes)
        self.assertEqual(summary["machines"]["iot"]["runtime_state"], "stopped")
        self.assertEqual(
            summary["capacity"]["memory"]["expected_guest_memory_mib"],
            512 + 1024 + 1024 + 1024 + 4096,
        )

    def test_running_stopped_shared_guest_is_reported(self):
        summary = self.mod.summarize_box(
            "k001",
            tailnet_index=self.tailnet_index(include_app_vm=False),
            remote_facts={"k001-dom0": self.dom0_fact(include_app_domain=False)},
            overrides={},
            expected_shared_guests={
                "iot": {"runtime_state": "stopped", "autostart": False}
            },
        )
        codes = [item["code"] for item in summary["findings"]]
        self.assertIn("shared_guest_running_while_stopped", codes)

    def test_platform_map_app_vm_plan_preserves_runtime_state(self):
        plan = {
            "platform_map": {
                "app_vms": [
                    {
                        "node": "k001",
                        "hostname": "k001-usr-alice",
                        "tailnet_hostname": "k001-usr-alice",
                        "guest_name": "usr-alice",
                        "runtime_state": "stopped",
                    }
                ]
            }
        }
        app_vms = self.mod.platform_map_app_vms_from_plan(plan)
        self.assertEqual(app_vms[0]["runtime_state"], "stopped")

    def test_absent_optional_ops_vm_does_not_create_missing_findings(self):
        summary = self.summarize(include_ops=False, include_app_vm=False)
        scopes = [item["scope"] for item in summary["findings"]]
        self.assertNotIn("k001-ops", scopes)
        self.assertNotIn("ops", summary["machines"])

    def test_absent_optional_usr_vm_does_not_create_missing_findings(self):
        summary = self.summarize(
            include_usr=False,
            include_ops=False,
            include_app_vm=False,
        )
        scopes = [item["scope"] for item in summary["findings"]]
        self.assertNotIn("k001-usr", scopes)
        self.assertNotIn("usr", summary["machines"])

    def test_unknown_oob_physical_connection_is_informational(self):
        summary = self.summarize(include_app_vm=False, include_oob=True)
        codes = [item["code"] for item in summary["findings"]]
        self.assertNotIn("oob_connected_box_unknown", codes)

    def test_unexpected_app_vm_xen_domain_is_reported_without_compiled_state(self):
        summary = self.summarize()
        findings = [
            item
            for item in summary["findings"]
            if item["code"] == "unmanaged_xen_domain"
        ]
        self.assertEqual(len(findings), 1)
        self.assertIn("usr-alice", findings[0]["message"])

    def test_app_vm_tailnet_tags_are_checked_against_compiled_expectations(self):
        summary = self.summarize(
            expected_app_vms=self.expected_app_vms(),
            app_vm_tags=["tag:vm"],
        )
        findings = [
            item
            for item in summary["findings"]
            if item["code"] == "app_vm_tailnet_tags_mismatch"
        ]
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["missing_tags"], ["tag:user-shell-alice"])

    def test_expected_app_vms_can_be_loaded_from_platform_resource_plan(self):
        plan = {
            "platform_map": {
                "app_vms": [
                    {
                        "node": "k001",
                        "app": "user-shell",
                        "resource": "user-runtime",
                        "hostname": "k001-usr-alice",
                        "tailnet_hostname": "k001-usr-alice",
                        "guest_name": "usr-alice",
                        "user_slug": "alice",
                        "expected_tags": ["tag:vm", "tag:user-shell-alice"],
                        "memory_mb": 4096,
                        "vcpus": 4,
                        "autostart": True,
                    }
                ]
            }
        }
        by_box = self.mod.expected_app_vms_by_box_from_plans([plan])
        self.assertEqual(by_box["k001"]["k001-usr-alice"]["guest_name"], "usr-alice")
        self.assertEqual(
            by_box["k001"]["k001-usr-alice"]["expected_tags"],
            ["tag:user-shell-alice", "tag:vm"],
        )

    def test_expected_resource_tailnet_hosts_are_not_unexpected(self):
        summary = self.summarize(
            include_app_vm=False,
            include_usr=False,
            include_ops=False,
            expected_tailnet_hostnames={"k001-audio", "k001-music"},
            extra_peers=[
                self.peer("k001-audio", ["tag:streamer"]),
                self.peer("k001-music", ["tag:music"]),
            ],
        )
        findings = [
            item
            for item in summary["findings"]
            if item["code"] == "unexpected_tailscale_machine"
        ]
        self.assertEqual(findings, [])

    def test_expected_shared_guests_load_from_latest_platform_resource_plan(self):
        plans = [
            {
                "platform_map": {
                    "shared_guests": [
                        {
                            "node": "k001",
                            "role": "iot",
                            "runtime_state": "running",
                            "autostart": True,
                        }
                    ]
                }
            },
            {
                "platform_map": {
                    "shared_guests": [
                        {
                            "node": "k001",
                            "role": "iot",
                            "runtime_state": "stopped",
                            "autostart": False,
                        }
                    ]
                }
            },
        ]
        states = self.mod.expected_shared_guests_by_box_from_plans(plans)
        self.assertEqual(states["k001"]["iot"]["runtime_state"], "stopped")

    def test_airunner_runtime_names_are_not_unexpected(self):
        summary = self.summarize(
            include_app_vm=False,
            extra_peers=[
                self.peer("k001-ops-airunner", ["tag:airunner"]),
                self.peer("k001-ops-airunner-candidate", ["tag:airunner"]),
            ],
        )
        findings = [
            item
            for item in summary["findings"]
            if item["code"] == "unexpected_tailscale_machine"
        ]
        self.assertEqual(findings, [])

    def test_airunner_name_with_wrong_tag_is_unexpected(self):
        summary = self.summarize(
            include_app_vm=False,
            extra_peers=[
                self.peer("k001-ops-airunner-candidate", ["tag:infra"]),
            ],
        )
        findings = [
            item
            for item in summary["findings"]
            if item["code"] == "unexpected_tailscale_machine"
        ]
        self.assertEqual(len(findings), 1)

    def test_expected_tailnet_hosts_load_from_platform_resource_plan(self):
        plan = {
            "tailnet_resources": [
                {"node": "k001", "hostname": "k001-music"},
                {"node": "", "hostname": "photos"},
                {"node": "k001", "hostname": "{box}-usr-{slug}"},
            ],
            "platform_map": {
                "managed_iot_devices": [
                    {"node": "k001", "hostname": "k001-audio"},
                ],
            },
        }
        by_box = self.mod.expected_tailnet_hostnames_by_box_from_plans([plan])
        self.assertEqual(by_box["k001"], {"k001-audio", "k001-music"})

    def test_app_podman_containers_are_managed_from_desired_json(self):
        fact = {
            "inventory_hostname": "k001-bak",
            "podman": {
                "ps_stdout": "\n".join(
                    [
                        '{"Names":["static-site-web"],"Pod":"","Image":"web","State":"running"}',
                        '{"Names":["e7ab-infra"],"Pod":"pod-1","Image":"infra","State":"running"}',
                    ]
                ),
                "pods_stdout": '[{"Id":"pod-1","Name":"klokast-music"}]',
                "volumes_stdout": "[]",
                "info_stdout": "{}",
                "info_rc": "0",
            },
            "platform_resources": {
                "desired_json_stdout": (
                    '{"apps":{"static-site":{"enabled":true},"music":{"enabled":true}}}'
                ),
            },
        }
        summary = self.mod.summarize_podman_fact(fact)
        managed_by_name = {container["name"]: container["managed"] for container in summary["containers"]}
        self.assertTrue(managed_by_name["static-site-web"])
        self.assertTrue(managed_by_name["e7ab-infra"])

    def test_ansible_control_path_dir_stays_short(self):
        path = self.mod.ansible_ssh_control_path_dir()
        self.assertEqual(path, Path("/tmp") / f"klokast-platform-map-ssh-{os.getuid()}")
        self.assertLess(len(str(path / "31b2341e46.YQuNWlZVxP8dQpgh")), 100)


if __name__ == "__main__":
    unittest.main()
