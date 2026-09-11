#!/usr/bin/env python3
"""One authorized maintenance repair; not an installed Apply executor.

See target architecture 11.7.1. All private bytes stay on the controller.
"""

import argparse
import datetime
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import secrets
import shutil


ENGINE = "1b081298c2d97d33557a41ba51e707e20139c3e3"
STATE = "8622695e9361d281620dc5f69d9b556f2ab637f748899985899d929cf3eaf188"
BEFORE = "f83add11a151ef8a9beb5d09d16bef1eed78f800ddfe67d03165cbc349e3854a"
AFTER = "67bbf6e4cf51badb07c007f76c94c9ade3080a6a348a37ad5f3521e77a2f05a5"
ARCHIVE = Path("/var/lib/klokast/policy-comment-repair-20260911")
OLD = [
    b'\t\t// Source of truth for Tailscale ACL and Grants is the Tailscale API.\n',
    b'\t\t// The file `ops/tailscale-config.json` in the git repository is copied from that API output, and might be out of date.\n',
]
NEW = [
    b'\t\t// The live Tailscale API is observed runtime state. This public template\n',
    b'\t\t// and the private instance are the sources for intended policy inputs.\n',
]
FIELDS = {"plan", "authority-state", "controller-toolchain-receipt",
          "source-recovery-receipt", "instance-source-receipt", "observation", "build-dir"}


def check_comments(live, candidate):
    left, right = live.splitlines(keepends=True), candidate.splitlines(keepends=True)
    if (left[2:4] != OLD or right[2:4] != NEW or
            left[:2] != right[:2] or left[4:] != right[4:]):
        raise ValueError("refusing policy change beyond the two reviewed header comments")


def save(path, content):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def repair(m, args, execute):
    binding = m.validate_inputs_v3(args)
    if (binding["plan"]["kind"] != m.KIND_PLAN_V8 or
            binding["plan"]["engine"]["commit"] != ENGINE or
            binding["state"]["authority_state_sha256"] != STATE):
        raise ValueError("repair requires the exact activated engine and Authority State baseline")
    nonce = "comment-repair-" + secrets.token_hex(12)
    work = m.new_work(nonce)
    archive = None
    result = {"kind": "klokast.policy-comment-maintenance.v1", "signed": False,
              "authorization": "human task instruction 2026-09-11",
              "engine_commit": ENGINE, "authority_state_sha256": STATE,
              "plan_sha256": binding["plan"]["plan_sha256"],
              "before_sha256": BEFORE, "candidate_sha256": AFTER,
              "post_attempted": False, "result": "failed_before_post"}
    try:
        m.run([m.RENDERER, "--instance", m.INSTANCE / "klokast-instance.json",
               "--template", m.POLICY_TEMPLATE, "--output", work / "candidate.body"], capture=True)
        m.run([m.MUTATION_HELPER, "get-before", nonce], capture=True)
        candidate = m.read_regular(work / "candidate.body", 1024 * 1024)
        live = m.read_regular(work / "live.body", 1024 * 1024)
        if m.sha256_bytes(live) != BEFORE or m.sha256_bytes(candidate) != AFTER:
            raise ValueError("policy hashes differ from the reviewed repair; inspect current state")
        check_comments(live, candidate)
        etag = m.read_regular(work / "live.etag", 4096)
        m.run([m.MUTATION_HELPER, "validate-candidate", nonce], capture=True)
        if m.validate_inputs_v3(args) != binding:
            raise ValueError("repair inputs changed during validation")
        if not execute:
            return {**result, "result": "prepared", "post_attempted": False}

        # Exclusive durable directory: even an uncertain previous attempt refuses.
        ARCHIVE.mkdir(mode=0o700)
        archive = ARCHIVE
        save(archive / "preimage.body", live)
        save(archive / "candidate.body", candidate)
        save(archive / "preimage.etag", etag)
        save(archive / "inputs.json", (json.dumps(vars(args), sort_keys=True) + "\n").encode())
        m.run([m.MUTATION_HELPER, "get-before", nonce], capture=True)
        if (m.read_regular(work / "live.body", 1024 * 1024) != live or
                m.read_regular(work / "live.etag", 4096) != etag or
                m.validate_inputs_v3(args) != binding):
            raise ValueError("policy, ETag, or inputs changed before mutation")
        save(archive / "attempt.json", (json.dumps({**result, "nonce": nonce,
             "issued_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}, sort_keys=True) + "\n").encode())
        result.update(post_attempted=True, result="inspection_required")
        m.run([m.MUTATION_HELPER, "post-candidate", nonce], capture=True)
        m.run([m.MUTATION_HELPER, "get-after", nonce], capture=True)
        after = m.read_regular(work / "after.body", 1024 * 1024)
        save(archive / "after.body", after)
        save(archive / "after.etag", m.read_regular(work / "after.etag", 4096))
        if after != candidate or m.validate_inputs_v3(args) != binding:
            raise ValueError("post-write policy or input check failed; inspect retained evidence")
        result["result"] = "verified"
        return result
    finally:
        try:
            if archive is not None:
                save(archive / "result.json", (json.dumps(result, sort_keys=True) + "\n").encode())
        finally:
            shutil.rmtree(work)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    options = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("run through the controller-local Ansible maintenance playbook")
    loader = importlib.machinery.SourceFileLoader("installed_apply", "/usr/local/sbin/ksa-apply")
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    evidence = module.load_json(options.evidence, expected_fields=FIELDS)
    args = argparse.Namespace(**{key.replace("-", "_"): value for key, value in evidence.items()})
    with module.authority_publication_lock():
        result = repair(module, args, options.execute)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
