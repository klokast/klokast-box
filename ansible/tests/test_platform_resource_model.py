"""Resource model regression tests."""
from platform_resource_test_support import ResourceTestCase, REPO_ROOT
import io
import unittest
from contextlib import redirect_stderr
import platform_resource_model as model
import platform_resource_guests as guests


class ResourceModelTest(ResourceTestCase):
    def test_shared_usr_compute_is_rejected(self):
        manifest = self.per_user_app_manifest()
        manifest['resources']['compute'] = [{'id': 'runtime', 'type': 'podman_workload', 'zone': 'usr'}]
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            model.validate_manifest_resources('user-shell', manifest, model.load_topology(repo_root=REPO_ROOT))

    def test_reserved_manifest_is_rejected_before_loading(self):
        for reserved in ('platform', 'doctor'):
            stderr = io.StringIO()
            with redirect_stderr(stderr), self.assertRaises(SystemExit):
                model.app_manifest(reserved, repo_root=REPO_ROOT)
            self.assertIn(f'application name {reserved} is reserved for kk {reserved}', stderr.getvalue())

    def test_reserved_manifest_id_is_rejected(self):
        for reserved in ('platform', 'doctor'):
            stderr = io.StringIO()
            with redirect_stderr(stderr), self.assertRaises(SystemExit):
                model.validate_manifest_resources('sample', {'app': reserved}, model.load_topology(repo_root=REPO_ROOT))
            self.assertIn(f'application name {reserved} is reserved for kk {reserved}', stderr.getvalue())

    def test_dedicated_app_cannot_recreate_fixed_usr_name(self):
        manifest = {'resources': {'compute': [{'id': 'runtime', 'type': 'app_vm', 'zone': 'usr',
                                             'hostname_suffix': 'usr', 'guest_os': 'alpine'}]}}
        entry = {'enabled': True, 'app_vms': {'runtime': {'boxa': {'vm_ipv4_address': '192.168.175.20'}}}}
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            guests.selected_app_vms('user-shell', manifest, entry, ['boxa'], model.load_topology(repo_root=REPO_ROOT))

    def test_legacy_raw_topology_fields_are_rejected(self):
        topology = model.load_topology(repo_root=REPO_ROOT)
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
                model.validate_network_resource_shape("badapp", resource, topology)

    def test_app_manifest_cannot_declare_tailnet_tag_ownership(self):
        topology = model.load_topology(repo_root=REPO_ROOT)
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
                model.validate_manifest_resources("badapp", manifest, topology)

    def test_app_manifest_cannot_use_reserved_control_tailnet_tag(self):
        topology = model.load_topology(repo_root=REPO_ROOT)
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
                model.validate_manifest_resources("badapp", manifest, topology)

    def test_app_manifest_cannot_default_app_vm_to_reserved_control_tailnet_tag(self):
        topology = model.load_topology(repo_root=REPO_ROOT)
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
                model.validate_manifest_resources("badapp", manifest, topology)

    def test_app_manifest_cannot_place_privileged_builder_directly(self):
        topology = model.load_topology(repo_root=REPO_ROOT)
        manifest = {
            "_manifest_path": "test",
            "resources": {
                "compute": [
                    {
                        "id": "bad-builder",
                        "type": "ephemeral_privileged_builder",
                        "zone": "bak",
                        "builder_host": "boxa-bak",
                    }
                ]
            },
        }
        with self.assertRaises(SystemExit):
            with redirect_stderr(io.StringIO()):
                model.validate_manifest_resources("badapp", manifest, topology)

    def test_app_manifest_rejects_unknown_resource_section(self):
        topology = model.load_topology(repo_root=REPO_ROOT)
        manifest = {
            "_manifest_path": "test",
            "resources": {
                "network": [],
                "sudoers": [{"id": "bad"}],
            },
        }
        with self.assertRaises(SystemExit):
            with redirect_stderr(io.StringIO()):
                model.validate_manifest_resources("badapp", manifest, topology)

    def test_app_manifest_accepts_a_strict_dataset_catalog_entry(self):
        topology = model.load_topology(repo_root=REPO_ROOT)
        manifest = {
            "_manifest_path": "test",
            "datasets": [
                {
                    "id": "library",
                    "type": "durable_user_data",
                    "rationale": "Preserve user media after service removal.",
                }
            ],
            "resources": {},
        }
        model.validate_manifest_resources("music", manifest, topology)

    def test_app_manifest_rejects_a_duplicate_dataset_id(self):
        topology = model.load_topology(repo_root=REPO_ROOT)
        dataset = {
            "id": "library",
            "type": "durable_user_data",
            "rationale": "Preserve user media after service removal.",
        }
        manifest = {
            "_manifest_path": "test",
            "datasets": [dataset, dict(dataset)],
            "resources": {},
        }
        with self.assertRaises(SystemExit):
            with redirect_stderr(io.StringIO()):
                model.validate_manifest_resources("music", manifest, topology)

    def test_app_manifest_rejects_unknown_compute_field(self):
        topology = model.load_topology(repo_root=REPO_ROOT)
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
                model.validate_manifest_resources("badapp", manifest, topology)

    def test_app_manifest_rejects_unknown_tailnet_grant_field(self):
        topology = model.load_topology(repo_root=REPO_ROOT)
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
                model.validate_manifest_resources("badapp", manifest, topology)

    def test_unknown_zone_is_rejected(self):
        topology = model.load_topology(repo_root=REPO_ROOT)
        resource = {
            "id": "bad-flow",
            "type": "interzone_tcp",
            "from_zone": "internet",
            "to_zone": "bak",
            "ports": [443],
        }
        with self.assertRaises(SystemExit):
            with redirect_stderr(io.StringIO()):
                model.validate_network_resource_shape("badapp", resource, topology)

    def test_ops_role_hostname_is_not_accepted_as_box_name(self):
        with self.assertRaises(SystemExit):
            with redirect_stderr(io.StringIO()):
                model.validate_box("boxa-ops", "apps.example.placement.active_master")


if __name__ == "__main__":
    unittest.main()
