"""Pure inventory conversion and comparison; no host or credential access."""

import hashlib
import json


POLICY_GROUPS = (
    "all", "backend", "bootstrap", "dmz", "dom0", "iot", "ops",
    "podman_vms", "router", "usr", "vm_dom0",
)
PROVENANCE_FIELDS = frozenset({"inventory_file", "inventory_dir"})


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def static_inventory(graph):
    """Convert a sealed dynamic graph to Ansible's static JSON/YAML shape."""
    if not isinstance(graph, dict) or not isinstance(graph.get("all"), dict):
        raise ValueError("inventory lacks its all group")
    metadata = graph.get("_meta")
    if not isinstance(metadata, dict) or set(metadata) != {"hostvars"} or not isinstance(metadata["hostvars"], dict):
        raise ValueError("inventory lacks complete host variables")
    hostvars = metadata["hostvars"]
    groups = {}
    referenced = set()
    for name, value in graph.items():
        if name == "_meta":
            continue
        if not isinstance(value, dict) or set(value) - {"children", "hosts", "vars"}:
            raise ValueError("inventory group contains unknown fields")
        output = {}
        for field in ("children", "hosts"):
            members = value.get(field, [])
            if not isinstance(members, list) or any(not isinstance(v, str) for v in members) or len(set(members)) != len(members):
                raise ValueError("inventory group members must be unique names")
            if field == "children":
                if any(child not in graph or child in {name, "all", "_meta"} for child in members):
                    raise ValueError("inventory group refers to an unknown or recursive child")
                output[field] = {child: {} for child in members}
            else:
                if any(host not in hostvars for host in members):
                    raise ValueError("inventory host has no variables")
                referenced.update(members)
                output[field] = {host: hostvars[host] for host in members}
        if "vars" in value:
            if not isinstance(value["vars"], dict):
                raise ValueError("inventory group variables must be an object")
            output["vars"] = value["vars"]
        groups[name] = output
    if referenced != set(hostvars):
        raise ValueError("inventory host variable coverage is incomplete")
    # Check cycles and references before giving the graph to Ansible.
    expanded_groups(graph, set(hostvars))
    result = groups.pop("all")
    result["children"] = groups
    return {"all": result}


def expanded_groups(graph, selected, *, ansible_output=False):
    """Return complete role membership for selected hosts, rejecting cycles."""
    result = {}
    visiting = set()

    def visit(name):
        if name in result:
            return result[name]
        # Ansible --list retains child references to empty groups but omits
        # their objects. The sealed input graph must still define every group.
        if ansible_output and name not in graph:
            return set()
        if name in visiting or name not in graph or name == "_meta":
            raise ValueError("inventory group graph is recursive or incomplete")
        visiting.add(name)
        value = graph[name]
        if not isinstance(value, dict):
            raise ValueError("inventory group is not an object")
        members = set(value.get("hosts", []))
        for child in value.get("children", []):
            members.update(visit(child))
        visiting.remove(name)
        result[name] = members & selected
        return result[name]

    for name in graph:
        if name != "_meta":
            visit(name)
    return {name: sorted(members) for name, members in sorted(result.items()) if members}


def normalized_inventory(graph, selected):
    selected = set(selected)
    hostvars = graph.get("_meta", {}).get("hostvars", {})
    if not selected or not selected <= set(hostvars):
        raise ValueError("effective inventory does not cover every instance host")
    variables = {}
    for host in sorted(selected):
        if not isinstance(hostvars[host], dict):
            raise ValueError("host variables must be an object")
        variables[host] = {k: v for k, v in hostvars[host].items() if k not in PROVENANCE_FIELDS}
    return {"hostvars": variables, "groups": expanded_groups(graph, selected, ansible_output=True)}


def compare_inventories(old, effective, projection):
    selected = set(projection["inventory"]["_meta"]["hostvars"])
    if set(effective.get("_meta", {}).get("hostvars", {})) != selected:
        raise ValueError("effective inventory contains extra or missing hosts")
    old_value = normalized_inventory(old, selected)
    effective_value = normalized_inventory(effective, selected)
    if old_value != effective_value:
        hosts = [host for host in sorted(selected) if old_value["hostvars"][host] != effective_value["hostvars"][host]]
        group_difference = old_value["groups"] != effective_value["groups"]
        raise ValueError("effective inventory differs: host variables=" + ",".join(hosts) + "; group membership=" + str(group_difference).lower())
    return {"schema_version": 1, "kind": "klokast.inventory-comparison.v1", "equal": True,
            "hosts": sorted(selected), "excluded_legacy_hosts": sorted(set(old["_meta"]["hostvars"]) - selected),
            "old_inventory_sha256": digest(old_value), "effective_inventory_sha256": digest(effective_value),
            "inventory_projection_sha256": digest(projection), "policy_groups": list(POLICY_GROUPS)}
