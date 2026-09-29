#!/usr/bin/env python3
"""Exercise the workstation development-signing transport through a fake controller."""

import os
import pathlib
import pty
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
SIGNER = ROOT / "klokast-dev/bin/sign-secret-authority-intent"


class DevelopmentApprovalTransportTest(unittest.TestCase):
    def test_remote_development_signer_writes_and_cleans_exact_intent(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            remote = root / "remote"
            remote.mkdir()
            tailscale = fake_bin / "tailscale"
            tailscale.write_text("""#!/bin/sh
set -eu
[ "$1" = ssh ] && [ "$2" = smith@k002-ops ] || exit 2
shift 2
case "$*" in
  'sh -s')
    payload=$(cat)
    case "$payload" in
      *human-approval-mode*) printf 'development\\n' ;;
      *'mktemp -d'*) printf '/home/smith/private/klokast/development-approvals/request.fake\\n' ;;
      *) exit 2 ;;
    esac ;;
  *'cat > '*intent.json*) cat > "$FAKE_REMOTE_ROOT/intent.json" ;;
  'sh -s -- platform-apply /home/smith/private/klokast/development-approvals/request.fake')
    cat >/dev/null
    [ -s "$FAKE_REMOTE_ROOT/intent.json" ] || exit 2
    printf '%s\\n' '-----BEGIN SSH SIGNATURE-----' 'fake-development-signature' '-----END SSH SIGNATURE-----' > "$FAKE_REMOTE_ROOT/intent.json.sig" ;;
  *'cat '*intent.json.sig*) cat "$FAKE_REMOTE_ROOT/intent.json.sig" ;;
  'sh -s -- /home/smith/private/klokast/development-approvals/request.fake')
    cat >/dev/null
    rm -f "$FAKE_REMOTE_ROOT/intent.json" "$FAKE_REMOTE_ROOT/intent.json.sig" ;;
  *) exit 2 ;;
esac
""")
            tailscale.chmod(0o755)
            intent = root / "intent.json"
            intent.write_text('{"action":"test"}\n')
            master, slave = pty.openpty()
            try:
                completed = subprocess.run(
                    [os.environ.get("KLOKAST_TEST_BASH", "bash"), str(SIGNER), "--purpose", "platform-apply", "--intent", str(intent),
                     "--controller", "k002-ops"],
                    stdin=slave, stdout=slave, stderr=subprocess.PIPE, timeout=15,
                    env={**os.environ, "PATH": str(fake_bin) + os.pathsep + os.environ["PATH"],
                         "FAKE_REMOTE_ROOT": str(remote)},
                )
            finally:
                os.close(slave)
                os.close(master)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn(b"BEGIN SSH SIGNATURE", intent.with_suffix(".json.sig").read_bytes())
            self.assertEqual(list(remote.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
