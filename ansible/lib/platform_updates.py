"""VM update evidence and safety rules. Observations never grant authority."""

import datetime as dt
import hashlib
import json
import re

ROLES = ("bak", "dmz", "iot")
BRANCH = re.compile(r"v([0-9]+)\.([0-9]+)")
DIGEST = re.compile(r"[0-9a-f]{64}")
REPORT_KIND = "klokast.vm-update-report.v1"
DISCOVERY_AGE = dt.timedelta(hours=30)
VERIFY_AGE = dt.timedelta(hours=2)
METADATA_AGE = dt.timedelta(hours=24)


class UpdateError(Exception):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise UpdateError("JSON contains duplicate fields")
        result[key] = value
    return result


def utc(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value):
        raise UpdateError("evidence time must use UTC with whole seconds")
    try:
        return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    except ValueError as error:
        raise UpdateError("evidence time is invalid") from error


def timestamp(value):
    return value.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fresh(value, now, limit):
    try:
        age = now - utc(value)
        return dt.timedelta(0) <= age <= limit
    except (UpdateError, TypeError):
        return False


def branch_number(value):
    found = BRANCH.fullmatch(value or "")
    if not found:
        raise UpdateError("an explicit stable Alpine branch is required")
    return tuple(int(v) for v in found.groups())


def next_branch(current, available):
    """Never skip an intermediate stable branch or use latest-stable."""
    major, minor = branch_number(current)
    successor = f"v{major}.{minor + 1}"
    return successor if successor in available else current


def repository_support(releases, branch, now):
    """Community has its own EOL. Never inherit main's EOL for community."""
    result = {"main": "unknown", "community": "unknown"}
    for entry in releases.get("release_branches", []):
        if entry.get("rel_branch") != branch:
            continue
        for repository in entry.get("repos", []):
            name = repository.get("name")
            if name not in result:
                continue
            expiry = repository.get("eol_date")
            if name == "main":
                expiry = expiry or entry.get("eol_date")
            try:
                end = dt.date.fromisoformat(expiry)
                result[name] = "supported" if now.date() < end else "unsupported"
            except (ValueError, TypeError):
                pass
    return result


def parse_apk_database(content, compare=None):
    """Read APK v2 installed/index records; never execute or extract them."""
    packages = {}
    for block in content.strip().split("\n\n"):
        fields = {}
        for line in block.splitlines():
            if len(line) >= 2 and line[1] == ":" and line[0] in "PV oA".replace(" ", ""):
                key = line[0]
                if key in fields:
                    raise UpdateError("package record has duplicate identity fields")
                fields[key] = line[2:]
        if not fields:
            continue
        if not fields.get("P") or not fields.get("V"):
            raise UpdateError("package record has no name or version")
        name = fields["P"]
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9+_.-]*", name) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9+_.~-]*", fields["V"]):
            raise UpdateError("package identity uses an unsupported format")
        item = {"version": fields["V"], "origin": fields.get("o", name), "architecture": fields.get("A", "")}
        if name in packages:
            if compare is None:
                raise UpdateError("package index contains multiple versions; native resolution is required")
            order = compare(packages[name]["version"], item["version"])
            if order not in ("<", "=", ">") or (order == "=" and packages[name] != item):
                raise UpdateError("package index contains conflicting package identities")
            if order != "<":
                continue
        packages[name] = item
    if not packages:
        raise UpdateError("package database is empty or uses an unsupported format")
    return packages


def findings(code, message, severity="warning", scope="installation"):
    return {"code": code, "message": message, "severity": severity, "scope": scope}


def compare_packages(installed, indexes, secdb, compare):
    """compare must call native apk version -t; no custom version algorithm."""
    if set(indexes) != {"main", "community"} or set(secdb) != {"main", "community"}:
        raise UpdateError("both repository indexes and security databases are required")
    available = {}
    for repository in ("main", "community"):
        for name, package in indexes[repository].items():
            if name in available:
                order = compare(available[name]["version"], package["version"])
                if order not in ("<", "=", ">"):
                    raise UpdateError("native APK comparison failed")
                if order != "<":
                    continue
            available[name] = {**package, "repository": repository}
    updates, missing, security = [], [], []
    fixes = {}
    for repository, document in secdb.items():
        for entry in document["packages"]:
            package = entry["pkg"]
            if not isinstance(package.get("secfixes"), dict):
                raise UpdateError("security metadata has an invalid fix map")
            fixes[(repository, package["name"])] = package["secfixes"]
    for name, old in sorted(installed.items()):
        new = available.get(name)
        if new is None:
            missing.append(name)
            continue
        order = compare(old["version"], new["version"])
        if order not in ("<", "=", ">"):
            raise UpdateError("native APK comparison failed")
        if order == "<":
            updates.append({"name": name, "installed": old["version"], "available": new["version"], "repository": new["repository"]})
        for version, issues in fixes.get((new["repository"], old["origin"]), {}).items():
            if compare(old["version"], version) == "<":
                if not isinstance(issues, list) or any(not isinstance(issue, str) for issue in issues):
                    raise UpdateError("security metadata has an invalid issue list")
                security.append({"name": name, "fixed_version": version, "issues": sorted(issues),
                                 "available": compare(new["version"], version) in ("=", ">")})
    return updates, missing, security


def assess_host(host, fact, metadata, required_packages, compare, now):
    result = {"host": host, "profile": "unsupported", "update_status": "unknown",
              "replacement_ready": False, "findings": [], "facts": fact or {},
              "updates": [], "missing_packages": [], "security_fixes": []}
    def add(code, message, severity="warning"):
        result["findings"].append(findings(code, message, severity, host))
    if not fact or not fresh(fact.get("observed_at"), now, VERIFY_AGE):
        add("inventory.unknown", "Fresh VM facts are not available.", "critical")
        return result
    role = fact.get("role")
    if role not in ROLES or fact.get("os", {}).get("id") != "alpine" or fact.get("architecture") != "x86_64":
        add("replacement.unsupported", "Replacement supports only shared Alpine x86_64 bak, dmz, and iot VMs.")
        return result
    result["profile"] = "shared-alpine-v1"
    if fact.get("kernel") not in fact.get("module_releases", []):
        add("kernel.modules-mismatch", "The running kernel has no matching installed module directory.", "critical")
    if not fact.get("tailscale", {}).get("running_version"):
        add("tailscale.unknown", "The running Tailscale daemon version is unknown.")
    branch = fact.get("branch", "")
    result["branch"] = branch
    try:
        branch_number(branch)
    except UpdateError:
        add("branch.unknown", "The installed stable Alpine branch is unknown.")
        return result
    # A fact or a file on the guest cannot establish accepted update authority.
    add("replacement.not-adopted", "A verified retained-data adoption and controller release assignment are required.")
    if fact.get("configuration", {}).get("status") != "verified":
        add("configuration.unknown", "Configuration hashes have no verified approved baseline.")
    evidence = metadata.get(branch, {})
    if not fresh(evidence.get("observed_at"), now, METADATA_AGE) or evidence.get("signature_verified") is not True or evidence.get("error"):
        add("metadata.unknown", "Fresh official metadata and native APK signature verification are required.", "critical")
        if evidence.get("error"):
            result["metadata_error"] = evidence["error"]
        return result
    support = repository_support(evidence.get("releases", {}), branch, now)
    result["support"] = support
    for repository, state in support.items():
        if state != "supported":
            add("branch." + state, repository + " support is " + state + ".", "critical")
    try:
        installed = fact["packages"]
        if not installed:
            raise UpdateError("installed package inventory is empty")
        updates, missing, security = compare_packages(installed, evidence["indexes"], evidence["security"], compare)
        result.update(updates=updates, missing_packages=missing, security_fixes=security)
        missing_required = sorted(set(required_packages) - set(installed))
        if missing_required:
            add("profile.incomplete", "Required base packages are absent: " + ", ".join(missing_required))
        if missing:
            add("packages.missing", "Installed packages are absent from the approved repositories.", "critical")
        if security:
            add("security.blocked", "Security fixes require a tested template and an authorized replacement.", "critical")
        if support != {"main": "supported", "community": "supported"} or missing or missing_required:
            result["update_status"] = "blocked"
        else:
            result["update_status"] = "updates-available" if updates or security else "packages-current"
        result["next_branch"] = next_branch(branch, [entry.get("rel_branch") for entry in evidence["releases"].get("release_branches", [])])
        if result["next_branch"] != branch:
            add("branch.advance", "The next stable branch requires a complete build and compatibility tests.")
    except (KeyError, TypeError, ValueError, UpdateError):
        result["update_status"] = "unknown"
        add("packages.unknown", "Package or security comparison failed; no current-version claim is available.")
    return result


def health(report, verification, now):
    output = []
    if not report or report.get("kind") != REPORT_KIND or not fresh(report.get("generated_at"), now, DISCOVERY_AGE):
        output.append(findings("discovery.overdue", "Update discovery is missing or older than 30 hours.", "critical"))
    if report and report.get("complete") is not True:
        output.append(findings("discovery.incomplete", "The latest discovery did not complete.", "critical"))
    if not verification or not fresh(verification.get("generated_at"), now, VERIFY_AGE):
        output.append(findings("verification.overdue", "Release verification is missing or older than two hours.", "critical"))
    if report:
        output.extend(report.get("findings", []))
        for host in report.get("hosts", []):
            output.extend(host.get("findings", []))
    if verification:
        output.extend(verification.get("findings", []))
    return output


def replacement_window(now, policy):
    """At 03:00 the last start is permitted; one second later it is closed."""
    if now.utcoffset() != dt.timedelta(0):
        raise UpdateError("the replacement clock must be UTC")
    time = now.time().replace(tzinfo=None)
    window = policy["maintenance-window"]
    start = dt.time.fromisoformat(window["start"])
    cutoff = dt.time.fromisoformat(window["last-start"])
    end = dt.time.fromisoformat(window["end"])
    budget = dt.timedelta(minutes=policy["replacement-minutes"] + policy["recovery-minutes"])
    return start <= time <= cutoff and now + budget <= dt.datetime.combine(now.date(), end, dt.timezone.utc)


def validate_release(release, engine, profile, artifact_hashes):
    """Reject incomplete or mismatched immutable artifact evidence."""
    expected = {"kind", "engine_commit", "profile", "branch", "architecture", "packages", "artifacts",
                "kernel_release", "modules_release", "inputs_sha256", "tests", "release_sha256"}
    if not isinstance(release, dict) or set(release) != expected or release.get("kind") != "klokast.vm-release.v1":
        raise UpdateError("release has an unknown or incomplete contract")
    if release["engine_commit"] != engine or release["profile"] != profile or release["architecture"] != "x86_64":
        raise UpdateError("release is not bound to this approved engine and profile")
    branch_number(release["branch"])
    if not release["kernel_release"] or release["kernel_release"] != release["modules_release"]:
        raise UpdateError("kernel and root filesystem modules differ")
    if set(release["artifacts"]) != {"root", "kernel", "initramfs"} or release["artifacts"] != artifact_hashes:
        raise UpdateError("release artifact checksums differ")
    if any(not isinstance(v, str) or not DIGEST.fullmatch(v) for v in artifact_hashes.values()):
        raise UpdateError("release artifact checksum is invalid")
    if not isinstance(release["inputs_sha256"], str) or not DIGEST.fullmatch(release["inputs_sha256"]):
        raise UpdateError("release has no build input identity")
    if not isinstance(release["packages"], dict) or not {"linux-virt", "tailscale", "podman"} <= set(release["packages"]):
        raise UpdateError("release lacks its complete package manifest")
    tests = {"boot", "kernel_modules", "tailscale", "rootless_podman", "firewall", "application_compatibility", "no_machine_identity"}
    if not isinstance(release["tests"], dict) or set(release["tests"]) != tests or any(v is not True for v in release["tests"].values()):
        raise UpdateError("release compatibility tests are incomplete or failed")
    if release["release_sha256"] != digest({k: v for k, v in release.items() if k != "release_sha256"}):
        raise UpdateError("release manifest checksum differs")


def check_dependencies(target, dependencies):
    """The required controller, DNS, dom0 and artifact graph must be closed."""
    roots = ("controller", "dom0", "dns", "artifacts", "recovery")
    visited, visiting = set(), set()
    def visit(node):
        if node == target or node in visiting or node not in dependencies:
            raise UpdateError("update dependencies are cyclic, unknown, or depend on the target")
        if node in visited:
            return
        visiting.add(node)
        edges = dependencies[node]
        if not isinstance(edges, list) or any(not isinstance(edge, str) for edge in edges):
            raise UpdateError("dependency graph is invalid")
        for edge in edges:
            visit(edge)
        visiting.remove(node)
        visited.add(node)
    for root in roots:
        visit(root)


# This state machine is shared safety logic, not a privileged executor.
# Recovery is monotonic: after acceptance, old data must never be restored.
STAGES = ("staged", "armed", "fenced", "stopped", "checkpointed", "booted", "tested", "accepted", "opened", "complete")


def transition(journal, stage, now):
    current = journal["stage"]
    if current not in STAGES or stage not in STAGES or STAGES.index(stage) != STAGES.index(current) + 1:
        raise UpdateError("operation stage cannot be skipped, repeated, or reversed")
    if utc(journal["deadline"]) <= now and STAGES.index(current) < STAGES.index("accepted"):
        raise UpdateError("replacement exceeded 30 minutes; recover the recorded old release")
    if stage == "accepted" and (journal.get("checks_passed") is not True or journal.get("checkpoint_healthy") is not True):
        raise UpdateError("acceptance requires healthy services and a usable retained-data checkpoint")
    return {**journal, "stage": stage, "updated_at": timestamp(now)}


def recovery_action(journal):
    stage = journal.get("stage")
    if stage not in STAGES:
        raise UpdateError("unknown journal stage; keep production fenced")
    if STAGES.index(stage) >= STAGES.index("accepted"):
        return "preserve-production-data"
    if STAGES.index(stage) < STAGES.index("stopped"):
        return "verify-old-release"
    if stage == "stopped":
        return "restart-recorded-old-release"
    return "restore-recorded-checkpoint-and-old-release"
