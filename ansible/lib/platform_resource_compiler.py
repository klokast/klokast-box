"""Compile resource plans, ownership ledgers, and application grants."""
import platform_vpn_egress
import hashlib
import json

import platform_resource_model as model
import platform_resource_guests as guests


def compile_tailnet_policy_resources(app_name, manifest, users, placement, selected_tailnet=None):
    resources = []
    per_user_resources = model.per_user_app_vm_resources(manifest)
    tag_prefix = (
        per_user_resources[0].get("tailnet_tag_prefix", app_name)
        if per_user_resources
        else app_name
    )
    vm_prefix = (
        per_user_resources[0].get("vm_name_prefix", "usr")
        if per_user_resources
        else "usr"
    )
    active_master = placement.get("active_master", "")
    tailnet_grants = []
    for resource in (selected_tailnet if selected_tailnet is not None else ((manifest.get("resources") or {}).get("tailnet") or [])):
        tailnet_grants.extend(resource.get("grants") or [])
    exact_login_grants = [
        grant for grant in tailnet_grants if grant.get("src") == "exact_user_login"
    ]
    if exact_login_grants:
        tcp_ports = []
        udp_ports = []
        for grant in exact_login_grants:
            tcp_ports.extend(grant.get("tcp_ports") or grant.get("ports") or [])
            udp_ports.extend(grant.get("udp_ports") or [])
        tcp_ports = model.validate_ports(tcp_ports, f"{app_name}.tailnet.tcp_ports")
        udp_ports = model.validate_tailnet_port_selectors(
            udp_ports,
            f"{app_name}.tailnet.udp_ports",
        ) if udp_ports else []
    else:
        tcp_ports = []
        udp_ports = []
    ip_selectors = [f"tcp:{port}" for port in tcp_ports] + [
        f"udp:{port}" for port in udp_ports
    ]
    for user in users:
        slug = user["slug"]
        tag = f"tag:{tag_prefix}-{slug}"
        hostname = f"{active_master}-{vm_prefix}-{slug}" if active_master else slug
        ssh_users = [user["system_user"]]
        resources.append(
            {
                "app": app_name,
                "id": f"user-{slug}",
                "hostname": hostname,
                "tag": tag,
                "tag_owner": "tag:ops",
                "tag_owners": ["tag:ops"],
                "grants": [
                    {
                        "src": user["tailscale_login"],
                        "dst": tag,
                        "ports": tcp_ports,
                        "udp_ports": udp_ports,
                        "ip": ip_selectors,
                    }
                ],
                "ssh": [
                    {
                        "src": user["tailscale_login"],
                        "dst": tag,
                        "users": ssh_users,
                        "action": "accept",
                    }
                ],
            }
        )
    return resources


def compile_resource_for_box(
    app_name,
    box,
    resource,
    router_rules,
    vm_rules,
    topology,
    users=None,
    devices=None,
):
    resource_id = resource["id"]
    resource_type = resource.get("type")
    devices = devices or {}

    # The usr zone has no shared host. Existing per-user/dedicated sources
    # remain valid; destinations that imply the former shared IP do not.
    for field in ("from_zone", "to_zone"):
        if resource.get(field):
            zone_name, _ = model.resolve_zone(resource[field], f"{app_name}.{resource_id}.{field}", topology)
            if zone_name == "usr" and (field == "to_zone" or not users):
                model.die(f"{app_name}.{resource_id}.{field} cannot target the retired shared usr VM; declare a dedicated VM source")

    if resource_type == "interzone_tcp":
        _, from_zone = model.resolve_zone(
            resource.get("from_zone"),
            f"{app_name}.{resource_id}.from_zone",
            topology,
        )
        _, to_zone = model.resolve_zone(
            resource.get("to_zone"),
            f"{app_name}.{resource_id}.to_zone",
            topology,
        )
        sources = users or [{"slug": "", "vm_ipv4_address": from_zone["vm_ipv4_address"]}]
        for user in sources:
            suffix = f"{user['slug']}-" if user.get("slug") else ""
            ports = guests.user_port_values(resource, user, f"{app_name}.{resource_id}.ports")
            router_rules.append(
                {
                    "node": box,
                    "app": app_name,
                    "resource": resource_id,
                    "in_interface": from_zone["router_interface"],
                    "out_interface": to_zone["router_interface"],
                    "source": user["vm_ipv4_address"],
                    "destination": to_zone["vm_ipv4_address"],
                    "protocol": "tcp",
                    "ports": ports,
                    "exclusive": bool(resource.get("exclusive", False)),
                    "comment": model.resource_comment(app_name, resource_id, f"{suffix}router"),
                }
            )
            vm_rules.append(
                {
                    "node": box,
                    "app": app_name,
                    "resource": resource_id,
                    "target_role": to_zone["role"],
                    "interface": to_zone["vm_interface"],
                    "source": user["vm_ipv4_address"],
                    "destination": to_zone["vm_ipv4_address"],
                    "ports": ports,
                    "exclusive": bool(resource.get("exclusive", False)),
                    "comment": model.resource_comment(app_name, resource_id, f"{suffix}vm-input"),
                }
            )
        return

    if resource_type == "realm_to_zone_tcp":
        _, from_realm = model.resolve_realm(
            resource.get("from_realm"),
            f"{app_name}.{resource_id}.from_realm",
            topology,
            require_cidr=True,
        )
        _, to_zone = model.resolve_zone(
            resource.get("to_zone"),
            f"{app_name}.{resource_id}.to_zone",
            topology,
        )
        ports = model.validate_ports(
            resource.get("ports", [resource.get("port")]),
            f"{app_name}.{resource_id}.ports",
        )
        router_rules.append(
            {
                "node": box,
                "app": app_name,
                "resource": resource_id,
                "in_interface": from_realm["router_interface"],
                "out_interface": to_zone["router_interface"],
                "source": from_realm["cidr"],
                "destination": to_zone["vm_ipv4_address"],
                "protocol": "tcp",
                "ports": ports,
                "exclusive": bool(resource.get("exclusive", False)),
                "comment": model.resource_comment(app_name, resource_id, "router"),
            }
        )
        vm_rules.append(
            {
                "node": box,
                "app": app_name,
                "resource": resource_id,
                "target_role": to_zone["role"],
                "interface": to_zone["vm_interface"],
                "source": from_realm["cidr"],
                "destination": to_zone["vm_ipv4_address"],
                "ports": ports,
                "exclusive": bool(resource.get("exclusive", False)),
                "comment": model.resource_comment(app_name, resource_id, "vm-input"),
            }
        )
        return

    if resource_type == "wan_egress":
        _, from_zone = model.resolve_zone(
            resource.get("from_zone"),
            f"{app_name}.{resource_id}.from_zone",
            topology,
        )
        wan_interface = topology["realms"]["wan"]["router_interface"]
        sources = users or [{"slug": "", "vm_ipv4_address": from_zone["vm_ipv4_address"]}]
        for protocol, key in (("tcp", "tcp_ports"), ("udp", "udp_ports")):
            ports = resource.get(key, [])
            if not ports:
                continue
            for user in sources:
                suffix = f"{user['slug']}-{protocol}" if user.get("slug") else protocol
                router_rules.append(
                    {
                        "node": box,
                        "app": app_name,
                        "resource": resource_id,
                        "in_interface": from_zone["router_interface"],
                        "out_interface": wan_interface,
                        "source": user["vm_ipv4_address"],
                        "destination": "",
                        "protocol": protocol,
                        "ports": model.validate_ports(ports, f"{app_name}.{resource_id}.{key}"),
                        "exclusive": bool(resource.get("exclusive", False)),
                        "comment": model.resource_comment(app_name, resource_id, suffix),
                    }
                )
        return

    if resource_type == "zone_to_device_tcp":
        _, from_zone = model.resolve_zone(
            resource.get("from_zone"),
            f"{app_name}.{resource_id}.from_zone",
            topology,
        )
        device = devices.get(resource.get("device"))
        if not device:
            model.die(f"{app_name}.{resource_id} references unknown device {resource.get('device')}")
        _, to_zone = model.resolve_zone(
            device["zone"],
            f"{app_name}.{resource_id}.device.zone",
            topology,
        )
        ports = model.validate_ports(
            resource.get("ports", [resource.get("port")]),
            f"{app_name}.{resource_id}.ports",
        )
        sources = users or [{"slug": "", "vm_ipv4_address": from_zone["vm_ipv4_address"]}]
        for user in sources:
            suffix = f"{user['slug']}-" if user.get("slug") else ""
            router_rules.append(
                {
                    "node": box,
                    "app": app_name,
                    "resource": resource_id,
                    "in_interface": from_zone["router_interface"],
                    "out_interface": to_zone["router_interface"],
                    "source": user["vm_ipv4_address"],
                    "destination": device["ipv4_address"],
                    "protocol": "tcp",
                    "ports": ports,
                    "exclusive": bool(resource.get("exclusive", False)),
                    "comment": model.resource_comment(app_name, resource_id, f"{suffix}router"),
                }
            )
        return

    if resource_type == "device_to_zone_tcp":
        device = devices.get(resource.get("device"))
        if not device:
            model.die(f"{app_name}.{resource_id} references unknown device {resource.get('device')}")
        _, from_zone = model.resolve_zone(
            device["zone"],
            f"{app_name}.{resource_id}.device.zone",
            topology,
        )
        _, to_zone = model.resolve_zone(
            resource.get("to_zone"),
            f"{app_name}.{resource_id}.to_zone",
            topology,
        )
        ports = model.validate_ports(
            resource.get("ports", [resource.get("port")]),
            f"{app_name}.{resource_id}.ports",
        )
        router_rules.append(
            {
                "node": box,
                "app": app_name,
                "resource": resource_id,
                "in_interface": from_zone["router_interface"],
                "out_interface": to_zone["router_interface"],
                "source": device["ipv4_address"],
                "destination": to_zone["vm_ipv4_address"],
                "protocol": "tcp",
                "ports": ports,
                "exclusive": bool(resource.get("exclusive", False)),
                "comment": model.resource_comment(app_name, resource_id, "router"),
            }
        )
        vm_rules.append(
            {
                "node": box,
                "app": app_name,
                "resource": resource_id,
                "target_role": to_zone["role"],
                "interface": to_zone["vm_interface"],
                "source": device["ipv4_address"],
                "destination": to_zone["vm_ipv4_address"],
                "ports": ports,
                "exclusive": bool(resource.get("exclusive", False)),
                "comment": model.resource_comment(app_name, resource_id, "vm-input"),
            }
        )
        return

    if resource_type == "device_wan_egress":
        device = devices.get(resource.get("device"))
        if not device:
            model.die(f"{app_name}.{resource_id} references unknown device {resource.get('device')}")
        _, from_zone = model.resolve_zone(
            device["zone"],
            f"{app_name}.{resource_id}.device.zone",
            topology,
        )
        wan_interface = topology["realms"]["wan"]["router_interface"]
        for protocol, key in (("tcp", "tcp_ports"), ("udp", "udp_ports")):
            ports = resource.get(key, [])
            if not ports:
                continue
            router_rules.append(
                {
                    "node": box,
                    "app": app_name,
                    "resource": resource_id,
                    "in_interface": from_zone["router_interface"],
                    "out_interface": wan_interface,
                    "source": device["ipv4_address"],
                    "destination": "",
                    "protocol": protocol,
                    "ports": model.validate_ports(ports, f"{app_name}.{resource_id}.{key}"),
                    "exclusive": bool(resource.get("exclusive", False)),
                    "comment": model.resource_comment(app_name, resource_id, protocol),
                }
            )
        return

    if resource_type == "tailnet_tcp":
        return

    model.die(f"unsupported network resource type for {app_name}.{resource_id}: {resource_type}")


def nft_port_expr(ports):
    values = model.validate_ports(ports, "nft ports")
    if len(values) == 1:
        return str(values[0])
    return "{ " + ", ".join(str(port) for port in values) + " }"


def normalized_router_rule(rule):
    normalized = {
        "kind": "router-forward",
        "node": rule["node"],
        "in_interface": rule["in_interface"],
        "out_interface": rule["out_interface"],
        "source": rule["source"],
        "destination": rule.get("destination") or "",
        "protocol": rule.get("protocol") or "tcp",
        "ports": model.validate_ports(rule["ports"], f"{rule.get('comment', 'router')}.ports"),
    }
    return normalized


def normalized_vm_rule(rule):
    protocol = rule.get("protocol") or "tcp"
    if protocol not in ("tcp", "udp"):
        model.die("VM input rule protocol must be tcp or udp")
    normalized = {
        "kind": "vm-input",
        "node": rule["node"],
        "target_role": rule["target_role"],
        "interface": rule.get("interface") or "eth0",
        "source": rule["source"],
        "destination": rule["destination"],
        "protocol": protocol,
        "ports": model.validate_ports(rule["ports"], f"{rule.get('comment', 'vm')}.ports"),
    }
    return normalized


def resource_key(normalized):
    digest = hashlib.sha256(
        json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"{normalized['kind']}-{digest[:24]}"


def rendered_rule_identity(key):
    return f"klokast-{key[:32]}"


def render_router_resource_rule(normalized, identity):
    destination = ""
    if normalized["destination"]:
        destination = f" ip daddr {normalized['destination']}"
    return (
        f"iifname \"{normalized['in_interface']}\" "
        f"oifname \"{normalized['out_interface']}\" "
        f"ip saddr {normalized['source']}{destination} "
        f"{normalized['protocol']} dport {nft_port_expr(normalized['ports'])} "
        f"accept comment \"{identity}\""
    )


def render_vm_resource_rule(normalized, identity):
    return (
        f"iifname \"{normalized['interface']}\" "
        f"ip saddr {normalized['source']} "
        f"ip daddr {normalized['destination']} "
        f"{normalized['protocol']} dport {nft_port_expr(normalized['ports'])} "
        f"accept comment \"{identity}\""
    )


def build_resource_claim(kind, rule):
    if kind == "router-forward":
        normalized = normalized_router_rule(rule)
        host_role = "router"
        line_renderer = render_router_resource_rule
    elif kind == "vm-input":
        normalized = normalized_vm_rule(rule)
        host_role = rule["target_role"]
        line_renderer = render_vm_resource_rule
    else:
        model.die(f"unsupported app-resource kind: {kind}")

    key = resource_key(normalized)
    identity = rendered_rule_identity(key)
    content = line_renderer(normalized, identity) + "\n"
    return {
        "schema_version": 1,
        "kind": kind,
        "key": key,
        "app": rule["app"],
        "resource": rule["resource"],
        "node": rule["node"],
        "host_role": host_role,
        "exclusive": bool(rule.get("exclusive", False)),
        "claim_comment": rule.get("comment", ""),
        "rendered_rule_identity": identity,
        "normalized": normalized,
        "content": content,
        "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "approved_commit": "",
    }


def build_effective_resource_file(claims):
    first = claims[0]
    owners = sorted({claim["app"] for claim in claims})
    exclusive_owners = sorted({claim["app"] for claim in claims if claim.get("exclusive")})
    if exclusive_owners and len(owners) > 1:
        model.die(
            "exclusive platform-resource conflict for "
            f"{first['key']}: {', '.join(owners)}"
        )
    content_hashes = {claim["content_sha256"] for claim in claims}
    if len(content_hashes) != 1:
        model.die(
            "platform-resource claims normalized to the same key but render "
            f"different snippets: {first['key']}"
        )
    header = (
        "# Managed by Klokast platform-resources. Do not edit.\n"
        f"# klokast-resource-kind: {first['kind']}\n"
        f"# klokast-resource-key: {first['key']}\n"
        f"# klokast-resource-owners: {','.join(owners)}\n"
        f"# klokast-resource-rendered-identity: {first['rendered_rule_identity']}\n"
        f"# klokast-resource-exclusive: {'true' if exclusive_owners else 'false'}\n"
    )
    content = header + first["content"]
    return {
        "schema_version": 1,
        "kind": first["kind"],
        "key": first["key"],
        "filename": f"{first['key']}.nft",
        "node": first["node"],
        "host_role": first["host_role"],
        "owners": owners,
        "resources": sorted({f"{claim['app']}:{claim['resource']}" for claim in claims}),
        "exclusive": bool(exclusive_owners),
        "rendered_rule_identity": first["rendered_rule_identity"],
        "content": content,
        "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "claim_keys": sorted({claim["claim_comment"] for claim in claims if claim.get("claim_comment")}),
        "approved_commit": "",
    }


def build_app_resource_ledger(router_rules, vm_rules):
    claims = []
    for rule in router_rules:
        claims.append(build_resource_claim("router-forward", rule))
    for rule in vm_rules:
        claims.append(build_resource_claim("vm-input", rule))

    grouped = {}
    for claim in claims:
        grouped.setdefault(
            (claim["node"], claim["host_role"], claim["kind"], claim["key"]),
            [],
        ).append(claim)

    effective = [
        build_effective_resource_file(items)
        for _, items in sorted(grouped.items(), key=lambda item: item[0])
    ]
    return {
        "claims": sorted(
            claims,
            key=lambda item: (
                item["node"],
                item["host_role"],
                item["kind"],
                item["key"],
                item["app"],
                item["resource"],
            ),
        ),
        "effective_files": effective,
    }


def build_app_resource_cleanup_scope(app_name, node, reason):
    return {
        "schema_version": 1,
        "app": app_name,
        "node": node,
        "host_roles": list(model.CLEANUP_HOST_ROLES),
        "reason": reason,
    }


def _plan_metadata(registry_path, registry_input):
    return {
        "schema_version": 1,
        "compiler": "platform-resources",
        "compiler_version": model.COMPILER_VERSION,
        "registry_path": str(registry_path) if registry_input is None else registry_input["registry_path"],
        "registry_sha256": model.sha256_file(registry_path) if registry_input is None else registry_input["registry_sha256"],
    }


def _shared_guest_projection(box_configs):
    return [
        {
            "node": box,
            "role": role,
            "hostname": f"{box}-{role}",
            "guest_name": role,
            "runtime_state": guest.get("runtime_state", "running"),
            "autostart": guest.get("runtime_state", "running") == "running",
        }
        for box, config in sorted(box_configs.items())
        for role, guest in sorted((config.get("shared_guests") or {}).items())
    ]


def compile_airunners(registry, topology, *, bootstrap_boxes=()):
    """Platform-owned rules from the one ordered Instance placement list."""
    runners = registry.get('airunners', [])
    if not isinstance(runners, list) or any(not isinstance(v, str) for v in runners) or len(set(runners)) != len(runners):
        model.die('airunners must be an ordered list of unique runtime names')
    if not any(v.endswith('-air') for v in runners) and not bootstrap_boxes:
        return [], []
    allocation = topology['air']
    zone = topology['zones'][allocation['zone']]
    # This address is owned by Platform topology, never by an app declaration.
    address = allocation['ipv4_address']
    if address not in zone.get('reserved_ipv4_addresses', []):
        model.die('Platform runner address must be reserved in its zone')
    rules, guests = [], []
    for runner in runners:
        if not runner.endswith('-air'):
            continue
        box = runner[:-4]
        if box not in registry.get('boxes', {}):
            model.die('airunner references an unknown box: ' + box)
        guests.append({'node': box, 'hostname': runner, 'guest_name': 'air',
                       'zone': allocation['zone'], 'vm_ipv4_address': address,
                       'memory_mb': allocation['memory_mb'], 'vcpus': allocation['vcpus'],
                       'expected_tags': ['tag:airunner']})
        def add(resource, incoming, outgoing, source, destination, protocol, ports):
            rules.append({'node': box, 'app': 'platform', 'resource': resource,
                          'in_interface': incoming, 'out_interface': outgoing,
                          'source': source, 'destination': destination,
                          'protocol': protocol, 'ports': ports, 'exclusive': True,
                          'comment': 'platform-air-' + resource})
        wan = topology['realms']['wan']['router_interface']
        add('web', zone['router_interface'], wan, address, '', 'tcp', [80, 443])
        for protocol in ('tcp', 'udp'):
            for resolver in ('1.1.1.1', '1.0.0.1'):
                add('dns', zone['router_interface'], wan, address, resolver, protocol, [53])
        add('tailscale', zone['router_interface'], wan, address, '', 'udp', [3478, 41641])
        ops = topology['control_zones']['ops']
        add('controller-transport', zone['router_interface'], ops['router_interface'],
            address, ops['vm_ipv4_address'], 'udp', [41641])
        add('runner-transport', ops['router_interface'], zone['router_interface'],
            ops['vm_ipv4_address'], address, 'udp', [41641])
        if box in bootstrap_boxes:
            bak = topology['zones']['bak']
            add('bootstrap-ssh', bak['router_interface'], zone['router_interface'],
                bak['dom0_ipv4_address'], address, 'tcp', [22])
    if set(bootstrap_boxes) - {g['node'] for g in guests}:
        model.die('bootstrap SSH requires a declared airunner VM')
    return guests, rules


def compile_registry(registry_path, app_filter, *, registry_input=None, repo_root, air_bootstrap_boxes=(), vpn_bootstrap_boxes=()):
    topology = model.load_topology(repo_root=repo_root)
    registry = model.load_yaml(registry_path) if registry_input is None else registry_input["registry"]
    if registry.get("schema_version") != 1:
        model.die(f"{registry_path} must use schema_version: 1")
    apps = registry.get("apps") or {}
    if not isinstance(apps, dict):
        model.die(f"{registry_path} apps must be a mapping")
    box_configs = model.registry_box_configs(registry, topology)

    try:
        vpn_guests, vpn_rules, vpn_vm_rules = platform_vpn_egress.compile_network(
            registry, box_configs, topology, bootstrap_boxes=vpn_bootstrap_boxes)
    except ValueError as error:
        model.die(str(error))
    requested = set(app_filter)
    router_rules = list(vpn_rules)
    vm_rules = list(vpn_vm_rules)
    tailnet_resources = []
    tailnet_policy_resources = []
    app_vm_specs = []
    managed_devices = []
    cleanup_scopes = []
    boxes = {g["node"] for g in vpn_guests}
    compiled_apps = {}
    manifest_paths = {}
    air_guests, air_rules = compile_airunners(registry, topology, bootstrap_boxes=air_bootstrap_boxes)
    router_rules.extend(air_rules)
    boxes.update(guest['node'] for guest in air_guests)
    for guest in air_guests:
        ops = topology['control_zones']['ops']
        vm_rules.append({
            'node': guest['node'], 'app': 'platform', 'resource': 'air-controller-transport',
            'target_role': 'ops', 'interface': ops['vm_interface'],
            'source': guest['vm_ipv4_address'], 'destination': ops['vm_ipv4_address'],
            'protocol': 'udp', 'ports': [41641], 'exclusive': True,
            'comment': 'platform-air-controller-transport',
        })

    for app_name, entry in sorted(apps.items()):
        model.validate_app_id(app_name)
        if requested and app_name not in requested:
            continue
        if not isinstance(entry, dict):
            model.die(f"apps.{app_name} must be a mapping")
        app_enabled = bool(entry.get("enabled"))
        manifest_path = repo_root / "apps" / app_name / "platform-resources.yml"
        if not app_enabled and not manifest_path.is_file():
            manifest = {
                "schema_version": 1,
                "app": app_name,
                "resources": {},
                "_manifest_path": f"compatibility-only://apps/{app_name}",
            }
        else:
            manifest = model.app_manifest(app_name, repo_root=repo_root)
            manifest_paths[app_name] = manifest["_manifest_path"]
        model.validate_manifest_resources(app_name, manifest, topology)
        model.validate_privileged_runtime(app_name, manifest, entry)
        app_boxes = model.selected_boxes(app_name, manifest, entry)
        model.validate_selected_resource_capabilities(
            app_name, manifest, entry, app_boxes, box_configs
        )
        model.validate_shared_guest_app_compatibility(
            app_name, manifest, entry, app_boxes, box_configs, topology
        )
        app_users = model.selected_users(app_name, manifest, entry, topology)
        app_box_vms = (
            guests.selected_app_vms(app_name, manifest, entry, app_boxes, topology, box_configs)
            if app_enabled
            else []
        )
        app_managed_devices = (
            guests.selected_managed_iot_devices(
                app_name,
                manifest,
                entry,
                app_boxes,
                topology,
                box_configs,
            )
            if app_enabled
            else []
        )
        tailnet_candidates = model.selected_tailnet_resources(manifest, entry) if app_enabled else []
        managed_devices.extend(app_managed_devices)
        app_network_resource_ids = []
        app_tailnet_resource_ids = []

        if not app_enabled:
            for box in app_boxes:
                cleanup_scopes.append(
                    build_app_resource_cleanup_scope(app_name, box, "disabled-app")
                )

        for box in app_boxes:
            boxes.add(box)
            box_config = model.box_config_for(box_configs, box)
            box_app_vm_specs = [
                spec for spec in app_box_vms if spec["node"] == box
            ]
            app_vm_specs.extend(box_app_vm_specs)
            app_vm_specs.extend(
                guests.compile_app_vm_specs_for_box(app_name, box, entry, manifest, app_users, topology)
            )
            network_sources = app_users or guests.app_vm_sources_for_box(box_app_vm_specs, box)
            network_resources = (
                model.selected_network_resources(manifest, entry, box_config) if app_enabled else []
            )
            for resource in network_resources:
                if resource["id"] not in app_network_resource_ids:
                    app_network_resource_ids.append(resource["id"])
                box_devices = {
                    device["resource"]: device
                    for device in app_managed_devices
                    if device["node"] == box
                }
                compile_resource_for_box(
                    app_name,
                    box,
                    resource,
                    router_rules,
                    vm_rules,
                    topology,
                    users=network_sources,
                    devices=box_devices,
                )
        tailnet_policy_selected = []
        for resource in tailnet_candidates:
            hostname_value = model.tailnet_resource_value(
                resource,
                "hostname_default",
                "hostname",
                "hostname_pattern",
            )
            tag_value = model.tailnet_resource_value(
                resource,
                "tag_default",
                "tag",
                "tag_pattern",
            )
            scope_boxes = app_boxes if "{box}" in hostname_value or "{box}" in tag_value else [""]
            for box in scope_boxes:
                if box:
                    if not model.resource_access_allowed(resource, model.box_config_for(box_configs, box)):
                        continue
                elif not model.resource_access_allowed_for_any_box(resource, box_configs, app_boxes):
                    continue
                hostname = model.render_tailnet_resource_value(hostname_value, box)
                tag = model.render_tailnet_resource_value(tag_value, box)
                if hostname and "{" not in hostname:
                    model.validate_hostname(hostname, f"{app_name}.{resource['id']}.hostname")
                if tag and "{" not in tag:
                    model.validate_app_tailnet_tag(tag, f"{app_name}.{resource['id']}.tag")
                tailnet_resources.append(
                    {
                        "app": app_name,
                        "id": resource["id"],
                        "node": box,
                        "hostname": hostname,
                        "tag": tag,
                        "grants": resource.get("grants") or [],
                    }
                )
                if resource["id"] not in app_tailnet_resource_ids:
                    app_tailnet_resource_ids.append(resource["id"])
                if resource not in tailnet_policy_selected:
                    tailnet_policy_selected.append(resource)
        placement = model.entry_placement(entry)
        tailnet_policy_resources.extend(
            compile_tailnet_policy_resources(
                app_name,
                manifest,
                app_users,
                placement,
                selected_tailnet=tailnet_policy_selected,
            )
        )
        compiled_apps[app_name] = {
            "enabled": app_enabled,
            "runtime_state": model.app_runtime_state(app_name, entry, manifest) if app_enabled else "stopped",
            "boxes": app_boxes,
            "placement": placement,
            "resources": app_network_resource_ids,
            "tailnet_resources": app_tailnet_resource_ids,
            "isolation": entry.get("isolation", manifest.get("default_isolation", "")),
            "resource_flags": entry.get("resources") or {},
            "controls": entry.get("controls") or entry.get("ephemeral") or {},
            "users": app_users,
            "app_vms": sorted({spec["resource"] for spec in app_box_vms}),
            "managed_iot_devices": sorted({device["resource"] for device in app_managed_devices}),
        }

    if requested:
        missing = sorted(requested - set(compiled_apps))
        if missing:
            model.die(f"registry has no app entry for: {', '.join(missing)}")

    router_rules.extend(guests.compile_app_vm_bootstrap_router_rules(app_vm_specs, topology))
    router_rules.extend(
        guests.compile_app_vm_tailscale_underlay_router_rules(app_vm_specs, topology)
    )

    app_resource_ledger = build_app_resource_ledger(router_rules, vm_rules)
    effective_box_configs = {
        box: model.box_config_for(box_configs, box)
        for box in sorted(set(boxes) | set(box_configs))
    }
    platform_map_shared_guests = _shared_guest_projection(effective_box_configs)

    return {
        **_plan_metadata(registry_path, registry_input),
        "apps": compiled_apps,
        "box_configs": effective_box_configs,
        "boxes": sorted(boxes),
        "manifest_paths": manifest_paths,
        "tailnet_resources": tailnet_resources,
        "tailnet_policy_resources": tailnet_policy_resources,
        "app_vm_specs": app_vm_specs,
        "managed_iot_devices": sorted(
            managed_devices,
            key=lambda item: (item["node"], item["app"], item["resource"]),
        ),
        "platform_map": {
            "airunners": air_guests,
            "app_vms": guests.compile_platform_map_app_vms(app_vm_specs) + vpn_guests,
            "shared_guests": platform_map_shared_guests,
            "managed_iot_devices": sorted(
                managed_devices,
                key=lambda item: (item["node"], item["app"], item["resource"]),
            ),
        },
        "app_resource_claims": app_resource_ledger["claims"],
        "app_resource_effective_files": app_resource_ledger["effective_files"],
        "app_resource_cleanup_scopes": cleanup_scopes,
    }


def compile_box_registry_plan(registry_path, *, registry_input=None, repo_root):
    topology = model.load_topology(repo_root=repo_root)
    registry = model.load_yaml(registry_path) if registry_input is None else registry_input["registry"]
    if registry.get("schema_version") != 1:
        model.die(f"{registry_path} must use schema_version: 1")
    apps = registry.get("apps") or {}
    if not isinstance(apps, dict):
        model.die(f"{registry_path} apps must be a mapping")
    box_configs = model.registry_box_configs(registry, topology)
    for app_name, entry in sorted(apps.items()):
        model.validate_app_id(app_name)
        if not isinstance(entry, dict):
            model.die(f"apps.{app_name} must be a mapping")
        if not entry.get("enabled") or entry.get("runtime_state", "running") == "stopped":
            continue
        manifest = model.app_manifest(app_name, repo_root=repo_root)
        model.validate_manifest_resources(app_name, manifest, topology)
        app_boxes = model.selected_boxes(app_name, manifest, entry)
        model.validate_shared_guest_app_compatibility(
            app_name, manifest, entry, app_boxes, box_configs, topology
        )
    platform_map_shared_guests = _shared_guest_projection(box_configs)
    return {
        **_plan_metadata(registry_path, registry_input),
        "apps": {},
        "box_configs": box_configs,
        "boxes": sorted(box_configs),
        "app_vm_specs": [],
        "managed_iot_devices": [],
        "tailnet_resources": [],
        "tailnet_policy_resources": [],
        "app_resource_claims": [],
        "app_resource_effective_files": [],
        "app_resource_cleanup_scopes": [],
        "platform_map": {
            "app_vms": [],
            "shared_guests": platform_map_shared_guests,
            "managed_iot_devices": [],
        },
    }


def build_app_grant(compiled, app_name, approved_commit):
    apps = compiled.get("apps") or {}
    if app_name not in apps:
        model.die(f"compiled resources do not include app: {app_name}")
    app = apps[app_name]
    effective_files = []
    for item in compiled.get("app_resource_effective_files") or []:
        if app_name not in (item.get("owners") or []):
            continue
        sanitized = {
            key: item[key]
            for key in (
                "schema_version",
                "kind",
                "key",
                "filename",
                "node",
                "host_role",
                "exclusive",
                "rendered_rule_identity",
                "content_sha256",
                "approved_commit",
            )
            if key in item
        }
        sanitized["owned_by_app"] = True
        sanitized["approved_commit"] = approved_commit
        effective_files.append(sanitized)

    app_vms = []
    for spec in compiled.get("app_vm_specs") or []:
        if spec.get("app") != app_name:
            continue
        guest_spec = spec.get("guest_spec") or {}
        user = spec.get("user") or {}
        app_vms.append(
            {
                "schema_version": 1,
                "app": app_name,
                "resource": spec["resource"],
                "node": spec["node"],
                "site_role": spec["site_role"],
                "runtime_state": spec.get("runtime_state", "running"),
                "inventory_hostname": spec["inventory_hostname"],
                "node_domain_role": spec["node_domain_role"],
                "node_hostname": spec["node_hostname"],
                "tailnet_hostname": spec["tailnet_hostname"],
                "tailnet_tag": spec["tailnet_tag"],
                "advertised_tags": spec["advertised_tags"],
                "zone": spec["zone"],
                "vm_ipv4_address": spec["vm_ipv4_address"],
                "user_slug": spec["user_slug"],
                "system_user": user.get("system_user", spec["user_slug"]),
                "guest_name": guest_spec.get("guest_name", ""),
                "guest_os": guest_spec.get("guest_os", ""),
                "container_runtime": guest_spec.get("container_runtime", ""),
            }
        )

    return {
        "schema_version": 1,
        "kind": "platform-resource-grant",
        "app": app_name,
        "enabled": app["enabled"],
        "runtime_state": app.get("runtime_state", "stopped"),
        "approved_commit": approved_commit,
        "compiler": compiled["compiler"],
        "compiler_version": compiled["compiler_version"],
        "registry_sha256": compiled["registry_sha256"],
        "boxes": app["boxes"],
        "placement": app["placement"],
        "resources": app["resources"],
        "tailnet_resources": [
            item for item in compiled["tailnet_resources"] if item.get("app") == app_name
        ],
        "managed_iot_devices": [
            item for item in compiled.get("managed_iot_devices", []) if item.get("app") == app_name
        ],
        "app_vms": app_vms,
        "app_resource_effective_files": effective_files,
    }


def resource_host_for_node_role(node, host_role):
    suffix = model.ROLE_HOST_SUFFIXES.get(host_role)
    if not suffix:
        model.die(f"unknown app-resource host_role: {host_role}")
    return f"{node}-{suffix}"


def resource_host_for_effective_file(resource):
    if resource["kind"] == "router-forward":
        return resource_host_for_node_role(resource["node"], "router")
    if resource["kind"] == "vm-input":
        return resource_host_for_node_role(resource["node"], resource["host_role"])
    model.die(f"unsupported app-resource kind: {resource['kind']}")


def add_host(hosts, seen, host):
    if host not in seen:
        seen.add(host)
        hosts.append(host)


def resource_hosts_for_scope(compiled, scope_apps=None):
    scope = set(scope_apps or [])
    hosts = []
    seen = set()

    effective_files = compiled.get("app_resource_effective_files") or []
    ordered_files = [
        item for item in effective_files if item.get("kind") == "router-forward"
    ] + [
        item for item in effective_files if item.get("kind") != "router-forward"
    ]
    for resource in ordered_files:
        if scope and not (scope & set(resource.get("owners") or [])):
            continue
        add_host(hosts, seen, resource_host_for_effective_file(resource))
    for cleanup in compiled.get("app_resource_cleanup_scopes") or []:
        if scope and cleanup.get("app") not in scope:
            continue
        for host_role in cleanup.get("host_roles") or []:
            add_host(hosts, seen, resource_host_for_node_role(cleanup["node"], host_role))
    if scope:
        for app_name in sorted(scope):
            app = (compiled.get("apps") or {}).get(app_name)
            if not app:
                model.die(f"compiled resources do not include app: {app_name}")
            for box in app.get("boxes") or []:
                for suffix in model.ROLE_SUFFIXES:
                    add_host(hosts, seen, f"{box}-{suffix}")
    for spec in compiled["app_vm_specs"]:
        if scope and spec.get("app") not in scope:
            continue
        add_host(hosts, seen, spec["inventory_hostname"])
    return hosts


def is_podman_resource_host(host):
    try:
        _node, suffix = host.rsplit("-", 1)
    except ValueError:
        return False
    return suffix in model.PODMAN_ROLE_SUFFIXES


def podman_host_node_role(host):
    try:
        node, suffix = host.rsplit("-", 1)
    except ValueError:
        model.die(f"invalid Podman resource host: {host}")
    role = model.PODMAN_NODE_ROLES.get(suffix)
    if not role:
        model.die(f"unsupported Podman resource host role: {host}")
    return node, role


def shared_guest_host_is_running(compiled, host):
    try:
        node, suffix = host.rsplit("-", 1)
    except ValueError:
        return True
    if suffix not in model.SHARED_GUEST_ROLES:
        return True
    config = (compiled.get("box_configs") or {}).get(node) or model.default_box_config()
    guest = (config.get("shared_guests") or {}).get(suffix) or {}
    return guest.get("runtime_state", "running") == "running"


def router_resource_hosts(compiled, scope_apps=None):
    return [
        host
        for host in resource_hosts_for_scope(compiled, scope_apps)
        if host.endswith("-router")
    ]


def ansible_resource_hosts(compiled, scope_apps=None):
    return [
        host
        for host in resource_hosts_for_scope(compiled, scope_apps)
        if not is_podman_resource_host(host)
    ]


def podman_resource_hosts(compiled, scope_apps=None):
    return [
        host
        for host in resource_hosts_for_scope(compiled, scope_apps)
        if is_podman_resource_host(host) and shared_guest_host_is_running(compiled, host)
    ]


def limit_for_app_vms(boxes, app_vm_specs):
    hosts = [f"{box}-bak" for box in boxes]
    hosts.extend(f"{box}-dom0" for box in boxes)
    hosts.extend(spec["inventory_hostname"] for spec in app_vm_specs)
    return ",".join(hosts)


def app_vm_specs_for_scope(compiled, scope_apps=None):
    scope = set(scope_apps or [])
    if not scope:
        return compiled["app_vm_specs"]
    return [
        spec for spec in compiled["app_vm_specs"]
        if spec.get("app") in scope
    ]


def boxes_for_scope(compiled, scope_apps=None):
    scope = sorted(set(scope_apps or []))
    if not scope:
        return sorted(set(compiled["boxes"]) | set(compiled.get("box_configs") or {}))
    boxes = set()
    for app_name in scope:
        app = (compiled.get("apps") or {}).get(app_name)
        if not app:
            model.die(f"compiled resources do not include app: {app_name}")
        boxes.update(app.get("boxes") or [])
    return sorted(boxes)


def selected_shared_guest_boxes(compiled, requested_boxes):
    available = sorted((compiled.get("box_configs") or {}).keys())
    if not requested_boxes:
        return available
    selected = []
    for box in requested_boxes:
        model.validate_box(box, "--box")
        if box not in available:
            model.die(f"registry has no boxes.{box} configuration")
        if box not in selected:
            selected.append(box)
    return selected


def selected_box_access_box(compiled, requested_boxes):
    if len(requested_boxes) != 1:
        model.die("box access operations require exactly one --box")
    box = requested_boxes[0]
    model.validate_box(box, "--box")
    if box not in (compiled.get("box_configs") or {}):
        model.die(f"registry has no boxes.{box} configuration")
    return box


def box_access_router_vars(compiled, box):
    selected = selected_box_access_box(compiled, [box])
    return {
        "platform_resources_box_access": {
            selected: (
                compiled["box_configs"][selected].get("access")
                or model.default_box_access()
            )
        },
        "router_managed_dhcp_hosts": [
            item
            for item in router_managed_dhcp_hosts(compiled)
            if item.get("node") == selected
        ],
    }


def router_managed_dhcp_hosts(compiled):
    hosts = [
        {
            "node": device["node"],
            "name": device["hostname"],
            "mac": device["mac"],
            "address": device["ipv4_address"],
            "app": device["app"],
            "resource": device["resource"],
        }
        for device in compiled.get("managed_iot_devices", [])
    ]
    for node, config in sorted((compiled.get("box_configs") or {}).items()):
        for reservation in config.get("dhcp_reservations") or []:
            hosts.append(
                {
                    "node": node,
                    "name": reservation["hostname"],
                    "mac": reservation["mac"],
                    "address": reservation["ipv4_address"],
                    "app": "",
                    "resource": "box-dhcp-reservation",
                }
            )
    return hosts


def attach_approved_commit(compiled, approved_commit):
    if not approved_commit:
        return compiled
    encoded = json.loads(json.dumps(compiled))
    encoded["approved_commit"] = approved_commit
    for key in ("app_resource_claims", "app_resource_effective_files"):
        for item in encoded.get(key) or []:
            item["approved_commit"] = approved_commit
    return encoded
