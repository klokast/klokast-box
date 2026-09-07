package authoritystate

import (
	"fmt"
	"strings"
)

const KindV4 = "klokast.authority-state.v4"
const RegistryGroupID = "registry-settings-v1"

// ValidateRegistryScopes closes the vocabulary and requires complete substrate
// ownership. The planner and root reader additionally compare the exact private
// renderer and original signed adoption Plan scope sets.
func ValidateRegistryScopes(scopes, boxes []string) error {
	wantedBoxes := map[string]bool{}
	for _, box := range boxes {
		for _, field := range []string{"dom0_bridge_ports", "dhcp_reservations", "shared_guests"} {
			wantedBoxes["boxes."+box+"."+field] = false
		}
	}
	apps, enabled := map[string]bool{}, map[string]bool{}
	for i, scope := range scopes {
		if i > 0 && scopes[i-1] >= scope {
			return fmt.Errorf("registry scopes must be sorted and unique")
		}
		if _, ok := wantedBoxes[scope]; ok {
			wantedBoxes[scope] = true
			continue
		}
		parts := strings.Split(scope, ".")
		if len(parts) < 3 || parts[0] != "apps" || !boxIDPattern.MatchString(parts[1]) {
			return fmt.Errorf("unknown registry scope")
		}
		if len(parts) == 4 && parts[2] == "data" && boxIDPattern.MatchString(parts[3]) {
			continue
		}
		if len(parts) != 3 {
			return fmt.Errorf("registry app field scope must be complete")
		}
		switch parts[2] {
		case "enabled":
			enabled[parts[1]] = true
		case "placement", "resources", "runtime_state", "ingress_mode", "isolation", "devices", "app_vms", "users", "ephemeral":
		default:
			return fmt.Errorf("unknown registry app field")
		}
		apps[parts[1]] = true
	}
	for _, present := range wantedBoxes {
		if !present {
			return fmt.Errorf("registry substrate scope set is partial")
		}
	}
	for app := range apps {
		if !enabled[app] {
			return fmt.Errorf("registry app lacks its disabled state scope")
		}
	}
	return nil
}

func validateV4(state StateV2) error {
	if state.SchemaVersion != 4 || (state.PriorStateKind != KindV3 && state.PriorStateKind != KindV4) {
		return fmt.Errorf("Authority State v4 has an invalid version or prior kind")
	}
	anchor := state.RegistryAdoption
	if anchor == nil || !digest(anchor.IntentSHA256) || !digest(anchor.PlanSHA256) || len(anchor.Nonce) < 12 || len(anchor.Nonce) > 128 {
		return fmt.Errorf("registry adoption binding is incomplete")
	}
	for _, c := range anchor.Nonce {
		if !((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '_' || c == '-') {
			return fmt.Errorf("registry adoption nonce is invalid")
		}
	}
	base := state
	base.SettingGroups = nil
	var registry *SettingGroup
	boxes := []string{}
	for i, group := range state.SettingGroups {
		if i > 0 && state.SettingGroups[i-1].ID >= group.ID {
			return fmt.Errorf("authority groups are not sorted and unique")
		}
		if group.Source != InstanceAuthority {
			return fmt.Errorf("registry adoption requires all prior sources adopted")
		}
		if group.ID == RegistryGroupID {
			copy := group
			registry = &copy
			continue
		}
		if strings.HasPrefix(group.ID, BoxConnectivityPrefix) {
			boxes = append(boxes, strings.TrimPrefix(group.ID, BoxConnectivityPrefix))
		}
		base.SettingGroups = append(base.SettingGroups, group)
	}
	if registry == nil || len(boxes) != 2 {
		return fmt.Errorf("registry adoption requires the closed two-box group set")
	}
	if err := ValidateRegistryScopes(registry.Scopes, boxes); err != nil {
		return err
	}
	if state.PriorStateKind == KindV3 && (state.SignedIntentSHA256 != anchor.IntentSHA256 || state.TransitionID != anchor.Nonce) {
		return fmt.Errorf("initial registry adoption binding differs from transition")
	}
	want, err := HashV2(state)
	if err != nil || want != state.AuthorityStateSHA256 {
		return fmt.Errorf("Authority State v4 hash does not match canonical content")
	}
	base.RegistryAdoption = nil
	base.Kind, base.SchemaVersion, base.PriorStateKind = KindV3, 3, KindV3
	base.AuthorityStateSHA256, _ = HashV2(base)
	return ValidateCurrent(base)
}
