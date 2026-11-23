"""Platform map summary: compare collected facts with the declared Platform.

`ansible/bin/platform-map` collects facts and owns the command line. This
module has no I/O. It parses the collected facts, derives the expected
machines and guests from compiled resource plans, and reports findings.
"""

from __future__ import annotations

import json
import re
import sys
from typing import Any


ROLE_SUFFIXES = ("bootstrap", "dom0", "router", "bak", "dmz", "iot", "ops", "air")
MANAGED_SUFFIXES = ("dom0", "router", "bak", "dmz", "iot")
OPTIONAL_MANAGED_SUFFIXES = ("ops",)
AIRUNNER_SUFFIXES = ("ops-airunner", "ops-airunner-candidate")
PODMAN_SUFFIXES = ("bak", "dmz", "iot")
EXPECTED_GUEST_BY_SUFFIX = {
    "router": "router",
    "bak": "bak",
    "dmz": "dmz",
    "iot": "iot",
}
OPTIONAL_GUEST_BY_SUFFIX = {
    "ops": "ops",
}
KNOWN_MANAGED_CONTAINER_NAMES = {
    "bootstrap-live-builder",
    "nextcloud",
    "nextcloud-postgres",
    "nextcloud-redis",
    "nextcloud-fpm",
    "nextcloud-web",
    "nextcloud-cron",
    "nextcloud-backup",
    "nextcloud-backup-run",
    "nextcloud-cloudflared",
    "immich",
    "immich-server",
    "immich-machine-learning",
    "immich-postgres",
    "immich-valkey",
    "immich-backup-run",
}


def die(message: str) -> None:
    print(f"platform-map: {message}", file=sys.stderr)
    raise SystemExit(1)


def parse_os_release(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] == '"':
            value = value[1:-1]
        result[key] = value
    return result


def parse_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    match = re.search(r"-?\d+", str(value).replace(",", ""))
    if not match:
        return None
    return int(match.group(0))


def parse_json_text(text: str, default: Any) -> Any:
    if isinstance(text, (dict, list)):
        return text
    if text is None:
        return default
    if not isinstance(text, str):
        text = str(text)
    stripped = (text or "").strip()
    if not stripped:
        return default
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        values = []
        for line in stripped.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                values.append(json.loads(line))
            except json.JSONDecodeError:
                return default
        return values


def parse_xl_info(text: str) -> dict[str, Any]:
    info: dict[str, Any] = {}
    for raw_line in text.splitlines():
        if ":" not in raw_line:
            continue
        key, value = raw_line.split(":", 1)
        key = key.strip().replace(" ", "_")
        value = value.strip()
        number = parse_int(value)
        info[key] = number if number is not None and re.fullmatch(r"-?\d+", value) else value
    return info


def parse_xl_list(text: str) -> list[dict[str, Any]]:
    domains: list[dict[str, Any]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.lower().startswith("name "):
            continue
        parts = line.split()
        if len(parts) < 6:
            continue
        domains.append(
            {
                "name": parts[0],
                "id": parse_int(parts[1]),
                "memory_mib": parse_int(parts[2]),
                "vcpus": parse_int(parts[3]),
                "state": parts[4],
                "time_s": parts[5],
            }
        )
    return domains


def parse_free_bytes(text: str) -> dict[str, dict[str, int]]:
    rows: dict[str, dict[str, int]] = {}
    headers: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.lower().startswith("total"):
            headers = line.lower().split()
            continue
        if ":" not in line or not headers:
            continue
        name, rest = line.split(":", 1)
        numbers = [parse_int(value) or 0 for value in rest.split()]
        rows[name.lower()] = {header: numbers[index] for index, header in enumerate(headers[: len(numbers)])}
    return rows


def parse_df(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return rows
    header = lines[0]
    gnu_order = "Mounted on" in header and "Type" in header and header.index("Mounted on") < header.index("Type")
    for raw_line in lines[1:]:
        if not raw_line.strip():
            continue
        parts = raw_line.split()
        if len(parts) < 7:
            continue
        if gnu_order:
            source, target, fstype, size, used, avail, percent = parts[:7]
        else:
            source, fstype, size, used, avail, percent = parts[:6]
            target = parts[6]
        rows.append(
            {
                "source": source,
                "target": target,
                "fstype": fstype,
                "size_bytes": parse_int(size),
                "used_bytes": parse_int(used),
                "avail_bytes": parse_int(avail),
                "used_percent": percent,
            }
        )
    return rows


def peer_index(tailnet: dict[str, Any]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for peer in tailnet.get("peers") or []:
        for name in peer.get("names") or []:
            index[str(name).lower()] = peer
    return index


def peer_for_hostname(index: dict[str, dict[str, Any]], hostname: str) -> dict[str, Any] | None:
    key = hostname.rstrip(".").lower()
    return index.get(key) or index.get(key.split(".", 1)[0])


def managed_labels(labels: dict[str, Any]) -> dict[str, str]:
    return {str(key): str(value) for key, value in labels.items() if str(key).startswith("io.klokast.")}


def container_name(container: dict[str, Any]) -> str:
    for key in ("Names", "names"):
        value = container.get(key)
        if isinstance(value, list) and value:
            return str(value[0])
        if isinstance(value, str) and value:
            return value.split(",", 1)[0]
    for key in ("Name", "name"):
        value = container.get(key)
        if value:
            return str(value)
    return ""


def pod_name(pod: dict[str, Any]) -> str:
    for key in ("Name", "name"):
        value = pod.get(key)
        if value:
            return str(value)
    return ""


def pod_identifiers(pod: dict[str, Any]) -> set[str]:
    values = set()
    for key in ("Id", "ID", "id"):
        value = pod.get(key)
        if value:
            values.add(str(value))
    name = pod_name(pod)
    if name:
        values.add(name)
    return values


def name_matches_managed_app(name: str, app_names: set[str]) -> bool:
    if not name:
        return False
    for app_name in app_names:
        if name == app_name or name.startswith(f"{app_name}-"):
            return True
        prefixed = f"klokast-{app_name}"
        if name == prefixed or name.startswith(f"{prefixed}-"):
            return True
    return False


def managed_app_names_from_fact(fact: dict[str, Any]) -> set[str]:
    desired = desired_platform_resources_from_fact(fact)
    apps = desired.get("apps") if isinstance(desired.get("apps"), dict) else {}
    names = set()
    for app_name, app in apps.items():
        if isinstance(app, dict) and app.get("enabled"):
            names.add(str(app_name))
    return names


def managed_pod_identifiers(pods: list[Any], app_names: set[str]) -> set[str]:
    managed = set()
    for pod in pods:
        if not isinstance(pod, dict):
            continue
        if name_matches_managed_app(pod_name(pod), app_names):
            managed.update(pod_identifiers(pod))
    return managed


def summarize_container(
    container: dict[str, Any],
    *,
    managed_app_names: set[str] | None = None,
    managed_pods: set[str] | None = None,
) -> dict[str, Any]:
    labels = container.get("Labels") or container.get("labels") or {}
    if not isinstance(labels, dict):
        labels = {}
    name = container_name(container)
    app_names = managed_app_names or set()
    pod_ref = str(container.get("Pod") or container.get("PodName") or "")
    managed = (
        bool(managed_labels(labels))
        or name in KNOWN_MANAGED_CONTAINER_NAMES
        or name_matches_managed_app(name, app_names)
        or (bool(pod_ref) and pod_ref in (managed_pods or set()))
    )
    return {
        "name": name,
        "image": container.get("Image") or container.get("ImageName") or "",
        "state": container.get("State") or container.get("Status") or "",
        "pod": pod_ref,
        "ports": container.get("Ports") or [],
        "managed": managed,
        "managed_labels": managed_labels(labels),
    }


def summarize_podman_fact(fact: dict[str, Any]) -> dict[str, Any]:
    podman = fact.get("podman") or {}
    info = parse_json_text(podman.get("info_stdout", ""), {})
    containers_raw = parse_json_text(podman.get("ps_stdout", ""), [])
    pods = parse_json_text(podman.get("pods_stdout", ""), [])
    volumes = parse_json_text(podman.get("volumes_stdout", ""), [])
    if isinstance(containers_raw, dict):
        containers_raw = [containers_raw]
    pods_list = pods if isinstance(pods, list) else []
    managed_app_names = managed_app_names_from_fact(fact)
    managed_pods = managed_pod_identifiers(pods_list, managed_app_names)
    containers = [
        summarize_container(item, managed_app_names=managed_app_names, managed_pods=managed_pods)
        for item in containers_raw
        if isinstance(item, dict)
    ]
    return {
        "host": fact.get("inventory_hostname", ""),
        "reachable": True,
        "runner_user": podman.get("runner_user", "neo"),
        "os": parse_os_release(fact.get("os_release_stdout", "")),
        "memory": parse_free_bytes((fact.get("memory") or {}).get("free_stdout", "")),
        "filesystems": parse_df((fact.get("filesystems") or {}).get("df_stdout", "")),
        "runtime": {
            "available": podman.get("info_rc") == "0" or podman.get("info_rc") == 0,
            "rootless": bool((((info.get("host") or {}).get("security") or {}).get("rootless"))),
            "cgroup_version": (info.get("host") or {}).get("cgroupVersion", ""),
            "network_backend": (info.get("host") or {}).get("networkBackend", ""),
        },
        "containers": containers,
        "pods": pods_list,
        "volumes": volumes if isinstance(volumes, list) else [],
    }


def expected_guests_from_fact(fact: dict[str, Any]) -> dict[str, dict[str, Any]]:
    expected: dict[str, dict[str, Any]] = {}
    specs = ((fact.get("xen") or {}).get("expected_guests") or {})
    if not isinstance(specs, dict):
        return expected
    for role, spec in specs.items():
        if not isinstance(spec, dict):
            continue
        guest_name = str(spec.get("guest_name") or "")
        if not guest_name:
            continue
        expected[guest_name] = {
            "role": role,
            "guest_name": guest_name,
            "memory_mb": parse_int(spec.get("memory_mb")) or 0,
            "vcpus": parse_int(spec.get("vcpus")) or 0,
            "autostart": bool(spec.get("autostart")),
            "runtime_state": str(spec.get("runtime_state") or "running"),
        }
    return expected


def platform_map_app_vms_from_plan(plan: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(plan, dict):
        return []

    app_vms = ((plan.get("platform_map") or {}).get("app_vms") or [])
    if isinstance(app_vms, list) and app_vms:
        normalized = []
        for item in app_vms:
            if not isinstance(item, dict):
                continue
            node = str(item.get("node") or "")
            hostname = str(item.get("hostname") or item.get("tailnet_hostname") or "")
            guest_name = str(item.get("guest_name") or "")
            if not node or not hostname or not guest_name:
                continue
            normalized.append(
                {
                    "node": node,
                    "app": str(item.get("app") or ""),
                    "resource": str(item.get("resource") or ""),
                    "hostname": hostname,
                    "tailnet_hostname": str(item.get("tailnet_hostname") or hostname),
                    "inventory_hostname": str(item.get("inventory_hostname") or hostname),
                    "guest_name": guest_name,
                    "user_slug": str(item.get("user_slug") or ""),
                    "tailscale_login": str(item.get("tailscale_login") or ""),
                    "site_role": str(item.get("site_role") or ""),
                    "zone": str(item.get("zone") or ""),
                    "vm_ipv4_address": str(item.get("vm_ipv4_address") or ""),
                    "expected_tags": sorted(str(tag) for tag in (item.get("expected_tags") or [])),
                    "tailnet_tag": str(item.get("tailnet_tag") or ""),
                    "runtime_state": str(item.get("runtime_state") or "running"),
                    "memory_mb": parse_int(item.get("memory_mb")) or 0,
                    "vcpus": parse_int(item.get("vcpus")) or 0,
                    "autostart": bool(item.get("autostart")),
                }
            )
        return normalized

    normalized = []
    for spec in plan.get("app_vm_specs") or []:
        if not isinstance(spec, dict):
            continue
        guest_spec = spec.get("guest_spec") or {}
        node = str(spec.get("node") or "")
        hostname = str(spec.get("node_hostname") or spec.get("tailnet_hostname") or "")
        guest_name = str(guest_spec.get("guest_name") or "")
        if not node or not hostname or not guest_name:
            continue
        normalized.append(
            {
                "node": node,
                "app": str(spec.get("app") or ""),
                "resource": str(spec.get("resource") or ""),
                "hostname": hostname,
                "tailnet_hostname": str(spec.get("tailnet_hostname") or hostname),
                "inventory_hostname": str(spec.get("inventory_hostname") or hostname),
                "guest_name": guest_name,
                "user_slug": str(spec.get("user_slug") or ""),
                "tailscale_login": str(spec.get("tailscale_login") or ""),
                "site_role": str(spec.get("site_role") or ""),
                "zone": str(spec.get("zone") or ""),
                "vm_ipv4_address": str(spec.get("vm_ipv4_address") or ""),
                "expected_tags": sorted(str(tag) for tag in (spec.get("advertised_tags") or [])),
                "tailnet_tag": str(spec.get("tailnet_tag") or ""),
                "runtime_state": str(spec.get("runtime_state") or "running"),
                "memory_mb": parse_int(guest_spec.get("memory_mb")) or 0,
                "vcpus": parse_int(guest_spec.get("vcpus")) or 0,
                "autostart": bool(guest_spec.get("autostart")),
            }
        )
    return normalized


def desired_platform_resources_from_fact(fact: dict[str, Any]) -> dict[str, Any]:
    platform_resources = fact.get("platform_resources") or {}
    desired = parse_json_text(platform_resources.get("desired_json_stdout", ""), {})
    return desired if isinstance(desired, dict) else {}


def expected_app_vms_by_box_from_plans(plans: list[dict[str, Any]]) -> dict[str, dict[str, dict[str, Any]]]:
    by_box: dict[str, dict[str, dict[str, Any]]] = {}
    for plan in plans:
        for app_vm in platform_map_app_vms_from_plan(plan):
            box = app_vm["node"]
            hostname = app_vm["hostname"]
            by_box.setdefault(box, {})[hostname] = app_vm
    return by_box


def expected_tailnet_hostnames_by_box_from_plans(plans: list[dict[str, Any]]) -> dict[str, set[str]]:
    by_box: dict[str, set[str]] = {}
    for plan in plans:
        platform_map = plan.get("platform_map") if isinstance(plan.get("platform_map"), dict) else {}
        managed_devices = platform_map.get("managed_iot_devices") or plan.get("managed_iot_devices") or []
        for device in managed_devices:
            if not isinstance(device, dict):
                continue
            box = str(device.get("node") or "")
            hostname = str(device.get("hostname") or "")
            if box and hostname:
                by_box.setdefault(box, set()).add(hostname)
        for resource in plan.get("tailnet_resources") or []:
            if not isinstance(resource, dict):
                continue
            box = str(resource.get("node") or "")
            hostname = str(resource.get("hostname") or "")
            if box and hostname and "{" not in hostname and "}" not in hostname:
                by_box.setdefault(box, set()).add(hostname)
    return by_box


def expected_air_by_box_from_plans(plans):
    by_box = {}
    for plan in plans:
        # The controller-private plan is last and can remove old declarations.
        for box in plan.get('box_configs') or {}:
            by_box.pop(box, None)
        for guest in (plan.get('platform_map') or {}).get('airunners', []):
            if guest.get('hostname') == guest.get('node', '') + '-air':
                by_box[guest['node']] = guest
    return by_box


def expected_shared_guests_by_box_from_plans(
    plans: list[dict[str, Any]],
) -> dict[str, dict[str, dict[str, Any]]]:
    by_box: dict[str, dict[str, dict[str, Any]]] = {}
    for plan in plans:
        platform_map = plan.get("platform_map") if isinstance(plan.get("platform_map"), dict) else {}
        for item in platform_map.get("shared_guests") or []:
            if not isinstance(item, dict):
                continue
            node = str(item.get("node") or "")
            role = str(item.get("role") or "")
            if not node or role not in {"bak", "dmz", "iot"}:
                continue
            by_box.setdefault(node, {})[role] = {
                "runtime_state": str(item.get("runtime_state") or "running"),
                "autostart": bool(item.get("autostart")),
            }
    return by_box


def summarize_storage(storage: dict[str, Any], findings: list[dict[str, Any]], scope: str) -> dict[str, Any]:
    managed_layout = storage.get("managed_layout") or {}
    vg_size = parse_int(managed_layout.get("volume_group_size_bytes"))
    vg_free = parse_int(managed_layout.get("volume_group_free_bytes"))
    if vg_size and vg_free is not None:
        free_ratio = vg_free / vg_size
        if free_ratio < 0.05:
            findings.append(finding("critical", "low_vg_free_space", scope, "managed volume group free space is below 5%"))
        elif free_ratio < 0.10:
            findings.append(finding("warning", "low_vg_free_space", scope, "managed volume group free space is below 10%"))
    return {
        "managed_layout": managed_layout,
        "disks": storage.get("disks") or [],
        "partitions": storage.get("partitions") or [],
        "volume_groups": storage.get("volume_groups") or [],
        "logical_volumes": storage.get("logical_volumes") or [],
    }


def finding(severity: str, code: str, scope: str, message: str, **details: Any) -> dict[str, Any]:
    item = {"severity": severity, "code": code, "scope": scope, "message": message}
    item.update(details)
    return item


def summarize_memory(
    *,
    xl_info: dict[str, Any],
    domains: list[dict[str, Any]],
    expected_guests: dict[str, dict[str, Any]],
    findings: list[dict[str, Any]],
    scope: str,
) -> dict[str, Any]:
    total_mib = parse_int(xl_info.get("total_memory"))
    free_mib = parse_int(xl_info.get("free_memory"))
    live_domain_memory_mib = sum(domain.get("memory_mib") or 0 for domain in domains)
    live_guest_memory_mib = sum(
        domain.get("memory_mib") or 0 for domain in domains if domain.get("name") != "Domain-0"
    )
    dom0_memory_mib = next((domain.get("memory_mib") or 0 for domain in domains if domain.get("name") == "Domain-0"), 0)
    # The update system sets the size of a guest that has no declared size,
    # so that guest counts at its live size.
    live_memory_mib = {domain.get("name"): domain.get("memory_mib") or 0 for domain in domains}
    expected_guest_memory_mib = sum(
        spec.get("memory_mb") or live_memory_mib.get(name, 0)
        for name, spec in expected_guests.items()
        if spec.get("runtime_state", "running") == "running"
    )
    pressure = "unknown"
    if total_mib:
        pressure = "ok"
        if free_mib is not None:
            free_ratio = free_mib / total_mib
            if free_mib < 256 or free_ratio < 0.03:
                pressure = "critical"
                findings.append(finding("critical", "xen_memory_pressure", scope, "Xen free memory is critically low"))
            elif free_mib < 1024 or free_ratio < 0.08:
                pressure = "warning"
                findings.append(finding("warning", "xen_memory_pressure", scope, "Xen free memory is low"))
        if expected_guest_memory_mib + dom0_memory_mib > total_mib:
            pressure = "critical"
            findings.append(
                finding(
                    "critical",
                    "xen_expected_memory_overcommit",
                    scope,
                    "expected VM memory plus dom0 memory exceeds host memory",
                )
            )
    return {
        "host_total_mib": total_mib,
        "xen_free_mib": free_mib,
        "dom0_live_memory_mib": dom0_memory_mib,
        "live_domain_memory_mib": live_domain_memory_mib,
        "live_guest_memory_mib": live_guest_memory_mib,
        "expected_guest_memory_mib": expected_guest_memory_mib,
        "pressure": pressure,
    }


def box_override(overrides: dict[str, Any], box: str) -> dict[str, Any]:
    boxes = overrides.get("boxes") if isinstance(overrides.get("boxes"), dict) else {}
    value = boxes.get(box)
    return value if isinstance(value, dict) else {}


def summarize_router_updates(fact, box):
    unavailable = {"available": False}
    if not isinstance(fact, dict) or fact.get("rc") not in (0, "0"):
        return unavailable
    outer = parse_json_text(fact.get("stdout", ""), {})
    if (not isinstance(outer, dict) or outer.get("kind") != "klokast.router-command-result.v1" or
            outer.get("action") != "map-status" or outer.get("box") != box):
        return unavailable
    value = outer.get("result")
    def generation(item):
        return item is None or (isinstance(item, dict) and set(item) == {
            "generation_id", "origin", "kernel_release", "record_sha256",
            "machine_id", "tailnet_hostname"} and
            isinstance(item["generation_id"], str) and re.fullmatch("[0-9a-f]{24}", item["generation_id"]) and
            item["origin"] in ("legacy", "template") and isinstance(item["kernel_release"], str) and
            re.fullmatch("[A-Za-z0-9_.+-]{1,128}", item["kernel_release"]) and
            isinstance(item["record_sha256"], str) and re.fullmatch("[0-9a-f]{64}", item["record_sha256"]) and
            (item["machine_id"] is None or isinstance(item["machine_id"], str) and
             re.fullmatch("[A-Za-z0-9_-]{1,128}", item["machine_id"])) and
            (item["tailnet_hostname"] is None or item["tailnet_hostname"] in
             (box + "-router", box + "-router-" + item["generation_id"])))
    if (not isinstance(value, dict) or set(value) != {"kind", "box", "current", "previous", "pending", "state_copy"} or
            value["kind"] != "klokast.router-map.v2" or value["box"] != box or
            not generation(value["current"]) or not generation(value["previous"])):
        return unavailable
    pending, copied = value["pending"], value["state_copy"]
    if pending is not None and (not isinstance(pending, dict) or set(pending) != {
            "phase", "candidate_started", "old_started", "operation_id", "old", "candidate"} or
            not isinstance(pending["phase"], str) or not re.fullmatch("[a-z-]{1,40}", pending["phase"]) or
            any(type(pending[k]) is not bool for k in ("candidate_started", "old_started")) or
            not isinstance(pending["operation_id"], str) or not re.fullmatch("[0-9a-f]{24}", pending["operation_id"]) or
            any(pending[k] is None or not generation(pending[k]) for k in ("old", "candidate"))):
        return unavailable
    if copied is not None and (not isinstance(copied, dict) or set(copied) != {"operation_id", "forward", "reverse"} or
            not isinstance(copied["operation_id"], str) or not re.fullmatch("[0-9a-f]{24}", copied["operation_id"]) or
            any(copied[k] not in ("complete", "absent", "unverified") for k in ("forward", "reverse")) or
            pending is not None and copied["operation_id"] != pending["operation_id"]):
        return unavailable
    return {"available": True, **{k:value[k] for k in ("current", "previous", "pending", "state_copy")}}


def default_expected_guest(role: str, guest_name: str) -> dict[str, Any]:
    return {"role": role, "guest_name": guest_name, "memory_mb": 0, "vcpus": 0, "autostart": True}


def expected_shared_guests_for_box(
    dom0_fact: dict[str, Any],
    expected_air: dict[str, Any] | None,
    expected_shared_guests: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    expected_guests = expected_guests_from_fact(dom0_fact)
    if not expected_guests:
        expected_guests = {
            guest: default_expected_guest(suffix, guest)
            for suffix, guest in EXPECTED_GUEST_BY_SUFFIX.items()
        }
    # Old host facts cannot make the retired shared guest managed again.
    expected_guests.pop("usr", None)
    expected_guests.pop("air", None)
    if expected_air:
        expected_guests['air'] = {**expected_air, 'role': 'air', 'autostart': True,
                                  'runtime_state': 'running'}
    for role, state in expected_shared_guests.items():
        guest_name = EXPECTED_GUEST_BY_SUFFIX.get(role, role)
        if guest_name in expected_guests:
            expected_guests[guest_name]["runtime_state"] = state.get(
                "runtime_state", "running"
            )
            expected_guests[guest_name]["autostart"] = bool(state.get("autostart"))
    return expected_guests


def select_optional_suffixes(
    box: str,
    tailnet_index: dict[str, dict[str, Any]],
    live_guest_names: set[str],
    override: dict[str, Any],
    expected_guests: dict[str, dict[str, Any]],
) -> list[str]:
    """Return the optional roles that the box has, and update expected guests."""
    optional_suffixes: list[str] = []
    for suffix in OPTIONAL_MANAGED_SUFFIXES:
        hostname = f"{box}-{suffix}"
        guest_name = OPTIONAL_GUEST_BY_SUFFIX.get(suffix, suffix)
        if (
            peer_for_hostname(tailnet_index, hostname) is not None
            or guest_name in live_guest_names
            or bool(override.get(f"expect_{suffix}"))
        ):
            optional_suffixes.append(suffix)
        else:
            expected_guests.pop(guest_name, None)

    for suffix in optional_suffixes:
        guest_name = OPTIONAL_GUEST_BY_SUFFIX.get(suffix, suffix)
        expected_guests.setdefault(guest_name, default_expected_guest(suffix, guest_name))
    return optional_suffixes


def summarize_machines(
    box: str,
    machine_suffixes: list[str],
    tailnet_index: dict[str, dict[str, Any]],
    expected_guests: dict[str, dict[str, Any]],
    findings: list[dict[str, Any]],
) -> dict[str, Any]:
    machines: dict[str, Any] = {}
    for suffix in machine_suffixes:
        hostname = f"{box}-{suffix}"
        peer = peer_for_hostname(tailnet_index, hostname)
        guest_name = EXPECTED_GUEST_BY_SUFFIX.get(suffix, suffix)
        runtime_state = (
            (expected_guests.get(guest_name) or {}).get("runtime_state", "running")
            if suffix in EXPECTED_GUEST_BY_SUFFIX
            else "running"
        )
        machines[suffix] = {
            "hostname": hostname,
            "runtime_state": runtime_state,
            "tailscale": {
                "present": peer is not None,
                "online": bool(peer.get("online")) if peer else False,
                "dns_name": peer.get("dns_name", "") if peer else "",
                "tags": peer.get("tags", []) if peer else [],
            },
        }
        if runtime_state == "running" and peer is None:
            findings.append(finding("warning", "missing_tailscale_machine", hostname, "expected machine is absent from Tailscale"))
        elif runtime_state == "running" and not peer.get("online"):
            findings.append(finding("warning", "offline_tailscale_machine", hostname, "expected machine is offline in Tailscale"))
        if suffix == 'air' and peer and peer.get('tags') != ['tag:airunner']:
            findings.append(finding('critical', 'airunner_tags', hostname, 'runner must have exactly tag:airunner'))
    return machines


def check_unexpected_peers(
    box: str,
    tailnet_index: dict[str, dict[str, Any]],
    expected_app_hostnames: set[str],
    expected_tailnet_hostnames: set[str],
    expected_air: dict[str, Any] | None,
    findings: list[dict[str, Any]],
) -> None:
    seen_peers: set[str] = set()
    for peer_name, peer in sorted(tailnet_index.items()):
        identity = peer.get("dns_name") or peer.get("name") or peer_name
        if identity in seen_peers:
            continue
        seen_peers.add(identity)
        if not peer_name.startswith(f"{box}-"):
            continue
        short = peer_name.split(".", 1)[0]
        suffix = short.removeprefix(f"{box}-")
        if suffix == "bootstrap" and peer.get("online"):
            findings.append(finding("warning", "bootstrap_identity_online", short, "bootstrap Tailscale identity is still online"))
        elif short in expected_app_hostnames:
            continue
        elif short in expected_tailnet_hostnames:
            continue
        elif suffix == 'air' and expected_air:
            continue
        elif suffix in AIRUNNER_SUFFIXES and peer.get("tags") == ["tag:airunner"]:
            continue
        elif suffix not in ROLE_SUFFIXES:
            findings.append(finding("warning", "unexpected_tailscale_machine", short, "Tailscale machine uses the box prefix but is not an expected role"))


def expected_app_guests(expected_app_vms: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    guests: dict[str, dict[str, Any]] = {}
    for app_vm in expected_app_vms.values():
        guests[app_vm["guest_name"]] = {
            "role": "app_vm",
            "app": app_vm.get("app", ""),
            "user_slug": app_vm.get("user_slug", ""),
            "hostname": app_vm["hostname"],
            "guest_name": app_vm["guest_name"],
            "memory_mb": (parse_int(app_vm.get("memory_mb")) or 0) if app_vm.get("runtime_state", "running") == "running" else 0,
            "vcpus": parse_int(app_vm.get("vcpus")) or 0,
            "autostart": bool(app_vm.get("autostart")),
            "runtime_state": app_vm.get("runtime_state", "running"),
        }
    return guests


def summarize_app_vms(
    expected_app_vms: dict[str, dict[str, Any]],
    tailnet_index: dict[str, dict[str, Any]],
    xen_domains: list[dict[str, Any]],
    findings: list[dict[str, Any]],
) -> dict[str, Any]:
    domain_by_name = {domain["name"]: domain for domain in xen_domains if domain.get("name")}
    app_vms: dict[str, Any] = {}
    for hostname, app_vm in sorted(expected_app_vms.items()):
        peer = peer_for_hostname(tailnet_index, app_vm.get("tailnet_hostname") or hostname)
        expected_tags = sorted(str(tag) for tag in (app_vm.get("expected_tags") or []))
        actual_tags = sorted(peer.get("tags", []) if peer else [])
        missing_tags = sorted(set(expected_tags) - set(actual_tags))
        guest_name = app_vm["guest_name"]
        live_domain = domain_by_name.get(guest_name)
        app_vms[hostname] = {
            "hostname": hostname,
            "app": app_vm.get("app", ""),
            "resource": app_vm.get("resource", ""),
            "user_slug": app_vm.get("user_slug", ""),
            "tailscale_login": app_vm.get("tailscale_login", ""),
            "site_role": app_vm.get("site_role", ""),
            "runtime_state": app_vm.get("runtime_state", "running"),
            "zone": app_vm.get("zone", ""),
            "vm_ipv4_address": app_vm.get("vm_ipv4_address", ""),
            "guest_name": guest_name,
            "expected_tags": expected_tags,
            "tailscale": {
                "present": peer is not None,
                "online": bool(peer.get("online")) if peer else False,
                "dns_name": peer.get("dns_name", "") if peer else "",
                "tags": actual_tags,
                "missing_tags": missing_tags,
                "tags_ok": not missing_tags,
            },
            "xen": {
                "running": live_domain is not None,
                "memory_mib": live_domain.get("memory_mib") if live_domain else None,
                "vcpus": live_domain.get("vcpus") if live_domain else None,
                "state": live_domain.get("state") if live_domain else "",
            },
        }
        runtime_state = app_vm.get("runtime_state", "running")
        if runtime_state == "stopped" and live_domain is not None:
            findings.append(finding("warning", "app_vm_running_while_stopped", hostname, "app VM is running but desired runtime_state is stopped"))
        if runtime_state == "running" and peer is None:
            findings.append(finding("warning", "missing_tailscale_machine", hostname, "expected app VM machine is absent from Tailscale"))
        elif runtime_state == "running" and not peer.get("online"):
            findings.append(finding("warning", "offline_tailscale_machine", hostname, "expected app VM machine is offline in Tailscale"))
        if runtime_state == "running" and missing_tags:
            findings.append(
                finding(
                    "critical",
                    "app_vm_tailnet_tags_mismatch",
                    hostname,
                    "app VM Tailscale tags do not match compiled expectations",
                    expected_tags=expected_tags,
                    actual_tags=actual_tags,
                    missing_tags=missing_tags,
                )
            )
    return app_vms


def check_xen_domains(
    box: str,
    expected_guests: dict[str, dict[str, Any]],
    live_guest_names: set[str],
    machine_suffixes: list[str],
    machines: dict[str, Any],
    app_vms: dict[str, Any],
    findings: list[dict[str, Any]],
) -> None:
    expected_guest_names = set(expected_guests)
    scope = f"{box}-dom0"
    for live_name in sorted(live_guest_names - expected_guest_names):
        findings.append(finding("critical", "unmanaged_xen_domain", scope, f"unexpected Xen domain is running: {live_name}"))
    for missing_name in sorted(expected_guest_names - live_guest_names):
        if (expected_guests.get(missing_name) or {}).get("runtime_state", "running") == "stopped":
            continue
        findings.append(finding("warning", "missing_xen_domain", scope, f"expected Xen domain is not running: {missing_name}"))
    for running_name in sorted(expected_guest_names & live_guest_names):
        if (expected_guests.get(running_name) or {}).get("runtime_state") == "stopped":
            findings.append(
                finding(
                    "warning",
                    "shared_guest_running_while_stopped",
                    scope,
                    f"shared Xen guest is running while desired runtime_state is stopped: {running_name}",
                )
            )
    guest_by_machine_suffix = {**EXPECTED_GUEST_BY_SUFFIX, **OPTIONAL_GUEST_BY_SUFFIX}
    for suffix in machine_suffixes:
        guest_name = guest_by_machine_suffix.get(suffix, suffix)
        hostname = f"{box}-{suffix}"
        if guest_name in live_guest_names and not machines[suffix]["tailscale"]["present"]:
            findings.append(
                finding(
                    "critical",
                    "vm_not_enrolled_to_tailscale",
                    hostname,
                    "Xen VM is running but no matching Tailscale machine was found",
                )
            )
    for hostname, app_vm in app_vms.items():
        if app_vm["guest_name"] in live_guest_names and not app_vm["tailscale"]["present"]:
            findings.append(
                finding(
                    "critical",
                    "vm_not_enrolled_to_tailscale",
                    hostname,
                    "Xen app VM is running but no matching Tailscale machine was found",
                )
            )


def summarize_box_podman(
    box: str,
    remote_facts: dict[str, dict[str, Any]],
    findings: list[dict[str, Any]],
) -> dict[str, Any]:
    podman: dict[str, Any] = {}
    for suffix in PODMAN_SUFFIXES:
        hostname = f"{box}-{suffix}"
        fact = remote_facts.get(hostname)
        if not fact:
            podman[suffix] = {"host": hostname, "reachable": False, "containers": []}
            continue
        summary = summarize_podman_fact(fact)
        for container in summary.get("containers") or []:
            if not container.get("managed"):
                findings.append(
                    finding(
                        "warning",
                        "unmanaged_podman_container",
                        hostname,
                        f"container is not recognized as Klokast-managed: {container.get('name')}",
                    )
                )
        podman[suffix] = summary
    return podman


def summarize_box(
    box: str,
    *,
    tailnet_index: dict[str, dict[str, Any]],
    remote_facts: dict[str, dict[str, Any]],
    overrides: dict[str, Any],
    expected_app_vms: dict[str, dict[str, Any]] | None = None,
    expected_tailnet_hostnames: set[str] | None = None,
    expected_shared_guests: dict[str, dict[str, Any]] | None = None,
    expected_air: dict[str, Any] | None = None,
) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    expected_app_vms = {name: value for name, value in (expected_app_vms or {}).items()
                        if name != f"{box}-usr" and value.get("guest_name") != "usr"}
    expected_app_hostnames = set(expected_app_vms)
    expected_tailnet_hostnames = (expected_tailnet_hostnames or set()) - {f"{box}-usr"}
    dom0_fact = remote_facts.get(f"{box}-dom0") or {}
    xen_fact = dom0_fact.get("xen") or {}
    expected_guests = expected_shared_guests_for_box(dom0_fact, expected_air, expected_shared_guests or {})
    xen_info = parse_xl_info(xen_fact.get("xl_info_stdout") or "")
    xen_domains = parse_xl_list(xen_fact.get("xl_list_stdout") or "")
    live_guest_names = {domain["name"] for domain in xen_domains if domain.get("name") != "Domain-0"}

    override = box_override(overrides, box)
    if "expect_usr" in override:
        die(f"boxes.{box}.expect_usr is retired; remove it from Platform map overrides")
    optional_suffixes = select_optional_suffixes(box, tailnet_index, live_guest_names, override, expected_guests)

    machine_suffixes = [*MANAGED_SUFFIXES, *optional_suffixes, *(['air'] if expected_air else [])]
    expected_hosts = [f"{box}-{suffix}" for suffix in machine_suffixes]
    expected_hosts.extend(sorted(expected_app_hostnames))
    expected_hosts.extend(sorted(expected_tailnet_hostnames - expected_app_hostnames))
    machines = summarize_machines(box, machine_suffixes, tailnet_index, expected_guests, findings)
    check_unexpected_peers(
        box, tailnet_index, expected_app_hostnames, expected_tailnet_hostnames, expected_air, findings
    )
    expected_guests.update(expected_app_guests(expected_app_vms))
    app_vms = summarize_app_vms(expected_app_vms, tailnet_index, xen_domains, findings)

    if dom0_fact:
        check_xen_domains(box, expected_guests, live_guest_names, machine_suffixes, machines, app_vms, findings)
    else:
        findings.append(finding("warning", "missing_dom0_remote_facts", f"{box}-dom0", "dom0 remote facts were not collected"))

    storage = summarize_storage(dom0_fact.get("storage") or {}, findings, f"{box}-dom0") if dom0_fact else {}
    memory = summarize_memory(
        xl_info=xen_info,
        domains=xen_domains,
        expected_guests=expected_guests,
        findings=findings,
        scope=f"{box}-dom0",
    )
    podman = summarize_box_podman(box, remote_facts, findings)

    return {
        "name": box,
        "overrides": override,
        "expected_hosts": expected_hosts,
        "machines": machines,
        "app_vms": app_vms,
        "dom0": {
            "reachable": bool(dom0_fact),
            "router_updates": summarize_router_updates(dom0_fact.get("router_updates"), box),
            "os": parse_os_release(dom0_fact.get("os_release_stdout", "")) if dom0_fact else {},
            "storage": storage,
            "xen": {
                "available": bool(dom0_fact and xen_fact.get("xl_info_rc") in ("0", 0)),
                "info": xen_info,
                "domains": xen_domains,
                "expected_guests": expected_guests,
                "config_files": (xen_fact.get("config_files_stdout") or "").splitlines(),
                "autostart_files": (xen_fact.get("autostart_files_stdout") or "").splitlines(),
            },
        },
        "capacity": {
            "memory": memory,
        },
        "podman": podman,
        "findings": findings,
    }
