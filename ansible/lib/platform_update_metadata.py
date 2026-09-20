"""Fetch official Alpine evidence. Native APK verifies repository signatures."""

import hashlib
import json
import datetime as dt
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.parse
import urllib.request

from platform_updates import UpdateError, branch_number, parse_apk_database, timestamp, unique_object

MAX_DOWNLOAD = 32 * 1024 * 1024


def adjacent_stable_branch(current, releases, now, delay_days=21):
    """Delay only the first stable release; patches never reset this clock.

    Incomplete next-branch evidence defers the upgrade. It cannot block
    signed package updates on the current branch, including during a support gap.
    """
    major, minor = branch_number(current)
    selected = f'v{major}.{minor + 1}'
    if type(delay_days) is not int or not 0 <= delay_days <= 365:
        raise UpdateError('branch delay must be between zero and 365 days')
    try:
        if supported_stable_branch(selected, releases, now) is None:
            return None
        entry = next(row for row in releases['release_branches'] if row['rel_branch'] == selected)
        first = [row for row in entry['releases'] if row.get('version') == selected[1:] + '.0']
        if len(first) != 1 or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', first[0]['date']):
            return None
        released = dt.datetime.combine(dt.date.fromisoformat(first[0]['date']), dt.time(), dt.timezone.utc)
        return selected if now >= released + dt.timedelta(days=delay_days) else None
    except (UpdateError, KeyError, TypeError, ValueError, StopIteration):
        return None


def supported_stable_branch(selected, releases, now):
    """Return a supported explicit branch or None from official release data."""
    branch_number(selected)
    if (not isinstance(releases, dict) or not isinstance(releases.get('release_branches'), list) or
            not isinstance(now, dt.datetime) or now.tzinfo is None or
            now.utcoffset() != dt.timedelta(0)):
        raise UpdateError('stable branch selection requires release metadata and UTC time')
    matches = [item for item in releases['release_branches']
               if isinstance(item, dict) and item.get('rel_branch') == selected]
    if not matches:
        return None
    if len(matches) != 1:
        raise UpdateError('adjacent stable branch is duplicated')
    value = matches[0]
    try:
        branch_date = dt.date.fromisoformat(value['branch_date'])
        main_eol = dt.date.fromisoformat(value['eol_date'])
        repositories = value['repos']
        community = next(item for item in repositories if item['name'] == 'community')
        community_eol = dt.date.fromisoformat(community['eol_date'])
        names = [item['name'] for item in repositories]
        versions = value['releases']
        valid_release = any(isinstance(item, dict) and
                            re.fullmatch(re.escape(selected[1:]) + r'\.[0-9]+', item.get('version', '')) and
                            dt.date.fromisoformat(item['date']) <= now.date()
                            for item in versions)
    except (KeyError, TypeError, ValueError, StopIteration) as error:
        raise UpdateError('adjacent stable branch has incomplete release or support metadata') from error
    if (not isinstance(value.get('arches'), list) or 'x86_64' not in value['arches'] or
            value.get('git_branch') != selected[1:] + '-stable' or
            names != ['main', 'community'] or not isinstance(versions, list)):
        raise UpdateError('adjacent stable branch has unsupported architecture or repositories')
    if not valid_release or not branch_date <= now.date() < min(main_eol, community_eol):
        return None
    return selected


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
        try:
            output["releases"], output["inputs_sha256"]["releases"] = fetch_json(profile["release_metadata"])
        except (UpdateError, OSError, ValueError):
            output["releases"] = {}
            output["release_metadata_unavailable"] = True
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
                    output["indexes"][repository] = parse_apk_database(stream.extractfile(members[0]).read().decode(), compare_versions)
                output["inputs_sha256"][repository + ":index"] = hashlib.sha256(archive.read_bytes()).hexdigest()
            try:
                security, checksum = fetch_json(f"{profile['security_origin']}/{branch}/{repository}.json")
                if security.get("distroversion") != branch or security.get("reponame") != repository or not isinstance(security.get("packages"), list) or profile["architecture"] not in security.get("archs", []):
                    raise UpdateError("security metadata identifies a different branch, repository, or architecture")
                output["security"][repository] = security
                output["inputs_sha256"][repository + ":security"] = checksum
            except (UpdateError, OSError, ValueError, TypeError):
                output.setdefault("security_unavailable", []).append(repository)
        output["signature_verified"] = True
    except (UpdateError, OSError, ValueError, tarfile.TarError, UnicodeError) as error:
        output["error"] = str(error)
    return output
