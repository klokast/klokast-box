package planner

import (
	"crypto/sha1"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"sort"
	"strings"

	"klokast-box/internal/contract"
)

// InventoryProjection is the Ansible host graph before upstream group policy
// is loaded. It contains no legacy host variables or observed machines.
type InventoryProjection struct {
	Inventory       map[string]any `json:"inventory"`
	InventorySHA256 string         `json:"inventory_sha256"`
	Boxes           []string       `json:"boxes"`
	Airunners       []string       `json:"airunners"`
	Scopes          []string       `json:"scopes"`
}

type InventoryResult struct {
	SchemaVersion int                   `json:"schema_version"`
	Kind          string                `json:"kind"`
	Valid         bool                  `json:"valid"`
	Engine        Engine                `json:"engine"`
	Repository    Repository            `json:"repository"`
	Inputs        []InputDigest         `json:"inputs"`
	Projection    *InventoryProjection  `json:"projection,omitempty"`
	Diagnostics   []contract.Diagnostic `json:"diagnostics"`
}

func Inventory(path string, engine contract.Engine) (InventoryResult, error) {
	result := InventoryResult{SchemaVersion: 1, Kind: "klokast.inventory.v1",
		Engine: Engine{Repository: engine.Repository, Ref: engine.Ref, Commit: engine.Commit},
		Inputs: []InputDigest{}, Diagnostics: []contract.Diagnostic{}}
	snapshot, report, err := contract.Load(path, engine)
	if err != nil {
		return result, err
	}
	if !report.Valid {
		result.Diagnostics = report.Diagnostics
		return result, nil
	}
	projection, err := ResolveInventory(snapshot)
	if err != nil {
		result.Diagnostics = append(result.Diagnostics, contract.Diagnostic{Path: contract.InstancePath, Code: "inventory.incomplete", Message: err.Error()})
		return result, nil
	}
	repository, err := inspectRepository(snapshot.Root)
	if err != nil {
		return result, err
	}
	second, checked, err := contract.Load(path, engine)
	if err != nil || !checked.Valid || !sameInputs(snapshot.Inputs, second.Inputs) {
		return result, fmt.Errorf("inventory inputs changed during rendering")
	}
	secondRepository, err := inspectRepository(snapshot.Root)
	if err != nil || !equivalent(repository, secondRepository) {
		return result, fmt.Errorf("inventory repository changed during rendering")
	}
	result.Valid, result.Repository = true, repository
	result.Inputs, result.Projection = inputDigests(snapshot.Inputs), &projection
	return result, nil
}

// ResolveInventory preserves the host roles and defaults from the existing
// one-box generator. Caller-supplied boxes, suffixes, disks, and variables are
// deliberately absent from this interface.
func ResolveInventory(snapshot contract.Snapshot) (InventoryProjection, error) {
	result := InventoryProjection{Boxes: sortedKeys(snapshot.Instance.Boxes),
		Airunners: append([]string{}, snapshot.Instance.Airunners...), Scopes: []string{"execution_inventory"}}
	if len(result.Boxes) != 2 || snapshot.Instance.Controllers.Standby == "" {
		return result, fmt.Errorf("inventory source requires two boxes and a configured controller pair")
	}
	if _, err := ResolveRegistry(snapshot); err != nil {
		return result, fmt.Errorf("inventory source requires complete registry inputs: %w", err)
	}
	groups := map[string]any{}
	hostvars := map[string]any{}
	children := map[string][]string{}
	hosts := map[string][]string{}
	claimed := map[string]bool{"all": true, "ungrouped": true, "_meta": true}
	addChild := func(parent, child string) { children[parent] = append(children[parent], child) }
	for _, name := range []string{"bootstrap", "dom0", "vm_dom0", "router", "backend", "dmz", "iot", "usr", "ops", "control_vms", "podman_vms"} {
		children[name] = []string{}
		claimed[name] = true
		addChild("all", name)
	}
	addChild("control_vms", "ops")
	for _, name := range []string{"backend", "dmz", "iot", "usr"} {
		addChild("podman_vms", name)
	}
	suffix := snapshot.Instance.Tailscale.DNSName
	for _, box := range result.Boxes {
		prefix := strings.ReplaceAll(box, "-", "_")
		for _, name := range []string{prefix, prefix + "_bootstrap", prefix + "_dom0", prefix + "_router", prefix + "_backend", prefix + "_dmz", prefix + "_iot", prefix + "_ops"} {
			if claimed[name] {
				return result, fmt.Errorf("box identity collides with inventory group %s", name)
			}
			claimed[name] = true
		}
		// This is the existing non-secret MAC octet derivation, not a security hash.
		mac := fmt.Sprintf("%x", sha1.Sum([]byte(box)))[:2]
		addChild("all", prefix)
		for _, role := range []struct{ group, suffix string }{{"bootstrap", "bootstrap"}, {"dom0", "dom0"}, {"router", "router"}, {"backend", "bak"}, {"dmz", "dmz"}, {"iot", "iot"}, {"ops", "ops"}} {
			group := prefix + "_" + role.group
			host := box + "-" + role.suffix
			addChild("all", group)
			addChild(prefix, group)
			addChild(role.group, group)
			if role.group == "dom0" {
				addChild("vm_dom0", group)
			}
			hosts[group] = []string{host}
			vars := map[string]any{"node_name": box}
			switch role.group {
			case "bootstrap":
				vars["node_domain_role"], vars["node_hostname"] = "dom0", box+"-dom0"
				vars["ansible_host"] = "{{ bootstrap_tailscale_hostname }}." + suffix
				vars["ansible_ssh_common_args"] = "-o StrictHostKeyChecking=accept-new -o UserKnownHostsFile={{ bootstrap_known_hosts_file }}"
				vars["ansible_python_interpreter"], vars["target_disk_device"] = "/usr/bin/python3", "/dev/nvme0n1"
			case "dom0":
				vars["node_domain_role"], vars["node_xen_mac_octet"] = "dom0", mac
				if ports := snapshot.Instance.Boxes[box].Substrate.BridgePorts; len(ports) != 0 {
					vars["dom0_bridge_physical_ports"] = ports
				}
			case "ops":
				enabled := false
				for _, runner := range result.Airunners {
					enabled = enabled || runner == box+"-ops-airunner"
				}
				vars["ops_airunner_enabled"] = enabled
			}
			hostvars[host] = vars
		}
	}
	for name, values := range children {
		sort.Strings(values)
		groups[name] = map[string]any{"children": values}
	}
	for name, values := range hosts {
		groups[name] = map[string]any{"hosts": values}
	}
	groups["all"].(map[string]any)["vars"] = map[string]any{"platform_magicdns_suffix": suffix}
	groups["_meta"] = map[string]any{"hostvars": hostvars}
	for _, runner := range result.Airunners {
		result.Scopes = append(result.Scopes, "deployment.control_plane.airunners."+runner)
	}
	sort.Strings(result.Scopes)
	result.Inventory = groups
	content, err := json.Marshal(groups)
	if err != nil {
		return result, err
	}
	result.InventorySHA256 = fmt.Sprintf("%x", sha256.Sum256(content))
	return result, nil
}
