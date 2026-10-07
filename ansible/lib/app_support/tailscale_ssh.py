"""Ansible environment for the installed Tailscale SSH transport adapter."""

import os
from pathlib import Path


DEFAULT_EXECUTABLE = Path("/usr/local/bin/platform-tailscale-ssh")


def ansible_environment(base_env, *, executable=None):
    """Copy an environment and select Tailscale SSH without selecting targets.

    Raise OSError before Ansible starts if the adapter is unavailable. An
    explicit executable is useful for isolated tests; there is no fallback.
    """
    path = Path(executable) if executable is not None else DEFAULT_EXECUTABLE
    if not path.is_absolute() or not path.is_file() or not os.access(path, os.X_OK):
        raise OSError(
            f"Tailscale SSH adapter is missing or not executable at {path}; "
            "converge the controller toolchain before running application playbooks"
        )
    env = dict(base_env)
    env.update(
        ANSIBLE_SSH_EXECUTABLE=str(path),
        ANSIBLE_SSH_ARGS="-o ControlMaster=no -o ControlPersist=no",
        ANSIBLE_SSH_COMMON_ARGS="",
        ANSIBLE_SSH_TRANSFER_METHOD="piped",
        ANSIBLE_SSH_USETTY="false",
    )
    return env
