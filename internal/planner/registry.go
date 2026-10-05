package planner

import (
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"sort"

	"klokast-box/internal/contract"
)

// RegistryProjection contains only instance-derived values. It never reads a
// legacy file or an application manifest. Disabled configurations without a
// current manifest must survive the migration too.
type RegistryProjection struct {
	Registry       map[string]any `json:"registry"`
	RegistrySHA256 string         `json:"registry_sha256"`
	Scopes         []string       `json:"scopes"`
}

type RegistryResult struct {
	SchemaVersion int                   `json:"schema_version"`
	Kind          string                `json:"kind"`
	Valid         bool                  `json:"valid"`
	Engine        Engine                `json:"engine"`
	Repository    Repository            `json:"repository"`
	Inputs        []InputDigest         `json:"inputs"`
	Projection    *RegistryProjection   `json:"projection,omitempty"`
	Diagnostics   []contract.Diagnostic `json:"diagnostics"`
}

// Registry checks the complete private input twice and reports its provenance.
// Publication and active-source checks belong to the controller's root reader.
func Registry(path string, engine contract.Engine) (RegistryResult, error) {
	result := RegistryResult{SchemaVersion: 1, Kind: "klokast.registry.v1",
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
	projection, err := ResolveRegistry(snapshot)
	if err != nil {
		result.Diagnostics = append(result.Diagnostics, contract.Diagnostic{Path: contract.InstancePath, Code: "registry.incomplete", Message: err.Error()})
		return result, nil
	}
	repository, err := inspectRepository(snapshot.Root)
	if err != nil {
		return result, err
	}
	second, secondReport, err := contract.Load(path, engine)
	if err != nil || !secondReport.Valid || !sameInputs(snapshot.Inputs, second.Inputs) {
		return result, fmt.Errorf("registry inputs changed during rendering")
	}
	secondRepository, err := inspectRepository(snapshot.Root)
	if err != nil || !equivalent(repository, secondRepository) {
		return result, fmt.Errorf("registry repository changed during rendering")
	}
	result.Valid, result.Repository = true, repository
	result.Inputs, result.Projection = inputDigests(snapshot.Inputs), &projection
	return result, nil
}

// ResolveRegistry consumes the checked raw document: typed struct round trips
// would lose explicit empty strings, empty collections, and false selections.
func ResolveRegistry(snapshot contract.Snapshot) (RegistryProjection, error) {
	result := RegistryProjection{Scopes: []string{}}
	var raw map[string]any
	for _, input := range snapshot.Inputs {
		if input.Path == contract.InstancePath {
			if err := json.Unmarshal(input.Content, &raw); err != nil {
				return result, err
			}
		}
	}
	if raw == nil {
		return result, fmt.Errorf("registry rendering requires checked instance bytes")
	}
	inactive, _ := raw["inactive-apps"].(map[string]any)
	rawBoxes, _ := raw["boxes"].(map[string]any)
	boxes, apps := map[string]any{}, map[string]any{}
	for _, id := range sortedKeys(snapshot.Instance.Boxes) {
		box, _ := rawBoxes[id].(map[string]any)
		substrate, _ := box["substrate"].(map[string]any)
		access := accessForCapabilities(snapshot.Instance.Boxes[id].Connectivity)
		value := map[string]any{"access": map[string]any{
			"available_capabilities": access.LegacyAvailable, "enabled_capabilities": access.Enabled,
			"prohibited_capabilities": access.Prohibited}}
		for _, field := range []struct{ instance, legacy string }{
			{"bridge-ports", "dom0_bridge_ports"}, {"dhcp-reservations", "dhcp_reservations"}, {"shared-guests", "shared_guests"},
		} {
			result.Scopes = append(result.Scopes, "boxes."+id+"."+field.legacy)
			if saved, present := substrate[field.instance]; present {
				// Only schema field names are translated. Box/resource IDs are data.
				switch field.instance {
				case "dhcp-reservations":
					saved = registryList(saved, map[string]string{"ipv4-address": "ipv4_address"})
				case "shared-guests":
					saved = registryBindings(saved, 1, map[string]string{"runtime-state": "runtime_state"})
				}
				value[field.legacy] = saved
			}
		}
		boxes[id] = value
	}
	for _, id := range sortedKeys(inactive) {
		value := map[string]any{"enabled": false}
		saved, _ := inactive[id].(map[string]any)
		for key, item := range saved {
			legacy := key
			switch key {
			case "placement":
				item = registryRename(item, map[string]string{"primary": "active_master", "secondary": "passive_backup", "builder": "builder_box"})
			case "runtime-state":
				legacy = "runtime_state"
			case "ingress-mode":
				legacy = "ingress_mode"
			case "app-vms":
				legacy = "app_vms"
				item = registryBindings(item, 2, map[string]string{"vm-ipv4-address": "vm_ipv4_address"})
			case "devices":
				item = registryBindings(item, 2, map[string]string{"ipv4-address": "ipv4_address"})
			case "users":
				item = registryList(item, map[string]string{"system-user": "system_user", "tailscale-login": "tailscale_login", "vm-ipv4-address": "vm_ipv4_address"})
			case "ephemeral":
				item = registryRename(item, map[string]string{"privileged-approval": "privileged_approval", "cleanup-required": "cleanup_required", "expires-at": "expires_at"})
			case "resources", "isolation":
			default:
				return result, fmt.Errorf("registry renderer has no mapping for inactive field %s", key)
			}
			value[legacy] = item
		}
		for key := range value {
			result.Scopes = append(result.Scopes, "apps."+id+"."+key)
		}
		apps[id] = value
	}
	for _, id := range sortedKeys(snapshot.Instance.Apps) {
		app := snapshot.Instance.Apps[id]
		if app.DesiredState == "present" {
			manifests, err := loadManifests()
			if err != nil {
				return result, err
			}
			entry := map[string]any{"enabled": true, "placement": legacyPlacement(resolvePlacement(*app.Placement)), "resources": resourceMapForManifest(manifests[id], resourceBindings(app.Features))}
			for _, feature := range resourceBindings(app.Features) {
				if feature.ID == "public-ingress" {
					entry["ingress_mode"] = feature.Value
				}
			}
			apps[id] = entry
		} else if _, saved := apps[id]; !saved {
			apps[id] = map[string]any{"enabled": false}
		}
		for _, data := range sortedKeys(app.Data) {
			result.Scopes = append(result.Scopes, "apps."+id+".data."+data)
		}
	}
	result.Registry = map[string]any{"schema_version": 1, "boxes": boxes, "apps": apps}
	content, err := json.Marshal(result.Registry)
	if err != nil {
		return result, err
	}
	result.RegistrySHA256 = fmt.Sprintf("%x", sha256.Sum256(content))
	sort.Strings(result.Scopes)
	return result, nil
}

func registryRename(value any, names map[string]string) map[string]any {
	object, _ := value.(map[string]any)
	result := map[string]any{}
	for key, item := range object {
		if mapped, ok := names[key]; ok {
			key = mapped
		}
		result[key] = item
	}
	return result
}

func registryList(value any, names map[string]string) []any {
	items, _ := value.([]any)
	result := []any{}
	for _, item := range items {
		result = append(result, registryRename(item, names))
	}
	return result
}

func registryBindings(value any, depth int, names map[string]string) any {
	if depth == 0 {
		return registryRename(value, names)
	}
	object, _ := value.(map[string]any)
	result := map[string]any{}
	for key, item := range object {
		result[key] = registryBindings(item, depth-1, names)
	}
	return result
}
