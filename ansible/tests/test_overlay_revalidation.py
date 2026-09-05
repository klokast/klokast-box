"""Approval-interval regression tests; fixtures contain no deployment state."""
import copy
import importlib.util
import json
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]


class OverlayRevalidationTest(unittest.TestCase):
    def setUp(self):
        loader = SourceFileLoader("overlay_apply_test", str(ROOT / "klokast-ops/secret-authority/bin/ksa-apply"))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        self.mod = importlib.util.module_from_spec(spec)
        loader.exec_module(self.mod)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.binding, self.evidence, self.intent = {}, {}, {}
        self.intent["nonce"] = "overlay_test_nonce"
        self.docs = {}
        for role in ("router_preimage", "ops_preimage"):
            paths = {"/etc/network/interfaces", "/etc/sysctl.d/91-klokast-ops-ipv6.conf"}
            if role == "router_preimage":
                paths.update({"/etc/dhcpcd.conf", "/etc/dnsmasq.conf", "/etc/dnsmasq.d/91-klokast-ops-ipv6.conf", "/etc/klokast/overlay-ipv6.nft", "/etc/network/if-up.d/91-klokast-ops-ipv6"})
            else:
                paths.add("/etc/klokast/overlay-ipv6-input.nft")
            self.docs[role] = {
                "schema_version": 1,
                "kind": f"klokast.overlay-ipv6-{role.removesuffix('_preimage')}-preimage.v1",
                "box": "boxa",
                "files": {path: [False, "", "0600"] for path in paths},
                "runtime": "forwarding=0\naccept_ra=1\nwan_addresses_begin\n"
                "2: eth0    inet6 2606:4700::1/64 scope global dynamic mngtmpaddr \\\n"
                "       valid_lft 7200sec preferred_lft 3600sec\nwan_addresses_end",
                "nft_ruleset": 'table inet filter {\n\tchain input {\n\t\tudp dport 41641 counter packets 10 bytes 1000 accept comment "counter packets 10 bytes 1000"\n\t}\n}',
            }
            # ip -o replaces embedded newlines with a literal backslash.
            self.docs[role]["runtime"] = self.docs[role]["runtime"].replace("\\\n", "\\")
        self.docs["huawei_prerequisite"] = {
            "schema_version": 1, "kind": "klokast.huawei-ipv6-pinhole-prerequisite.v1",
            "active_router": "boxa-router", "peer_router": "boxb-router",
            "peer_global_ipv6": "2606:4700::2", "udp_port": 41641,
            "direct_ping": "pong from boxb-router (100.64.0.2) via [2606:4700::2]:41641 in 291ms",
        }
        for role, doc in self.docs.items():
            stored = self.directory / (role + "-stored.json")
            stored.write_text(json.dumps(doc, indent=4))
            self.binding[role + "_path"] = str(stored)
            self.intent[role + "_sha256"] = self.mod.sha256_file(stored)
            self.write_fresh(role, doc)

    def write_fresh(self, role, doc):
        path = self.directory / (role + "-fresh.json")
        path.write_text(json.dumps(doc, indent=4))
        self.evidence[role + "_path"] = str(path)
        self.evidence[role + "_sha256"] = self.mod.sha256_file(path)

    def compare(self):
        return self.mod.compare_overlay_evidence(self.binding, self.evidence, self.intent, "boxa", "boxb", "example.ts.net")

    def traffic_during_review(self):
        for role, doc in self.docs.items():
            doc = copy.deepcopy(doc)
            if role == "huawei_prerequisite":
                doc["direct_ping"] = doc["direct_ping"].replace("291ms", "192.5ms")
            else:
                doc["runtime"] = doc["runtime"].replace("7200sec", "6900sec").replace("3600sec", "3300sec")
                doc["nft_ruleset"] = doc["nft_ruleset"].replace("counter packets 10 bytes 1000 accept", "counter packets 50 bytes 5000 accept")
            self.write_fresh(role, doc)

    def test_review_delay_passes_without_rewriting_signed_evidence(self):
        originals = {key: Path(path).read_bytes() for key, path in self.binding.items()}
        self.traffic_during_review()
        self.compare()
        for key, path in self.binding.items():
            self.assertEqual(Path(path).read_bytes(), originals[key])
        self.assertNotEqual(self.intent["ops_preimage_sha256"], self.evidence["ops_preimage_sha256"])

    def test_configuration_and_unknown_field_changes_fail(self):
        role = "router_preimage"
        changes = [
            ("box", "boxb"), ("extra", True), ("schema_version", True),
            ("runtime", self.docs[role]["runtime"].replace("accept_ra=1", "accept_ra=2")),
            ("runtime", self.docs[role]["runtime"].replace("eth0", "eth1")),
            ("runtime", self.docs[role]["runtime"].replace("::1/64", "::3/64")),
            ("runtime", self.docs[role]["runtime"].replace("/64", "/80")),
            ("runtime", self.docs[role]["runtime"].replace("dynamic", "temporary")),
            ("runtime", "forwarding=0\naccept_ra=1"),
            ("nft_ruleset", self.docs[role]["nft_ruleset"].replace("accept", "drop")),
            ("nft_ruleset", self.docs[role]["nft_ruleset"].replace("41641", "41642")),
            ("nft_ruleset", self.docs[role]["nft_ruleset"] + "\nunknown output"),
        ]
        for field, value in changes:
            with self.subTest(field=field, value=value):
                self.write_fresh(role, dict(self.docs[role], **{field: value}))
                with self.assertRaises(self.mod.ApplyError):
                    self.compare()

    def test_file_content_modes_and_scope_stay_exact(self):
        for change in ("content", "mode", "added", "removed"):
            doc = copy.deepcopy(self.docs["ops_preimage"])
            if change == "content":
                doc["files"]["/etc/network/interfaces"] = [True, "YQ==", "0600"]
            elif change == "mode":
                doc["files"]["/etc/network/interfaces"][2] = "0644"
            elif change == "added":
                doc["files"]["/etc/unknown"] = [False, "", "0600"]
            else:
                del doc["files"]["/etc/network/interfaces"]
            self.write_fresh("ops_preimage", doc)
            with self.subTest(change=change), self.assertRaises(self.mod.ApplyError):
                self.compare()

    def test_bad_lifetimes_and_address_flags_fail(self):
        baseline = self.docs["router_preimage"]
        for old, new in (
            ("3600sec", "59sec"), ("3600sec", "0sec"), ("3600sec", "3601sec"),
            ("3600sec", "2999sec"), ("3600sec", "forever"), ("7200sec", "3500sec"),
            ("3600sec", "-1sec"), ("3600sec", "unknown"),
            ("dynamic", "deprecated"), ("dynamic", "tentative"), ("dynamic", "dadfailed"),
        ):
            self.write_fresh("router_preimage", dict(baseline, runtime=baseline["runtime"].replace(old, new)))
            with self.subTest(new=new), self.assertRaises(self.mod.ApplyError):
                self.compare()
        text = baseline["runtime"].replace("7200sec", "forever").replace("3600sec", "forever")
        self.assertEqual(self.mod.overlay_runtime_view(text)[1], [None, None])

    def test_lifetime_countdown_boundaries(self):
        baseline = self.docs["router_preimage"]
        self.write_fresh("router_preimage", dict(baseline, runtime=baseline["runtime"].replace("7200sec", "6600sec").replace("3600sec", "3000sec")))
        self.compare()
        text = baseline["runtime"].replace("7200sec", "60sec").replace("3600sec", "60sec")
        self.assertEqual(self.mod.overlay_runtime_view(text)[1], [60, 60])

    def test_counters_may_increase_but_not_reset(self):
        baseline = self.docs["ops_preimage"]
        for phrase in ("counter packets 9 bytes 1000", "counter packets 10 bytes 999"):
            self.write_fresh("ops_preimage", dict(baseline, nft_ruleset=baseline["nft_ruleset"].replace("counter packets 10 bytes 1000 accept", phrase + " accept")))
            with self.assertRaisesRegex(self.mod.ApplyError, "counter reset"):
                self.compare()

    def test_nft_comments_named_counters_limits_and_quotas_are_not_masked(self):
        for text in (
            'comment "counter packets 10 bytes 1000"',
            'comment "escaped \\" counter packets 10 bytes 1000"',
            '# counter packets 10 bytes 1000\n',
            'counter named {\n packets 10 bytes 1000\n}',
            'quota over 1000 bytes used 10 bytes',
            'limit rate 10/second burst 1000 packets',
        ):
            with self.subTest(text=text):
                self.assertEqual(self.mod.overlay_nft_view(text), (text, []))
                changed = text.replace("10", "20")
                self.assertNotEqual(self.mod.overlay_nft_view(text), self.mod.overlay_nft_view(changed))
        with self.assertRaises(self.mod.ApplyError):
            self.mod.overlay_nft_view('comment "unterminated')

    def test_firewall_order_and_quoted_counter_text_cannot_change(self):
        baseline = self.docs["ops_preimage"]
        for text in (
            baseline["nft_ruleset"].replace('comment "counter packets 10', 'comment "counter packets 20'),
            baseline["nft_ruleset"].replace("udp dport 41641 counter packets 10 bytes 1000", "counter packets 10 bytes 1000 udp dport 41641"),
        ):
            self.write_fresh("ops_preimage", dict(baseline, nft_ruleset=text))
            with self.assertRaises(self.mod.ApplyError):
                self.compare()

    def test_direct_endpoint_identity_and_no_derp_remain_required(self):
        baseline = self.docs["huawei_prerequisite"]
        for old, new in (
            ("291ms", "NaNms"), ("boxb-router", "boxc-router"),
            ("100.64.0.2", "100.64.0.3"), ("2606:4700::2", "2606:4700::3"),
            ("41641", "41642"), ("[2606:4700::2]:41641", "DERP(hkg)"),
            ("291ms", "291ms\nextra output"),
        ):
            self.write_fresh("huawei_prerequisite", dict(baseline, direct_ping=baseline["direct_ping"].replace(old, new)))
            with self.subTest(new=new), self.assertRaises(self.mod.ApplyError):
                self.compare()
        doc = dict(baseline, peer_global_ipv6="2606:4700::3", direct_ping=baseline["direct_ping"].replace("::2", "::3"))
        self.write_fresh("huawei_prerequisite", doc)
        with self.assertRaises(self.mod.ApplyError):
            self.compare()

    def test_stored_bytes_remain_exact_even_for_ignored_measurements(self):
        path = Path(self.binding["ops_preimage_path"])
        original = path.read_text()
        for content in (original + "\n", original.replace("packets 10", "packets 20")):
            path.write_text(content)
            with self.assertRaisesRegex(self.mod.ApplyError, "signed hash"):
                self.compare()

    def test_duplicate_keys_trailing_json_and_unknown_formats_fail(self):
        path = Path(self.evidence["router_preimage_path"])
        original = path.read_text()
        for content in (original + "{}", original.replace('"schema_version": 1', '"schema_version": 1, "schema_version": 1'), original.replace('"runtime":', '"unknown":')):
            path.write_text(content)
            with self.assertRaises(self.mod.ApplyError):
                self.compare()

    def test_revalidation_calls_real_comparison_after_exact_input_checks(self):
        self.traffic_during_review()
        current = {"plan": {"plan_sha256": "plan"}, "state": {"authority_state_sha256": "state"}, "group": {}, "binary_sha256": "binary", "builder_receipt_sha256": "builder"}
        inspection = {"gateway_id_sha256": "gateway", "api_version": "12.0", "slot": 1, "prefix": "2606:4700:1::/64", "delegation_document_sha256": "delegation"}
        self.binding.update({key: "/fixture" for key in ("plan_path", "authority_path", "toolchain_path", "recovery_path", "source_path", "observation", "build_dir")})
        self.intent.update({"plan_sha256": "plan", "authority_state_sha256": "state", "active_controller_box": "boxa", "peer_box": "boxb", "freebox_gateway_id_sha256": "gateway", "freebox_api_version": "12.0", "delegation_slot": 1, "delegated_prefix": "2606:4700:1::/64", "freebox_preimage_sha256": "delegation", "binary_sha256": "binary", "builder_receipt_sha256": "builder", "router_next_hop": "fe80::1"})
        with patch.object(self.mod, "validate_inputs_v3", return_value=current), patch.object(self.mod, "overlay_boxes", return_value=("boxa", "boxb")), patch.object(self.mod, "require_overlay_controller_box"), patch.object(self.mod, "plan_magicdns_suffix", return_value="example.ts.net"), patch.object(self.mod, "inspect_freebox", return_value=inspection), patch.object(self.mod, "collect_overlay_evidence", return_value=self.evidence), patch.object(self.mod, "append_audit") as audit:
            self.assertEqual(self.mod.overlay_revalidate(self.binding, self.intent, self.directory), (current, "example.ts.net"))
            audit.assert_called_once_with("overlay-repair.revalidated", nonce="overlay_test_nonce", comparison="overlay_runtime_v1", **{role + "_sha256": self.evidence[role + "_sha256"] for role in self.docs})
            for field in ("plan_sha256", "authority_state_sha256", "binary_sha256", "builder_receipt_sha256", "freebox_preimage_sha256", "active_controller_box"):
                with self.subTest(field=field), self.assertRaisesRegex(self.mod.ApplyError, "evidence changed"):
                    self.mod.overlay_revalidate(self.binding, dict(self.intent, **{field: "changed"}), self.directory)


if __name__ == "__main__":
    unittest.main()
