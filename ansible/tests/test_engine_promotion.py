#!/usr/bin/env python3
import argparse
import datetime as dt
import importlib.util
import io
import json
import os
import pwd
import stat
import subprocess
import sys
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "klokast-ops" / "secret-authority" / "bin" / "ksa-instance"
PROMOTION_HELPER = REPO_ROOT / "klokast-dev/bin/promote-private-instance-engine"
OLD_COMMIT = "a" * 40
NEW_COMMIT = "b" * 40


def load_module():
    loader = SourceFileLoader("ksa_engine_promotion_test", str(SCRIPT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class BinaryInput:
    def __init__(self, content):
        self.buffer = io.BytesIO(content)


class EnginePromotionTest(unittest.TestCase):
    def setUp(self):
        self.mod = load_module()
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.checkout = self.root / "instance"
        self.checkout.mkdir(mode=0o700)
        self.make_private_checkout()
        self.args = argparse.Namespace(
            checkout=str(self.checkout),
            infra_user=pwd.getpwuid(os.getuid()).pw_name,
            activation_root=str(self.root / "activations"),
        )
        self.checker = self.root / "klokast"
        self.checker.write_text(
            "#!/bin/sh\nprintf '{\"valid\":true}\\n'\n",
            encoding="utf-8",
        )
        self.checker.chmod(0o755)
        self.old_build = {"binary_path": self.checker}
        self.new_build = {"binary_path": self.checker}

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def git(root, *arguments):
        return subprocess.check_output(
            ["git", "-C", str(root), *arguments], text=True
        ).strip()

    @staticmethod
    def schema(commit, name):
        return (
            "https://raw.githubusercontent.com/klokast/klokast-box/"
            f"{commit}/schemas/{name}"
        )

    def make_private_checkout(self):
        files = {
            ".gitignore": "*.private\n",
            "AGENTS.md": "# Private instructions\n",
            "README.md": "# Private instance\n",
            "klokast-instance.json": json.dumps({
                "$schema": self.schema(OLD_COMMIT, "klokast-instance-v1.schema.json"),
                "schema-version": 1,
                "instance": {"id": "klokast-instance"},
                "tailnet": {
                    "tailnet-dns-name": "example.ts.net",
                    "members": {"human@example.invalid": {"roles": ["operator", "family"]}},
                },
                "sites": {
                    "site-a": {"country": "XA", "description": ""},
                    "site-b": {"country": "XB", "description": "Example"},
                },
                "boxes": {
                    "boxa": {"site": "site-a", "connectivity-profiles": ["tailscale"]},
                    "boxb": {"site": "site-b", "connectivity-profiles": ["tailscale"]},
                },
                "controllers": {"active": "boxa", "standby": "boxb"},
                "airunners": ["boxa-ops-airunner"],
                "apps": {},
            }, indent=2, sort_keys=True) + "\n",
            "klokast.lock.json": json.dumps({
                "$schema": self.schema(OLD_COMMIT, "klokast-lock-v1.schema.json"),
                "schema-version": 1,
                "engine": {
                    "repository": "https://github.com/klokast/klokast-box",
                    "ref": "main",
                    "commit": OLD_COMMIT,
                },
            }, indent=2, sort_keys=True) + "\n",
        }
        for name, content in files.items():
            (self.checkout / name).write_text(content, encoding="utf-8")
        subprocess.run(["git", "-C", self.checkout, "init", "-q", "--initial-branch=main"], check=True)
        subprocess.run(["git", "-C", self.checkout, "config", "user.name", "test"], check=True)
        subprocess.run(["git", "-C", self.checkout, "config", "user.email", "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", self.checkout, "add", "-A"], check=True)
        subprocess.run(["git", "-C", self.checkout, "commit", "-qm", "initial"], check=True)

    def candidate_envelope(self, change_private_value=False, transition=None):
        instance = json.loads((self.checkout / "klokast-instance.json").read_text())
        lock = json.loads((self.checkout / "klokast.lock.json").read_text())
        transition = transition or self.mod.SCHEMA_TRANSITION_LEGACY_TO_CURRENT
        instance = self.mod.transition_instance_v1(instance, transition, NEW_COMMIT)
        lock["$schema"] = self.schema(NEW_COMMIT, "klokast-lock-v1.schema.json")
        lock["engine"]["commit"] = NEW_COMMIT
        if change_private_value:
            instance["airunners"] = ["boxb-ops-airunner"]
        instance_content = json.dumps(instance, indent=2, sort_keys=True) + "\n"
        lock_content = json.dumps(lock, indent=2, sort_keys=True) + "\n"
        candidate = self.root / ("changed" if change_private_value else "candidate")
        candidate.mkdir()
        for name in (".gitignore", "AGENTS.md", "README.md"):
            (candidate / name).write_bytes((self.checkout / name).read_bytes())
        (candidate / "klokast-instance.json").write_text(instance_content)
        (candidate / "klokast.lock.json").write_text(lock_content)
        subprocess.run(["git", "-C", candidate, "init", "-q", "--initial-branch=main"], check=True)
        subprocess.run(["git", "-C", candidate, "add", "-A"], check=True)
        return {
            "schema_version": 1,
            "action": "promote-engine",
            "engine_repository": "https://github.com/klokast/klokast-box",
            "engine_ref": "main",
            "schema_transition": transition,
            "old_engine_commit": OLD_COMMIT,
            "new_engine_commit": NEW_COMMIT,
            "private_base_commit": self.git(self.checkout, "rev-parse", "HEAD"),
            "private_base_tree": self.git(self.checkout, "rev-parse", "HEAD^{tree}"),
            "candidate_tree": self.git(candidate, "write-tree"),
            "rollback_tree": self.git(self.checkout, "rev-parse", "HEAD^{tree}"),
            "candidate_instance_json": instance_content,
            "candidate_lock_json": lock_content,
        }

    def test_candidate_allows_exact_reversible_schema_transition(self):
        envelope = self.candidate_envelope()
        commit, tree = self.mod.validate_candidate_tree(
            self.args, envelope, self.old_build, self.new_build
        )
        self.assertEqual(commit, envelope["private_base_commit"])
        self.assertEqual(tree, envelope["private_base_tree"])
        self.assertEqual(list(self.root.glob(".engine-promotion.*")), [])

    def run_workstation_candidate(self, requested_transition=""):
        # Execute the actual Mac helper program, including transition selection,
        # file preservation, Git tree construction, and the closed envelope.
        source = PROMOTION_HELPER.read_text()
        start = source.index('python3 - "$PRIVATE_WORKTREE" "$CANDIDATE" "$ENVELOPE"')
        program = source[start:].split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
        output = Path(tempfile.mkdtemp(prefix="workstation-", dir=self.root))
        result = subprocess.run([
            sys.executable, "-c", program, str(self.checkout),
            str(output / "candidate"), str(output / "envelope.json"),
            OLD_COMMIT, NEW_COMMIT, self.git(self.checkout, "rev-parse", "HEAD"),
            self.git(self.checkout, "rev-parse", "HEAD^{tree}"), "false",
            requested_transition, str(output / "transition"),
        ], text=True, capture_output=True)
        return result, output

    def use_current_registry_instance(self):
        instance = json.loads((self.checkout / "klokast-instance.json").read_text())
        instance = self.mod.transition_instance_v1(
            instance, self.mod.SCHEMA_TRANSITION_LEGACY_TO_CURRENT, OLD_COMMIT
        )
        instance = self.mod.transition_instance_v1(
            instance, self.mod.SCHEMA_TRANSITION_PROFILES_TO_CAPABILITIES, OLD_COMMIT
        )
        instance["boxes"]["boxa"]["substrate"] = {"bridge-ports": {"lan": ["eth1"]}}
        instance["boxes"]["boxb"]["substrate"] = {}
        instance["inactive-apps"] = {"nextcloud": {
            "placement": {"primary": "", "secondary": ""},
            "resources": {"cloudflare-tunnel-egress": False},
        }}
        (self.checkout / "klokast-instance.json").write_text(json.dumps(instance, indent=2, sort_keys=True) + "\n")
        self.git(self.checkout, "add", "klokast-instance.json")
        self.git(self.checkout, "commit", "-qm", "published registry settings")
        return instance

    def test_workstation_registry_promotion_preserves_bytes_and_passes_controller(self):
        instance = self.use_current_registry_instance()
        original = {name: (self.checkout / name).read_bytes()
                    for name in ("klokast-instance.json", "klokast.lock.json")}
        result, output = self.run_workstation_candidate()
        self.assertEqual(result.returncode, 0, result.stderr)
        envelope = json.loads((output / "envelope.json").read_text())
        self.assertEqual(envelope["schema_transition"], "metadata-only")
        candidate = json.loads(envelope["candidate_instance_json"])
        self.assertEqual(candidate, {**instance, "$schema": self.schema(NEW_COMMIT, "klokast-instance-v1.schema.json")})
        self.assertEqual(envelope["candidate_instance_json"].encode(), original["klokast-instance.json"].replace(OLD_COMMIT.encode(), NEW_COMMIT.encode()))
        self.mod.validate_candidate_tree(self.args, envelope, self.old_build, self.new_build)
        for name, content in original.items():
            self.assertEqual((self.checkout / name).read_bytes(), content)
        self.assertEqual(self.git(self.checkout, "status", "--porcelain"), "")
        candidate["inactive-apps"]["nextcloud"]["placement"]["primary"] = "boxa"
        envelope["candidate_instance_json"] = json.dumps(candidate)
        with self.assertRaisesRegex(self.mod.InstanceAuthorityError, "exact deterministic"):
            self.mod.validate_candidate_tree(self.args, envelope, self.old_build, self.new_build)

    def test_workstation_legacy_transition_still_works(self):
        result, output = self.run_workstation_candidate()
        self.assertEqual(result.returncode, 0, result.stderr)
        envelope = json.loads((output / "envelope.json").read_text())
        self.assertEqual(envelope["schema_transition"], self.mod.SCHEMA_TRANSITION_LEGACY_TO_CURRENT)
        self.mod.validate_candidate_tree(self.args, envelope, self.old_build, self.new_build)

    def test_workstation_optional_update_settings_survive_metadata_promotion(self):
        instance = self.use_current_registry_instance()
        inactive = instance.pop("inactive-apps")
        updates = {
            "enabled": True,
            "targets": {"boxa": ["dmz"], "boxb": ["dmz", "iot"]},
            "exclusions": [], "branch-policy": "tested-stable",
            "check-frequency": "daily", "check-time": "00:10",
            "branch-delay-days": 21, "report-max-age-hours": 30,
            "maintenance-window": {"start": "02:00", "end": "04:00", "last-start": "03:00"},
            "replacement-minutes": 30, "recovery-minutes": 30,
        }
        for has_updates, has_inactive in ((False, False), (True, False), (True, True)):
            with self.subTest(updates=has_updates, inactive=has_inactive):
                value = dict(instance)
                if has_updates:
                    value["vm-updates"] = updates
                if has_inactive:
                    value["inactive-apps"] = inactive
                path = self.checkout / "klokast-instance.json"
                path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
                self.git(self.checkout, "add", "klokast-instance.json")
                self.git(self.checkout, "commit", "-qm", "publish optional settings")
                original = path.read_bytes()
                result, output = self.run_workstation_candidate()
                self.assertEqual(result.returncode, 0, result.stderr)
                envelope = json.loads((output / "envelope.json").read_text())
                self.assertEqual(envelope["schema_transition"], "metadata-only")
                self.assertEqual(envelope["candidate_instance_json"].encode(),
                                 original.replace(OLD_COMMIT.encode(), NEW_COMMIT.encode()))
                self.mod.validate_candidate_tree(self.args, envelope, self.old_build, self.new_build)
                candidate = json.loads(envelope["candidate_instance_json"])
                self.assertEqual(self.mod.transition_instance_v1(candidate, "metadata-only", OLD_COMMIT), value)
                self.assertEqual(path.read_bytes(), original)
                self.assertEqual(self.git(self.checkout, "status", "--porcelain"), "")
                if has_updates:
                    lossy, _ = self.run_workstation_candidate(self.mod.SCHEMA_TRANSITION_CURRENT_TO_LEGACY)
                    self.assertNotEqual(lossy.returncode, 0)
                    self.assertIn("cannot be converted to the legacy shape", lossy.stderr)
                    candidate["vm-updates"]["branch-delay-days"] = 0
                    envelope["candidate_instance_json"] = json.dumps(candidate)
                    with self.assertRaisesRegex(self.mod.InstanceAuthorityError, "exact deterministic"):
                        self.mod.validate_candidate_tree(self.args, envelope, self.old_build, self.new_build)

    def test_workstation_registry_shape_rejects_unknown_fields(self):
        instance = self.use_current_registry_instance()
        instance["unknown-setting"] = {}
        (self.checkout / "klokast-instance.json").write_text(json.dumps(instance))
        result, _ = self.run_workstation_candidate()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no supported promotion shape", result.stderr)

    def test_workstation_registry_cannot_use_lossy_legacy_transition(self):
        self.use_current_registry_instance()
        result, _ = self.run_workstation_candidate(self.mod.SCHEMA_TRANSITION_CURRENT_TO_LEGACY)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cannot be converted to the legacy shape", result.stderr)

    def test_schema_transition_moves_only_redundant_legacy_structure(self):
        legacy = json.loads((self.checkout / "klokast-instance.json").read_text())
        current = self.mod.transition_instance_v1(
            legacy, self.mod.SCHEMA_TRANSITION_LEGACY_TO_CURRENT, NEW_COMMIT
        )
        self.assertNotIn("instance", current)
        self.assertNotIn("tailnet", current)
        self.assertNotIn("sites", current)
        self.assertIn("tailscale", current)
        self.assertEqual(current["boxes"]["boxa"]["country"], "XA")
        self.assertEqual(current["boxes"]["boxa"]["connectivity"], ["tailscale"])
        reconstructed = self.mod.transition_instance_v1(
            current, self.mod.SCHEMA_TRANSITION_CURRENT_TO_LEGACY, OLD_COMMIT
        )
        self.assertEqual(reconstructed, legacy)

    def test_schema_transition_rejects_unused_legacy_site(self):
        legacy = json.loads((self.checkout / "klokast-instance.json").read_text())
        legacy["sites"]["unused"] = {"country": "XC", "description": ""}
        with self.assertRaisesRegex(
            self.mod.InstanceAuthorityError, "unused site metadata"
        ):
            self.mod.transition_instance_v1(
                legacy, self.mod.SCHEMA_TRANSITION_LEGACY_TO_CURRENT, NEW_COMMIT
            )

    def test_connectivity_transition_is_exact_and_reversible(self):
        legacy = json.loads((self.checkout / "klokast-instance.json").read_text())
        current = self.mod.transition_instance_v1(
            legacy, self.mod.SCHEMA_TRANSITION_LEGACY_TO_CURRENT, OLD_COMMIT
        )
        current["boxes"]["boxb"]["connectivity"] = [
            "local-ap-direct-egress", "tailscale"
        ]
        migrated = self.mod.transition_instance_v1(
            current,
            self.mod.SCHEMA_TRANSITION_PROFILES_TO_CAPABILITIES,
            NEW_COMMIT,
        )
        self.assertEqual(migrated["boxes"]["boxa"]["connectivity"], ["overlay"])
        self.assertEqual(
            migrated["boxes"]["boxb"]["connectivity"],
            ["local-ap-uplink", "direct-wan-egress", "overlay"],
        )
        reconstructed = self.mod.transition_instance_v1(
            migrated,
            self.mod.SCHEMA_TRANSITION_CAPABILITIES_TO_PROFILES,
            OLD_COMMIT,
        )
        self.assertEqual(reconstructed, current)

    def test_connectivity_inverse_rejects_noninvertible_values_and_partial_pairs(self):
        legacy = json.loads((self.checkout / "klokast-instance.json").read_text())
        current = self.mod.transition_instance_v1(
            legacy, self.mod.SCHEMA_TRANSITION_LEGACY_TO_CURRENT, OLD_COMMIT
        )
        for connectivity in (
            ["overlay", "local-ap-uplink"],
            ["overlay", "direct-wan-egress"],
            ["overlay", "edge-tunnel-ingress"],
            ["overlay", "direct-wan-ingress"],
            ["overlay", "unknown"],
        ):
            with self.subTest(connectivity=connectivity):
                candidate = json.loads(json.dumps(current))
                candidate["boxes"]["boxa"]["connectivity"] = connectivity
                with self.assertRaisesRegex(
                    self.mod.InstanceAuthorityError, "not exactly invertible"
                ):
                    self.mod.transition_instance_v1(
                        candidate,
                        self.mod.SCHEMA_TRANSITION_CAPABILITIES_TO_PROFILES,
                        OLD_COMMIT,
                    )

    def test_candidate_allows_recorded_inverse_transition(self):
        legacy = json.loads((self.checkout / "klokast-instance.json").read_text())
        current = self.mod.transition_instance_v1(
            legacy, self.mod.SCHEMA_TRANSITION_LEGACY_TO_CURRENT, OLD_COMMIT
        )
        (self.checkout / "klokast-instance.json").write_text(
            json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        subprocess.run(["git", "-C", self.checkout, "add", "klokast-instance.json"], check=True)
        subprocess.run(["git", "-C", self.checkout, "commit", "-qm", "current shape"], check=True)
        envelope = self.candidate_envelope(
            transition=self.mod.SCHEMA_TRANSITION_CURRENT_TO_LEGACY
        )
        commit, tree = self.mod.validate_candidate_tree(
            self.args, envelope, self.old_build, self.new_build
        )
        self.assertEqual(commit, envelope["private_base_commit"])
        self.assertEqual(tree, envelope["private_base_tree"])

    def test_candidate_rejects_a_private_intent_change(self):
        envelope = self.candidate_envelope(change_private_value=True)
        with self.assertRaisesRegex(
            self.mod.InstanceAuthorityError, "exact deterministic"
        ):
            self.mod.validate_candidate_tree(
                self.args, envelope, self.old_build, self.new_build
            )

    def test_bounded_envelope_rejects_unknown_fields(self):
        envelope = self.candidate_envelope()
        envelope["unexpected"] = True
        content = json.dumps(envelope).encode()
        with mock.patch.object(sys, "stdin", BinaryInput(content)), self.assertRaisesRegex(
            self.mod.InstanceAuthorityError, "closed schema"
        ):
            self.mod.read_promotion_envelope()

    def test_mac_intent_validator_accepts_one_hour_and_rejects_overlong_or_stale(self):
        source = PROMOTION_HELPER.read_text().split('INTENT_NONCE="$(python3', 1)[1]
        code = source.split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
        now = self.mod.now_utc().replace(microsecond=0)
        arguments = [OLD_COMMIT, NEW_COMMIT, "c" * 40, "d" * 40, "e" * 40, "f" * 40, "metadata-only"]
        value = dict(zip(("old_engine_commit", "new_engine_commit", "private_base_commit", "private_base_tree",
                          "candidate_tree", "controller_public_commit", "schema_transition"), arguments))
        value.update(engine_repository="https://github.com/klokast/klokast-box", engine_ref="main",
                     signer_id="human-private-instance", nonce="promotion-test-nonce")
        for age, lifetime, accepted in ((0, 3600, True), (1800, 3600, True), (0, 3601, False), (3601, 3600, False)):
            with self.subTest(age=age, lifetime=lifetime):
                issued = now - dt.timedelta(seconds=age)
                value.update(issued_at=self.mod.format_utc(issued),
                             expires_at=self.mod.format_utc(issued + dt.timedelta(seconds=lifetime)))
                path = self.root / "mac-intent.json"
                path.write_text(json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n")
                result = subprocess.run([sys.executable, "-", str(path), *arguments], input=code, text=True, capture_output=True)
                self.assertEqual(result.returncode == 0, accepted, result.stderr)

    def test_intent_lifetime_is_limited_to_one_hour(self):
        issued = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
        intent = {
            "schema_version": 1,
            "authority": "klokast-secret-authority",
            "app": "instance",
            "action": "promote-engine",
            "engine_repository": "https://github.com/klokast/klokast-box",
            "engine_ref": "main",
            "schema_transition": self.mod.SCHEMA_TRANSITION_LEGACY_TO_CURRENT,
            "old_engine_commit": OLD_COMMIT,
            "new_engine_commit": NEW_COMMIT,
            "old_build_operation": "c" * 12,
            "new_build_operation": "d" * 12,
            "old_binary_sha256": "e" * 64,
            "new_binary_sha256": "f" * 64,
            "old_builder_receipt_sha256": "1" * 64,
            "new_builder_receipt_sha256": "2" * 64,
            "controller_public_commit": NEW_COMMIT,
            "private_repository_sha256": "3" * 64,
            "private_repository_id": 42,
            "private_base_commit": "4" * 40,
            "private_base_tree": "5" * 40,
            "candidate_tree": "6" * 40,
            "rollback_tree": "5" * 40,
            "source_receipt_sha256": "7" * 64,
            "signer_id": "human-private-instance",
            "nonce": "nonce_123456789",
            "issued_at": self.mod.format_utc(issued),
            "expires_at": self.mod.format_utc(issued + dt.timedelta(hours=1)),
        }
        self.mod.validate_promotion_intent(intent)
        intent["expires_at"] = self.mod.format_utc(issued + dt.timedelta(hours=1, seconds=1))
        with self.assertRaisesRegex(self.mod.InstanceAuthorityError, "exceeds one hour"):
            self.mod.validate_promotion_intent(intent)

    def test_immutable_receipt_has_required_hash_and_modes(self):
        receipt = {"schema_version": 1, "kind": self.mod.PROMOTION_KIND, "value": "bound"}
        with mock.patch.object(self.mod.os, "chown"):
            path, complete = self.mod.write_group_receipt(
                self.args, self.root / "promotions", NEW_COMMIT, receipt
            )
        self.assertEqual(path.name, f"{complete['receipt_sha256']}.json")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o440)
        self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o750)
        self.assertEqual(json.loads(path.read_text()), complete)


class PromotionSourceSelectionTest(unittest.TestCase):
    """Exercise the remote POSIX payload against real, separate Git checkouts."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.approved = self.root / "approved"
        self.candidate = self.root / "candidate"
        self.approved.mkdir()
        self.git(self.approved, "init", "-q", "--initial-branch=main")
        self.git(self.approved, "config", "user.name", "Test")
        self.git(self.approved, "config", "user.email", "test@example.invalid")
        (self.approved / "source").write_text("approved\n")
        self.git(self.approved, "add", "source")
        self.git(self.approved, "commit", "-qm", "approved")
        self.old = self.git(self.approved, "rev-parse", "HEAD")
        self.git(self.root, "clone", "-q", str(self.approved), str(self.candidate))
        self.git(self.candidate, "config", "user.name", "Test")
        self.git(self.candidate, "config", "user.email", "test@example.invalid")
        (self.candidate / "source").write_text("candidate\n")
        self.git(self.candidate, "commit", "-qam", "candidate")
        self.new = self.git(self.candidate, "rev-parse", "HEAD")
        for checkout in (self.approved, self.candidate):
            if checkout == self.approved:
                self.git(checkout, "remote", "add", "origin", "https://github.com/klokast/klokast-box")
            else:
                self.git(checkout, "remote", "set-url", "origin", "https://github.com/klokast/klokast-box")
            self.git(checkout, "update-ref", "refs/remotes/origin/main", self.git(checkout, "rev-parse", "HEAD"))
            self.git(checkout, "branch", "--set-upstream-to=origin/main", "main")
        self.payload = PROMOTION_HELPER.read_text().split("<<'SELECT_SOURCE'\n", 1)[1].split("\nSELECT_SOURCE\n", 1)[0]

    @staticmethod
    def git(root, *args):
        return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()

    def select(self, commit=None, candidate=None):
        return subprocess.run(
            ["sh", "-s", "--", commit or self.new, str(candidate or self.candidate), str(self.approved)],
            input=self.payload, text=True, capture_output=True, check=False,
        )

    def run_transport(self, *, fail=False):
        source = PROMOTION_HELPER.read_text()
        block = "# Select public source only." + source.split("# Select public source only.", 1)[1].split(
            'require_clean_branch "$PRIVATE_WORKTREE" "private"', 1
        )[0]
        script = '''
set -euo pipefail
ksa_die() { printf '%s\\n' "$*" >&2; exit 1; }
tailscale() {
  printf '%s\\n' "$@" > "$WORK/arguments"
  cat > "$WORK/received"
  printf '%s\\n' /home/smith/src/klokast/klokast-box-update-candidate/ansible/bin/platform-instance
  return "$TRANSPORT_EXIT"
}
''' + block + '\nprintf "%s\\n" "$CONTROLLER_CLI"\n'
        environment = {
            **os.environ, "WORK": str(self.root), "PUBLIC_HEAD": self.new,
            "SSH_TARGET": "smith@test-ops", "TRANSPORT_EXIT": "1" if fail else "0",
        }
        return subprocess.run(
            [os.environ.get("KLOKAST_TEST_BASH", "bash"), "-c", script],
            env=environment, text=True, capture_output=True, check=False,
        )

    def test_parent_shell_sends_literal_complete_payload(self):
        result = self.run_transport()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / "received").read_text(), self.payload + "\n")
        self.assertEqual((self.root / "arguments").read_text().splitlines(), [
            "ssh", "smith@test-ops", "sh", "-s", "--", self.new,
            "/home/smith/src/klokast/klokast-box-update-candidate",
            "/home/smith/src/klokast/klokast-box",
        ])
        self.assertEqual(result.stdout.strip(),
                         "/home/smith/src/klokast/klokast-box-update-candidate/ansible/bin/platform-instance")

    def test_parent_shell_refuses_partial_output_on_transport_failure(self):
        result = self.run_transport(fail=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("controller candidate source is not ready", result.stderr)

    def test_candidate_selected_without_changing_approved_checkout(self):
        result = self.select()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), str(self.candidate / "ansible/bin/platform-instance"))
        self.assertEqual(self.git(self.approved, "rev-parse", "HEAD"), self.old)
        self.assertEqual((self.approved / "source").read_text(), "approved\n")
        self.assertEqual(self.git(self.approved, "status", "--porcelain"), "")

    def test_existing_current_fixed_checkout_remains_supported(self):
        result = self.select(commit=self.old, candidate=self.root / "absent")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), str(self.approved / "ansible/bin/platform-instance"))

    def test_dirty_candidate_is_refused(self):
        (self.candidate / "source").write_text("unreviewed\n")
        result = self.select()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("keep the approved deployment checkout unchanged", result.stderr)

    def test_stale_tracking_ref_is_refused(self):
        self.git(self.candidate, "update-ref", "refs/remotes/origin/main", self.old)
        self.assertNotEqual(self.select().returncode, 0)

    def test_noncanonical_origin_is_refused(self):
        self.git(self.candidate, "remote", "set-url", "origin", "https://example.invalid/repo")
        self.assertNotEqual(self.select().returncode, 0)

    def test_alias_and_wrong_branch_are_refused(self):
        alias = self.root / "alias"
        alias.symlink_to(self.candidate)
        self.assertNotEqual(self.select(candidate=alias).returncode, 0)
        self.git(self.candidate, "switch", "-qc", "candidate")
        self.assertNotEqual(self.select().returncode, 0)



if __name__ == "__main__":
    unittest.main()
