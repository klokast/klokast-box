#!/usr/bin/env python3
"""Delete only the nine extra backups authorized by the human on 2026-09-13.

Controller-only maintenance, not an installed executor or a retirement approval.
No backup contents are retained. Use the matching Ansible playbook.
"""

import argparse
import datetime
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path


STATE = "8622695e9361d281620dc5f69d9b556f2ab637f748899985899d929cf3eaf188"
EVIDENCE = Path("/var/lib/klokast/extra-registry-backup-deletion-20260913")
# Name, SHA-256, size, mode. The inspected owner/group are 1002:1002.
TARGETS = (
    ("platform-resources.yml.20260611T020145Z.bak", "ebb17e0e3a4c87350219246b95136662762bfd7356a321596e5562eb14fd3da4", 1547, "0644"),
    ("platform-resources.yml.20260611T073643Z.pre-k001-reboot.bak", "d0d289b6b877ca79a5637a65c3faaf6472cea2546c344174985a36f00de265a7", 1547, "0644"),
    ("platform-resources.yml.20260611T091728Z.bak", "40f7cb2c5f8856a0a94a62c5d1363bd5ad545ff41519f848856379ee0ac297cc", 1547, "0644"),
    ("platform-resources.yml.20260719T075235Z.bak", "15e21c63dce00ba5ffda5420238dc47b6bf2a828f95e837642b6b0368806ad1f", 2409, "0644"),
    ("platform-resources.yml.20260719T080153Z.bak", "f51f618f853aff02f882a76fc93ad8497245170e0812e90a27dc12787de340cf", 2410, "0644"),
    ("platform-resources.yml.20260808T110239Z.bak", "f51f618f853aff02f882a76fc93ad8497245170e0812e90a27dc12787de340cf", 2410, "0644"),
    ("platform-resources.yml.20260820T152236Z.bak", "d265a35661d9e93d16e18302061991b83ea5bd5b120eafb1e1f94bfdc438090e", 2471, "0644"),
    ("platform-resources.yml.20260823T235503Z.elementary-connectivity.bak", "9acca978533a92022a4648c8333699332ce9a7cc8e19c7516f03b78792acc64d", 2499, "0600"),
    ("platform-resources.yml.pre-ap-20260608T053746Z.bak", "6eafeb8c30194b42fc431729d26641fae876a7f9891b44b03e255c064c26242d", 1417, "0600"),
)


def save(m, name, value):
    descriptor = os.open(EVIDENCE / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
    with os.fdopen(descriptor, "w") as stream:
        stream.write(json.dumps(value, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    m.fsync_directory(EVIDENCE)


def inspect(m):
    m.require_root_active()
    m.require_self_match()
    if m.read_regular(m.AUTHORITY_POINTER, 66) != (STATE + "\n").encode():
        raise ValueError("Authority State changed; review before backup deletion")
    protected = [m.retirement_metadata(p) for p in (*m.LEGACY_INPUTS, *m.OBSOLETE_BACKUPS)]
    targets = []
    for name, digest, size, mode in TARGETS:
        path = m.PRIVATE_ROOT / name
        expected = dict(path=str(path), sha256=digest, size=size, mode=mode, uid=1002, gid=1002, nlink=1)
        if m.retirement_metadata(path) != expected:
            raise ValueError(f"Backup bytes or metadata changed; deletion refused: {name}")
        targets.append(expected)
    expected_names = {p.name for p in m.OBSOLETE_BACKUPS} | {t["path"].rsplit("/", 1)[-1] for t in targets}
    if {p.name for p in m.PRIVATE_ROOT.glob("platform-resources.yml.*.bak")} != expected_names:
        raise ValueError("Backup name set changed; review the exact deletion scope")
    return dict(protected=protected, targets=targets, authority_state_sha256=STATE)


def maintain(m, execute):
    # Reuse the installed retirement/publication lock, including in prepare mode.
    with m.authority_publication_lock():
        before = inspect(m)
        if not execute:
            return dict(result="prepared", targets=len(before["targets"]), signed=False)
        # Exclusive attempt directory: interrupted or completed attempts require
        # inspection, never an automatic retry. No deleted bytes are archived.
        EVIDENCE.mkdir(mode=0o700)
        m.fsync_directory(EVIDENCE.parent)
        save(m, "before.json", before)
        if inspect(m) != before:
            raise ValueError("Inputs changed before deletion; no files removed")
        save(m, "attempt.json", dict(authorization="human task instruction 2026-09-13",
             signed=False, started_at=datetime.datetime.now(datetime.timezone.utc).isoformat()))
        removed = []
        try:
            for entry in before["targets"]:
                path = Path(entry["path"])
                if m.retirement_metadata(path) != entry:
                    raise ValueError(f"Backup changed immediately before deletion: {path.name}")
                path.unlink()
                m.fsync_directory(m.PRIVATE_ROOT)
                removed.append(path.name)
                save(m, f"removed-{len(removed):02d}.json", entry)
            m.retirement_absent([Path(entry["path"]) for entry in before["targets"]])
            if [m.retirement_metadata(p) for p in (*m.LEGACY_INPUTS, *m.OBSOLETE_BACKUPS)] != before["protected"]:
                raise ValueError("Preserved input metadata changed; inspection required")
            if m.read_regular(m.AUTHORITY_POINTER, 66) != (STATE + "\n").encode():
                raise ValueError("Authority State changed; inspection required")
            result = dict(result="deleted", removed=removed, preserved_inputs_unchanged=True,
                          authority_state_sha256=STATE, signed=False, evidence=str(EVIDENCE))
            save(m, "result.json", result)
            return result
        except Exception as error:
            # Logs retain metadata only. Deletion is permanent, not restorable.
            save(m, "failure.json", dict(result="inspection_required", removed=removed, error=str(error)))
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    loader = importlib.machinery.SourceFileLoader("installed_apply", "/usr/local/sbin/ksa-apply")
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    print(json.dumps(maintain(module, args.execute), sort_keys=True))


if __name__ == "__main__":
    main()
