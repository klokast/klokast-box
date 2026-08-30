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
