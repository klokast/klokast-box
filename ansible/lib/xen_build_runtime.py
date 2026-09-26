"""Fixed Xen build transport. No profile, production disk, or adoption authority.

Callers own fresh operation storage and exact domain names and UUIDs. A failed
stop keeps disks attached and files present for reconciliation.
"""
import hashlib
import json
import os
from pathlib import Path
import pty
import re
import subprocess
import threading
import time

MIB = 1024 * 1024
TIMEOUT = 1500


def run(argv, check=True, timeout=60):
    result = subprocess.run([str(v) for v in argv], capture_output=True, text=True,
                            stdin=subprocess.DEVNULL, timeout=timeout)
    if check and result.returncode:
        raise RuntimeError("dom0 builder command failed: " + str(argv[0]))
    return result

def checksum(path, count=None):
    result = hashlib.sha256()
    with path.open("rb") as source:
        remaining = path.stat().st_size if count is None else count
        while remaining:
            chunk = source.read(min(remaining, MIB))
            if not chunk:
                raise RuntimeError("builder output is truncated")
            result.update(chunk)
            remaining -= len(chunk)
    return result.hexdigest()

def safe_file(path, maximum):
    info = path.lstat()
    if (path.is_symlink() or not path.is_file() or info.st_uid != 0 or info.st_nlink != 1 or
            info.st_mode & 0o022 or not 0 < info.st_size <= maximum):
        raise RuntimeError("builder file has unsafe ownership, type, mode, or size: " + path.name)

def safe_directory(path):
    info = path.lstat()
    if path.is_symlink() or not path.is_dir() or info.st_uid != 0 or info.st_mode & 0o022:
        raise RuntimeError("builder directory is not private root-owned storage")

def write(path, value):
    temporary = path.with_suffix(".new")
    with temporary.open("x") as stream:
        json.dump(value, stream, sort_keys=True, separators=(",", ":"))
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)

def duplicate_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise RuntimeError("duplicate builder JSON field")
        result[key] = value
    return result

def domain(name):
    # List all domains successfully before concluding a builder no longer exists.
    output = run(["xl", "list", "-l"]).stdout
    records = json.loads(output)
    if not isinstance(records, list) or not records:
        raise RuntimeError("Xen returned no complete domain inventory")
    for record in records:
        info = record.get("config", {}).get("c_info", {})
        if (type(record.get("domid")) is not int or not isinstance(info.get("name"), str) or
                (record["domid"] == 0 and info["name"] != "Domain-0") or
                (record["domid"] != 0 and not isinstance(info.get("uuid"), str))):
            raise RuntimeError("Xen returned an unsupported domain inventory format")
    matches = [item for item in records if item.get("config", {}).get("c_info", {}).get("name") == name]
    if len(matches) > 1:
        raise RuntimeError("builder domain identity is ambiguous")
    return matches[0] if matches else None

def require_identity(record, identity):
    if (record.get("config", {}).get("c_info", {}).get("uuid") != identity or
            type(record.get("domid")) is not int or record["domid"] <= 0):
        raise RuntimeError("builder domain UUID changed; refusing to control it")

def read_slot(path):
    with path.open("rb") as stream:
        data = stream.read(MIB)
    try:
        return json.loads(data.split(b"\0", 1)[0], object_pairs_hook=duplicate_fields)
    except (ValueError, UnicodeError) as error:
        raise RuntimeError("guest stopped without a complete result; inspect its isolated console log") from error

def completion_ready(work, request, slot="result.slot", kind="klokast.vm-template-build-result.v1"):
    # PVH firmware can halt instead of powering off. The guest publishes this
    # bounded result only after unmounting its root output. Use it only as a
    # signal to stop our disposable domain. Verify all output after it stops.
    with (work / slot).open("rb") as stream:
        data = stream.read(MIB).split(b"\0", 1)[0]
    try:
        value = json.loads(data, object_pairs_hook=duplicate_fields)
    except (ValueError, UnicodeError, RuntimeError):
        return False
    return (isinstance(value, dict) and value.get("kind") == kind and
            type(value.get("success")) is bool and value.get("operation_id") == request["operation_id"] and
            value.get("inputs_sha256") == request["inputs_sha256"])

def capture_console(descriptor, path):
    # Keep bounded diagnostics even if an upstream package floods its console.
    # Continue draining after the limit so logging cannot block guest progress.
    with path.open("xb") as stream:
        written = 0
        while True:
            try:
                chunk = os.read(descriptor, 4096)
            except OSError:
                return  # Linux PTYs report EIO when the child closes its end.
            if not chunk:
                return
            kept = chunk[:max(0, MIB - written)]
            stream.write(kept)
            stream.flush()
            written += len(kept)

def loop_devices(path):
    return sorted("/dev/" + item.parents[1].name for item in Path("/sys/block").glob("loop*/loop/backing_file")
                  if item.read_text().strip().lstrip("/") == str(path).lstrip("/"))

def attach_loop(path, readonly=False):
    if loop_devices(path):
        raise RuntimeError("builder file already has a loop attachment")
    run(["losetup", *(["-r"] if readonly else []), "-f", path])
    devices = loop_devices(path)
    if len(devices) != 1 or not re.fullmatch(r"/dev/loop[0-9]+", devices[0]):
        raise RuntimeError("builder loop attachment is ambiguous")
    return devices[0]

def detach_loop(path, device):
    if loop_devices(path) != [device]:
        raise RuntimeError("builder loop identity changed; refusing to detach it")
    run(["losetup", "-d", device])
    if loop_devices(path):
        raise RuntimeError("builder loop device remains; retain its backing file")

def boot_guest(work, name, identity, config, request, *, slot="result.slot",
               kind="klokast.vm-template-build-result.v1", timeout=TIMEOUT):
    console, collector, console_fd = None, None, None
    try:
        run(["xl", "create", "-p", config])
        current = domain(name)
        if current is None:
            raise RuntimeError("paused builder domain was not created")
        require_identity(current, identity)
        console_fd, slave = pty.openpty()
        try:
            console = subprocess.Popen(["xl", "console", str(current["domid"])], stdin=slave, stdout=slave, stderr=slave)
        finally:
            os.close(slave)
        collector = threading.Thread(target=capture_console, args=(console_fd, work / (name + ".log")), daemon=True)
        collector.start()
        run(["xl", "unpause", str(current["domid"])])
        deadline = time.monotonic() + timeout
        while True:
            current = domain(name)
            if current is None:
                return
            require_identity(current, identity)
            if completion_ready(work, request, slot, kind):
                return  # The finally block stops this exact guest before reads.
            if time.monotonic() >= deadline:
                raise RuntimeError("isolated guest exceeded its build or test deadline")
            time.sleep(2)
    finally:
        try:
            current = domain(name)
            if current is not None:
                require_identity(current, identity)
                run(["xl", "destroy", str(current["domid"])])
                if domain(name) is not None:
                    raise RuntimeError("disposable guest remains; retain its disks")
        finally:
            if console is not None:
                console.terminate()
                try:
                    console.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    console.kill()
                    console.wait(timeout=10)
                if collector is not None:
                    collector.join(timeout=10)
            if console_fd is not None:
                os.close(console_fd)
