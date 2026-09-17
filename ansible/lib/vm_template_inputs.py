"""Freeze verified Alpine package inputs without installing controller packages.

This module creates evidence and a build payload. It cannot activate a release
or authorize a Xen operation. All paths are controller-local staging paths.
"""

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import gzip
import tempfile

from platform_updates import UpdateError, branch_number, canonical, digest, unique_object

KIND = "klokast.vm-template-inputs.v1"
NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9+_.-]*")
VERSION = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9+_.~-]*")
HASH = re.compile(r"[0-9a-f]{64}")
KEY = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9@+_.-]*\.pub")
MAX_PACKAGE = 512 * 1024 * 1024
MAX_INPUTS = 2 * 1024 * 1024 * 1024
MAX_PACKAGES = 512
ORIGIN = "https://dl-cdn.alpinelinux.org/alpine"


def matches(pattern, value):
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def regular(path, maximum):
    path = Path(path)
    return not path.is_symlink() and path.is_file() and 0 < path.stat().st_size <= maximum


def sha256(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def invoke(argv, *, cwd=None, timeout=900):
    try:
        result = subprocess.run([str(v) for v in argv], cwd=cwd, timeout=timeout,
                                stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C"})
    except (OSError, subprocess.TimeoutExpired) as error:
        raise UpdateError("template input command is unavailable or timed out") from error
    if result.returncode:
        # Package metadata is untrusted text. Do not emit it as a log instruction.
        raise UpdateError("template input command failed: " + str(argv[0]) + " (exit " + str(result.returncode) + ")")
    return result.stdout


def read_package(path):
    """Read one bounded identity after native APK signature verification.

    APK v3 is deliberately refused until its native manifest format is wired
    into the complete build path. No archive member is extracted by Python.
    """
    path = Path(path)
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= MAX_PACKAGE:
        raise UpdateError("package input is not a bounded regular file")
    try:
        # gzip.GzipFile follows APK's concatenated gzip members. tarfile's own
        # streaming gzip adapter stops after the first (signature) member.
        with gzip.open(path, "rb") as compressed, tarfile.open(fileobj=compressed, mode="r|", ignore_zeros=True) as archive:
            found = []
            # Streaming iteration avoids allocating an unbounded member list.
            expanded = 0
            for index, member in enumerate(archive):
                expanded += member.size
                if index > 100000 or expanded > 4 * 1024 * 1024 * 1024:
                    raise UpdateError("package archive has too many members")
                if member.name == ".PKGINFO":
                    if not member.isfile() or member.size > 128 * 1024:
                        raise UpdateError("package identity has an unsafe archive entry")
                    found.append(archive.extractfile(member).read().decode("utf-8"))
            if len(found) != 1:
                raise UpdateError("package has no unique identity record")
    except (tarfile.TarError, UnicodeError, OSError) as error:
        raise UpdateError("package format is unsupported or unreadable") from error
    fields = {}
    for line in found[0].splitlines():
        key, separator, value = line.partition(" = ")
        if separator and key in {"pkgname", "pkgver", "arch", "origin"}:
            if key in fields:
                raise UpdateError("package has duplicate identity fields")
            fields[key] = value
    if (not NAME.fullmatch(fields.get("pkgname", "")) or
            not VERSION.fullmatch(fields.get("pkgver", "")) or
            not NAME.fullmatch(fields.get("origin", fields.get("pkgname", ""))) or
            fields.get("arch") not in {"x86_64", "noarch"}):
        raise UpdateError("package identity or architecture is invalid")
    filename = fields["pkgname"] + "-" + fields["pkgver"] + ".apk"
    if path.name != filename:
        raise UpdateError("package filename does not match its signed identity")
    return {"name": fields["pkgname"], "version": fields["pkgver"],
            "origin": fields.get("origin", fields["pkgname"]), "architecture": fields["arch"],
            "file": "packages/" + filename, "bytes": path.stat().st_size, "sha256": sha256(path)}


def freeze(directory, profile, branch, engine_commit, *, key_root=Path("/etc/apk/keys")):
    """Use a new empty resolver root and native solver; never reuse stale indexes."""
    branch_number(branch)
    if not re.fullmatch(r"[0-9a-f]{40}", engine_commit or ""):
        raise UpdateError("template inputs require the exact engine commit")
    if (not isinstance(profile, dict) or profile.get("kind") != "klokast.vm-template-profile.v1" or
            profile.get("profile") != "shared-alpine-v1" or
            profile.get("architecture") != "x86_64" or profile.get("repository_origin") != ORIGIN or
            profile.get("repositories") != ["main", "community"]):
        raise UpdateError("template profile has unsupported repositories or architecture")
    world = profile.get("packages")
    if (not isinstance(world, list) or not world or
            any(not matches(NAME, v) for v in world) or len(world) != len(set(world)) or
            not {"linux-virt", "tailscale", "podman", "python3", "e2fsprogs", "mkinitfs"} <= set(world)):
        raise UpdateError("template profile package set is incomplete or invalid")
    directory = Path(directory)
    # A caller must allocate a fresh directory. Failed work is never published.
    if directory.exists() or directory.is_symlink():
        raise UpdateError("refusing to reuse an existing template input directory")
    directory.mkdir(mode=0o700)
    resolver = directory / "resolver"
    keys = directory / "keys"
    packages = directory / "packages"
    for path in (resolver / "etc/apk", resolver / "lib/apk/db", resolver / "var/cache/apk", keys, packages):
        path.mkdir(parents=True, mode=0o700)
    (resolver / "lib/apk/db/installed").touch()
    (resolver / "etc/apk/world").touch()
    repositories = [f"{ORIGIN}/{branch}/{name}" for name in ("main", "community")]
    repo_file = resolver / "etc/apk/repositories"
    repo_file.write_text("\n".join(repositories) + "\n")
    key_hashes = {}
    for key in sorted(Path(key_root).glob("*.pub")):
        if key.is_symlink() or not key.is_file() or key.stat().st_size > 16384:
            raise UpdateError("installed APK signing key is unsafe")
        shutil.copyfile(key, keys / key.name)
        key_hashes[key.name] = sha256(keys / key.name)
    if not key_hashes:
        raise UpdateError("installed Alpine signing keys are missing")
    options = ["/sbin/apk", "--root", resolver, "--arch", "x86_64", "--keys-dir", keys,
               "--repositories-file", repo_file, "--cache-dir", resolver / "var/cache/apk", "--no-progress"]
    invoke([*options, "update"], timeout=180)
    indexes = sorted((resolver / "var/cache/apk").glob("APKINDEX.*.tar.gz"))
    if len(indexes) != 2:
        raise UpdateError("two authenticated APK v2 indexes are required")
    index_hashes = {path.name: sha256(path) for path in indexes}
    # Prevent a refresh during dependency solving. The downloaded package set
    # must be resolved by exactly the index hashes recorded above.
    invoke([*options, "--cache-max-age", "1440", "fetch", "--recursive", "--output", packages, *sorted(world)])
    if index_hashes != {path.name: sha256(path) for path in indexes}:
        raise UpdateError("repository indexes changed while resolving template inputs")
    archives = sorted(packages.glob("*.apk"))
    if not 1 <= len(archives) <= MAX_PACKAGES or sum(v.stat().st_size for v in archives) > MAX_INPUTS:
        raise UpdateError("template package closure exceeds its limits")
    invoke(["/sbin/apk", "--keys-dir", keys, "verify", *archives])
    records = [read_package(path) for path in archives]
    if len({record["name"] for record in records}) != len(records) or not set(world) <= {v["name"] for v in records}:
        raise UpdateError("template solver produced an incomplete or ambiguous package closure")
    manifest = {"kind": KIND, "engine_commit": engine_commit, "profile": profile["profile"],
                "profile_sha256": digest(profile), "branch": branch, "architecture": "x86_64",
                "world": sorted(world), "repositories": repositories, "keys": key_hashes,
                "indexes": index_hashes, "packages": sorted(records, key=lambda v: v["name"])}
    manifest["inputs_sha256"] = digest(manifest)
    (directory / "inputs.json").write_text(canonical(manifest) + "\n")
    verify_inputs(directory, manifest)
    return manifest


def verify_inputs(directory, manifest):
    directory = Path(directory)
    fields = {"kind", "engine_commit", "profile", "profile_sha256", "branch", "architecture", "world",
              "repositories", "keys", "indexes", "packages", "inputs_sha256"}
    if (not isinstance(manifest, dict) or set(manifest) != fields or manifest["kind"] != KIND or
            manifest["inputs_sha256"] != digest({k: v for k, v in manifest.items() if k != "inputs_sha256"})):
        raise UpdateError("template input manifest has an invalid contract or checksum")
    if (not isinstance(manifest["branch"], str) or
            not isinstance(manifest["engine_commit"], str) or
            not re.fullmatch(r"[0-9a-f]{40}", manifest["engine_commit"]) or
            manifest["profile"] != "shared-alpine-v1" or not matches(HASH, manifest["profile_sha256"])):
        raise UpdateError("template source or profile identity is invalid")
    branch_number(manifest["branch"])
    if (manifest["architecture"] != "x86_64" or
            manifest["repositories"] != [f"{ORIGIN}/{manifest['branch']}/{v}" for v in ("main", "community")]):
        raise UpdateError("template input repositories or architecture differ")
    if directory.is_symlink() or any((directory / name).is_symlink() or not (directory / name).is_dir() for name in ("keys", "packages")):
        raise UpdateError("template input directories are unsafe")
    names, files, total = set(), set(), 0
    if not isinstance(manifest["packages"], list) or not 1 <= len(manifest["packages"]) <= MAX_PACKAGES:
        raise UpdateError("template input package set is invalid")
    for record in manifest["packages"]:
        if (not isinstance(record, dict) or set(record) != {"name", "version", "origin", "architecture", "file", "bytes", "sha256"} or
                not matches(NAME, record["name"]) or not matches(VERSION, record["version"]) or
                not matches(NAME, record["origin"]) or not matches(HASH, record["sha256"]) or
                record["architecture"] not in ("x86_64", "noarch") or
                record["file"] != f"packages/{record['name']}-{record['version']}.apk" or
                record["name"] in names or type(record["bytes"]) is not int or not 0 < record["bytes"] <= MAX_PACKAGE):
            raise UpdateError("template input package record is unsafe or duplicated")
        path = directory / record["file"]
        if path.is_symlink() or not path.is_file() or path.stat().st_size != record["bytes"] or sha256(path) != record["sha256"]:
            raise UpdateError("frozen package bytes changed: " + record["name"])
        names.add(record["name"])
        files.add(path.name)
        total += record["bytes"]
    if total > MAX_INPUTS or files != {path.name for path in (directory / "packages").iterdir()}:
        raise UpdateError("frozen package set changed or exceeds its limit")
    world = manifest["world"]
    if (not isinstance(world, list) or not world or any(not matches(NAME, name) for name in world) or
            world != sorted(set(world)) or not set(world) <= names):
        raise UpdateError("frozen package set does not satisfy the declared world")
    if (not isinstance(manifest["keys"], dict) or not 1 <= len(manifest["keys"]) <= 64 or
            set(manifest["keys"]) != {p.name for p in (directory / "keys").iterdir()}):
        raise UpdateError("frozen APK signing key set changed")
    for name, expected in manifest["keys"].items():
        path = directory / "keys" / name
        if (not matches(KEY, name) or not matches(HASH, expected) or
                not regular(path, 16384) or sha256(path) != expected):
            raise UpdateError("frozen APK signing key changed")
    if (not isinstance(manifest["indexes"], dict) or len(manifest["indexes"]) != 2 or
            any(not re.fullmatch(r"APKINDEX\.[a-zA-Z0-9]+\.tar\.gz", name) or not matches(HASH, value)
                for name, value in manifest["indexes"].items())):
        raise UpdateError("frozen APK index identity is invalid")


def capsule(directory, output, guest_job, smoke_job, retained_job, retained_test_job, app_library, app_adapter):
    """Produce a flat, bounded input tar disk; dom0 never mounts this disk."""
    directory, output = Path(directory), Path(output)
    manifest = json.loads((directory / "inputs.json").read_text(), object_pairs_hook=unique_object)
    verify_inputs(directory, manifest)
    if output.exists() or output.is_symlink():
        raise UpdateError("build input capsule already exists")
    with tarfile.open(output, "x", format=tarfile.USTAR_FORMAT) as archive:
        for relative in ["inputs.json", *["keys/" + k for k in sorted(manifest["keys"])],
                         *[p["file"] for p in manifest["packages"]]]:
            archive.add(directory / relative, arcname=relative, recursive=False)
        archive.add(guest_job, arcname="build.py", recursive=False)
        archive.add(smoke_job, arcname="smoke.py", recursive=False)
        archive.add(retained_job, arcname="retained_data.py", recursive=False)
        archive.add(retained_test_job, arcname="retained_data_test.py", recursive=False)
        archive.add(app_library, arcname="vm_app_compatibility.py", recursive=False)
        archive.add(app_adapter, arcname="static_site_test.py", recursive=False)
    return {"sha256": sha256(output), "bytes": output.stat().st_size}


BOOT_INIT = """#!/bin/busybox sh
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
/bin/busybox --install -s /bin
trap 'echo "Builder bootstrap failed"; poweroff -f' EXIT
set -eu
mkdir -p /proc /sys /dev /run /tmp
mount -t proc proc /proc
mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev
exec >/dev/console 2>&1
mount -t tmpfs -o mode=0755,nosuid,nodev tmpfs /run
depmod -a
modprobe xen-blkfront
modprobe ext4
modprobe tun
python3 /klokast-build.py
result=$?
echo "VM template build ended with status $result"
sync
trap - EXIT
poweroff -f
"""


def bootstrap(directory, output, guest_job):
    """Assemble an initramfs as smith. Native extract never executes scripts.

    This is a one-operation boot environment, not a persistent builder VM.
    Package installation, mkfs, chroot, and mkinitfs run only inside Xen.
    """
    directory, output = Path(directory), Path(output)
    if os.geteuid() == 0:
        raise UpdateError("assemble the disposable boot environment as unprivileged smith")
    manifest = json.loads((directory / "inputs.json").read_text(), object_pairs_hook=unique_object)
    verify_inputs(directory, manifest)
    if output.exists() or output.is_symlink():
        raise UpdateError("bootstrap output already exists")
    output.mkdir(mode=0o700)
    with tempfile.TemporaryDirectory(prefix="unpack-", dir=output) as temporary:
        root = Path(temporary)
        for record in manifest["packages"]:
            invoke(["/sbin/apk", "--keys-dir", directory / "keys", "extract", "--no-chown",
                    "--destination", root, directory / record["file"]])
        kernel = root / "boot/vmlinuz-virt"
        if kernel.is_symlink() or not kernel.is_file():
            raise UpdateError("linux-virt package has no regular boot kernel")
        shutil.copyfile(kernel, output / "kernel")
        for relative in ("init", "klokast-build.py"):
            path = root / relative
            if path.exists() or path.is_symlink():
                raise UpdateError("package closure overlaps the fixed bootstrap job")
        (root / "init").write_text(BOOT_INIT)
        (root / "init").chmod(0o755)
        shutil.copyfile(guest_job, root / "klokast-build.py")
        # Names are generated by the package extractor, never shell-expanded.
        paths = []
        for parent, directories, files in os.walk(root, followlinks=False):
            for name in sorted(directories + files):
                path = Path(parent) / name
                # Some signed packages contain execute-only files (bbsuid).
                # smith owns these scriptlessly extracted files, so grant the
                # builder read access and remove set-ID bits in the transient
                # boot archive. The real guest installation restores package
                # ownership and modes from verified archives.
                if not path.is_symlink():
                    path.chmod((path.stat().st_mode & 0o777) | (0o500 if path.is_dir() else 0o400))
                paths.append(str(path.relative_to(root)))
        raw = output / "bootstrap.cpio"
        with raw.open("xb") as stream:
            process = subprocess.run(["/usr/bin/cpio", "-0", "-o", "-H", "newc", "-R", "0:0"], cwd=root,
                                     input=b"".join(os.fsencode(name) + b"\0" for name in paths),
                                     stdout=stream, stderr=subprocess.PIPE, timeout=300)
        if process.returncode:
            (output / "cpio.log").write_bytes(process.stderr[:65536])
            raise UpdateError("native cpio failed; inspect " + str(output / "cpio.log"))
        if raw.stat().st_size > 1024 * 1024 * 1024:
            raise UpdateError("bootstrap archive exceeds one GiB")
        with raw.open("rb") as source, (output / "initramfs").open("xb") as target:
            with gzip.GzipFile(fileobj=target, mode="wb", compresslevel=3, mtime=0) as zipped:
                shutil.copyfileobj(source, zipped)
        raw.unlink()
    return {name: {"sha256": sha256(output / name), "bytes": (output / name).stat().st_size}
            for name in ("kernel", "initramfs")}
