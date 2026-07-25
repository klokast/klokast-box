#!/usr/bin/env python3
import argparse
import base64
import mimetypes
import os
import posixpath
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def parse_header(value):
    if ":" not in value:
        raise argparse.ArgumentTypeError("headers must be in 'Name: value' form")
    name, header_value = value.split(":", 1)
    return name.strip(), header_value.strip()


def request(method, url, auth, headers, body=None, content_type=None, timeout=60):
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Authorization", f"Basic {auth}")
    for name, value in headers:
        req.add_header(name, value)
    if content_type:
        req.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        if method == "MKCOL" and exc.code == 405:
            return exc.code, exc.read()
        raise


def join_url(base_url, remote_path):
    return base_url.rstrip("/") + "/" + urllib.parse.quote(remote_path.strip("/"))


def iter_files(local_dir):
    for path in sorted(local_dir.rglob("*")):
        if path.is_file():
            yield path


def main():
    parser = argparse.ArgumentParser(description="Publish a static directory to WebDAV and verify byte counts.")
    parser.add_argument("--local-dir", required=True, type=Path)
    parser.add_argument("--remote-dir", required=True, help="Remote directory such as www/my-article")
    parser.add_argument("--base-url", required=True, help="WebDAV files base URL, without the remote dir")
    parser.add_argument("--username", required=True)
    parser.add_argument("--password-env", required=True, help="Environment variable containing the password")
    parser.add_argument("--header", action="append", default=[], type=parse_header, help="Extra request header, e.g. 'Host: next.example.com'")
    args = parser.parse_args()

    password = os.environ.get(args.password_env)
    if not password:
        print(f"missing password env var: {args.password_env}", file=sys.stderr)
        return 2
    if not args.local_dir.is_dir():
        print(f"local dir does not exist: {args.local_dir}", file=sys.stderr)
        return 2

    auth = base64.b64encode(f"{args.username}:{password}".encode()).decode()
    remote_dir = args.remote_dir.strip("/")

    directories = set()
    files = []
    for path in iter_files(args.local_dir):
        rel = path.relative_to(args.local_dir).as_posix()
        remote_path = posixpath.join(remote_dir, rel)
        files.append((path, remote_path))
        parent = posixpath.dirname(remote_path)
        while parent:
            directories.add(parent)
            parent = posixpath.dirname(parent)

    for directory in sorted(directories, key=lambda item: (item.count("/"), item)):
        status, _ = request("MKCOL", join_url(args.base_url, directory), auth, args.header)
        print("MKCOL", directory, status)

    for local_path, remote_path in files:
        data = local_path.read_bytes()
        content_type = mimetypes.guess_type(local_path.name)[0] or "application/octet-stream"
        status, _ = request("PUT", join_url(args.base_url, remote_path), auth, args.header, data, content_type)
        print("PUT", remote_path, status, len(data))

    for local_path, remote_path in files:
        status, data = request("GET", join_url(args.base_url, remote_path), auth, args.header)
        expected = local_path.stat().st_size
        print("GET", remote_path, status, len(data), "expected", expected)
        if len(data) != expected:
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
