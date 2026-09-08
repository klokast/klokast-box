package authoritystate

import (
	"fmt"
	"regexp"
	"strings"
)

const KindV5 = "klokast.authority-state.v5"
const InventoryGroupID = "execution-inventory-v1"
const InventoryScope = "execution_inventory"
const RunnerScopePrefix = "deployment.control_plane.airunners."

var inventoryRunnerPattern = regexp.MustCompile(`^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$`)

func ValidateInventoryScopes(scopes, boxes []string) error {
	if len(scopes) < 2 {
		return fmt.Errorf("inventory scopes must contain execution inventory and the complete sorted runner set")
	}
	inventory := false
	for i, scope := range scopes {
		if i > 0 && scopes[i-1] >= scope {
			return fmt.Errorf("inventory scopes must be sorted and unique")
		}
		if scope == InventoryScope {
			inventory = true
			continue
		}
		if !strings.HasPrefix(scope, RunnerScopePrefix) {
			return fmt.Errorf("unknown inventory source scope")
		}
		runner := strings.TrimPrefix(scope, RunnerScopePrefix)
		if !inventoryRunnerPattern.MatchString(runner) || len(runner) > 63 {
			return fmt.Errorf("invalid inventory runner identity")
		}
		knownBox := false
		for _, box := range boxes {
			knownBox = knownBox || runner == box+"-ops-airunner"
			if runner == box+"-ops" {
				return fmt.Errorf("inventory runner cannot be a box controller")
			}
		}
		if !knownBox && !strings.HasSuffix(runner, "-ops") {
			return fmt.Errorf("inventory runner has an unsupported runtime role")
		}
	}
	if !inventory {
		return fmt.Errorf("inventory source scope is missing")
	}
	return nil
}

func validateV5(state StateV2) error {
	if state.SchemaVersion != 5 || (state.PriorStateKind != KindV4 && state.PriorStateKind != KindV5) {
		return fmt.Errorf("Authority State v5 has an invalid version or prior kind")
	}
	anchor := state.InventoryAdoption
	if anchor == nil || !digest(anchor.IntentSHA256) || !digest(anchor.PlanSHA256) || len(anchor.Nonce) < 12 || len(anchor.Nonce) > 128 {
		return fmt.Errorf("inventory adoption binding is incomplete")
	}
	for _, c := range anchor.Nonce {
		if !((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '_' || c == '-') {
			return fmt.Errorf("inventory adoption nonce is invalid")
		}
	}
	base := state
	base.SettingGroups = nil
	var inventory *SettingGroup
	boxes := []string{}
	for i, group := range state.SettingGroups {
		if i > 0 && state.SettingGroups[i-1].ID >= group.ID || group.Source != InstanceAuthority {
			return fmt.Errorf("inventory adoption requires sorted complete instance-owned groups")
		}
		if group.ID == InventoryGroupID {
			copy := group
			inventory = &copy
			continue
		}
		if strings.HasPrefix(group.ID, BoxConnectivityPrefix) {
			boxes = append(boxes, strings.TrimPrefix(group.ID, BoxConnectivityPrefix))
		}
		base.SettingGroups = append(base.SettingGroups, group)
	}
	if inventory == nil || len(boxes) != 2 {
		return fmt.Errorf("inventory adoption requires its group and two boxes")
	}
	if err := ValidateInventoryScopes(inventory.Scopes, boxes); err != nil {
		return err
	}
	if state.PriorStateKind == KindV4 && (state.SignedIntentSHA256 != anchor.IntentSHA256 || state.TransitionID != anchor.Nonce) {
		return fmt.Errorf("initial inventory adoption differs from its transition")
	}
	want, err := HashV2(state)
	if err != nil || want != state.AuthorityStateSHA256 {
		return fmt.Errorf("Authority State v5 hash does not match canonical content")
	}
	base.InventoryAdoption = nil
	base.Kind, base.SchemaVersion, base.PriorStateKind = KindV4, 4, KindV4
	base.AuthorityStateSHA256, _ = HashV2(base)
	return ValidateCurrent(base)
}
