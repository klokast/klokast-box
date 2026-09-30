#!/usr/bin/env python3
"""Bound a temporary dom0 APK tool transaction without upgrading existing packages."""

import argparse
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


APK = "/sbin/apk"
PROFILES = {
    "sealed-builder": (("curl", "kpartx", "sfdisk", "xorriso"), ".klokast-builder-tools", 12),
    "router-copy": (("sfdisk",), ".klokast-router-copy-tools", 24),
}
PROFILE_STATES = {
    "sealed-builder": Path("/run/klokast-builder-tools"),
    "router-copy": Path("/run/klokast-router-copy-tools"),
}
TOOLS, VIRTUAL, OPERATION_LENGTH = PROFILES["sealed-builder"]
STATE_DIR = PROFILE_STATES["sealed-builder"]
STATE_FILE = STATE_DIR / "state.json"
CACHE_DIR = STATE_DIR / "cache"
LOCK_FILE = Path("/run/klokast-dom0-temporary-tools.lock")
WORLD_FILE = Path("/etc/apk/world")
UNLOCK_FILE = Path("/run/klokast-dom0-apk-unlocked")
UNLOCK_CONTENT = "klokast-dom0-apk-maintenance-v1\n"
OPERATION_RE = re.compile(r"^[0-9a-f]{12}$")
PACKAGE_RE = re.compile(r"^[a-z0-9][a-z0-9+_.-]*$")
VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9+_.:~+-]*$")
ACTION_RE = re.compile(r"^\(\s*\d+/\d+\) ([A-Za-z]+) ([^ ]+) \([^)]*\)$")


class ToolError(Exception):
    pass


def select_profile(profile):
    global TOOLS, VIRTUAL, OPERATION_LENGTH, STATE_DIR, STATE_FILE, CACHE_DIR, OPERATION_RE
    if profile not in PROFILES:
        raise ToolError("temporary dom0 tools require a reviewed closed profile")
    TOOLS, VIRTUAL, OPERATION_LENGTH = PROFILES[profile]
    STATE_DIR = PROFILE_STATES[profile]
    STATE_FILE = STATE_DIR / "state.json"
    CACHE_DIR = STATE_DIR / "cache"
    OPERATION_RE = re.compile(rf"^[0-9a-f]{{{OPERATION_LENGTH}}}$")


def command(argv):
    result = subprocess.run(argv, capture_output=True, text=True, check=False)
    if result.returncode:
        raise ToolError(f"{' '.join(argv[:3])} failed: {(result.stderr or result.stdout).strip()}")
    return result.stdout


def installed_packages():
    records = json.loads(
        command([APK, "query", "--installed", "--format", "json", "--fields", "name,version", "*"])
    )
    if not isinstance(records, list) or not records:
        raise ToolError("APK returned no installed package records")
    packages = {}
    for item in records:
        if not isinstance(item, dict):
            raise ToolError("APK returned a malformed package record")
        name, version = item.get("name"), item.get("version")
        if not isinstance(name, str) or (name != VIRTUAL and not PACKAGE_RE.fullmatch(name)):
            raise ToolError("APK returned an invalid package name")
        if not isinstance(version, str) or not VERSION_RE.fullmatch(version):
            raise ToolError("APK returned an invalid package version")
        if name in packages:
            raise ToolError(f"APK returned duplicate installed package {name}")
        packages[name] = version
    return packages


def world_lines(text, *, allow_virtual=False):
    lines = text.splitlines()
    if not lines or len(lines) != len(set(lines)) or any(
        not PACKAGE_RE.fullmatch(x) and not (allow_virtual and x == VIRTUAL) for x in lines
    ):
        raise ToolError("APK world has unsupported or duplicate constraints")
    return set(lines)


def require_world(expected):
    actual = WORLD_FILE.read_text(encoding="utf-8")
    if actual != expected:
        raise ToolError("dom0 APK world differs from the exact managed world")


def check_simulation(output, baseline, *, adding):
    actions = []
    for line in output.splitlines():
        if line.startswith("OK: "):
            continue
        match = ACTION_RE.fullmatch(line)
        if match is None:
            raise ToolError(f"unrecognized APK simulation line: {line}")
        action, name = match.groups()
        if adding:
            if action != "Installing" or name in baseline:
                raise ToolError(f"APK would change an existing package: {line}")
        elif action not in ("Purging", "Removing") or name in baseline:
            raise ToolError(f"APK cleanup would change an existing package: {line}")
        actions.append((action, name))
    if not actions or (adding and not set(TOOLS).issubset({name for _, name in actions})):
        raise ToolError("APK simulation did not contain the expected tool transaction")
    if VIRTUAL not in {name for _, name in actions}:
        raise ToolError("APK simulation omitted the temporary virtual package")


def write_state(state):
    STATE_DIR.mkdir(mode=0o700)
    if STATE_DIR.is_symlink():
        raise ToolError("temporary tool state directory is a symlink")
    temporary = STATE_DIR / "state.json.tmp"
    with temporary.open("x", encoding="utf-8") as handle:
        json.dump(state, handle, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(STATE_FILE)


def remove_state():
    if CACHE_DIR.exists() or CACHE_DIR.is_symlink():
        if CACHE_DIR.is_symlink() or not CACHE_DIR.is_dir():
            raise ToolError("temporary tool cache is not a private directory")
        shutil.rmtree(CACHE_DIR)
    STATE_FILE.unlink()
    STATE_DIR.rmdir()


def read_state(operation):
    if STATE_DIR.is_symlink() or STATE_FILE.is_symlink():
        raise ToolError("temporary tool state is a symlink")
    if not STATE_FILE.is_file():
        raise ToolError("no recoverable temporary tool transaction exists")
    state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    if state.get("operation") != operation:
        raise ToolError("temporary tool operation does not match the recovery record")
    if not isinstance(state.get("packages"), dict) or not isinstance(state.get("world"), str):
        raise ToolError("temporary tool recovery record is malformed")
    return state


def unlock():
    if UNLOCK_FILE.exists() or UNLOCK_FILE.is_symlink():
        raise ToolError("another dom0 APK maintenance unlock already exists")
    fd = os.open(UNLOCK_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(UNLOCK_CONTENT)


def lock_apk():
    if UNLOCK_FILE.is_symlink():
        raise ToolError("dom0 APK unlock is a symlink")
    if UNLOCK_FILE.exists():
        if UNLOCK_FILE.read_text(encoding="utf-8") != UNLOCK_CONTENT:
            raise ToolError("dom0 APK unlock belongs to another transaction")
        UNLOCK_FILE.unlink()


def begin(operation, expected_world_path):
    if any(path.exists() or path.is_symlink() for path in PROFILE_STATES.values()):
        raise ToolError("an earlier temporary tool transaction needs recovery")
    if UNLOCK_FILE.exists() or UNLOCK_FILE.is_symlink():
        raise ToolError("dom0 APK maintenance is already unlocked")
    expected = expected_world_path.read_text(encoding="utf-8")
    world_lines(expected)
    require_world(expected)
    baseline = installed_packages()
    if any(tool in baseline for tool in TOOLS):
        raise ToolError("requested temporary tools must all be absent before this transaction")
    pins = [f"{name}={baseline[name]}" for name in sorted(baseline)]
    add = [APK, "add", "--virtual", VIRTUAL, *TOOLS, *pins]
    check_simulation(command([APK, "add", "--simulate", "--virtual", VIRTUAL, *TOOLS, *pins]), baseline, adding=True)
    write_state({"operation": operation, "packages": baseline, "world": expected})
    CACHE_DIR.mkdir(mode=0o700)
    held_unlock = False
    try:
        unlock()
        held_unlock = True
        command([APK, "--cache-dir", str(CACHE_DIR), "--cache-predownload", *add[1:]])
        after = installed_packages()
        if any(after.get(name) != version for name, version in baseline.items()):
            raise ToolError("a pre-existing dom0 package changed during tool installation")
        actual_world = world_lines(WORLD_FILE.read_text(encoding="utf-8"), allow_virtual=True)
        if actual_world != world_lines(expected) | {VIRTUAL}:
            raise ToolError("temporary tool installation changed unexpected APK world entries")
    finally:
        if held_unlock:
            lock_apk()


def end(operation, expected_world_path):
    if not expected_world_path.is_file() or expected_world_path.is_symlink():
        raise ToolError("the recorded expected APK world is missing or unsafe")
    expected_from_controller = expected_world_path.read_text(encoding="utf-8")
    world_lines(expected_from_controller)
    if not STATE_DIR.exists() and not STATE_DIR.is_symlink():
        if UNLOCK_FILE.exists() or UNLOCK_FILE.is_symlink():
            raise ToolError("APK is unlocked without a temporary tool recovery record")
        require_world(expected_from_controller)
        if any(tool in installed_packages() for tool in TOOLS):
            raise ToolError("temporary tools remain installed without a recovery record")
        return
    state = read_state(operation)
    if UNLOCK_FILE.exists() or UNLOCK_FILE.is_symlink():
        raise ToolError("APK is unlocked; inspect the interrupted transaction before recovery")
    baseline = state["packages"]
    expected = state["world"]
    if expected != expected_from_controller:
        raise ToolError("the expected APK world changed during the tool transaction")
    current_world = world_lines(WORLD_FILE.read_text(encoding="utf-8"), allow_virtual=True)
    if current_world == world_lines(expected) and installed_packages() == baseline:
        remove_state()
        return
    if current_world != world_lines(expected) | {VIRTUAL}:
        raise ToolError("APK world drift blocks automatic temporary-tool cleanup")
    current_packages = installed_packages()
    if any(current_packages.get(name) != version for name, version in baseline.items()):
        raise ToolError("pre-existing package drift blocks temporary-tool cleanup")
    check_simulation(command([APK, "--no-network", "del", "--simulate", VIRTUAL]), baseline, adding=False)
    held_unlock = False
    try:
        unlock()
        held_unlock = True
        command([APK, "--no-network", "del", VIRTUAL])
        require_world(expected)
        if installed_packages() != baseline:
            raise ToolError("temporary-tool cleanup did not restore the exact package set")
    finally:
        if held_unlock:
            lock_apk()
    remove_state()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("begin", "end"))
    parser.add_argument("--profile", required=True, choices=tuple(PROFILES))
    parser.add_argument("--operation", required=True)
    parser.add_argument("--expected-world", required=True, type=Path)
    args = parser.parse_args()
    select_profile(args.profile)
    if not OPERATION_RE.fullmatch(args.operation):
        parser.error(f"operation must contain exactly {OPERATION_LENGTH} lowercase hex characters")
    if os.geteuid() != 0:
        parser.error("run as root through the active controller")
    with LOCK_FILE.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            if args.action == "begin":
                begin(args.operation, args.expected_world)
            else:
                end(args.operation, args.expected_world)
        except (ToolError, OSError, ValueError, json.JSONDecodeError) as error:
            print(f"dom0-temporary-tools: {error}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
