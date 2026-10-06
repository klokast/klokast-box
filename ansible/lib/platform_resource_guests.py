"""Compile application guests and managed devices from validated declarations."""
import hashlib
import ipaddress
import json
import re

import platform_resource_model as model


def selected_managed_iot_devices(app_name, manifest, entry, boxes, topology, box_configs=None):
    devices_config = model.registry_managed_devices(entry)
    devices = []
    for resource in model.managed_iot_device_resources(manifest):
        resource_id = resource["id"]
        zone_name, zone = model.resolve_zone(
            resource.get("zone"),
            f"{app_name}.{resource_id}.zone",
            topology,
        )
        by_box = devices_config.get(resource_id) or {}
        if not isinstance(by_box, dict):
            model.die(f"apps.{app_name}.devices.{resource_id} must be a mapping by box")
        for box in boxes:
            if not model.resource_access_allowed(resource, model.box_config_for(box_configs, box)):
                continue
            config = by_box.get(box) or {}
            if not isinstance(config, dict):
                model.die(f"apps.{app_name}.devices.{resource_id}.{box} must be a mapping")
            mac = config.get("mac")
            if not mac:
                model.die(f"apps.{app_name}.devices.{resource_id}.{box}.mac is required")
            model.validate_mac(mac, f"apps.{app_name}.devices.{resource_id}.{box}.mac")
            ipv4_address = config.get("ipv4_address")
            model.validate_ip_in_cidr(
                ipv4_address,
                zone["cidr"],
                f"apps.{app_name}.devices.{resource_id}.{box}.ipv4_address",
            )
            hostname = (
                config.get("hostname")
                or resource.get("hostname_default")
                or f"{box}-{resource.get('hostname_suffix', resource_id)}"
            )
            model.validate_hostname(hostname, f"apps.{app_name}.devices.{resource_id}.{box}.hostname")
            tag = config.get("tailnet_tag") or resource.get("management_tag_default") or "tag:iot"
            model.validate_app_tailnet_tag(tag, f"apps.{app_name}.devices.{resource_id}.{box}.tailnet_tag")
            devices.append(
                {
                    "schema_version": 1,
                    "app": app_name,
                    "resource": resource_id,
                    "node": box,
                    "zone": zone_name,
                    "role": zone["role"],
                    "hostname": hostname,
                    "mac": mac.lower(),
                    "ipv4_address": ipv4_address,
                    "tailnet_tag": tag,
                }
            )
    return devices


def app_vm_lv_fragment(value):
    return re.sub(r"[^a-z0-9_]+", "_", value.replace("-", "_")).strip("_")


def compile_alpine_app_vm_guest_spec(app_name, box, resource, config, zone, runtime_state="running"):
    resource_id = resource["id"]
    guest_name = resource.get("hostname_suffix") or resource_id
    model.validate_id(guest_name, f"{app_name}.{resource_id}.guest_name")
    hostname = config["hostname"]
    vm_ip = config["vm_ipv4_address"]
    lv_name = f"lv_{app_vm_lv_fragment(app_name)}_{app_vm_lv_fragment(guest_name)}"
    digest = hashlib.sha1(f"{box}:{app_name}:{resource_id}".encode("utf-8")).hexdigest()
    mac = f"00:16:3e:{digest[0:2]}:{digest[2:4]}:{digest[4:6]}"
    network_interfaces = (
        "auto lo\n"
        "iface lo inet loopback\n\n"
        f"auto {zone['vm_interface']}\n"
        f"iface {zone['vm_interface']} inet static\n"
        f"    address {vm_ip}/{zone['router_ipv4_prefix']}\n"
        f"    gateway {zone['router_ipv4_address']}\n"
    )
    return {
        "guest_name": guest_name,
        "guest_os": "alpine",
        "container_runtime": "none",
        "stage": "installed",
        "config_path": f"{model.DOM0_XEN_CONFIG_DIR}/{guest_name}.cfg",
        "autostart": runtime_state == "running",
        "runtime_state": runtime_state,
        "memory_mb": int(resource.get("memory_mb", 1024)),
        "vcpus": int(resource.get("vcpus", 1)),
        "lv_size": str(resource.get("lv_size", "20G")),
        "network_interfaces": network_interfaces,
        "installed": {
            "required_lvs": [f"/dev/vg0/{lv_name}"],
            "kernel_path": f"{model.DOM0_XEN_IMAGES_PATH}/{guest_name}-kernel",
            "initramfs_path": f"{model.DOM0_XEN_IMAGES_PATH}/{guest_name}-initramfs",
            "extra": "console=hvc0 root=/dev/xvda3 rw modules=ext4",
            "required_paths": [
                f"{model.DOM0_XEN_IMAGES_PATH}/{guest_name}-kernel",
                f"{model.DOM0_XEN_IMAGES_PATH}/{guest_name}-initramfs",
            ],
            "disks": [f"phy:/dev/vg0/{lv_name},xvda,w"],
        },
        "vifs": [{"bridge": zone["bridge"], "mac": mac}],
        "hostname": hostname,
        "vm_interface": zone["vm_interface"],
    }


def selected_app_vms(app_name, manifest, entry, boxes, topology, box_configs=None):
    if not bool(entry.get("enabled")):
        return []
    resources = model.app_vm_resources(manifest)
    if not resources:
        return []

    registry = model.registry_app_vms(entry)
    runtime_state = model.app_runtime_state(app_name, entry, manifest)
    specs = []
    seen_ips = set()
    for resource in resources:
        resource_id = resource["id"]
        zone_name, zone = model.resolve_zone(
            resource.get("zone"),
            f"{app_name}.{resource_id}.zone",
            topology,
        )
        reserved_ips = {
            str(ipaddress.ip_address(zone["router_ipv4_address"])),
            str(ipaddress.ip_address(zone["vm_ipv4_address"])),
        }
        dom0_ip = zone.get("dom0_ipv4_address")
        if dom0_ip:
            model.validate_ip(dom0_ip, f"platform_zones.{zone_name}.dom0_ipv4_address")
            reserved_ips.add(str(ipaddress.ip_address(dom0_ip)))
        by_box = registry.get(resource_id) or {}
        if not isinstance(by_box, dict):
            model.die(f"apps.{app_name}.app_vms.{resource_id} must be a mapping by box")
        for box in boxes:
            if not model.resource_access_allowed(resource, model.box_config_for(box_configs, box)):
                continue
            config = by_box.get(box) or {}
            if not isinstance(config, dict):
                model.die(f"apps.{app_name}.app_vms.{resource_id}.{box} must be a mapping")
            vm_ip = config.get("vm_ipv4_address")
            model.validate_ip_in_cidr(
                vm_ip,
                zone["cidr"],
                f"apps.{app_name}.app_vms.{resource_id}.{box}.vm_ipv4_address",
            )
            if vm_ip in reserved_ips:
                model.die(f"apps.{app_name}.app_vms.{resource_id}.{box}.vm_ipv4_address is reserved: {vm_ip}")
            if vm_ip in seen_ips:
                model.die(f"apps.{app_name}.app_vms duplicate vm_ipv4_address: {vm_ip}")
            seen_ips.add(vm_ip)
            hostname_suffix = resource.get("hostname_suffix") or resource_id
            hostname = config.get("hostname") or f"{box}-{hostname_suffix}"
            model.validate_hostname(hostname, f"apps.{app_name}.app_vms.{resource_id}.{box}.hostname")
            if hostname == f"{box}-usr" or hostname_suffix == "usr":
                model.die(f"{app_name}.{resource_id} cannot recreate the retired shared usr VM; use a dedicated VM name")
            tailnet_tag = (
                config.get("tailnet_tag")
                or resource.get("tailnet_tag_default")
                or f"tag:{app_name}"
            )
            model.validate_app_tailnet_tag(
                tailnet_tag,
                f"apps.{app_name}.app_vms.{resource_id}.{box}.tailnet_tag",
            )
            normalized_config = {
                "hostname": hostname,
                "vm_ipv4_address": vm_ip,
            }
            guest_spec = compile_alpine_app_vm_guest_spec(
                app_name,
                box,
                resource,
                normalized_config,
                zone,
                runtime_state=runtime_state,
            )
            specs.append(
                {
                    "node": box,
                    "app": app_name,
                    "resource": resource_id,
                    "user_slug": resource_id,
                    "tailscale_login": "",
                    "site_role": "active",
                    "runtime_state": runtime_state,
                    "inventory_hostname": hostname,
                    "node_domain_role": zone["role"],
                    "node_hostname": hostname,
                    "box_scoped_hostname": hostname,
                    "tailnet_hostname": hostname,
                    "tailnet_tag": tailnet_tag,
                    "advertised_tags": ["tag:vm", tailnet_tag],
                    "zone": zone_name,
                    "vm_ipv4_address": vm_ip,
                    "vm_interface": zone["vm_interface"],
                    "router_ipv4_address": zone["router_ipv4_address"],
                    "router_ipv4_prefix": zone["router_ipv4_prefix"],
                    "guest_spec": guest_spec,
                    "user": {
                        "slug": resource_id,
                        "tailscale_login": "",
                        "system_user": "neo",
                        "vm_ipv4_address": vm_ip,
                    },
                }
            )
    return specs


def app_vm_sources_for_box(app_vm_specs, box):
    return [
        {"slug": spec["resource"], "vm_ipv4_address": spec["vm_ipv4_address"]}
        for spec in app_vm_specs
        if spec["node"] == box
    ]


def user_port_values(resource, user, field):
    source_field = resource.get("ports_from_user")
    if source_field:
        value = user.get(source_field)
        if not isinstance(value, int):
            model.die(f"{field} user field {source_field} must be an integer port")
        return model.validate_ports([value], field)
    return model.validate_ports(resource.get("ports", [resource.get("port")]), field)


def compile_app_vm_guest_spec(app_name, box, user, resource, zone, topology, runtime_state="running"):
    slug = user["slug"]
    vm_prefix = resource.get("vm_name_prefix", "usr")
    guest_name = f"{vm_prefix}-{slug}"
    guest_os = str(resource.get("guest_os") or "debian")
    if guest_os != "debian":
        model.die(f"{app_name}.{resource['id']}.guest_os must be debian for per-user app VMs")
    lv_name = f"lv_{app_name}_{vm_prefix.replace('-', '_')}_{slug.replace('-', '_')}"
    digest = hashlib.sha1(f"{box}:{app_name}:{slug}".encode("utf-8")).hexdigest()
    mac = f"00:16:3e:{digest[0:2]}:{digest[2:4]}:{digest[4:6]}"
    release = str(resource.get("debian_release") or "trixie")
    mirror = str(resource.get("debian_mirror") or "http://deb.debian.org/debian")
    node_hostname = f"{box}-{guest_name}"
    network_interfaces = (
        "auto lo\n"
        "iface lo inet loopback\n\n"
        f"auto {zone['vm_interface']}\n"
        f"iface {zone['vm_interface']} inet static\n"
        f"    address {user['vm_ipv4_address']}/{zone['router_ipv4_prefix']}\n"
        f"    gateway {zone['router_ipv4_address']}\n"
    )
    backend_zone = topology["zones"].get("bak")
    if not backend_zone:
        model.die("platform_zones.bak is required for Debian app VM image building")
    dom0_source = backend_zone.get("dom0_ipv4_address")
    model.validate_ip(dom0_source, "platform_zones.bak.dom0_ipv4_address")
    image_inputs = {
        "schema_version": 1,
        "build_profile_version": model.DEBIAN_APP_VM_BUILD_PROFILE_VERSION,
        "guest_name": guest_name,
        "hostname": node_hostname,
        "release": release,
        "mirror": mirror,
        "security_mirror": model.DEBIAN_APP_VM_SECURITY_MIRROR,
        "architecture": model.DEBIAN_APP_VM_ARCHITECTURE,
        "variant": model.DEBIAN_APP_VM_VARIANT,
        "image_size": str(resource.get("debian_image_size") or model.DEBIAN_APP_VM_IMAGE_SIZE),
        "packages": list(model.DEBIAN_APP_VM_PACKAGES),
        "network_interfaces": network_interfaces,
        "bootstrap_interface": zone["vm_interface"],
        "bootstrap_host": user["vm_ipv4_address"],
        "bootstrap_ssh_sources": [dom0_source],
        "artifact_port": int(
            resource.get("debian_builder_artifact_port")
            or model.DEBIAN_APP_VM_BUILDER_ARTIFACT_PORT
        ),
        "builder_host": f"{box}-bak",
    }
    image_inputs["build_id"] = hashlib.sha256(
        json.dumps(image_inputs, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "guest_name": guest_name,
        "guest_os": guest_os,
        "container_runtime": str(resource.get("container_runtime") or "docker"),
        "debian_release": release,
        "debian_mirror": mirror,
        "debian_image": image_inputs,
        "stage": "installed",
        "config_path": f"{model.DOM0_XEN_CONFIG_DIR}/{guest_name}.cfg",
        "autostart": runtime_state == "running",
        "runtime_state": runtime_state,
        "memory_mb": int(resource.get("memory_mb", 4096)),
        "vcpus": int(resource.get("vcpus", 4)),
        "lv_size": str(resource.get("lv_size", "50G")),
        "installed": {
            "required_lvs": [f"/dev/vg0/{lv_name}"],
            "kernel_path": f"{model.DOM0_XEN_IMAGES_PATH}/{guest_name}-kernel",
            "initramfs_path": f"{model.DOM0_XEN_IMAGES_PATH}/{guest_name}-initramfs",
            "extra": "console=hvc0 root=/dev/xvda rw net.ifnames=0 biosdevname=0",
            "required_paths": [
                f"{model.DOM0_XEN_IMAGES_PATH}/{guest_name}-kernel",
                f"{model.DOM0_XEN_IMAGES_PATH}/{guest_name}-initramfs",
            ],
            "disks": [f"phy:/dev/vg0/{lv_name},xvda,w"],
        },
        "vifs": [{"bridge": zone["bridge"], "mac": mac}],
        "vm_interface": zone["vm_interface"],
    }


def compile_app_vm_specs_for_box(app_name, box, entry, manifest, users, topology):
    placement = model.entry_placement(entry)
    active_master = placement.get("active_master")
    runtime_state = model.app_runtime_state(app_name, entry, manifest)
    specs = []
    for resource in model.per_user_app_vm_resources(manifest):
        zone_name, zone = model.resolve_zone(
            resource.get("zone"),
            f"{app_name}.{resource['id']}.zone",
            topology,
        )
        vm_prefix = resource.get("vm_name_prefix", "usr")
        tag_prefix = resource.get("tailnet_tag_prefix", app_name)
        for user in users:
            slug = user["slug"]
            active = box == active_master
            inventory_hostname = f"{box}-{vm_prefix}-{slug}"
            box_scoped_hostname = f"{box}-{vm_prefix}-{slug}"
            tailnet_hostname = box_scoped_hostname
            tailnet_tag = f"tag:{tag_prefix}-{slug}"
            node_hostname = box_scoped_hostname
            advertised_tags = ["tag:vm", tailnet_tag]
            specs.append(
                {
                    "node": box,
                    "app": app_name,
                    "resource": resource["id"],
                    "user_slug": slug,
                    "tailscale_login": user["tailscale_login"],
                    "site_role": "active" if active else "passive",
                    "runtime_state": runtime_state,
                    "inventory_hostname": inventory_hostname,
                    "node_domain_role": zone["role"],
                    "node_hostname": node_hostname,
                    "box_scoped_hostname": box_scoped_hostname,
                    "tailnet_hostname": tailnet_hostname,
                    "tailnet_tag": tailnet_tag,
                    "advertised_tags": advertised_tags,
                    "zone": zone_name,
                    "vm_ipv4_address": user["vm_ipv4_address"],
                    "vm_interface": zone["vm_interface"],
                    "router_ipv4_address": zone["router_ipv4_address"],
                    "router_ipv4_prefix": zone["router_ipv4_prefix"],
                    "storage_port": user.get("storage_port"),
                    "guest_spec": compile_app_vm_guest_spec(
                        app_name,
                        box,
                        user,
                        resource,
                        zone,
                        topology,
                        runtime_state=runtime_state,
                    ),
                    "user": user,
                }
            )
    return specs


def compile_app_vm_bootstrap_router_rules(app_vm_specs, topology):
    backend_zone = topology["zones"].get("bak")
    if not backend_zone:
        model.die("platform_zones.bak is required for app VM bootstrap routing")
    dom0_source = backend_zone.get("dom0_ipv4_address")
    model.validate_ip(dom0_source, "platform_zones.bak.dom0_ipv4_address")

    rules = []
    for spec in app_vm_specs:
        zone_name, zone = model.resolve_zone(
            spec["zone"],
            f"{spec['app']}.{spec['resource']}.zone",
            topology,
        )
        rules.append(
            {
                "node": spec["node"],
                "app": spec["app"],
                "resource": "app-vm-bootstrap-ssh",
                "in_interface": backend_zone["router_interface"],
                "out_interface": zone["router_interface"],
                "source": dom0_source,
                "destination": spec["vm_ipv4_address"],
                "protocol": "tcp",
                "ports": [22],
                "comment": model.resource_comment(
                    spec["app"],
                    "app-vm-bootstrap-ssh",
                    f"{spec.get('user_slug') or spec['resource']}-{zone_name}-router",
                ),
            }
        )
    return rules


def compile_app_vm_tailscale_underlay_router_rules(app_vm_specs, topology):
    ops_zone = topology["control_zones"].get("ops")
    if not ops_zone:
        model.die("platform_control_zones.ops is required for app VM Tailscale underlay routing")
    ops_source = ops_zone.get("vm_ipv4_address")
    model.validate_ip(ops_source, "platform_control_zones.ops.vm_ipv4_address")

    rules = []
    for spec in app_vm_specs:
        zone_name, zone = model.resolve_zone(
            spec["zone"],
            f"{spec['app']}.{spec['resource']}.zone",
            topology,
        )
        suffix = f"{spec.get('user_slug') or spec['resource']}-{zone_name}"
        rules.append(
            {
                "node": spec["node"],
                "app": spec["app"],
                "resource": "app-vm-tailscale-underlay",
                "in_interface": ops_zone["router_interface"],
                "out_interface": zone["router_interface"],
                "source": ops_source,
                "destination": spec["vm_ipv4_address"],
                "protocol": "udp",
                "ports": [model.TAILSCALE_WIREGUARD_PORT],
                "comment": model.resource_comment(
                    spec["app"],
                    "app-vm-tailscale-underlay",
                    f"{suffix}-ops-to-app-router",
                ),
            }
        )
        rules.append(
            {
                "node": spec["node"],
                "app": spec["app"],
                "resource": "app-vm-tailscale-underlay",
                "in_interface": zone["router_interface"],
                "out_interface": ops_zone["router_interface"],
                "source": spec["vm_ipv4_address"],
                "destination": ops_source,
                "protocol": "udp",
                "ports": [model.TAILSCALE_WIREGUARD_PORT],
                "comment": model.resource_comment(
                    spec["app"],
                    "app-vm-tailscale-underlay",
                    f"{suffix}-app-to-ops-router",
                ),
            }
        )
    return rules


def compile_platform_map_app_vms(app_vm_specs):
    app_vms = []
    for spec in app_vm_specs:
        guest_spec = spec.get("guest_spec") or {}
        app_vms.append(
            {
                "node": spec["node"],
                "app": spec["app"],
                "resource": spec["resource"],
                "hostname": spec["node_hostname"],
                "tailnet_hostname": spec["tailnet_hostname"],
                "inventory_hostname": spec["inventory_hostname"],
                "guest_name": guest_spec["guest_name"],
                "user_slug": spec["user_slug"],
                "tailscale_login": spec["tailscale_login"],
                "site_role": spec["site_role"],
                "runtime_state": spec.get("runtime_state", "running"),
                "zone": spec["zone"],
                "vm_ipv4_address": spec["vm_ipv4_address"],
                "expected_tags": spec["advertised_tags"],
                "tailnet_tag": spec["tailnet_tag"],
                "memory_mb": int(guest_spec.get("memory_mb", 0)),
                "vcpus": int(guest_spec.get("vcpus", 0)),
                "autostart": bool(guest_spec.get("autostart")),
            }
        )
    return app_vms

