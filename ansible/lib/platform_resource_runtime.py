"""Controller checks and execution of compiled resource plans."""
import atexit
import shutil
from contextlib import contextmanager
import fcntl
import json
import os
import re
import shlex
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
import yaml

import platform_resource_model as model
import platform_resource_compiler as compiler


RUN_ROOT = Path('/run/klokast/platform-resources')


VM_UPDATE_INSTALL_LOCK = Path('/var/lib/klokast/updates/operation.lock')


def require_active_controller():
    guard = os.environ.get("KLOKAST_CONTROLLER_GUARD", "/usr/local/sbin/klokast-controller-guard")
    if not os.path.exists(guard) or not os.access(guard, os.X_OK):
        model.die("active-controller guard is missing")
    result = subprocess.run([guard, "--require-active"], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        model.die(f"controller is not active: {detail}")


@contextmanager
def vm_update_installation_lock():
    """Hold the same fixed lock as signed updates through shared VM Apply."""
    try:
        parent = VM_UPDATE_INSTALL_LOCK.parent.lstat()
        descriptor = os.open(VM_UPDATE_INSTALL_LOCK, os.O_RDWR | os.O_NOFOLLOW)
    except OSError as error:
        model.die(f"shared VM update operation lock is unavailable: {error}")
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid != 0 or
                parent.st_mode & 0o022 or not stat.S_ISREG(info.st_mode) or
                info.st_uid != 0 or info.st_gid != os.getgid() or info.st_nlink != 1 or
                stat.S_IMODE(info.st_mode) != 0o660):
            model.die('shared VM update operation lock has unsafe ownership or mode')
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            model.die('another VM update or provisioning operation holds the installation lock')
        yield
    finally:
        os.close(descriptor)


def render_inventory(box, output_path, magicdns_suffix, box_configs=None, *, repo_root):
    extra_args = []
    config = (box_configs or {}).get(box) or {}
    dom0_bridge_ports = config.get("dom0_bridge_ports") or {}
    for bridge in sorted(dom0_bridge_ports):
        ports = dom0_bridge_ports[bridge]
        if ports:
            extra_args.extend(["--dom0-bridge-port", f"{bridge}={','.join(ports)}"])
    subprocess.run(
        [
            str(repo_root / "ansible" / "bin" / "render-node-inventory"),
            "--node",
            box,
            "--magicdns-suffix",
            magicdns_suffix,
            "--output",
            str(output_path),
            *extra_args,
        ],
        cwd=repo_root,
        check=True,
    )


def render_app_vm_inventory(compiled, output_path, magicdns_suffix, app_vm_specs=None):
    app_vm_specs = app_vm_specs if app_vm_specs is not None else compiled["app_vm_specs"]
    hosts = {}
    active_hosts = {}
    passive_hosts = {}
    running_hosts = {}
    stopped_hosts = {}
    os_hosts = {}
    role_groups = {}
    app_groups = {}
    box_groups = {}
    for spec in app_vm_specs:
        user = dict(spec.get("user") or {})
        host_vars = {
            "node_name": spec["node"],
            "node_domain_role": spec["node_domain_role"],
            "node_hostname": spec["node_hostname"],
            "ansible_host": f"{spec['node_hostname']}.{magicdns_suffix}",
            "ansible_user": "neo",
            "ansible_become": True,
            "ansible_python_interpreter": "/usr/bin/python3",
            "platform_app_vm_guest_os": spec["guest_spec"]["guest_os"],
            "platform_app_vm_container_runtime": spec["guest_spec"]["container_runtime"],
            "platform_app_vm_runtime_state": spec.get("runtime_state", "running"),
            "platform_app_vm_zone": spec["zone"],
            "platform_app_vm_interface": spec["guest_spec"]["vm_interface"],
            "vm_bootstrap_host": spec["vm_ipv4_address"],
            "vm_bootstrap_proxyjump": "{{ 'neo@' ~ (hostvars[node_name ~ '-dom0'].ansible_host | default(node_name ~ '-dom0')) }}",
            "vm_bootstrap_private_key_file": "{{ lookup('env', 'HOME') }}/.ssh/github-klokast-codex",
            "vm_bootstrap_known_hosts_file": "{{ lookup('env', 'HOME') }}/.ssh/known_hosts_klokast_bootstrap",
            "vm_bootstrap_network_interfaces": (
                spec["guest_spec"].get("network_interfaces")
                or spec["guest_spec"].get("debian_image", {}).get("network_interfaces", "")
            ),
            "vm_admin_user_name": "neo",
            "vm_local_users": [
                {
                    "name": "neo",
                    "shell": "/bin/ash",
                    "groups": ["wheel"],
                }
            ],
            "vm_base_packages": ["python3", "doas", "openssh"],
            "vm_admin_authorized_key_file": "{{ lookup('env', 'HOME') }}/.ssh/github-klokast-codex.pub",
            "tailscale_enable_ssh": True,
            "alpine_repositories": [
                "http://dl-cdn.alpinelinux.org/alpine/v3.23/main",
                "http://dl-cdn.alpinelinux.org/alpine/v3.23/community",
            ],
            "vm_tailscale_authkey_wrapper": "/usr/local/sbin/ts-authkey-vm",
            "vm_tailscale_authkey_advertise_tags": ",".join(spec["advertised_tags"]),
            "vm_tailscale_authkey_expected_tag": "tag:vm",
            "vm_tailscale_authkey_expected_tags": spec["advertised_tags"],
        }
        if spec["guest_spec"]["guest_os"] == "debian":
            user.setdefault("tailscale_domain", f"{spec['tailnet_hostname']}.{magicdns_suffix}")
            host_vars.update(
                {
                    "ansible_become_method": "sudo",
                    "ansible_become_flags": "-n",
                    "ansible_become_timeout": 30,
                    "ansible_ssh_pipelining": False,
                }
            )
        else:
            host_vars.update(
                {
                    "ansible_become_method": "doas",
                    "ansible_become_flags": "-n",
                    "ansible_become_timeout": 30,
                    "ansible_ssh_pipelining": True,
                }
            )
        hosts[spec["inventory_hostname"]] = host_vars
        os_hosts.setdefault(f"{spec['guest_spec']['guest_os']}_app_vms", {})[
            spec["inventory_hostname"]
        ] = {}
        role_group = f"{spec['node_domain_role']}_app_vms"
        role_groups.setdefault(role_group, {})[spec["inventory_hostname"]] = {}
        app_group = f"{spec['app'].replace('-', '_')}_app_vms"
        app_groups.setdefault(app_group, {})[spec["inventory_hostname"]] = {}
        box_group_name = re.sub(r"[^A-Za-z0-9_]", "_", spec["node"])
        box_groups.setdefault(box_group_name, {})[spec["inventory_hostname"]] = {}
        if spec.get("runtime_state", "running") == "running":
            running_hosts[spec["inventory_hostname"]] = {}
            if spec["site_role"] == "active":
                active_hosts[spec["inventory_hostname"]] = {}
            else:
                passive_hosts[spec["inventory_hostname"]] = {}
        else:
            stopped_hosts[spec["inventory_hostname"]] = {}
    children = {
        "app_vms": {"hosts": {host: {} for host in hosts}},
        "active_app_vms": {"hosts": active_hosts},
        "passive_app_vms": {"hosts": passive_hosts},
        "running_app_vms": {"hosts": running_hosts},
        "stopped_app_vms": {"hosts": stopped_hosts},
    }
    for group_name, group_hosts in os_hosts.items():
        children[group_name] = {"hosts": group_hosts}
    for group_name, group_hosts in role_groups.items():
        children[group_name] = {"hosts": group_hosts}
    for group_name, group_hosts in app_groups.items():
        children[group_name] = {"hosts": group_hosts}
    for box_group_name, box_hosts in box_groups.items():
        app_group_name = f"{box_group_name}_app_vms"
        children.setdefault(box_group_name, {}).setdefault("children", {})[app_group_name] = {}
        children[app_group_name] = {"hosts": box_hosts}

    payload = {
        "all": {
            "children": children,
            "hosts": hosts,
        }
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(yaml.safe_dump(payload, sort_keys=True), encoding="utf-8")


def write_platform_resources_vars(vars_path, vars_payload, desired_json):
    vars_path.write_text(yaml.safe_dump(vars_payload, sort_keys=True), encoding="utf-8")
    with vars_path.open("a", encoding="utf-8") as handle:
        handle.write("platform_resources_desired_json: !unsafe |-\n")
        for line in desired_json.splitlines():
            handle.write(f"  {line}\n")


def _ansible_environment(temporary, *, repo_root):
    environment = os.environ.copy()
    environment["ANSIBLE_CONFIG"] = str(repo_root / "ansible" / "ansible.cfg")
    environment["ANSIBLE_ROLES_PATH"] = str(repo_root / "ansible" / "roles")
    environment["ANSIBLE_SSH_COMMON_ARGS"] = (
        "-o StrictHostKeyChecking=accept-new "
        f"-o UserKnownHostsFile={temporary / 'known_hosts'}"
    )
    return environment


def run_box_access(
    command, compiled, box, magicdns_suffix, approved_commit="", check_mode=False,
    *,
    repo_root,
):
    if command not in {"apply", "verify"}:
        model.die("box access operation must be apply or verify")
    selected = compiler.selected_box_access_box(compiled, [box])
    run_root = RUN_ROOT
    run_root.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f"box-access-{command}-", dir=run_root)
    )
    atexit.register(shutil.rmtree, temporary, ignore_errors=True)
    inventory_path = temporary / f"{selected}.yml"
    vars_path = temporary / "extra-vars.yml"
    render_inventory(
        selected,
        inventory_path,
        magicdns_suffix,
        compiled.get("box_configs"),
        repo_root=repo_root,
    )
    vars_payload = compiler.box_access_router_vars(compiled, selected)
    vars_payload["platform_box_access_operation"] = command
    vars_payload["platform_resources_approved_commit"] = approved_commit
    vars_payload["platform_resources_registry_sha256"] = compiled["registry_sha256"]
    vars_payload["platform_resources_compiler_version"] = compiled["compiler_version"]
    vars_path.write_text(
        yaml.safe_dump(vars_payload, sort_keys=True), encoding="utf-8"
    )
    environment = _ansible_environment(temporary, repo_root=repo_root)
    command_line = [
            "ansible-playbook",
            "-vv",
            "-i",
            str(repo_root / "ansible" / "execution-inventory" / "hosts"),
            "-i",
            str(inventory_path),
            str(repo_root / "ansible" / "playbooks" / "32-platform-box-access.yml"),
            "--limit",
            f"{selected}-router",
            "-e",
            f"@{vars_path}",
        ]
    if check_mode:
        command_line.append("--check")
    subprocess.run(
        command_line,
        cwd=repo_root,
        env=environment,
        check=True,
    )


def run_shared_guests(
    command,
    compiled,
    magicdns_suffix,
    approved_commit="",
    requested_boxes=None,
    requested_roles=None,
    *,
    repo_root,
):
    boxes = compiler.selected_shared_guest_boxes(compiled, requested_boxes or [])
    roles = list(dict.fromkeys(requested_roles or model.SHARED_GUEST_ROLES))
    if not boxes:
        print("platform-resources: no configured shared guests; nothing to do")
        return

    run_root = RUN_ROOT
    run_root.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f"shared-guests-{command}-", dir=run_root))
    atexit.register(shutil.rmtree, tmp, ignore_errors=True)
    desired_json = json.dumps(
        compiler.attach_approved_commit(compiled, approved_commit), indent=2, sort_keys=True
    )
    vars_path = tmp / "extra-vars.yml"
    vars_payload = {
        "platform_shared_guest_operation": command,
        "platform_shared_guest_roles": roles,
        "platform_shared_guest_runtime_states": {
            box: (compiled["box_configs"][box].get("shared_guests") or {})
            for box in boxes
        },
        "platform_resources_approved_commit": approved_commit,
        "platform_resources_registry_sha256": compiled["registry_sha256"],
        "platform_resources_compiler_version": compiled["compiler_version"],
    }
    write_platform_resources_vars(vars_path, vars_payload, desired_json)

    inventory_args = ["-i", str(repo_root / "ansible" / "execution-inventory" / "hosts")]
    for box in boxes:
        inventory_path = tmp / f"{box}.yml"
        render_inventory(
            box,
            inventory_path,
            magicdns_suffix,
            compiled.get("box_configs"),
            repo_root=repo_root,
        )
        inventory_args.extend(["-i", str(inventory_path)])

    env = _ansible_environment(tmp, repo_root=repo_root)
    subprocess.run(
        [
            "ansible-playbook",
            "-vv",
            *inventory_args,
            str(repo_root / "ansible" / "playbooks" / "44-vm-podman-guests-runtime.yml"),
            "--limit",
            ",".join(f"{box}-dom0" for box in boxes),
            "-e",
            f"@{vars_path}",
        ],
        cwd=repo_root,
        env=env,
        check=True,
    )


def git_output(args, *, repo_root):
    try:
        return subprocess.check_output(
            ["git", *args], cwd=repo_root, text=True, stderr=subprocess.STDOUT
        ).strip()
    except subprocess.CalledProcessError as error:
        output = (error.output or "").strip()
        suffix = f": {output}" if output else ""
        model.die(f"git {' '.join(args)} failed{suffix}")


def assert_approved_commit(approved_commit, *, repo_root):
    # In development, source identity is audit metadata, not code admission.
    from platform_source import require_development
    require_development()
    if approved_commit and approved_commit != git_output(["rev-parse", "HEAD"], repo_root=repo_root):
        model.die("requested source identity differs from the development checkout")


def run_tailscale_ssh(host, remote_args, input_text=None, timeout=180, *, repo_root):
    remote_command = " ".join(shlex.quote(str(arg)) for arg in remote_args)
    subprocess.run(
        ["tailscale", "ssh", f"neo@{host}", remote_command],
        cwd=repo_root,
        input=input_text,
        text=True,
        timeout=timeout,
        check=True,
    )


def upload_tailscale_ssh_text(host, remote_path, content, timeout=120, *, repo_root):
    quoted_dir = shlex.quote(str(Path(remote_path).parent))
    quoted_path = shlex.quote(remote_path)
    run_tailscale_ssh(
        host,
        [
            "sh",
            "-c",
            f"umask 077 && mkdir -p {quoted_dir} && cat > {quoted_path}",
        ],
        input_text=content,
        timeout=timeout,
        repo_root=repo_root,
    )


def podman_resource_remote_script():
    return r"""set -eu
staging_dir="$1"
desired_path="$2"
helper_path="$3"
last_applied_path="$4"
mode="$5"
node_name="$6"
node_role="$7"
shift 7
case "$mode" in
  apply|verify|cleanup) ;;
  *) echo "invalid Platform resource staging action" >&2; exit 64 ;;
esac

# The controller uploads only these three files. Remove them on every exit,
# including a failed verification, without removing unexpected guest data.
stage_name=${staging_dir##*/}
case "$stage_name" in
  apply-*|verify-*) ;;
  *) echo "invalid Platform resource staging name" >&2; exit 64 ;;
esac
case "$stage_name" in
  *[!a-zA-Z0-9_-]*|'') echo "invalid Platform resource staging name" >&2; exit 64 ;;
esac
if [ "$staging_dir" != "/home/neo/.cache/klokast-platform-resources/$stage_name" ] ||
   [ "$desired_path" != "$staging_dir/desired.json" ] ||
   [ "$helper_path" != "$staging_dir/klokast-app-resources-reconcile" ] ||
   [ "$last_applied_path" != "$staging_dir/last-applied.json" ]; then
  echo "Platform resource staging paths differ" >&2
  exit 64
fi
for stage_parent in /home/neo/.cache /home/neo/.cache/klokast-platform-resources "$staging_dir"; do
  if [ -L "$stage_parent" ] || [ ! -d "$stage_parent" ]; then
    echo "Platform resource staging directory is unsafe" >&2
    exit 64
  fi
done
cleanup_stage() {
  rm -f -- "$desired_path" "$helper_path" "$last_applied_path"
  if ! rmdir -- "$staging_dir" 2>/dev/null; then
    echo "Platform resource staging directory has unexpected content; review its exact files" >&2
  fi
}
trap cleanup_stage 0
if [ "$mode" = "cleanup" ]; then
  exit 0
fi

if [ ! -x /usr/sbin/nft ] || [ ! -f /etc/nftables.nft ]; then
  echo "missing Podman VM firewall baseline on $(hostname); run the Podman host convergence workflow before platform-resources" >&2
  exit 42
fi

doas -n install -d -o root -g root -m 0755 /usr/local/libexec
doas -n install -o root -g root -m 0755 "$helper_path" /usr/local/libexec/klokast-app-resources-reconcile
doas -n install -d -o root -g root -m 0755 /etc/klokast/platform-resources

if [ "$mode" = "apply" ]; then
  apply_output="$(
    doas -n /usr/bin/python3 /usr/local/libexec/klokast-app-resources-reconcile \
      apply \
      --desired "$desired_path" \
      --node-name "$node_name" \
      --node-role "$node_role" \
      "$@"
  )"
  printf '%s\n' "$apply_output"
  changed="$(
    printf '%s\n' "$apply_output" |
    /usr/bin/python3 -c 'import json, sys; print("1" if json.load(sys.stdin).get("changed") else "0")'
  )"
  doas -n /usr/sbin/nft -c -f /etc/nftables.nft
  if [ "$changed" = "1" ]; then
    doas -n /usr/sbin/nft -f /etc/nftables.nft
  fi
  doas -n install -o root -g root -m 0644 "$desired_path" /etc/klokast/platform-resources/desired.json
  doas -n install -o root -g root -m 0644 "$last_applied_path" /etc/klokast/platform-resources/last-applied.json
  doas -n /usr/bin/python3 /usr/local/libexec/klokast-app-resources-reconcile \
    verify \
    --desired "$desired_path" \
    --node-name "$node_name" \
    --node-role "$node_role" \
    "$@"
else
  doas -n /usr/bin/python3 /usr/local/libexec/klokast-app-resources-reconcile \
    verify \
    --desired "$desired_path" \
    --node-name "$node_name" \
    --node-role "$node_role" \
    "$@"
fi

"""


def run_podman_resource_host(
    command,
    host,
    compiled,
    desired_json,
    approved_commit,
    scope_apps,
    run_id,
    *,
    repo_root,
):
    node_name, node_role = compiler.podman_host_node_role(host)
    staging_dir = f"/home/neo/.cache/klokast-platform-resources/{run_id}"
    desired_path = f"{staging_dir}/desired.json"
    helper_path = f"{staging_dir}/klokast-app-resources-reconcile"
    last_applied_path = f"{staging_dir}/last-applied.json"
    scope_args = [f"--scope-app={app}" for app in sorted(set(scope_apps or []))]

    try:
        upload_tailscale_ssh_text(host, desired_path, f"{desired_json}\n", repo_root=repo_root)
        upload_tailscale_ssh_text(
            host,
            helper_path,
            (repo_root / 'ansible/roles/app-resources/files/reconcile-app-resources.py').read_text(encoding="utf-8"),
            repo_root=repo_root,
        )
        if command == "apply":
            last_applied = {
                "approved_commit": approved_commit,
                "registry_sha256": compiled["registry_sha256"],
                "compiler": "platform-resources",
                "compiler_version": model.COMPILER_VERSION,
                "inventory_hostname": host,
            }
            upload_tailscale_ssh_text(
                host,
                last_applied_path,
                json.dumps(last_applied, indent=4, sort_keys=True) + "\n",
                repo_root=repo_root,
            )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        # No verifier has started yet. A failed upload can still leave a
        # partial file; retry only this exact staging directory if reachable.
        try:
            run_tailscale_ssh(
                host,
                ["sh", "-s", "--", staging_dir, desired_path, helper_path,
                 last_applied_path, "cleanup", node_name, node_role],
                input_text=podman_resource_remote_script(), timeout=30,
                repo_root=repo_root,
            )
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            print(f"platform-resources: staged upload cleanup could not reach {host}; review {staging_dir}", file=sys.stderr)
        raise

    run_tailscale_ssh(
        host,
        [
            "sh",
            "-s",
            "--",
            staging_dir,
            desired_path,
            helper_path,
            last_applied_path,
            command,
            node_name,
            node_role,
            *scope_args,
        ],
        input_text=podman_resource_remote_script(),
        timeout=240,
        repo_root=repo_root,
    )


def run_podman_resource_hosts(
    command,
    compiled,
    desired_json,
    approved_commit,
    scope_apps,
    run_id,
    *,
    repo_root,
):
    hosts = compiler.podman_resource_hosts(compiled, scope_apps)
    if not hosts:
        return
    for host in hosts:
        run_podman_resource_host(
            command,
            host,
            compiled,
            desired_json,
            approved_commit,
            scope_apps,
            run_id,
            repo_root=repo_root,
        )


def run_ansible(command, compiled, magicdns_suffix, approved_commit="", scope_apps=None, *, repo_root):
    scope_apps = sorted(set(scope_apps or []))
    compiled = compiler.attach_approved_commit(compiled, approved_commit)
    app_vm_specs = compiler.app_vm_specs_for_scope(compiled, scope_apps)
    boxes = compiler.boxes_for_scope(compiled, scope_apps)
    if not boxes:
        print("platform-resources: no selected boxes; nothing to do")
        return
    if (
        not scope_apps
        and not compiled["app_vm_specs"]
        and not compiled.get("app_resource_effective_files")
        and not compiled.get("app_resource_cleanup_scopes")
        and not compiled.get("box_configs")
    ):
        print("platform-resources: no managed platform resources; nothing to apply")
        return

    run_root = RUN_ROOT
    run_root.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f"{command}-", dir=run_root))
    atexit.register(shutil.rmtree, tmp, ignore_errors=True)
    vars_path = tmp / "extra-vars.yml"
    desired_json = json.dumps(compiled, indent=2, sort_keys=True)
    vars_payload = {
        "app_resource_claims": compiled.get("app_resource_claims") or [],
        "app_resource_effective_files": compiled.get("app_resource_effective_files") or [],
        "app_resource_cleanup_scopes": compiled.get("app_resource_cleanup_scopes") or [],
        "platform_app_vm_specs": app_vm_specs,
        "platform_managed_iot_devices": compiled.get("managed_iot_devices") or [],
        "platform_resources_box_access": {
            box: (config.get("access") or model.default_box_access())
            for box, config in (compiled.get("box_configs") or {}).items()
        },
        "router_managed_dhcp_hosts": compiler.router_managed_dhcp_hosts(compiled),
        "platform_resources_approved_commit": approved_commit,
        "platform_resources_registry_sha256": compiled["registry_sha256"],
        "platform_resources_compiler_version": model.COMPILER_VERSION,
        "platform_resources_apply_scope_apps": scope_apps,
    }
    write_platform_resources_vars(vars_path, vars_payload, desired_json)

    inventory_args = ["-i", str(repo_root / "ansible" / "execution-inventory" / "hosts")]
    for box in boxes:
        inventory_path = tmp / f"{box}.yml"
        render_inventory(
            box,
            inventory_path,
            magicdns_suffix,
            compiled.get("box_configs"),
            repo_root=repo_root,
        )
        inventory_args.extend(["-i", str(inventory_path)])
    if app_vm_specs:
        app_inventory_path = tmp / "app-vms.yml"
        render_app_vm_inventory(
            compiled,
            app_inventory_path,
            magicdns_suffix,
            app_vm_specs=app_vm_specs,
        )
        inventory_args.extend(["-i", str(app_inventory_path)])

    env = _ansible_environment(tmp, repo_root=repo_root)

    resource_playbook = (
        repo_root
        / "ansible"
        / "playbooks"
        / ("80-platform-resources.yml" if command == "apply" else "81-platform-resources-verify.yml")
    )
    router_topology_playbook = repo_root / "ansible" / "playbooks" / "31-vm-router.yml"

    def run_router_topology_playbook():
        subprocess.run(
            [
                "ansible-playbook",
                "-vv",
                *inventory_args,
                str(router_topology_playbook),
                "--limit",
                ",".join(boxes),
                "-e",
                f"@{vars_path}",
            ],
            cwd=repo_root,
            env=env,
            check=True,
        )

    run_id = tmp.name

    def run_resource_playbook():
        resource_hosts = compiler.ansible_resource_hosts(compiled, scope_apps)
        resource_limit = ",".join(resource_hosts)
        if not resource_limit:
            print("platform-resources: no selected Ansible resource hosts; nothing to do")
            return
        subprocess.run(
            [
                "ansible-playbook",
                "-vv",
                *inventory_args,
                str(resource_playbook),
                "--limit",
                resource_limit,
                "-e",
                f"@{vars_path}",
            ],
            cwd=repo_root,
            env=env,
            check=True,
        )

    def run_resource_targets():
        run_resource_playbook()
        run_podman_resource_hosts(
            command,
            compiled,
            desired_json,
            approved_commit,
            scope_apps,
            run_id,
            repo_root=repo_root,
        )

    if command == "apply":
        box_config_boxes = set((compiled.get("box_configs") or {}).keys())
        if compiler.router_resource_hosts(compiled, scope_apps) or (set(boxes) & box_config_boxes):
            run_router_topology_playbook()

    run_resource_targets()

    if command == "apply" and app_vm_specs:
        app_vm_playbook = repo_root / "ansible" / "playbooks" / "79-platform-app-vms.yml"
        subprocess.run(
            [
                "ansible-playbook",
                "-vv",
                *inventory_args,
                str(app_vm_playbook),
                "--limit",
                compiler.limit_for_app_vms(boxes, app_vm_specs),
                "-e",
                f"@{vars_path}",
            ],
            cwd=repo_root,
            env=env,
            check=True,
        )
        run_resource_targets()

