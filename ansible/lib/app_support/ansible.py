"""Ansible invocation with caller-selected policy and private temporary inputs."""

from collections.abc import Mapping
from contextlib import contextmanager
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import threading


class AnsibleInvocationError(Exception):
    """A safe diagnostic and a shell-compatible exit status."""

    def __init__(self, message, exit_code=2):
        super().__init__(message)
        self.exit_code = exit_code


@contextmanager
def _interruptions():
    # Handlers record cancellation instead of raising during Popen construction.
    # This lets the owner obtain the child PID before stopping the process group.
    if threading.current_thread() is not threading.main_thread():
        raise AnsibleInvocationError("Ansible invocation requires the main thread")
    interrupted = []

    def remember(signum, _frame):
        if not interrupted:
            interrupted.append(signum)

    previous = {}
    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.signal(signum, remember)
        yield interrupted
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def _stop(child, signum):
    try:
        os.killpg(child.pid, signum)
    except ProcessLookupError:
        pass
    try:
        child.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass
    # Also stop local descendants that survived their group leader.
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    child.wait()


def _execute(argv, *, cwd, env, interrupted, stage):
    if interrupted:
        raise AnsibleInvocationError(f"{stage}: interrupted", 128 + interrupted[0])
    try:
        child = subprocess.Popen(argv, cwd=cwd, env=env, start_new_session=True)
    except OSError as error:
        raise AnsibleInvocationError(f"{stage}: cannot start {Path(argv[0]).name}: {error.strerror}") from None
    try:
        while True:
            if interrupted:
                _stop(child, interrupted[0])
                raise AnsibleInvocationError(f"{stage}: interrupted", 128 + interrupted[0])
            try:
                status = child.wait(timeout=0.1)
                break
            except subprocess.TimeoutExpired:
                continue
    except BaseException:
        if child.poll() is None:
            _stop(child, signal.SIGTERM)
        raise
    if interrupted:
        _stop(child, interrupted[0])
        raise AnsibleInvocationError(f"{stage}: interrupted", 128 + interrupted[0])
    if status:
        code = status if status > 0 else 128 - status
        raise AnsibleInvocationError(f"{stage}: exited with status {code}", code)


def _private_file(path, content=""):
    with open(path, "x", encoding="utf-8", opener=lambda name, flags: os.open(name, flags, 0o600)) as handle:
        handle.write(content)


def _invoke(*, playbook, repo_root, config, role_paths, inventories, boxes,
            magicdns_suffix, limit, extra_vars, env):
    operation = f"playbook {str(playbook)!r}" if playbook is not None else "ping"
    context = f"{operation}, limit {limit!r}"
    stage = f"{context}: setup"
    try:
        root = Path(repo_root).resolve()

        def absolute(path):
            value = Path(path)
            return str(value if value.is_absolute() else root / value)

        if not root.is_dir():
            raise AnsibleInvocationError(f"{stage}: repository root does not exist")
        if not role_paths or not inventories or not boxes or not limit or not magicdns_suffix:
            raise AnsibleInvocationError(f"{stage}: role paths, inventories, boxes, suffix, and limit are required")
        if not all(isinstance(box, str) and box for box in boxes):
            raise AnsibleInvocationError(f"{stage}: box names must be non-empty strings")
        encoded = None
        if extra_vars is not None:
            if not isinstance(extra_vars, Mapping) or any(not isinstance(key, str) for key in extra_vars):
                raise AnsibleInvocationError(f"{stage}: extra variables must be an object with string keys")
            try:
                encoded = json.dumps(dict(extra_vars), allow_nan=False) + "\n"
            except (TypeError, ValueError):
                raise AnsibleInvocationError(f"{stage}: extra variables must contain JSON values") from None
        environment = dict(env)
        environment["ANSIBLE_CONFIG"] = absolute(config)
        environment["ANSIBLE_ROLES_PATH"] = os.pathsep.join(absolute(path) for path in role_paths)
        with _interruptions() as interrupted:
            with tempfile.TemporaryDirectory(prefix="platform-ansible-") as directory:
                work = Path(directory)
                inventory_args = []
                for path in inventories:
                    inventory_args.extend(["-i", absolute(path)])
                for index, box in enumerate(dict.fromkeys(boxes)):
                    # Numeric names avoid using unvalidated input as a path.
                    path = work / f"inventory-{index}.yml"
                    _private_file(path)
                    stage = f"{context}: render inventory for {box!r}"
                    _execute([str(root / "ansible/bin/render-node-inventory"),
                              "--node", box, "--magicdns-suffix", magicdns_suffix,
                              "--output", str(path)],
                             cwd=root, env=environment, interrupted=interrupted, stage=stage)
                    inventory_args.extend(["-i", str(path)])
                if playbook is None:
                    argv = ["ansible", *inventory_args, limit, "-m", "ping", "-o"]
                else:
                    argv = ["ansible-playbook", "-vv", *inventory_args,
                            absolute(playbook), "--limit", limit]
                if encoded is not None:
                    path = work / "extra-vars.json"
                    _private_file(path, encoded)
                    argv.extend(["-e", "@" + str(path)])
                stage = f"{context}: Ansible"
                _execute(argv, cwd=root, env=environment, interrupted=interrupted, stage=stage)
    except OSError as error:
        raise AnsibleInvocationError(f"{stage}: local file operation failed: {error.strerror}") from None


def run_playbook(*, playbook, repo_root, config, role_paths, inventories, boxes,
                 magicdns_suffix, limit, env, extra_vars=None):
    """Run one playbook; raise AnsibleInvocationError after cleanup on failure."""
    _invoke(playbook=playbook, repo_root=repo_root, config=config,
            role_paths=role_paths, inventories=inventories, boxes=boxes,
            magicdns_suffix=magicdns_suffix, limit=limit, extra_vars=extra_vars, env=env)


def run_ping(*, repo_root, config, role_paths, inventories, boxes,
             magicdns_suffix, limit, env):
    """Run Ansible ping for the caller's exact host pattern."""
    _invoke(playbook=None, repo_root=repo_root, config=config,
            role_paths=role_paths, inventories=inventories, boxes=boxes,
            magicdns_suffix=magicdns_suffix, limit=limit, extra_vars=None, env=env)
