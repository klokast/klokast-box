#!/usr/bin/env python3
import datetime as dt
import importlib.util
import json
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


REPO_ROOT = Path(__file__).resolve().parents[2]
BROKER = REPO_ROOT / "klokast-ops/secret-authority/bin/freebox-ipv6-broker"


def load():
    loader = SourceFileLoader("freebox_ipv6_broker_test", str(BROKER))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class FreeboxIPv6BrokerTest(unittest.TestCase):
    def setUp(self):
        self.mod = load()
        self.uid = "gateway_uid_123456"
        self.discovery = {
            "uid": self.uid,
            "device_name": "Freebox Server",
            "api_version": "12.0",
            "api_base_url": "/api/",
            "device_type": "FreeboxServer9",
            "api_domain": "example.fbxos.fr",
            "https_available": True,
            "https_port": 443,
        }
        self.prefixes = [f"2a01:e30:1234:{index:x}::/64" for index in range(8)]

    def response(self, next_hops=None):
        hops = next_hops or [""] * 8
        return {
            "success": True,
            "result": {
                "ipv6_enabled": True,
                "ipv6ll": "fe80::1",
                "ipv6_firewall": True,
                "ipv6_prefix_firewall": False,
                "delegations": [
                    {"prefix": prefix, "next_hop": hops[index]}
                    for index, prefix in enumerate(self.prefixes)
                ],
            },
        }

    def test_authentication_never_exposes_app_token(self):
        token = "TOP-SECRET-APP-TOKEN"

        class Transport:
            def request(_self, method, path, body=None, headers=None):
                if path.endswith("/login/"):
                    return {"success": True, "result": {"logged_in": False, "challenge": "challenge"}}
                self.assertNotIn(token, json.dumps(body))
                raise self.mod.BrokerError("session refused")

        credential = {"app_id": "org.klokast.test", "app_token": token, "api_major": 12}
        with self.assertRaises(self.mod.BrokerError) as caught:
            self.mod.Session(Transport(), credential)
        self.assertNotIn(token, str(caught.exception))

    def test_login_and_session_accept_valid_password_metadata_and_reject_wrong_types(self):
        credential = {"app_id": "org.klokast.test", "app_token": "secret", "api_major": 15}

        class Transport:
            login_password_set = True
            session_password_set = True

            def request(inner, method, path, body=None, headers=None):
                if path.endswith("/login/"):
                    return {"success": True, "result": {
                        "logged_in": False,
                        "challenge": "challenge",
                        "password_salt": "salt",
                        "password_set": inner.login_password_set,
                    }}
                if path.endswith("/login/session/"):
                    return {"success": True, "result": {
                        "session_token": "session",
                        "challenge": "next-challenge",
                        "permissions": {"settings": True},
                        "password_salt": "salt",
                        "password_set": inner.session_password_set,
                    }}
                return {"success": True, "result": {}}

        self.mod.Session(Transport(), credential).close()
        transport = Transport()
        transport.login_password_set = "true"
        with self.assertRaisesRegex(self.mod.BrokerError, "password metadata"):
            self.mod.Session(transport, credential)
        transport = Transport()
        transport.session_password_set = "true"
        with self.assertRaisesRegex(self.mod.BrokerError, "session password metadata"):
            self.mod.Session(transport, credential)

    def test_discovery_and_delegations_reject_unknown_shapes(self):
        transport = Mock()
        transport.request.return_value = {**self.discovery, "redirect": "attacker"}
        with self.assertRaisesRegex(self.mod.BrokerError, "unknown response shape"):
            self.mod.discover(transport)
        bad = self.response()
        bad["result"]["delegations"][1]["unknown"] = True
        with self.assertRaisesRegex(self.mod.BrokerError, "unknown shape"):
            self.mod.validate_delegation_document(bad)
        unknown_result = self.response()
        unknown_result["result"]["unknown"] = True
        with self.assertRaisesRegex(self.mod.BrokerError, r"unknown result shape \(unknown=unknown\)"):
            self.mod.validate_delegation_document(unknown_result)
        missing_result = self.response()
        del missing_result["result"]["delegations"]
        with self.assertRaisesRegex(self.mod.BrokerError, r"unknown result shape \(missing=delegations\)"):
            self.mod.validate_delegation_document(missing_result)

    def test_delegations_accept_optional_read_only_link_local_identity(self):
        response = self.response()
        del response["result"]["ipv6ll"]
        document = self.mod.validate_delegation_document(response)
        self.assertNotIn("ipv6ll", document)
        self.assertEqual(document["delegations"][0]["prefix"], "2a01:e30:1234::/64")

        bad = self.response()
        bad["result"]["ipv6ll"] = "192.0.2.1"
        with self.assertRaisesRegex(self.mod.BrokerError, "link-local identity"):
            self.mod.validate_delegation_document(bad)

    def test_delegations_accept_only_complete_boolean_firewall_state(self):
        response = self.response()
        response["result"].update({
            "ipv6_firewall": True,
            "ipv6_prefix_firewall": False,
        })
        document = self.mod.validate_delegation_document(response)
        self.assertIs(document["ipv6_firewall"], True)
        self.assertIs(document["ipv6_prefix_firewall"], False)

        incomplete = self.response()
        del incomplete["result"]["ipv6_prefix_firewall"]
        with self.assertRaisesRegex(self.mod.BrokerError, "firewall state is incomplete"):
            self.mod.validate_delegation_document(incomplete)

        wrong_type = self.response()
        wrong_type["result"].update({
            "ipv6_firewall": "true",
            "ipv6_prefix_firewall": False,
        })
        with self.assertRaisesRegex(self.mod.BrokerError, "firewall state is invalid"):
            self.mod.validate_delegation_document(wrong_type)

    def test_firewall_state_must_remain_exactly_present_and_unchanged(self):
        before = {"ipv6_firewall": True, "ipv6_prefix_firewall": False}
        self.mod.require_preserved_firewall_state(before, dict(before), "configuration")
        for after in (
            {"ipv6_firewall": False, "ipv6_prefix_firewall": False},
            {},
        ):
            with self.subTest(after=after), self.assertRaisesRegex(
                self.mod.BrokerError, "changed the Freebox IPv6 firewall state"
            ):
                self.mod.require_preserved_firewall_state(before, after, "configuration")

    def test_configure_refuses_changed_firewall_state(self):
        next_hop = "fe80::1234"
        original = self.mod.validate_delegation_document(self.response())
        original_hash = self.mod.sha256_bytes(self.mod.canonical(original).encode())

        class Session:
            def request(inner, method, _suffix, body=None):
                if method == "GET":
                    return self.response()
                hops = [""] * 8
                hops[1] = next_hop
                response = self.response(hops)
                response["result"]["ipv6_firewall"] = False
                return response

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "rollback").mkdir()
            args = SimpleNamespace(
                slot=1,
                prefix=self.prefixes[1],
                next_hop=next_hop,
                expected_preimage_sha256=original_hash,
                rollback_id="rollback_nonce_123",
                check=False,
            )
            with patch.object(self.mod, "STATE", root / "state.json"), patch.object(
                self.mod, "ROLLBACK_ROOT", root / "rollback"
            ), patch.object(self.mod, "AUDIT_LOG", root / "audit.jsonl"), patch.object(
                self.mod, "ensure_root_dir"
            ), self.assertRaisesRegex(
                self.mod.BrokerError, "changed the Freebox IPv6 firewall state"
            ):
                self.mod.configure(args, Session(), self.discovery)

    def test_discovery_accepts_valid_model_metadata(self):
        transport = Mock()
        transport.request.return_value = {
            **self.discovery,
            "box_model": "fbxgw9-r1/full",
            "box_model_name": "Freebox v9 (r1)",
        }
        self.assertEqual(self.mod.discover(transport), transport.request.return_value)

        transport.request.return_value["box_model_name"] = "bad\nmodel"
        with self.assertRaisesRegex(self.mod.BrokerError, "invalid model metadata"):
            self.mod.discover(transport)

    def test_selects_lowest_unused_non_first_slot(self):
        document = self.mod.validate_delegation_document(self.response([
            "", "fe80::99", "", "", "", "", "", "",
        ]))
        with patch.object(self.mod, "STATE", Path("/missing/state")):
            slot, prefix, next_hop, gateway_hash = self.mod.select_slot(
                document, self.discovery, "fe80::1234"
            )
        self.assertEqual(slot, 2)
        self.assertEqual(prefix, self.prefixes[2])
        self.assertEqual(next_hop, "fe80::1234")
        self.assertEqual(gateway_hash, self.mod.sha256_bytes(self.uid.encode()))

    def test_recorded_slot_rejects_collision_prefix_drift_and_wrong_next_hop(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / "state.json"
            recorded = {
                "schema_version": 1,
                "kind": "klokast.freebox-ops-delegation.v1",
                "gateway_id_sha256": self.mod.sha256_bytes(self.uid.encode()),
                "api_version": "12.0",
                "slot": 2,
                "prefix": self.prefixes[2],
                "next_hop": "fe80::1234",
            }
            state.write_text(self.mod.canonical(recorded) + "\n", encoding="utf-8")
            state.chmod(0o600)
            occupied = self.mod.validate_delegation_document(self.response([
                "", "", "fe80::9999", "", "", "", "", "",
            ]))
            with patch.object(self.mod, "STATE", state), self.assertRaisesRegex(self.mod.BrokerError, "occupied"):
                self.mod.select_slot(occupied, self.discovery, "fe80::1234")
            drift = json.loads(json.dumps(self.response()))
            drift["result"]["delegations"][2]["prefix"] = "2a01:e30:ffff:2::/64"
            with patch.object(self.mod, "STATE", state), self.assertRaisesRegex(self.mod.BrokerError, "prefix drifted"):
                self.mod.select_slot(self.mod.validate_delegation_document(drift), self.discovery, "fe80::1234")
            with patch.object(self.mod, "STATE", state), self.assertRaisesRegex(self.mod.BrokerError, "next hop drifted"):
                self.mod.select_slot(self.mod.validate_delegation_document(self.response()), self.discovery, "fe80::4321")

    def test_check_mode_is_read_only_and_configure_stores_exact_preimage(self):
        next_hop = "fe80::1234"
        original = self.mod.validate_delegation_document(self.response())
        original_hash = self.mod.sha256_bytes(self.mod.canonical(original).encode())

        class Session:
            def __init__(self):
                self.puts = []

            def request(inner, method, _suffix, body=None):
                if method == "GET":
                    return self.response()
                inner.puts.append(body)
                hops = [""] * 8
                hops[1] = next_hop
                return self.response(hops)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "rollback").mkdir()
            session = Session()
            common = dict(
                slot=1, prefix=self.prefixes[1], next_hop=next_hop,
                expected_preimage_sha256=original_hash, rollback_id="rollback_nonce_123",
            )
            with patch.object(self.mod, "STATE", root / "state.json"), patch.object(self.mod, "ROLLBACK_ROOT", root / "rollback"), patch.object(self.mod, "AUDIT_LOG", root / "audit.jsonl"), patch.object(self.mod, "ensure_root_dir"):
                checked = self.mod.configure(SimpleNamespace(**common, check=True), session, self.discovery)
                self.assertEqual(checked["result"], "check")
                self.assertEqual(session.puts, [])
                self.assertEqual(list((root / "rollback").iterdir()), [])
                configured = self.mod.configure(SimpleNamespace(**common, check=False), session, self.discovery)
                self.assertEqual(configured["result"], "configured")
                preimage = root / "rollback" / common["rollback_id"] / "delegation-preimage.json"
                self.assertEqual(preimage.read_text(encoding="utf-8"), self.mod.canonical(original) + "\n")
                self.assertEqual(preimage.stat().st_mode & 0o777, 0o600)

    def test_restore_accepts_preimage_without_read_only_link_local_identity(self):
        next_hop = "fe80::1234"
        preimage_response = self.response()
        del preimage_response["result"]["ipv6ll"]
        preimage = self.mod.validate_delegation_document(preimage_response)
        preimage_hash = self.mod.sha256_bytes(self.mod.canonical(preimage).encode())

        class Session:
            def request(inner, method, _suffix, body=None):
                if method == "GET":
                    hops = [""] * 8
                    hops[1] = next_hop
                    return self.response(hops)
                self.assertEqual(body, {"delegations": preimage["delegations"]})
                return self.response()

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rollback = root / "rollback" / "rollback_nonce_123"
            rollback.mkdir(parents=True)
            (rollback / "delegation-preimage.json").write_text(
                self.mod.canonical(preimage) + "\n", encoding="utf-8"
            )
            (rollback / "preimage.sha256").write_text(
                preimage_hash + "\n", encoding="ascii"
            )
            (rollback / "delegation-preimage.json").chmod(0o600)
            (rollback / "preimage.sha256").chmod(0o600)
            args = SimpleNamespace(
                slot=1,
                prefix=self.prefixes[1],
                next_hop=next_hop,
                expected_preimage_sha256=preimage_hash,
                rollback_id="rollback_nonce_123",
            )
            with patch.object(self.mod, "STATE", root / "state.json"), patch.object(
                self.mod, "ROLLBACK_ROOT", root / "rollback"
            ), patch.object(self.mod, "AUDIT_LOG", root / "audit.jsonl"), patch.object(
                self.mod, "ensure_root_dir"
            ):
                restored = self.mod.restore(args, Session(), self.discovery)
            self.assertEqual(restored["result"], "restored")
            self.assertEqual(restored["restored_document_sha256"], preimage_hash)

    def test_transport_refuses_redirect_without_following_it(self):
        response = Mock(status=302)
        connection = Mock()
        connection.getresponse.return_value = response
        with patch.object(self.mod.http.client, "HTTPConnection", return_value=connection):
            with self.assertRaisesRegex(self.mod.BrokerError, "redirect"):
                self.mod.Transport("192.168.1.254").request("GET", "/api_version")

    def test_gateway_resolver_accepts_only_local_service_addresses(self):
        def answer(address):
            return [(self.mod.socket.AF_INET, self.mod.socket.SOCK_STREAM, 6, "", (address, 80))]

        for address in ("192.168.1.254", "212.27.38.253"):
            with self.subTest(address=address), patch.object(
                self.mod.socket, "getaddrinfo", return_value=answer(address)
            ):
                self.assertEqual(self.mod.resolve_local_gateway(), address)

        with patch.object(
            self.mod.socket, "getaddrinfo", return_value=answer("8.8.8.8")
        ), self.assertRaisesRegex(self.mod.BrokerError, "approved local-service address"):
            self.mod.resolve_local_gateway()


if __name__ == "__main__":
    unittest.main()
