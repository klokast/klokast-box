#!/usr/bin/env python3
"""VM inspection, local image preparation and checked artifact cleanup."""

import datetime as dt
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import tempfile
from platform_updates import UpdateError, canonical, unique_object
import platform_source

REPO = Path(__file__).resolve().parents[2]
APPROVED_REPO = Path("/home/smith/src/klokast/klokast-box")
STATE = Path("/var/lib/klokast/updates/discovery")
CACHE = Path("/var/cache/klokast/updates")
MAP = Path("/var/lib/klokast/platform-map/current.json")
PROFILE = REPO / "ansible/update-profiles/shared-alpine-v1.json"
RETENTION_CATALOGS = (REPO / 'apps/music/vm-retention.json',)
GUARD = Path("/usr/local/sbin/klokast-controller-guard")
HOST = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")

def now():
    return dt.datetime.now(dt.timezone.utc)


def load(path):
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 64 * 1024 * 1024:
        raise UpdateError("evidence file is absent, unsafe, or too large: " + str(path))
    return json.loads(path.read_text(), object_pairs_hook=unique_object)


def optional(path):
    return load(path) if path.exists() else None


def write(path, value):
    # State paths are fixed and live outside both Git repositories.
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, prefix=".update-", delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(canonical(value) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    try:
        temporary.replace(path)
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def command(argv, *, timeout=120, log=None):
    environment = os.environ.copy()
    environment['ANSIBLE_CONFIG'] = str(REPO / 'ansible/ansible.cfg')
    try:
        # Each bounded child has its own group. A timeout also stops its SSH
        # and Ansible children instead of leaving an abandoned scan running.
        with subprocess.Popen([str(v) for v in argv], stdin=subprocess.DEVNULL,
                              stdout=log or subprocess.PIPE, stderr=log or subprocess.PIPE,
                              text=True, start_new_session=True, env=environment) as process:
            try:
                stdout, _stderr = process.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                raise UpdateError("bounded inspection command timed out")
            if process.returncode:
                raise UpdateError("inspection command failed; inspect the controller scan log")
            return stdout or ""
    except OSError as error:
        raise UpdateError("required controller tool is unavailable") from error


def require_controller():
    status = json.loads(command([GUARD, "--status", "--json", "--require-active"]), object_pairs_hook=unique_object)
    if status.get("active") is not True or status.get("configured") is not True or status.get("role") != "active":
        raise UpdateError("VM update inspection requires the explicitly active controller")
    local = socket.gethostname().split(".")[0]
    if status.get("hostname") != local or not local.endswith("-ops") or local.startswith(("vultr-", "hetzner-")):
        raise UpdateError("run this command on the active box controller as smith")
    if os.geteuid() == 0 or __import__("pwd").getpwuid(os.geteuid()).pw_name != "smith":
        raise UpdateError("run platform-update as smith on the active controller")
    platform_source.require_development()
    return status


