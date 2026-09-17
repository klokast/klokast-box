"""Fetch official Alpine evidence. Native APK verifies repository signatures."""

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import urllib.parse
import urllib.request

from platform_updates import UpdateError, branch_number, parse_apk_database, timestamp, unique_object

MAX_DOWNLOAD = 32 * 1024 * 1024


def invoke(argv, timeout=120):
    try:
        result = subprocess.run([str(v) for v in argv], capture_output=True, text=True,
                                stdin=subprocess.DEVNULL, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise UpdateError("required native APK command is unavailable or timed out") from error
    if result.returncode:
        raise UpdateError("native APK command failed; repository evidence is not accepted")
    return result.stdout


def compare_versions(first, second):
    # The values come from installed records and authenticated indexes. APK
    # receives separate arguments and never a caller-supplied shell command.
    if any(not isinstance(v, str) or not v or v.startswith("-") for v in (first, second)):
        raise UpdateError("APK version has an invalid form")
    result = invoke(["/sbin/apk", "version", "-t", first, second], timeout=10).strip()
    if result not in ("<", "=", ">"):
        raise UpdateError("native APK did not return a version comparison")
    return result


class SameOriginRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        before, after = urllib.parse.urlsplit(request.full_url), urllib.parse.urlsplit(newurl)
        if after.scheme != "https" or after.netloc != before.netloc:
            raise UpdateError("official metadata redirected outside its approved HTTPS origin")
        return super().redirect_request(request, fp, code, message, headers, newurl)


def fetch_json(url):
    opener = urllib.request.build_opener(SameOriginRedirect())
    try:
        with opener.open(url, timeout=30) as response:
            content = response.read(MAX_DOWNLOAD + 1)
        if len(content) > MAX_DOWNLOAD:
            raise UpdateError("official metadata exceeds its size limit")
        value = json.loads(content, object_pairs_hook=unique_object)
        if not isinstance(value, dict):
            raise UpdateError("official metadata is not an object")
        return value, hashlib.sha256(content).hexdigest()
    except (OSError, ValueError) as error:
        raise UpdateError("official metadata could not be read") from error


def collect_branch(branch, cache_root, profile, now):
    branch_number(branch)
    output = {"observed_at": timestamp(now), "branch": branch, "signature_verified": False,
              "indexes": {}, "security": {}, "inputs_sha256": {}}
    # A new empty cache prevents a network failure from reusing an old index.
    # This root is not a guest filesystem and no package scripts are executed.
    try:
        output["releases"], output["inputs_sha256"]["releases"] = fetch_json(profile["release_metadata"])
        branches = output["releases"].get("release_branches")
        if not isinstance(branches, list) or not any(v.get("rel_branch") == branch for v in branches):
            raise UpdateError("installed branch is absent from official release metadata")
        for repository in profile["repositories"]:
            with tempfile.TemporaryDirectory(prefix="index-", dir=cache_root) as temporary:
                root = Path(temporary)
                keys = root / "etc/apk/keys"
                keys.mkdir(parents=True)
                installed_keys = list(Path("/etc/apk/keys").glob("*.pub"))
                if not installed_keys:
                    raise UpdateError("no installed Alpine signing keys are available")
                for key in installed_keys:
                    shutil.copyfile(key, keys / key.name)
                    output["inputs_sha256"]["key:" + key.name] = hashlib.sha256(key.read_bytes()).hexdigest()
                cache = root / "var/cache/apk"
                cache.mkdir(parents=True)
                (root / "lib/apk/db").mkdir(parents=True)
                (root / "lib/apk/db/installed").touch()
                (root / "etc/apk/world").touch()
                repositories = root / "etc/apk/repositories"
                url = f"{profile['repository_origin']}/{branch}/{repository}"
                repositories.write_text(url + "\n")
                invoke(["/sbin/apk", "--root", root, "--arch", profile["architecture"],
                        "--keys-dir", keys, "--repositories-file", repositories,
                        "--cache-dir", cache, "--no-progress", "update"])
                archives = list(cache.glob("APKINDEX.*.tar.gz"))
                if len(archives) != 1:
                    raise UpdateError("verified index format is unsupported or index is missing")
                archive = archives[0]
                if archive.stat().st_size > MAX_DOWNLOAD:
                    raise UpdateError("verified index exceeds its size limit")
                with tarfile.open(archive, "r:gz") as stream:
                    members = [v for v in stream.getmembers() if v.name == "APKINDEX"]
                    if len(members) != 1 or not members[0].isfile() or members[0].size > MAX_DOWNLOAD:
                        raise UpdateError("verified index has no bounded APKINDEX record")
                    # No archive paths are extracted to the controller filesystem.
                    output["indexes"][repository] = parse_apk_database(stream.extractfile(members[0]).read().decode())
                output["inputs_sha256"][repository + ":index"] = hashlib.sha256(archive.read_bytes()).hexdigest()
            security, checksum = fetch_json(f"{profile['security_origin']}/{branch}/{repository}.json")
            if security.get("distroversion") != branch or security.get("reponame") != repository or not isinstance(security.get("packages"), list) or profile["architecture"] not in security.get("archs", []):
                raise UpdateError("security metadata identifies a different branch, repository, or architecture")
            output["security"][repository] = security
            output["inputs_sha256"][repository + ":security"] = checksum
        output["signature_verified"] = True
    except (UpdateError, OSError, ValueError, tarfile.TarError, UnicodeError) as error:
        output["error"] = str(error)
    return output
