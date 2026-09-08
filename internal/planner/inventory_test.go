package planner

import (
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"reflect"
	"testing"

	"klokast-box/internal/contract"
)

func TestInventoryClosedHostsAndInstanceRunnerSelection(t *testing.T) {
	root := registryFixture(t, func(raw map[string]any) {
		raw["airunners"] = []any{"vultr-ops", "boxb-ops-airunner"}
		raw["boxes"].(map[string]any)["boxa"].(map[string]any)["substrate"] = map[string]any{"bridge-ports": map[string]any{"lan": []any{"eth2"}}}
	})
	result, err := Inventory(root, testEngine)
	if err != nil || !result.Valid {
		t.Fatalf("inventory failed: %#v %v", result, err)
	}
	p := result.Projection
	if !reflect.DeepEqual(p.Airunners, []string{"vultr-ops", "boxb-ops-airunner"}) || !reflect.DeepEqual(p.Boxes, []string{"boxa", "boxb"}) {
		t.Fatal("instance membership or runner priority changed")
	}
	hosts := p.Inventory["_meta"].(map[string]any)["hostvars"].(map[string]any)
	if len(hosts) != 14 {
		t.Fatalf("expected seven substrate roles per box, got %d", len(hosts))
	}
	for _, box := range p.Boxes {
		for _, role := range []string{"bootstrap", "dom0", "router", "bak", "dmz", "iot", "ops"} {
			if hosts[box+"-"+role].(map[string]any)["node_name"] != box {
				t.Fatal("missing or foreign host")
			}
		}
		if hosts[box+"-ops"].(map[string]any)["ops_airunner_enabled"] != (box == "boxb") {
			t.Fatal("runner selection did not come from instance identities")
		}
	}
	if _, exists := hosts["vultr-ops"]; exists {
		t.Fatal("cloud runner became a box execution target")
	}
	if hosts["boxa-bootstrap"].(map[string]any)["target_disk_device"] != "/dev/nvme0n1" {
		t.Fatal("bootstrap disk default changed")
	}
	if !reflect.DeepEqual(hosts["boxa-dom0"].(map[string]any)["dom0_bridge_physical_ports"], map[string][]string{"lan": {"eth2"}}) {
		t.Fatal("instance bridge binding was lost")
	}
	if !reflect.DeepEqual(p.Scopes, []string{"deployment.control_plane.airunners.boxb-ops-airunner", "deployment.control_plane.airunners.vultr-ops", "execution_inventory"}) {
		t.Fatal("inventory scope set is not complete and sorted")
	}
	content, _ := json.Marshal(p.Inventory)
	if len(p.InventorySHA256) != 64 || fmt.Sprintf("%x", sha256.Sum256(content)) != p.InventorySHA256 || len(result.Inputs) != 2 {
		t.Fatal("inventory lacks exact provenance")
	}
	again, err := Inventory(root, testEngine)
	if err != nil || !reflect.DeepEqual(result, again) {
		t.Fatal("inventory rendering is not deterministic")
	}
}

func TestInventoryRejectsPartialOrAmbiguousSource(t *testing.T) {
	for _, bad := range []string{"standby", "registry", "group-collision"} {
		t.Run(bad, func(t *testing.T) {
			root := registryFixture(t, func(raw map[string]any) {
				if bad == "standby" {
					delete(raw["controllers"].(map[string]any), "standby")
				} else if bad == "registry" {
					delete(raw, "inactive-apps")
				}
			})
			if bad == "group-collision" {
				snapshot, _, err := contract.Load(root, testEngine)
				if err != nil {
					t.Fatal(err)
				}
				snapshot.Instance.Boxes["all"] = snapshot.Instance.Boxes["boxa"]
				delete(snapshot.Instance.Boxes, "boxa")
				if _, err := ResolveInventory(snapshot); err == nil {
					t.Fatal("ambiguous group accepted")
				}
				return
			}
			result, err := Inventory(root, testEngine)
			if err != nil || result.Valid || len(result.Diagnostics) == 0 || result.Projection != nil {
				t.Fatalf("partial inventory accepted: %#v %v", result, err)
			}
		})
	}
}
