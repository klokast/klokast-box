"""Resource declarations, validation, and topology inputs."""
import hashlib
import ipaddress
import re
import sys
from datetime import datetime, timezone
import yaml


COMPILER_VERSION = 26


TAILSCALE_WIREGUARD_PORT = 41641


ROLE_SUFFIXES = ("router", "bak", "dmz", "iot")


CLEANUP_HOST_ROLES = ("router", "backend", "dmz", "iot")


PODMAN_ROLE_SUFFIXES = ("bak", "dmz", "iot")


PODMAN_NODE_ROLES = {
    "bak": "backend",
    "dmz": "dmz",
    "iot": "iot",
}


ROLE_HOST_SUFFIXES = {
    "router": "router",
    "backend": "bak",
    "bak": "bak",
    "dmz": "dmz",
    "iot": "iot",
}


BOX_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")


ID_RE = re.compile(r"[a-z][a-z0-9-]{0,63}")


USER_FIELD_RE = re.compile(r"[a-z][a-z0-9_]{0,63}")


SLUG_RE = re.compile(r"[a-z][a-z0-9-]{0,23}")


SYSTEM_USER_RE = re.compile(r"[a-z_][a-z0-9_-]{0,31}")


IFACE_RE = re.compile(r"[A-Za-z0-9_.:-]{1,32}")


TAG_RE = re.compile(r"tag:[A-Za-z0-9][A-Za-z0-9_-]*")


LOGIN_RE = re.compile(r"[^@\s]+@[^@\s]+")


MAC_RE = re.compile(r"[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}")


WORKLOAD_ROLES = ("backend", "dmz", "iot", "usr")


DEBIAN_APP_VM_SECURITY_MIRROR = "http://security.debian.org/debian-security"


DEBIAN_APP_VM_ARCHITECTURE = "amd64"


DEBIAN_APP_VM_VARIANT = "minbase"


DEBIAN_APP_VM_IMAGE_SIZE = "8G"


DEBIAN_APP_VM_BUILD_PROFILE_VERSION = 2


DEBIAN_APP_VM_BUILDER_ARTIFACT_PORT = 18082


DOM0_XEN_CONFIG_DIR = "/etc/xen"


DOM0_XEN_IMAGES_PATH = "/mnt/dom0_data/xen_images"


DEBIAN_APP_VM_PACKAGES = (
    "bash",
    "ca-certificates",
    "curl",
    "dbus",
    "dbus-user-session",
    "gnupg",
    "ifupdown",
    "initramfs-tools",
    "iproute2",
    "iptables",
    "linux-image-amd64",
    "locales",
    "netbase",
    "nftables",
    "openssh-client",
    "openssh-server",
    "procps",
    "python3",
    "sudo",
    "systemd-sysv",
    "udev",
)


RAW_NETWORK_FIELDS = {
    "router",
    "vm_input",
    "source",
    "destination",
    "interface",
    "in_interface",
    "out_interface",
    "wan_interface",
    "target_role",
}


TCB_CONTROL_FIELDS = {
    "auth_key",
    "authkey",
    "box",
    "boxes",
    "builder_box",
    "builder_host",
    "builder_node",
    "builder_vm",
    "node",
    "nodes",
    "oauth_client",
    "oauth_secret",
    "owner",
    "owners",
    "placement",
    "privileged_builder_host",
    "tag_owner",
    "tag_owners",
    "tailnet_tag_owner",
    "tailnet_tag_owners",
    "target_box",
}


MANIFEST_TOP_LEVEL_FIELDS = {
    "schema_version",
    "app",
    "risk_class",
    "default_isolation",
    "placement_mode",
    "datasets",
    "features",
    "resources",
    "_manifest_path",
}


FEATURE_FIELDS = {"id", "type", "values", "resource_bindings"}


FEATURE_TYPES = {"boolean", "enum"}


DATASET_FIELDS = {
    "id",
    "type",
    "rationale",
}


DATASET_TYPES = {"durable_user_data"}


RESOURCE_SECTION_FIELDS = {
    "compute",
    "network",
    "tailnet",
    "artifacts",
}


COMPUTE_COMMON_FIELDS = {
    "id",
    "type",
    "zone",
    "privilege",
    "lifecycle",
    "rationale",
    "isolation",
    "cleanup_required",
    "access",
    "requires_capability",
}


COMPUTE_FIELDS_BY_TYPE = {
    "podman_workload": COMPUTE_COMMON_FIELDS,
    "host_service": COMPUTE_COMMON_FIELDS,
    "ephemeral_privileged_builder": COMPUTE_COMMON_FIELDS,
    "managed_iot_device": COMPUTE_COMMON_FIELDS
    | {
        "hostname_suffix",
        "hostname_default",
        "management_tag_default",
    },
    "per_user_app_vm": COMPUTE_COMMON_FIELDS
    | {
        "vm_name_prefix",
        "tailnet_tag_prefix",
        "guest_os",
        "debian_release",
        "container_runtime",
        "memory_mb",
        "vcpus",
        "lv_size",
    },
    "app_vm": COMPUTE_COMMON_FIELDS
    | {
        "hostname_suffix",
        "tailnet_tag_default",
        "guest_os",
        "container_runtime",
        "memory_mb",
        "vcpus",
        "lv_size",
    },
}


NETWORK_COMMON_FIELDS = {
    "id",
    "type",
    "required",
    "enabled_by_default",
    "exclusive",
    "rationale",
    "access",
    "requires_capability",
}


NETWORK_FIELDS_BY_TYPE = {
    "interzone_tcp": NETWORK_COMMON_FIELDS
    | {"from_zone", "to_zone", "ports", "port", "ports_from_user"},
    "realm_to_zone_tcp": NETWORK_COMMON_FIELDS
    | {"from_realm", "to_zone", "ports", "port"},
    "wan_egress": NETWORK_COMMON_FIELDS | {"from_zone", "tcp_ports", "udp_ports"},
    "zone_to_device_tcp": NETWORK_COMMON_FIELDS
    | {"from_zone", "device", "ports", "port"},
    "device_to_zone_tcp": NETWORK_COMMON_FIELDS
    | {"device", "to_zone", "ports", "port"},
    "device_wan_egress": NETWORK_COMMON_FIELDS | {"device", "tcp_ports", "udp_ports"},
    "tailnet_tcp": NETWORK_COMMON_FIELDS,
}


TAILNET_RESOURCE_FIELDS = {
    "id",
    "required",
    "enabled_by_default",
    "hostname_default",
    "hostname",
    "hostname_pattern",
    "tag_default",
    "tag",
    "tag_pattern",
    "grants",
    "access",
    "requires_capability",
}


TAILNET_GRANT_FIELDS = {
    "src",
    "dst",
    "ports",
    "tcp_ports",
    "udp_ports",
}


ARTIFACT_COMMON_FIELDS = {
    "id",
    "type",
    "rationale",
}


ARTIFACT_FIELDS_BY_TYPE = {
    "release_artifact": ARTIFACT_COMMON_FIELDS | {"secret_free", "require_sha512"},
    "npm_package": ARTIFACT_COMMON_FIELDS | {"package", "version", "integrity"},
    "oci_images": ARTIFACT_COMMON_FIELDS | {"lock_file", "require_digest"},
}


RESERVED_APP_TAILNET_TAGS = {
    "tag:bootstrap",
    "tag:dom0",
    "tag:infra",
    "tag:infra-agent",
    "tag:oob",
    "tag:ops",
    "tag:router",
    "tag:vm",
}


ACCESS_CAPABILITIES = (
    "ap-uplink",
    "local-lan",
    "rg-lan",
    "overlay",
    "vpn-egress",
    "edge-ingress",
    "direct-egress",
    "direct-ingress",
)


ACCESS_POLICY_INTENTS = (
    "local-presence-control",
    "private-service-ingress",
    "file-upload",
    "household-wan-egress",
    "public-ingress",
)


ACCESS_RESOURCE_FIELDS = {
    "intent",
    "capability",
}


ACCESS_BOX_FIELDS = {
    "available_capabilities",
    "enabled_capabilities",
    "prohibited_capabilities",
}


BOX_CONFIG_FIELDS = {
    "vpn_egress",
    "access",
    "dhcp_reservations",
    "dom0_bridge_ports",
    "shared_guests",
}


SHARED_GUEST_ROLES = ("bak", "dmz", "iot")


SHARED_GUEST_STATES = ("running", "stopped")


SHARED_GUEST_FIELDS = {"runtime_state"}


def die(message):
    print(f"platform-resources: {message}", file=sys.stderr)
    raise SystemExit(1)


def load_yaml(path):
    try:
        with path.open(encoding="utf-8") as handle:
            value = yaml.safe_load(handle)
    except FileNotFoundError:
        die(f"missing YAML file: {path}")
    except yaml.YAMLError as error:
        die(f"invalid YAML in {path}: {error}")
    if value is None:
        return {}
    if not isinstance(value, dict):
        die(f"YAML root must be a mapping: {path}")
    return value


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_id(value, field):
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        die(f"{field} must be kebab-case and start with a letter")


def validate_app_id(value):
    validate_id(value, "application name")
    if value in ("platform", "doctor"):
        die(f"application name {value} is reserved for kk {value}")


def validate_user_field(value, field):
    if not isinstance(value, str) or not USER_FIELD_RE.fullmatch(value):
        die(f"{field} must be snake_case and start with a letter")


def validate_box(value, field):
    if not isinstance(value, str) or not BOX_RE.fullmatch(value):
        die(f"{field} must be a box name like boxa")
    for suffix in ("bootstrap", "dom0", "router", "bak", "dmz", "iot", "usr", "ops", "agent", "agt"):
        if value == suffix or value.endswith(f"-{suffix}"):
            die(f"{field} must be a box name, not a role hostname")


def validate_hostname(value, field):
    if not isinstance(value, str) or not BOX_RE.fullmatch(value):
        die(f"{field} must be a DNS-safe hostname")


def validate_mac(value, field):
    if not isinstance(value, str) or not MAC_RE.fullmatch(value):
        die(f"{field} must be a MAC address like aa:bb:cc:dd:ee:ff")


def validate_iface(value, field):
    if not isinstance(value, str) or not IFACE_RE.fullmatch(value):
        die(f"{field} must be a network interface name")


def validate_ip(value, field, allow_empty=False):
    if allow_empty and value == "":
        return
    if not isinstance(value, str) or not value:
        die(f"{field} must be an IPv4 address or CIDR")
    try:
        if "/" in value:
            ipaddress.ip_network(value, strict=False)
        else:
            ipaddress.ip_address(value)
    except ValueError:
        die(f"{field} must be an IPv4 address or CIDR: {value}")


def validate_ports(values, field):
    if not isinstance(values, list) or not values:
        die(f"{field} must be a non-empty list of explicit ports")
    ports = []
    for value in values:
        if not isinstance(value, int):
            die(f"{field} must contain integer ports only")
        if value < 1 or value > 65535:
            die(f"{field} contains an invalid TCP/UDP port: {value}")
        ports.append(value)
    return sorted(set(ports))


def validate_tailnet_port_selectors(values, field):
    if not isinstance(values, list) or not values:
        die(f"{field} must be a non-empty list of ports or port ranges")
    selectors = []
    for value in values:
        if isinstance(value, int):
            validate_ports([value], field)
            selectors.append(str(value))
            continue
        if isinstance(value, str):
            match = re.fullmatch(r"([0-9]+)-([0-9]+)", value)
            if match:
                start = int(match.group(1))
                end = int(match.group(2))
                validate_ports([start, end], field)
                if start > end:
                    die(f"{field} contains an invalid port range: {value}")
                selectors.append(value)
                continue
        die(f"{field} must contain integer ports or start-end ranges")
    return sorted(set(selectors), key=lambda item: (int(item.split("-", 1)[0]), item))


def validate_tag(value, field):
    if not isinstance(value, str) or not TAG_RE.fullmatch(value):
        die(f"{field} must be a Tailscale tag like tag:nextcloud")


def validate_app_tailnet_tag(value, field):
    validate_tag(value, field)
    if value in RESERVED_APP_TAILNET_TAGS:
        die(f"{field} must not use reserved Platform control tag {value}")


def validate_slug(value, field):
    if not isinstance(value, str) or not SLUG_RE.fullmatch(value):
        die(f"{field} must be a lowercase slug like alice")


def validate_login(value, field):
    if not isinstance(value, str) or not LOGIN_RE.fullmatch(value):
        die(f"{field} must be an exact Tailscale login email")


def validate_system_user(value, field):
    if not isinstance(value, str) or not SYSTEM_USER_RE.fullmatch(value):
        die(f"{field} must be a POSIX user name like alice")
    if value in {"root", "daemon", "bin", "sys", "sync", "games", "man", "lp", "mail", "news", "uucp", "proxy", "www-data", "backup", "list", "irc", "gnats", "nobody"}:
        die(f"{field} must not be a reserved system account: {value}")


def validate_ip_in_cidr(value, cidr, field):
    validate_ip(value, field)
    address = ipaddress.ip_address(value)
    network = ipaddress.ip_network(cidr, strict=False)
    if address not in network:
        die(f"{field} must be inside {cidr}: {value}")


def load_topology(*, repo_root):
    topology_path = repo_root / 'ansible/inventory-policy/group_vars/all.yml'
    inventory_vars = load_yaml(topology_path)
    zones = inventory_vars.get("platform_zones") or {}
    control_zones = inventory_vars.get("platform_control_zones") or {}
    aliases = inventory_vars.get("platform_zone_aliases") or {}
    realms = inventory_vars.get("platform_network_realms") or {}
    if not isinstance(zones, dict) or not zones:
        die(f"{topology_path} platform_zones must be a non-empty mapping")
    if not isinstance(aliases, dict):
        die(f"{topology_path} platform_zone_aliases must be a mapping")
    if not isinstance(control_zones, dict):
        die(f"{topology_path} platform_control_zones must be a mapping")
    if not isinstance(realms, dict):
        die(f"{topology_path} platform_network_realms must be a mapping")

    for zone_name, zone in zones.items():
        validate_id(zone_name, f"platform_zones.{zone_name}")
        if not isinstance(zone, dict):
            die(f"platform_zones.{zone_name} must be a mapping")
        role = zone.get("role")
        if role not in WORKLOAD_ROLES:
            die(
                f"platform_zones.{zone_name}.role must be one of "
                f"{', '.join(WORKLOAD_ROLES)}"
            )
        for key in ("hostname_suffix", "bridge", "router_interface", "vm_interface"):
            validate_iface(zone.get(key), f"platform_zones.{zone_name}.{key}")
        for key in ("router_ipv4_address", "vm_ipv4_address"):
            validate_ip(zone.get(key), f"platform_zones.{zone_name}.{key}")
        validate_ip(zone.get("cidr"), f"platform_zones.{zone_name}.cidr")

    ops_zone = control_zones.get("ops") or {}
    if not isinstance(ops_zone, dict):
        die("platform_control_zones.ops must be a mapping")
    for key in ("router_interface", "vm_interface"):
        validate_iface(ops_zone.get(key), f"platform_control_zones.ops.{key}")
    for key in ("router_ipv4_address", "vm_ipv4_address"):
        validate_ip(ops_zone.get(key), f"platform_control_zones.ops.{key}")
    validate_ip(ops_zone.get("cidr"), "platform_control_zones.ops.cidr")

    for alias, target in aliases.items():
        validate_id(alias, f"platform_zone_aliases.{alias}")
        validate_id(target, f"platform_zone_aliases.{alias}")
        if target not in zones:
            die(f"platform_zone_aliases.{alias} points to unknown zone {target}")

    for required_realm in ("wan", "lan"):
        if required_realm not in realms:
            die(f"platform_network_realms.{required_realm} is required")
    for realm_name, realm in realms.items():
        validate_id(realm_name, f"platform_network_realms.{realm_name}")
        if not isinstance(realm, dict):
            die(f"platform_network_realms.{realm_name} must be a mapping")
        validate_iface(
            realm.get("router_interface"),
            f"platform_network_realms.{realm_name}.router_interface",
        )
        if realm.get("parent_interface"):
            validate_iface(
                realm.get("parent_interface"),
                f"platform_network_realms.{realm_name}.parent_interface",
            )
        if realm.get("vlan_id") is not None:
            vlan_id = realm.get("vlan_id")
            if not isinstance(vlan_id, int) or vlan_id < 1 or vlan_id > 4094:
                die(f"platform_network_realms.{realm_name}.vlan_id must be 1-4094")
        for key in (
            "router_ipv4_address",
            "cidr",
            "dhcp_start",
            "dhcp_end",
            "ap_management_ipv4_address",
        ):
            if realm.get(key):
                validate_ip(realm.get(key), f"platform_network_realms.{realm_name}.{key}")

    return {
        "zones": zones,
        "air": inventory_vars.get("platform_air", {}),
        "vpn_egress": inventory_vars.get("platform_vpn_egress", {}),
        "control_zones": control_zones,
        "aliases": aliases,
        "realms": realms,
    }


def resolve_zone(value, field, topology):
    if not isinstance(value, str) or not value:
        die(f"{field} must be a Platform zone")
    canonical = topology["aliases"].get(value, value)
    if canonical not in topology["zones"]:
        valid = sorted(set(topology["zones"]) | set(topology["aliases"]))
        die(f"{field} must be one of: {', '.join(valid)}")
    return canonical, topology["zones"][canonical]


def resolve_realm(value, field, topology, require_cidr=False):
    if not isinstance(value, str) or not value:
        die(f"{field} must be a Platform network realm")
    if value not in topology["realms"]:
        valid = sorted(topology["realms"])
        die(f"{field} must be one of: {', '.join(valid)}")
    realm = topology["realms"][value]
    if require_cidr and not realm.get("cidr"):
        die(f"{field} must reference a realm with a cidr")
    return value, realm


def reject_raw_network_fields(value, field):
    if isinstance(value, dict):
        for key, child in value.items():
            child_field = f"{field}.{key}"
            if key in RAW_NETWORK_FIELDS:
                die(
                    f"{child_field} is implementation topology; "
                    "use from_zone/to_zone instead"
                )
            reject_raw_network_fields(child, child_field)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            reject_raw_network_fields(child, f"{field}[{index}]")


def reject_tcb_control_fields(value, field):
    if isinstance(value, dict):
        for key, child in value.items():
            child_field = f"{field}.{key}"
            if key in TCB_CONTROL_FIELDS:
                die(
                    f"{child_field} is TCB-owned control data; "
                    "app manifests may declare resource intent only"
                )
            reject_tcb_control_fields(child, child_field)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            reject_tcb_control_fields(child, f"{field}[{index}]")


def reject_unknown_fields(value, allowed, field):
    if not isinstance(value, dict):
        die(f"{field} must be a mapping")
    unknown = sorted(set(value) - set(allowed))
    if unknown:
        die(
            f"{field} contains unsupported app-manifest field(s): "
            f"{', '.join(unknown)}"
        )


def validate_access_capability(value, field):
    valid = set(ACCESS_CAPABILITIES)
    if value not in valid:
        choices = sorted(valid)
        die(f"{field} must be one of: {', '.join(choices)}")


def validate_access_intent(value, field):
    if value not in ACCESS_POLICY_INTENTS:
        die(f"{field} must be one of: {', '.join(sorted(ACCESS_POLICY_INTENTS))}")


def validate_access_capability_list(values, field):
    if not isinstance(values, list):
        die(f"{field} must be a list")
    normalized = []
    for index, value in enumerate(values):
        validate_access_capability(value, f"{field}[{index}]")
        if value not in normalized:
            normalized.append(value)
    return normalized


def default_box_access():
    enabled = ["overlay"]
    return {
        "available_capabilities": ["overlay"],
        "enabled_capabilities": enabled,
        "prohibited_capabilities": [],
    }


def normalize_box_access(access, field):
    if access is None:
        access = {}
    if not isinstance(access, dict):
        die(f"{field} must be a mapping")
    reject_unknown_fields(access, ACCESS_BOX_FIELDS, field)
    available = validate_access_capability_list(
        access.get("available_capabilities", ["overlay"]),
        f"{field}.available_capabilities",
    )
    prohibited = validate_access_capability_list(
        access.get("prohibited_capabilities", []),
        f"{field}.prohibited_capabilities",
    )
    prohibited_set = set(prohibited)
    available_prohibited = sorted(set(available) & prohibited_set)
    if available_prohibited:
        die(
            f"{field}.available_capabilities includes prohibited capability: "
            f"{', '.join(available_prohibited)}"
        )
    enabled_default = ["overlay"] if "overlay" in available else []
    enabled = validate_access_capability_list(
        access.get("enabled_capabilities", enabled_default),
        f"{field}.enabled_capabilities",
    )
    for capability in enabled:
        if capability not in available:
            die(f"{field}.enabled_capabilities contains unavailable capability: {capability}")
        if capability in prohibited_set:
            die(f"{field}.enabled_capabilities contains prohibited capability: {capability}")

    return {
        "available_capabilities": available,
        "enabled_capabilities": enabled,
        "prohibited_capabilities": prohibited,
    }


def validate_resource_access(resource, field):
    required_capability = resource.get("requires_capability")
    if required_capability is not None:
        validate_access_capability(required_capability, f"{field}.requires_capability")
    access = resource.get("access")
    if access is None:
        return
    if not isinstance(access, dict):
        die(f"{field}.access must be a mapping")
    reject_unknown_fields(access, ACCESS_RESOURCE_FIELDS, f"{field}.access")
    intent = access.get("intent")
    capability = access.get("capability")
    validate_access_intent(intent, f"{field}.access.intent")
    validate_access_capability(capability, f"{field}.access.capability")


def default_box_config():
    return {
        "dom0_bridge_ports": {},
        "access": default_box_access(),
        "dhcp_reservations": [],
        "shared_guests": {
            role: {"runtime_state": "running"}
            for role in SHARED_GUEST_ROLES
        },
    }


def box_config_for(box_configs, box):
    return (box_configs or {}).get(box) or default_box_config()


def resource_access_allowed(resource, box_config):
    access = resource.get("access") or {}
    capability = resource.get("requires_capability") or access.get("capability")
    if not capability:
        return True
    box_access = (box_config or default_box_config()).get("access") or default_box_access()
    return capability in set(box_access.get("enabled_capabilities") or [])


def resource_access_allowed_for_any_box(resource, box_configs, boxes):
    if not resource.get("access"):
        return True
    selected_boxes = boxes or [""]
    return any(
        resource_access_allowed(resource, box_config_for(box_configs, box))
        for box in selected_boxes
    )


def app_manifest(app_name, *, repo_root):
    validate_app_id(app_name)
    manifest_path = repo_root / "apps" / app_name / "platform-resources.yml"
    if not manifest_path.exists():
        die(f"missing platform resource manifest: {manifest_path}")

    manifest = load_yaml(manifest_path)
    if manifest.get("schema_version") != 1:
        die(f"{manifest_path} must use schema_version: 1")
    if manifest.get("app") != app_name:
        die(f"{manifest_path} app field must equal {app_name}")
    resources = manifest.get("resources") or {}
    if not isinstance(resources, dict):
        die(f"{manifest_path} resources must be a mapping")
    manifest["_manifest_path"] = str(manifest_path)
    return manifest


def entry_placement(entry):
    placement = entry.get("placement") or {}
    if not isinstance(placement, dict):
        die("app placement must be a mapping")
    merged = dict(placement)
    for field in ("active_master", "passive_backup", "builder_box", "boxes"):
        if field in entry and field not in merged:
            merged[field] = entry[field]
    return merged


def manifest_placement_mode(manifest):
    mode = manifest.get("placement_mode") or "active_passive"
    if mode not in ("active_passive", "single_box", "multi_box"):
        die(
            f"{manifest['_manifest_path']} placement_mode must be "
            "active_passive, single_box, or multi_box"
        )
    return mode


def selected_boxes(app_name, manifest, entry):
    enabled = bool(entry.get("enabled"))
    placement = entry_placement(entry)
    placement_mode = manifest_placement_mode(manifest)
    per_user_vm_app = bool(per_user_app_vm_resources(manifest))
    boxes = []
    placement_boxes = placement.get("boxes") or []
    if placement_boxes:
        if not isinstance(placement_boxes, list):
            die(f"apps.{app_name}.placement.boxes must be a list")
        for index, value in enumerate(placement_boxes):
            validate_box(value, f"apps.{app_name}.placement.boxes[{index}]")
            boxes.append(value)

    for field in ("active_master", "passive_backup", "builder_box"):
        value = placement.get(field)
        if value:
            validate_box(value, f"apps.{app_name}.placement.{field}")
            boxes.append(value)
    if enabled and app_name != "bootstrap-iso-debian":
        if placement_mode == "multi_box":
            if not placement_boxes:
                die(f"apps.{app_name}.placement.boxes is required when enabled")
            if placement.get("active_master") or placement.get("passive_backup"):
                die(
                    f"apps.{app_name}.placement active_master/passive_backup "
                    "must be empty for multi_box apps"
                )
        else:
            required_fields = (
                ("active_master",)
                if per_user_vm_app or placement_mode == "single_box"
                else ("active_master", "passive_backup")
            )
            for field in required_fields:
                if not placement.get(field):
                    die(f"apps.{app_name}.placement.{field} is required when enabled")
        if placement_mode == "single_box" and placement.get("passive_backup"):
            die(f"apps.{app_name}.placement.passive_backup must be empty for single_box apps")
    if enabled and app_name == "bootstrap-iso-debian" and not placement.get("builder_box"):
        die("apps.bootstrap-iso-debian.placement.builder_box is required when enabled")
    if len(set(boxes)) != len(boxes):
        die(f"apps.{app_name} placement boxes must be unique")
    return sorted(set(boxes))


def app_runtime_state(app_name, entry, manifest):
    enabled = bool(entry.get("enabled"))
    value = entry.get("runtime_state", "running")
    if value in (None, ""):
        value = "running"
    if value not in ("running", "stopped"):
        die(f"apps.{app_name}.runtime_state must be running or stopped")
    if not enabled:
        return "stopped"
    return value


def registry_box_configs(registry, topology):
    boxes = registry.get("boxes") or {}
    if not isinstance(boxes, dict):
        die("boxes must be a mapping")

    valid_bridge_keys = set(topology["zones"]) | set(topology["aliases"]) | set(topology["realms"])
    normalized = {}
    for box, config in boxes.items():
        validate_box(box, f"boxes.{box}")
        if config is None:
            config = {}
        if not isinstance(config, dict):
            die(f"boxes.{box} must be a mapping")
        unknown = sorted(set(config) - BOX_CONFIG_FIELDS)
        if unknown:
            die(
                f"boxes.{box} contains unsupported field(s): "
                f"{', '.join(unknown)}"
            )
        dom0_bridge_ports = config.get("dom0_bridge_ports") or {}
        if not isinstance(dom0_bridge_ports, dict):
            die(f"boxes.{box}.dom0_bridge_ports must be a mapping")
        normalized_ports = {}
        for bridge_key, ports in dom0_bridge_ports.items():
            if bridge_key not in valid_bridge_keys:
                die(
                    f"boxes.{box}.dom0_bridge_ports.{bridge_key} must be one of: "
                    f"{', '.join(sorted(valid_bridge_keys))}"
                )
            canonical = topology["aliases"].get(bridge_key, bridge_key)
            if not isinstance(ports, list) or not ports:
                die(f"boxes.{box}.dom0_bridge_ports.{bridge_key} must be a non-empty list")
            normalized_ports.setdefault(canonical, [])
            for index, port in enumerate(ports):
                validate_iface(port, f"boxes.{box}.dom0_bridge_ports.{bridge_key}[{index}]")
                if port not in normalized_ports[canonical]:
                    normalized_ports[canonical].append(port)
        dhcp_reservations = config.get("dhcp_reservations") or []
        if not isinstance(dhcp_reservations, list):
            die(f"boxes.{box}.dhcp_reservations must be a list")
        normalized_reservations = []
        seen_reservation_keys = set()
        for index, reservation in enumerate(dhcp_reservations):
            field = f"boxes.{box}.dhcp_reservations[{index}]"
            if not isinstance(reservation, dict):
                die(f"{field} must be a mapping")
            hostname = reservation.get("hostname")
            mac = reservation.get("mac")
            ipv4_address = reservation.get("ipv4_address")
            validate_hostname(hostname, f"{field}.hostname")
            validate_mac(mac, f"{field}.mac")
            validate_ip(ipv4_address, f"{field}.ipv4_address")
            normalized_mac = mac.lower()
            for key_name, key_value in (
                ("hostname", hostname),
                ("mac", normalized_mac),
                ("ipv4_address", ipv4_address),
            ):
                key = (key_name, key_value)
                if key in seen_reservation_keys:
                    die(f"{field}.{key_name} duplicates another reservation on {box}")
                seen_reservation_keys.add(key)
            normalized_reservations.append(
                {
                    "hostname": hostname,
                    "mac": normalized_mac,
                    "ipv4_address": ipv4_address,
                }
            )
        shared_guests = config.get("shared_guests") or {}
        if not isinstance(shared_guests, dict):
            die(f"boxes.{box}.shared_guests must be a mapping")
        unknown_roles = sorted(set(shared_guests) - set(SHARED_GUEST_ROLES))
        if unknown_roles:
            die(
                f"boxes.{box}.shared_guests contains unsupported role(s): "
                f"{', '.join(unknown_roles)}"
            )
        normalized_shared_guests = {}
        for role in SHARED_GUEST_ROLES:
            guest = shared_guests.get(role) or {}
            field = f"boxes.{box}.shared_guests.{role}"
            if not isinstance(guest, dict):
                die(f"{field} must be a mapping")
            unknown_guest_fields = sorted(set(guest) - SHARED_GUEST_FIELDS)
            if unknown_guest_fields:
                die(
                    f"{field} contains unsupported field(s): "
                    f"{', '.join(unknown_guest_fields)}"
                )
            runtime_state = guest.get("runtime_state", "running")
            if runtime_state not in SHARED_GUEST_STATES:
                die(
                    f"{field}.runtime_state must be one of: "
                    f"{', '.join(SHARED_GUEST_STATES)}"
                )
            normalized_shared_guests[role] = {"runtime_state": runtime_state}
        normalized[box] = {
            "dom0_bridge_ports": normalized_ports,
            "access": normalize_box_access(config.get("access"), f"boxes.{box}.access"),
            "dhcp_reservations": normalized_reservations,
            "shared_guests": normalized_shared_guests,
            **({"vpn_egress": config["vpn_egress"]} if "vpn_egress" in config else {}),
        }
    return normalized


def running_podman_workload_zones(manifest, topology):
    compute = ((manifest.get("resources") or {}).get("compute") or [])
    return {
        topology["aliases"].get(
            str(resource.get("zone") or ""), str(resource.get("zone") or "")
        )
        for resource in compute
        if isinstance(resource, dict) and resource.get("type") == "podman_workload"
    }


def validate_shared_guest_app_compatibility(
    app_name, manifest, entry, app_boxes, box_configs, topology
):
    if not entry.get("enabled") or app_runtime_state(app_name, entry, manifest) != "running":
        return
    workload_zones = running_podman_workload_zones(manifest, topology)
    for box in app_boxes:
        shared_guests = box_config_for(box_configs, box).get("shared_guests") or {}
        for role in sorted(workload_zones & set(SHARED_GUEST_ROLES)):
            state = (shared_guests.get(role) or {}).get("runtime_state", "running")
            if state == "stopped":
                die(
                    f"apps.{app_name} is running on {box} but "
                    f"boxes.{box}.shared_guests.{role}.runtime_state is stopped"
                )


def resource_comment(app_name, resource_id, suffix):
    base = f"app-{app_name}-{resource_id}"
    return f"{base}-{suffix}" if suffix else base


def resource_enabled(resource, entry):
    resource_flags = entry.get("resources") or {}
    if not isinstance(resource_flags, dict):
        die("app resources must be a mapping")
    resource_id = resource.get("id")
    return bool(
        resource.get("required")
        or resource.get("enabled_by_default")
        or resource_flags.get(resource_id)
    )


def resource_selected_for_box(resource, entry, box_config):
    return resource_enabled(resource, entry) and resource_access_allowed(resource, box_config)


def selected_network_resources(manifest, entry, box_config=None):
    network = (manifest.get("resources") or {}).get("network") or []
    if not isinstance(network, list):
        die(f"{manifest['_manifest_path']} resources.network must be a list")
    selected = []
    for resource in network:
        if not isinstance(resource, dict):
            die(f"{manifest['_manifest_path']} network resources must be mappings")
        resource_id = resource.get("id")
        validate_id(resource_id, f"{manifest['app']}.network.id")
        if resource_selected_for_box(resource, entry, box_config or default_box_config()):
            selected.append(resource)
    return selected


def validate_compute_resource_shape(app_name, resource, topology):
    resource_id = resource.get("id")
    validate_id(resource_id, f"{app_name}.compute.id")
    resource_type = resource.get("type")
    allowed = COMPUTE_FIELDS_BY_TYPE.get(resource_type)
    if not allowed:
        die(f"unsupported compute resource type for {app_name}.{resource_id}: {resource_type}")
    reject_unknown_fields(resource, allowed, f"{app_name}.{resource_id}")
    validate_resource_access(resource, f"{app_name}.{resource_id}")
    if resource.get("zone"):
        zone_name, zone = resolve_zone(
            resource.get("zone"),
            f"{app_name}.{resource_id}.zone",
            topology,
        )
    else:
        zone_name, zone = "", {}
    if resource_type == "managed_iot_device":
        if not resource.get("zone"):
            die(f"{app_name}.{resource_id}.zone is required for managed_iot_device")
        if zone_name != "iot":
            die(f"{app_name}.{resource_id}.zone must be iot for managed_iot_device")
        if resource.get("hostname_suffix"):
            validate_id(
                resource.get("hostname_suffix"),
                f"{app_name}.{resource_id}.hostname_suffix",
            )
        if resource.get("hostname_default"):
            validate_hostname(
                resource.get("hostname_default"),
                f"{app_name}.{resource_id}.hostname_default",
            )
        if resource.get("management_tag_default"):
            validate_app_tailnet_tag(
                resource.get("management_tag_default"),
                f"{app_name}.{resource_id}.management_tag_default",
            )
    if resource_type == "per_user_app_vm":
        validate_id(
            resource.get("vm_name_prefix", "usr"),
            f"{app_name}.{resource_id}.vm_name_prefix",
        )
        validate_id(
            resource.get("tailnet_tag_prefix", app_name),
            f"{app_name}.{resource_id}.tailnet_tag_prefix",
        )
    if resource_type == "app_vm":
        if not resource.get("zone"):
            die(f"{app_name}.{resource_id}.zone is required for app_vm")
        if resource.get("hostname_suffix"):
            validate_id(
                resource.get("hostname_suffix"),
                f"{app_name}.{resource_id}.hostname_suffix",
            )
        if resource.get("tailnet_tag_default"):
            validate_app_tailnet_tag(
                resource.get("tailnet_tag_default"),
                f"{app_name}.{resource_id}.tailnet_tag_default",
            )
        guest_os = str(resource.get("guest_os") or "alpine")
        if guest_os != "alpine":
            die(f"{app_name}.{resource_id}.guest_os must be alpine for app_vm")
        container_runtime = str(resource.get("container_runtime") or "none")
        if container_runtime != "none":
            die(f"{app_name}.{resource_id}.container_runtime must be none for app_vm")


def validate_network_resource_shape(app_name, resource, topology):
    resource_id = resource.get("id")
    validate_id(resource_id, f"{app_name}.network.id")
    reject_raw_network_fields(resource, f"{app_name}.{resource_id}")
    resource_type = resource.get("type")
    allowed = NETWORK_FIELDS_BY_TYPE.get(resource_type)
    if not allowed:
        die(f"unsupported network resource type for {app_name}.{resource_id}: {resource_type}")
    reject_unknown_fields(resource, allowed, f"{app_name}.{resource_id}")
    validate_resource_access(resource, f"{app_name}.{resource_id}")

    if resource_type == "interzone_tcp":
        from_zone, _ = resolve_zone(
            resource.get("from_zone"),
            f"{app_name}.{resource_id}.from_zone",
            topology,
        )
        to_zone, _ = resolve_zone(
            resource.get("to_zone"),
            f"{app_name}.{resource_id}.to_zone",
            topology,
        )
        if from_zone == to_zone:
            die(f"{app_name}.{resource_id} interzone_tcp requires distinct zones")
        if resource.get("ports_from_user"):
            validate_user_field(resource.get("ports_from_user"), f"{app_name}.{resource_id}.ports_from_user")
        else:
            validate_ports(
                resource.get("ports", [resource.get("port")]),
                f"{app_name}.{resource_id}.ports",
            )
        return

    if resource_type == "realm_to_zone_tcp":
        resolve_realm(
            resource.get("from_realm"),
            f"{app_name}.{resource_id}.from_realm",
            topology,
            require_cidr=True,
        )
        resolve_zone(
            resource.get("to_zone"),
            f"{app_name}.{resource_id}.to_zone",
            topology,
        )
        validate_ports(
            resource.get("ports", [resource.get("port")]),
            f"{app_name}.{resource_id}.ports",
        )
        return

    if resource_type == "wan_egress":
        resolve_zone(
            resource.get("from_zone"),
            f"{app_name}.{resource_id}.from_zone",
            topology,
        )
        has_ports = False
        for key in ("tcp_ports", "udp_ports"):
            if resource.get(key):
                validate_ports(resource.get(key), f"{app_name}.{resource_id}.{key}")
                has_ports = True
        if not has_ports:
            die(f"{app_name}.{resource_id} wan_egress requires tcp_ports or udp_ports")
        return

    if resource_type == "zone_to_device_tcp":
        resolve_zone(
            resource.get("from_zone"),
            f"{app_name}.{resource_id}.from_zone",
            topology,
        )
        validate_id(resource.get("device"), f"{app_name}.{resource_id}.device")
        validate_ports(
            resource.get("ports", [resource.get("port")]),
            f"{app_name}.{resource_id}.ports",
        )
        return

    if resource_type == "device_to_zone_tcp":
        validate_id(resource.get("device"), f"{app_name}.{resource_id}.device")
        resolve_zone(
            resource.get("to_zone"),
            f"{app_name}.{resource_id}.to_zone",
            topology,
        )
        validate_ports(
            resource.get("ports", [resource.get("port")]),
            f"{app_name}.{resource_id}.ports",
        )
        return

    if resource_type == "device_wan_egress":
        validate_id(resource.get("device"), f"{app_name}.{resource_id}.device")
        has_ports = False
        for key in ("tcp_ports", "udp_ports"):
            if resource.get(key):
                validate_ports(resource.get(key), f"{app_name}.{resource_id}.{key}")
                has_ports = True
        if not has_ports:
            die(f"{app_name}.{resource_id} device_wan_egress requires tcp_ports or udp_ports")
        return

    if resource_type == "tailnet_tcp":
        return


def validate_tailnet_resource_shape(app_name, resource):
    resource_id = resource.get("id")
    validate_id(resource_id, f"{app_name}.tailnet.id")
    reject_unknown_fields(resource, TAILNET_RESOURCE_FIELDS, f"{app_name}.{resource_id}")
    validate_resource_access(resource, f"{app_name}.{resource_id}")
    tag = resource.get("tag_default") or resource.get("tag") or resource.get("tag_pattern")
    if tag and "{" not in tag:
        validate_app_tailnet_tag(tag, f"{app_name}.{resource_id}.tag")
    grants = resource.get("grants") or []
    if not isinstance(grants, list):
        die(f"{app_name}.{resource_id}.grants must be a list")
    for index, grant in enumerate(grants):
        reject_unknown_fields(
            grant,
            TAILNET_GRANT_FIELDS,
            f"{app_name}.{resource_id}.grants[{index}]",
        )
        if "ports" in grant:
            validate_ports(grant.get("ports"), f"{app_name}.{resource_id}.grants[{index}].ports")
        if "tcp_ports" in grant:
            validate_ports(
                grant.get("tcp_ports"),
                f"{app_name}.{resource_id}.grants[{index}].tcp_ports",
            )
        if "udp_ports" in grant:
            validate_tailnet_port_selectors(
                grant.get("udp_ports"),
                f"{app_name}.{resource_id}.grants[{index}].udp_ports",
            )


def validate_artifact_resource_shape(app_name, resource):
    resource_id = resource.get("id")
    validate_id(resource_id, f"{app_name}.artifacts.id")
    resource_type = resource.get("type")
    allowed = ARTIFACT_FIELDS_BY_TYPE.get(resource_type)
    if not allowed:
        die(f"unsupported artifact resource type for {app_name}.{resource_id}: {resource_type}")
    reject_unknown_fields(resource, allowed, f"{app_name}.{resource_id}")


def validate_manifest_resources(app_name, manifest, topology):
    validate_app_id(app_name)
    if "app" in manifest:
        validate_app_id(manifest["app"])
    reject_unknown_fields(manifest, MANIFEST_TOP_LEVEL_FIELDS, app_name)
    features = manifest.get("features") or []
    if not isinstance(features, list):
        die(f"{manifest['_manifest_path']} features must be a list")
    feature_ids = set()
    raw_resources = manifest.get("resources") or {}
    resource_sections = raw_resources.values() if isinstance(raw_resources, dict) else []
    all_resource_ids = {
        resource.get("id")
        for section in resource_sections
        if isinstance(section, list)
        for resource in section
        if isinstance(resource, dict)
    }
    for index, feature in enumerate(features):
        field = f"{app_name}.features[{index}]"
        reject_unknown_fields(feature, FEATURE_FIELDS, field)
        feature_id = feature.get("id")
        validate_id(feature_id, f"{field}.id")
        if feature_id in feature_ids:
            die(f"{app_name}.features contains duplicate id: {feature_id}")
        feature_ids.add(feature_id)
        feature_type = feature.get("type")
        if feature_type not in FEATURE_TYPES:
            die(f"{field}.type must be one of: {', '.join(sorted(FEATURE_TYPES))}")
        values = feature.get("values") or []
        if feature_type == "enum" and (not isinstance(values, list) or not values):
            die(f"{field}.values must be a non-empty list")
        bindings = feature.get("resource_bindings") or {}
        if not isinstance(bindings, dict):
            die(f"{field}.resource_bindings must be a mapping")
        allowed_values = set(values) if feature_type == "enum" else {"true"}
        unknown_values = sorted(set(bindings) - allowed_values)
        if unknown_values:
            die(f"{field}.resource_bindings contains unsupported value(s): {', '.join(unknown_values)}")
        for value, resource_ids in bindings.items():
            if not isinstance(resource_ids, list) or not resource_ids:
                die(f"{field}.resource_bindings.{value} must be a non-empty list")
            for resource_id in resource_ids:
                if resource_id not in all_resource_ids:
                    die(f"{field}.resource_bindings.{value} references unknown resource: {resource_id}")
    datasets = manifest.get("datasets") or []
    if not isinstance(datasets, list):
        die(f"{manifest['_manifest_path']} datasets must be a list")
    dataset_ids = set()
    for index, dataset in enumerate(datasets):
        field = f"{app_name}.datasets[{index}]"
        reject_unknown_fields(dataset, DATASET_FIELDS, field)
        reject_tcb_control_fields(dataset, field)
        dataset_id = dataset.get("id")
        validate_id(dataset_id, f"{field}.id")
        if dataset_id in dataset_ids:
            die(f"{app_name}.datasets contains duplicate id: {dataset_id}")
        dataset_ids.add(dataset_id)
        if dataset.get("type") not in DATASET_TYPES:
            die(f"{field}.type must be one of: {', '.join(sorted(DATASET_TYPES))}")
        rationale = dataset.get("rationale")
        if not isinstance(rationale, str) or not rationale.strip():
            die(f"{field}.rationale must be a non-empty string")
    resources = manifest.get("resources") or {}
    if not isinstance(resources, dict):
        die(f"{manifest['_manifest_path']} resources must be a mapping")
    reject_tcb_control_fields(resources, f"{app_name}.resources")
    reject_unknown_fields(resources, RESOURCE_SECTION_FIELDS, f"{app_name}.resources")

    compute = resources.get("compute") or []
    if not isinstance(compute, list):
        die(f"{manifest['_manifest_path']} resources.compute must be a list")
    for resource in compute:
        if not isinstance(resource, dict):
            die(f"{manifest['_manifest_path']} compute resources must be mappings")
        validate_compute_resource_shape(app_name, resource, topology)

    has_usr_vm = any(
        isinstance(resource, dict)
        and resource.get("type") in ("per_user_app_vm", "app_vm")
        and resource.get("zone") == "usr"
        for resource in compute
    )
    for resource in compute:
        if (isinstance(resource, dict) and resource.get("zone") == "usr"
                and resource.get("type") in ("podman_workload", "host_service", "ephemeral_privileged_builder")
                and not has_usr_vm):
            die(f"{app_name}.{resource.get('id')} cannot use the retired shared usr VM; declare a dedicated VM")
    network = resources.get("network") or []
    if not isinstance(network, list):
        die(f"{manifest['_manifest_path']} resources.network must be a list")
    for resource in network:
        if not isinstance(resource, dict):
            die(f"{manifest['_manifest_path']} network resources must be mappings")
        validate_network_resource_shape(app_name, resource, topology)
    managed_device_ids = {
        resource["id"]
        for resource in compute
        if isinstance(resource, dict) and resource.get("type") == "managed_iot_device"
    }
    for resource in network:
        if resource.get("type") in (
            "zone_to_device_tcp",
            "device_to_zone_tcp",
            "device_wan_egress",
        ):
            device_id = resource.get("device")
            if device_id not in managed_device_ids:
                die(
                    f"{app_name}.{resource['id']}.device must reference a "
                    "managed_iot_device compute resource"
                )

    tailnet = resources.get("tailnet") or []
    if not isinstance(tailnet, list):
        die(f"{manifest['_manifest_path']} resources.tailnet must be a list")
    for resource in tailnet:
        if not isinstance(resource, dict):
            die(f"{manifest['_manifest_path']} tailnet resources must be mappings")
        validate_tailnet_resource_shape(app_name, resource)

    artifacts = resources.get("artifacts") or []
    if not isinstance(artifacts, list):
        die(f"{manifest['_manifest_path']} resources.artifacts must be a list")
    for resource in artifacts:
        if not isinstance(resource, dict):
            die(f"{manifest['_manifest_path']} artifact resources must be mappings")
        validate_artifact_resource_shape(app_name, resource)


def validate_selected_resource_capabilities(app_name, manifest, entry, boxes, box_configs):
    if not bool(entry.get("enabled")):
        return
    resource_flags = entry.get("resources") or {}
    if not isinstance(resource_flags, dict):
        die(f"apps.{app_name}.resources must be a mapping")
    resources = manifest.get("resources") or {}
    all_resources = []
    resource_ids = set()
    for section in ("compute", "network", "tailnet", "artifacts"):
        for resource in resources.get(section) or []:
            if isinstance(resource, dict):
                all_resources.append(resource)
                resource_ids.add(resource.get("id"))
    unknown = sorted(set(resource_flags) - resource_ids)
    if unknown:
        die(f"apps.{app_name}.resources contains unknown resource(s): {', '.join(unknown)}")
    for resource_id, selected in resource_flags.items():
        if not isinstance(selected, bool):
            die(f"apps.{app_name}.resources.{resource_id} must be Boolean")
    for resource in all_resources:
        if not resource_enabled(resource, entry):
            continue
        capability = resource.get("requires_capability") or (resource.get("access") or {}).get("capability")
        if not capability:
            continue
        for box in boxes:
            if not resource_access_allowed(resource, box_config_for(box_configs, box)):
                die(
                    f"apps.{app_name} resource {resource['id']} requires enabled "
                    f"capability {capability} on placement box {box}"
                )


def selected_tailnet_resources(manifest, entry):
    tailnet = (manifest.get("resources") or {}).get("tailnet") or []
    if not isinstance(tailnet, list):
        die(f"{manifest['_manifest_path']} resources.tailnet must be a list")
    selected = []
    for resource in tailnet:
        if not isinstance(resource, dict):
            die(f"{manifest['_manifest_path']} tailnet resources must be mappings")
        resource_id = resource.get("id")
        validate_id(resource_id, f"{manifest['app']}.tailnet.id")
        tag = resource.get("tag_default") or resource.get("tag") or resource.get("tag_pattern")
        if tag and "{" not in tag:
            validate_app_tailnet_tag(tag, f"{manifest['app']}.{resource_id}.tag")
        if resource_enabled(resource, entry):
            selected.append(resource)
    return selected


def tailnet_resource_value(resource, *keys):
    for key in keys:
        value = resource.get(key)
        if value:
            return value
    return ""


def render_tailnet_resource_value(value, box):
    if not isinstance(value, str) or "{box}" not in value or "{slug}" in value:
        return value
    return value.replace("{box}", box)


def per_user_app_vm_resources(manifest):
    compute = (manifest.get("resources") or {}).get("compute") or []
    return [
        resource
        for resource in compute
        if isinstance(resource, dict) and resource.get("type") == "per_user_app_vm"
    ]


def app_vm_resources(manifest):
    compute = (manifest.get("resources") or {}).get("compute") or []
    return [
        resource
        for resource in compute
        if isinstance(resource, dict) and resource.get("type") == "app_vm"
    ]


def managed_iot_device_resources(manifest):
    compute = (manifest.get("resources") or {}).get("compute") or []
    return [
        resource
        for resource in compute
        if isinstance(resource, dict) and resource.get("type") == "managed_iot_device"
    ]


def registry_managed_devices(entry):
    devices = entry.get("devices") or {}
    if not isinstance(devices, dict):
        die("app devices must be a mapping")
    return devices


def user_fields_referenced_by_network(manifest, entry):
    fields = set()
    for resource in selected_network_resources(manifest, entry):
        source_field = resource.get("ports_from_user")
        if source_field:
            validate_user_field(
                source_field,
                f"{manifest['app']}.{resource['id']}.ports_from_user",
            )
            fields.add(source_field)
    return fields


def selected_users(app_name, manifest, entry, topology):
    if not bool(entry.get("enabled")):
        return []
    if not per_user_app_vm_resources(manifest):
        return []

    users = entry.get("users")
    if not isinstance(users, list) or not users:
        die(f"apps.{app_name}.users must be a non-empty list")

    usr_zone = topology["zones"].get("usr")
    if not usr_zone:
        die("platform_zones.usr is required for per-user app VMs")
    reserved_ips = {
        str(ipaddress.ip_address(usr_zone["router_ipv4_address"])),
        str(ipaddress.ip_address(usr_zone["vm_ipv4_address"])),
        *usr_zone.get("reserved_ipv4_addresses", []),
    }

    normalized = []
    seen_slugs = set()
    seen_logins = set()
    seen_system_users = set()
    seen_ips = set()
    seen_storage_ports = set()
    required_fields = user_fields_referenced_by_network(manifest, entry)
    for index, user in enumerate(users):
        if not isinstance(user, dict):
            die(f"apps.{app_name}.users[{index}] must be a mapping")
        slug = user.get("slug")
        login = user.get("tailscale_login")
        system_user = user.get("system_user") or slug
        vm_ip = user.get("vm_ipv4_address")
        storage_port = user.get("storage_port")
        validate_slug(slug, f"apps.{app_name}.users[{index}].slug")
        validate_login(login, f"apps.{app_name}.users[{index}].tailscale_login")
        validate_system_user(system_user, f"apps.{app_name}.users[{index}].system_user")
        validate_ip_in_cidr(
            vm_ip,
            usr_zone["cidr"],
            f"apps.{app_name}.users[{index}].vm_ipv4_address",
        )
        if "storage_port" in required_fields or storage_port is not None:
            storage_ports = validate_ports(
                [storage_port],
                f"apps.{app_name}.users[{index}].storage_port",
            )
            storage_port = storage_ports[0]
        if slug in seen_slugs:
            die(f"apps.{app_name}.users duplicate slug: {slug}")
        if login in seen_logins:
            die(f"apps.{app_name}.users duplicate tailscale_login: {login}")
        if system_user in seen_system_users:
            die(f"apps.{app_name}.users duplicate system_user: {system_user}")
        if vm_ip in reserved_ips:
            die(f"apps.{app_name}.users[{index}].vm_ipv4_address is reserved: {vm_ip}")
        if vm_ip in seen_ips:
            die(f"apps.{app_name}.users duplicate vm_ipv4_address: {vm_ip}")
        if storage_port is not None and storage_port in seen_storage_ports:
            die(f"apps.{app_name}.users duplicate storage_port: {storage_port}")
        seen_slugs.add(slug)
        seen_logins.add(login)
        seen_system_users.add(system_user)
        seen_ips.add(vm_ip)
        if storage_port is not None:
            seen_storage_ports.add(storage_port)
        normalized_user = {
            "slug": slug,
            "tailscale_login": login,
            "system_user": system_user,
            "vm_ipv4_address": vm_ip,
        }
        if storage_port is not None:
            normalized_user["storage_port"] = storage_port
        normalized.append(normalized_user)
    return normalized


def registry_app_vms(entry):
    app_vms = entry.get("app_vms") or {}
    if not isinstance(app_vms, dict):
        die("app app_vms must be a mapping")
    return app_vms


def validate_privileged_runtime(app_name, manifest, entry):
    if not bool(entry.get("enabled")):
        return
    compute = (manifest.get("resources") or {}).get("compute") or []
    if not isinstance(compute, list):
        die(f"{manifest['_manifest_path']} resources.compute must be a list")
    privileged = []
    for resource in compute:
        if not isinstance(resource, dict):
            die(f"{manifest['_manifest_path']} compute resources must be mappings")
        resource_id = resource.get("id")
        validate_id(resource_id, f"{app_name}.compute.id")
        privilege = str(resource.get("privilege") or "")
        rtype = str(resource.get("type") or "")
        if privilege in ("privileged", "privileged_rootful_podman") or rtype == "ephemeral_privileged_builder":
            privileged.append(resource_id)
    if not privileged:
        return
    controls = entry.get("ephemeral") or entry.get("controls") or {}
    if not isinstance(controls, dict):
        die(f"apps.{app_name}.ephemeral/controls must be a mapping")
    if not bool(controls.get("privileged_approval")):
        die(f"apps.{app_name} enables privileged resources without privileged_approval")
    expires_at = controls.get("expires_at")
    if not isinstance(expires_at, str) or not expires_at:
        die(f"apps.{app_name} privileged resources require expires_at")
    normalized = expires_at.replace("Z", "+00:00")
    try:
        expires = datetime.fromisoformat(normalized)
    except ValueError:
        die(f"apps.{app_name}.expires_at must be ISO-8601 UTC")
    if expires.tzinfo is None:
        die(f"apps.{app_name}.expires_at must include timezone")
    if expires.astimezone(timezone.utc) <= datetime.now(timezone.utc):
        die(f"apps.{app_name} privileged resource approval expired at {expires_at}")
