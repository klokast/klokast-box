#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


APP_RESOURCE_ROOT = Path(os.environ.get("KLOKAST_APP_RESOURCE_ROOT", "/etc/klokast/app-resources"))
KIND_DIRS = {
    "router-forward": APP_RESOURCE_ROOT / "router-forward.d",
    "vm-input": APP_RESOURCE_ROOT / "vm-input.d",
}
SENTINEL = "000-empty.nft"


def parse_args():
    parser = argparse.ArgumentParser(
        description="Apply or verify keyed Klokast app-resource nft snippets."
    )
    parser.add_argument("mode", choices=("apply", "verify"))
    parser.add_argument("--desired", required=True)
    parser.add_argument("--node-name", required=True)
    parser.add_argument("--node-role", required=True)
    parser.add_argument("--scope-app", action="append", default=[])
    parser.add_argument("--nft", default="/usr/sbin/nft")
    parser.add_argument("--metadata-root", default="/etc/klokast/platform-resources")
    return parser.parse_args()


def load_json(path):
    try:
        with Path(path).open(encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        raise SystemExit(f"missing JSON file: {path}")
    except json.JSONDecodeError as error:
        raise SystemExit(f"invalid JSON in {path}: {error}")


def host_effective_files(desired, node_name, node_role):
    files = []
    for item in desired.get("app_resource_effective_files") or []:
        if item.get("node") != node_name:
            continue
        if item.get("host_role") != node_role:
            continue
        if item.get("kind") not in KIND_DIRS:
            raise SystemExit(f"unsupported resource kind: {item.get('kind')}")
        files.append(item)
    return files


def parse_header(path):
    metadata = {
        "owners": [],
        "key": "",
        "kind": "",
        "identity": "",
    }
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.startswith("#"):
                    break
                key, sep, value = line[1:].strip().partition(":")
                if not sep:
                    continue
                key = key.strip()
                value = value.strip()
                if key == "klokast-resource-owners":
                    metadata["owners"] = [
                        item for item in value.split(",") if item
                    ]
                elif key == "klokast-resource-key":
                    metadata["key"] = value
                elif key == "klokast-resource-kind":
                    metadata["kind"] = value
                elif key == "klokast-resource-rendered-identity":
                    metadata["identity"] = value
    except FileNotFoundError:
        pass
    return metadata


def ensure_dirs():
    APP_RESOURCE_ROOT.mkdir(parents=True, exist_ok=True)
    for directory in KIND_DIRS.values():
        directory.mkdir(parents=True, exist_ok=True)
        sentinel = directory / SENTINEL
        if not sentinel.exists():
            sentinel.write_text(
                "# Empty placeholder so nft include globs always match.\n",
                encoding="utf-8",
            )


def write_if_changed(path, content):
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return False
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(content, encoding="utf-8")
    os.chmod(tmp, 0o644)
    tmp.replace(path)
    return True


def desired_by_path(host_files):
    result = {}
    for item in host_files:
        path = KIND_DIRS[item["kind"]] / item["filename"]
        result[path] = item
    return result


def existing_resource_files():
    files = []
    for directory in KIND_DIRS.values():
        if not directory.exists():
            continue
        for path in sorted(directory.glob("*.nft")):
            if path.name == SENTINEL:
                continue
            files.append(path)
    return files


def scoped(existing_metadata, desired_item, scope):
    if not scope:
        return True
    owners = set(existing_metadata.get("owners") or [])
    if desired_item:
        owners.update(desired_item.get("owners") or [])
    return bool(owners & scope)


def apply_resources(desired, args):
    ensure_dirs()
    scope = set(args.scope_app)
    desired_files = desired_by_path(
        host_effective_files(desired, args.node_name, args.node_role)
    )
    changed = False
    written = []
    removed = []
    skipped = []

    for path, item in sorted(desired_files.items()):
        metadata = parse_header(path)
        if not scoped(metadata, item, scope):
            skipped.append(str(path))
            continue
        if write_if_changed(path, item["content"]):
            changed = True
            written.append(str(path))

    for path in existing_resource_files():
        item = desired_files.get(path)
        metadata = parse_header(path)
        if item is not None:
            continue
        if not scoped(metadata, None, scope):
            skipped.append(str(path))
            continue
        path.unlink()
        changed = True
        removed.append(str(path))

    print(
        json.dumps(
            {
                "changed": changed,
                "written": written,
                "removed": removed,
                "skipped": skipped,
            },
            sort_keys=True,
        )
    )
    return changed


def read_text(path):
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def verify_metadata(desired, args):
    metadata_root = Path(args.metadata_root)
    desired_on_host = load_json(metadata_root / "desired.json")
    if desired_on_host.get("registry_sha256") != desired.get("registry_sha256"):
        raise SystemExit("target desired ledger registry_sha256 does not match compiler output")
    if desired_on_host.get("compiler_version") != desired.get("compiler_version"):
        raise SystemExit("target desired ledger compiler_version does not match compiler output")
    last_applied = load_json(metadata_root / "last-applied.json")
    if last_applied.get("registry_sha256") != desired.get("registry_sha256"):
        raise SystemExit("target last-applied registry_sha256 does not match compiler output")


def verify_resources(desired, args):
    ensure_dirs()
    verify_metadata(desired, args)
    scope = set(args.scope_app)
    host_files = host_effective_files(desired, args.node_name, args.node_role)
    desired_files = desired_by_path(host_files)
    identities = [item["rendered_rule_identity"] for item in host_files]
    failures = []

    for path, item in sorted(desired_files.items()):
        if scope and not (scope & set(item.get("owners") or [])):
            continue
        if not path.exists():
            failures.append(f"missing snippet {path}")
            continue
        content = read_text(path)
        if content != item["content"]:
            failures.append(f"snippet differs {path}")

    for path in existing_resource_files():
        metadata = parse_header(path)
        if scope and not (scope & set(metadata.get("owners") or [])):
            continue
        if path not in desired_files:
            failures.append(f"stale snippet {path}")

    if identities and Path(args.nft).exists():
        ruleset = subprocess.run(
            [args.nft, "list", "ruleset"],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if ruleset.returncode != 0:
            failures.append("nft list ruleset failed")
        else:
            for identity in identities:
                if identity not in ruleset.stdout:
                    failures.append(f"missing live nft identity {identity}")

    if failures:
        print(json.dumps({"ok": False, "failures": failures}, sort_keys=True))
        raise SystemExit(1)

    print(json.dumps({"ok": True, "checked": len(host_files)}, sort_keys=True))


def main():
    args = parse_args()
    desired = load_json(args.desired)
    if args.mode == "apply":
        apply_resources(desired, args)
        return
    verify_resources(desired, args)


if __name__ == "__main__":
    main()
