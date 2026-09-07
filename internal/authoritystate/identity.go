package authoritystate

import (
	"encoding/json"
	"fmt"
	"os"
	"reflect"

	"klokast-box/internal/strictjson"
)

const KindV3 = "klokast.authority-state.v3"
const ControllerIdentityGroupID = "controller-identity-v1"

var ControllerIdentityScopes = []string{
	"controller_ha.controllers[0].box",
	"controller_ha.controllers[0].hostname",
	"controller_ha.controllers[1].box",
	"controller_ha.controllers[1].hostname",
	"deployment.control_plane.controller",
}

type IdentityAdoption struct {
	Nonce        string `json:"nonce"`
	IntentSHA256 string `json:"intent_sha256"`
	PlanSHA256   string `json:"plan_sha256"`
}

// LoadCurrent keeps the v2 contract closed and accepts its explicit v3 successor.
func LoadCurrent(path string) (StateV2, error) {
	info, err := os.Lstat(path)
	if err != nil {
		return StateV2{}, err
	}
	if !info.Mode().IsRegular() || info.Size() <= 0 || info.Size() > 64*1024 {
		return StateV2{}, fmt.Errorf("authority state must be a bounded regular file")
	}
	content, err := os.ReadFile(path)
	if err != nil {
		return StateV2{}, err
	}
	var state StateV2
	if err := strictjson.Decode(content, &state, true); err != nil {
		return StateV2{}, err
	}
	if err := rejectV3FieldOnV2(content, state); err != nil {
		return StateV2{}, err
	}
	if err := ValidateCurrent(state); err != nil {
		return StateV2{}, err
	}
	return state, nil
}

func rejectV3FieldOnV2(content []byte, state StateV2) error {
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(content, &fields); err != nil {
		return err
	}
	if _, present := fields["registry_adoption"]; present && state.Kind != KindV4 {
		return fmt.Errorf("older Authority State cannot contain a v4 registry adoption field, including null")
	}
	if _, present := fields["controller_identity_adoption"]; present && state.Kind == KindV2 {
		return fmt.Errorf("Authority State v2 cannot contain a v3 adoption field, including null")
	}
	return nil
}

func ValidateCurrent(state StateV2) error {
	if state.Kind == KindV4 {
		return validateV4(state)
	}
	if state.RegistryAdoption != nil {
		return fmt.Errorf("older Authority State cannot contain registry adoption")
	}
	if state.Kind == KindV2 {
		return ValidateV2(state)
	}
	if state.Kind != KindV3 || state.SchemaVersion != 3 || (state.PriorStateKind != KindV2 && state.PriorStateKind != KindV3) {
		return fmt.Errorf("authority state kind, version, or prior kind is invalid")
	}
	adoption := state.ControllerIdentityAdoption
	if adoption == nil || len(adoption.Nonce) < 12 || len(adoption.Nonce) > 128 || !digest(adoption.IntentSHA256) || !digest(adoption.PlanSHA256) {
		return fmt.Errorf("controller identity adoption binding is incomplete")
	}
	for _, c := range adoption.Nonce {
		if !((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '_' || c == '-') {
			return fmt.Errorf("controller identity adoption nonce is invalid")
		}
	}
	base := state
	base.SettingGroups = nil
	seen := false
	for i, group := range state.SettingGroups {
		if i > 0 && state.SettingGroups[i-1].ID >= group.ID {
			return fmt.Errorf("authority groups are not sorted and unique")
		}
		if group.ID == ControllerIdentityGroupID {
			if seen || group.Source != InstanceAuthority || !reflect.DeepEqual(group.Scopes, ControllerIdentityScopes) {
				return fmt.Errorf("controller identity group is partial or invalid")
			}
			seen = true
		} else {
			base.SettingGroups = append(base.SettingGroups, group)
		}
	}
	if !seen || len(base.SettingGroups) != 3 {
		return fmt.Errorf("controller identity adoption requires two boxes and Tailnet")
	}
	if state.PriorStateKind == KindV2 {
		if state.SignedIntentSHA256 != adoption.IntentSHA256 || state.TransitionID != adoption.Nonce {
			return fmt.Errorf("initial controller identity adoption binding differs from transition")
		}
		for _, group := range base.SettingGroups {
			if group.Source != InstanceAuthority {
				return fmt.Errorf("controller identity adoption requires all connectivity sources adopted")
			}
		}
	}
	want, err := HashV2(state)
	if err != nil || state.AuthorityStateSHA256 != want {
		return fmt.Errorf("authority state v3 hash does not match canonical content")
	}
	base.Kind, base.SchemaVersion, base.PriorStateKind = KindV2, 2, KindV2
	base.ControllerIdentityAdoption = nil
	base.AuthorityStateSHA256, _ = HashV2(base)
	return ValidateV2(base)
}

func CurrentGroupSource(state StateV2, groupID string) (string, error) {
	if err := ValidateCurrent(state); err != nil {
		return "", err
	}
	for _, group := range state.SettingGroups {
		if group.ID == groupID {
			return group.Source, nil
		}
	}
	return "", fmt.Errorf("authority state does not contain group %q", groupID)
}
