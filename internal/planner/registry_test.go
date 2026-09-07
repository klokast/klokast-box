package planner

import (
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"klokast-box/internal/contract"
)

func registryFixture(t *testing.T, change func(map[string]any)) string {
	t.Helper()
	return prepareTwoBoxInstance(t, func(root string) {
		path := filepath.Join(root, contract.InstancePath)
		content, err := os.ReadFile(path)
		if err != nil {
			t.Fatal(err)
		}
		var raw map[string]any
		if err := json.Unmarshal(content, &raw); err != nil {
			t.Fatal(err)
		}
		for _, box := range raw["boxes"].(map[string]any) {
			box.(map[string]any)["substrate"] = map[string]any{}
		}
		raw["inactive-apps"] = map[string]any{}
		change(raw)
		content, err = json.Marshal(raw)
		if err != nil {
			t.Fatal(err)
		}
		writeFile(t, path, string(content))
	})
}

func TestRegistryPreservesSavedFieldsAndDefaultOwnership(t *testing.T) {
	root := registryFixture(t, func(raw map[string]any) {
		var inactive map[string]any
		if err := json.Unmarshal([]byte(`{
		 "unavailable-app": {"placement":{"builder":""},"resources":{"saved-option":false},"ephemeral":{"expires-at":"","privileged-approval":false,"cleanup-required":true}},
		 "music": {"placement":{"boxes":["boxb"]},"devices":{"local-audio-endpoint":{"boxb":{"hostname":"speaker","ipv4-address":"192.0.2.12","mac":"02:00:00:00:00:12"}}},"runtime-state":"stopped"},
		 "openclaw": {"placement":{"primary":"boxa","secondary":""},"users":[{"slug":"tester","system-user":"tester","tailscale-login":"tester@example.com","vm-ipv4-address":"192.0.2.13"}],"app-vms":{},"ingress-mode":"none","isolation":"dedicated"}
		}`), &inactive); err != nil {
			t.Fatal(err)
		}
		raw["inactive-apps"] = inactive
		raw["apps"] = map[string]any{"music": map[string]any{"desired-state": "absent", "data": map[string]any{"library": map[string]any{"box": "boxb", "retention": "preserve"}}}}
		raw["boxes"].(map[string]any)["boxa"].(map[string]any)["substrate"] = map[string]any{
			"bridge-ports":      map[string]any{"lan": []any{"eth2"}},
			"dhcp-reservations": []any{}, "shared-guests": map[string]any{"iot": map[string]any{"runtime-state": "stopped"}},
		}
	})
	result, err := Registry(root, testEngine)
	if err != nil || !result.Valid {
		t.Fatalf("renderer failed: %#v %v", result, err)
	}
	apps := result.Projection.Registry["apps"].(map[string]any)
	var expected map[string]any
	if err := json.Unmarshal([]byte(`{
	 "unavailable-app":{"enabled":false,"placement":{"builder_box":""},"resources":{"saved-option":false},"ephemeral":{"expires_at":"","privileged_approval":false,"cleanup_required":true}},
	 "music":{"enabled":false,"placement":{"boxes":["boxb"]},"devices":{"local-audio-endpoint":{"boxb":{"hostname":"speaker","ipv4_address":"192.0.2.12","mac":"02:00:00:00:00:12"}}},"runtime_state":"stopped"},
	 "openclaw":{"enabled":false,"placement":{"active_master":"boxa","passive_backup":""},"users":[{"slug":"tester","system_user":"tester","tailscale_login":"tester@example.com","vm_ipv4_address":"192.0.2.13"}],"app_vms":{},"ingress_mode":"none","isolation":"dedicated"}
	}`), &expected); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(apps, expected) {
		t.Fatalf("saved fields changed: %#v", apps)
	}
	boxes := result.Projection.Registry["boxes"].(map[string]any)
	if len(boxes["boxb"].(map[string]any)) != 1 {
		t.Fatal("renderer materialized omitted defaults")
	}
	if reservations := boxes["boxa"].(map[string]any)["dhcp_reservations"]; !reflect.DeepEqual(reservations, []any{}) {
		t.Fatal("explicit empty reservations lost")
	}
	scopes := strings.Join(result.Projection.Scopes, "\n")
	for _, scope := range []string{"apps.music.data.library", "apps.unavailable-app.enabled", "boxes.boxb.dhcp_reservations", "boxes.boxb.dom0_bridge_ports", "boxes.boxb.shared_guests"} {
		if !strings.Contains(scopes, scope) {
			t.Fatalf("missing scope %s", scope)
		}
	}
	if len(result.Inputs) != 2 || len(result.Projection.RegistrySHA256) != 64 {
		t.Fatal("missing provenance")
	}
	second, err := Registry(root, testEngine)
	if err != nil || !reflect.DeepEqual(result, second) {
		t.Fatal("registry output is not deterministic")
	}
}

func TestRegistryRequiresCompleteInputs(t *testing.T) {
	for _, missing := range []string{"inactive-apps", "substrate", "standby", "present-app"} {
		t.Run(missing, func(t *testing.T) {
			root := registryFixture(t, func(raw map[string]any) {
				switch missing {
				case "inactive-apps":
					delete(raw, missing)
				case "substrate":
					delete(raw["boxes"].(map[string]any)["boxb"].(map[string]any), missing)
				case "standby":
					delete(raw["controllers"].(map[string]any), missing)
				case "present-app":
					raw["apps"] = map[string]any{"music": map[string]any{"desired-state": "present", "placement": map[string]any{"mode": "multi-box", "boxes": []any{"boxb"}}}}
				}
			})
			result, err := Registry(root, testEngine)
			if err != nil || result.Valid || len(result.Diagnostics) == 0 || result.Projection != nil {
				t.Fatalf("incomplete registry accepted: %#v %v", result, err)
			}
		})
	}
}
