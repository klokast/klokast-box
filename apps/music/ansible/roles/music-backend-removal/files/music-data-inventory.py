#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
import re
import stat
import subprocess


IDENTITY = re.compile(r"[a-z0-9](?:[a-z0-9_.-]{0,126}[a-z0-9])?")


def volume_mountpoint(user, volume):
    if not IDENTITY.fullmatch(user) or not IDENTITY.fullmatch(volume):
        raise SystemExit("Music data inventory received an invalid Podman identity")
    exists = subprocess.run(
        ["doas", "-u", user, "podman", "volume", "exists", volume],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=60,
    )
    if exists.returncode == 1:
        return None
    if exists.returncode != 0:
        raise SystemExit("Podman could not determine whether a declared data volume exists")
    inspected = subprocess.run(
        ["doas", "-u", user, "podman", "volume", "inspect", "--format={{.Mountpoint}}", volume],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=60,
    )
    if inspected.returncode != 0:
        raise SystemExit("Podman could not inspect a declared data volume")
    mountpoint = inspected.stdout.strip()
    if not mountpoint or not os.path.isabs(mountpoint):
        raise SystemExit("Podman returned an unsafe data-volume mountpoint")
    metadata = os.lstat(mountpoint)
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise SystemExit("Podman returned a data-volume mountpoint that is not a real directory")
    return mountpoint


def add_field(digest, value):
    encoded = value.encode("utf-8", errors="surrogateescape")
    digest.update(len(encoded).to_bytes(8, "big"))
    digest.update(encoded)


def inventory_volume(user, volume):
    mountpoint = volume_mountpoint(user, volume)
    digest = hashlib.sha256()
    add_field(digest, volume)
    if mountpoint is None:
        add_field(digest, "absent")
        return {"name": volume, "present": False, "files": 0, "bytes": 0, "sha256": digest.hexdigest()}

    files = 0
    total_bytes = 0
    add_field(digest, "present")
    for current, directories, names in os.walk(mountpoint, topdown=True, followlinks=False):
        directories.sort()
        names.sort()
        for name in directories + names:
            path = os.path.join(current, name)
            relative = os.path.relpath(path, mountpoint)
            metadata = os.lstat(path)
            add_field(digest, relative)
            add_field(digest, format(stat.S_IMODE(metadata.st_mode), "04o"))
            add_field(digest, str(metadata.st_uid))
            add_field(digest, str(metadata.st_gid))
            if stat.S_ISDIR(metadata.st_mode):
                add_field(digest, "directory")
                continue
            if stat.S_ISLNK(metadata.st_mode):
                add_field(digest, "symlink")
                add_field(digest, os.readlink(path))
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise SystemExit("data volume contains an unsupported filesystem object")
            add_field(digest, "file")
            digest.update(metadata.st_size.to_bytes(8, "big"))
            files += 1
            total_bytes += metadata.st_size
            with open(path, "rb") as handle:
                while chunk := handle.read(1024 * 1024):
                    digest.update(chunk)
    return {
        "name": volume,
        "present": True,
        "files": files,
        "bytes": total_bytes,
        "sha256": digest.hexdigest(),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--podman-user", required=True)
    parser.add_argument("--volume", action="append", required=True)
    args = parser.parse_args()
    volumes = [inventory_volume(args.podman_user, value) for value in args.volume]
    combined = hashlib.sha256()
    for value in volumes:
        combined.update(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    print(json.dumps({"volumes": volumes, "sha256": combined.hexdigest()}, sort_keys=True))


if __name__ == "__main__":
    main()
