package deploymentplan

import (
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"

	"klokast-box/internal/authoritystate"
	"klokast-box/internal/contract"
	"klokast-box/internal/doctor"
)

func TestRegistryAdoptionAndFinalPlannerTargets(t *testing.T) {
	instance := prepareInstance(t)
	path := filepath.Join(instance, contract.InstancePath)
	content, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var desired map[string]any
	if err := json.Unmarshal(content, &desired); err != nil {
		t.Fatal(err)
	}
	desired["controllers"].(map[string]any)["standby"] = "boxb"
	for _, box := range desired["boxes"].(map[string]any) {
		box.(map[string]any)["substrate"] = map[string]any{}
	}
	desired["inactive-apps"] = map[string]any{"nextcloud": map[string]any{"placement": map[string]any{"primary": "", "secondary": ""}, "resources": map[string]any{"cloudflare-tunnel-egress": false}}}
	writeFile(t, instance, contract.InstancePath, string(canonicalTestJSON(t, desired)))
	runGit(t, instance, "add", contract.InstancePath)
	runGit(t, instance, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "complete registry")
	options := compatibilityOptions(t, instance)
	content, _ = os.ReadFile(options.CompatibilityControllerHA)
	if err := os.WriteFile(options.CompatibilityControllerHA, append(content, []byte("  - box: boxb\n    hostname: boxb-ops\n")...), 0600); err != nil {
		t.Fatal(err)
	}
	content, _ = os.ReadFile(options.ObservationPath)
	var observation doctor.Observation
	if err := json.Unmarshal(content, &observation); err != nil {
		t.Fatal(err)
	}
	observation.TailnetMachines = append(observation.TailnetMachines, doctor.TailnetMachine{Hostname: "boxb-ops", Online: true, Tags: []string{"tag:ops"}})
	sort.Slice(observation.TailnetMachines, func(i, j int) bool {
		return observation.TailnetMachines[i].Hostname < observation.TailnetMachines[j].Hostname
	})
	for i := range observation.Boxes {
		box := &observation.Boxes[i]
		if box.HostnamePrefix == "boxb" {
			box.RunningGuests = append(box.RunningGuests, "ops")
			sort.Strings(box.RunningGuests)
			box.ConfiguredGuests = append(box.ConfiguredGuests, "ops")
			sort.Strings(box.ConfiguredGuests)
			box.AutostartGuests = append(box.AutostartGuests, "ops")
			sort.Strings(box.AutostartGuests)
		}
	}
	value := map[string]any{}
	if err := json.Unmarshal(canonicalTestJSON(t, observation), &value); err != nil {
		t.Fatal(err)
	}
	delete(value, "generation_sha256")
	observation.GenerationSHA256 = fmt.Sprintf("%x", sha256.Sum256([]byte(strings.TrimSuffix(string(canonicalTestJSON(t, value)), "\n"))))
	if err := os.WriteFile(options.ObservationPath, canonicalTestJSON(t, observation), 0600); err != nil {
		t.Fatal(err)
	}
	state, err := authoritystate.LoadV2(options.AuthorityState)
	if err != nil {
		t.Fatal(err)
	}
	for i := range state.SettingGroups {
		state.SettingGroups[i].Source = authoritystate.InstanceAuthority
	}
	state.Kind, state.SchemaVersion, state.PriorStateKind = authoritystate.KindV3, 3, authoritystate.KindV2
	state.TransitionID, state.SignedIntentSHA256 = "identity-test-anchor", strings.Repeat("c", 64)
	state.ControllerIdentityAdoption = &authoritystate.IdentityAdoption{Nonce: state.TransitionID, IntentSHA256: state.SignedIntentSHA256, PlanSHA256: strings.Repeat("d", 64)}
	state.SettingGroups = append(state.SettingGroups, authoritystate.SettingGroup{ID: authoritystate.ControllerIdentityGroupID, Scopes: authoritystate.ControllerIdentityScopes, Source: authoritystate.InstanceAuthority})
	sort.Slice(state.SettingGroups, func(i, j int) bool { return state.SettingGroups[i].ID < state.SettingGroups[j].ID })
	state.AuthorityStateSHA256, _ = authoritystate.HashV2(state)
	if err := os.WriteFile(options.AuthorityState, canonicalTestJSON(t, state), 0600); err != nil {
		t.Fatal(err)
	}
	before, err := Build(options, testEngine)
	if err != nil || before.Deployable {
		t.Fatalf("implicit registry migration accepted: %v %#v", err, before.Refusals)
	}
	options.MigrationTarget = "registry"
	plan, err := Build(options, testEngine)
	if err != nil || !plan.Deployable {
		t.Fatalf("registry adoption failed: %v %#v %#v", err, plan.Refusals, plan.Diagnostics)
	}
	group := actionGroup(plan, authoritystate.RegistryGroupID)
	if group.Executor != "registry_source_v1" || group.Operation != "adopt_instance_specification" || len(group.Scopes) != 9 {
		t.Fatalf("incomplete registry group: %#v", group)
	}
	pendingAirunner := false
	for _, action := range plan.Actions {
		if strings.HasPrefix(action.Scope, "deployment.control_plane.airunners.") {
			if action.Operation != "adopt_instance_specification" || action.Executor != "unimplemented_action" {
				t.Fatalf("registry migration changed the pending airunner action: %#v", action)
			}
			pendingAirunner = true
		}
		if strings.HasSuffix(action.Scope, "schema_version") || action.Scope == "controller_ha.remote_user" || action.Scope == "controller_ha.repo_dir" {
			if action.Operation != "verify_engine_policy" || action.Executor != "none" {
				t.Fatalf("metadata gained source adoption: %#v", action)
			}
		}
	}
	if !pendingAirunner {
		t.Fatal("registry fixture omitted the pending airunner migration")
	}
	state.Kind, state.SchemaVersion, state.PriorStateKind = authoritystate.KindV4, 4, authoritystate.KindV3
	state.PriorStateSHA256, state.TransitionID, state.SignedIntentSHA256 = state.AuthorityStateSHA256, "registry-test-anchor", strings.Repeat("e", 64)
	state.RegistryAdoption = &authoritystate.IdentityAdoption{Nonce: state.TransitionID, IntentSHA256: state.SignedIntentSHA256, PlanSHA256: plan.PlanSHA256}
	state.SettingGroups = append(state.SettingGroups, authoritystate.SettingGroup{ID: authoritystate.RegistryGroupID, Scopes: group.Scopes, Source: authoritystate.InstanceAuthority})
	sort.Slice(state.SettingGroups, func(i, j int) bool { return state.SettingGroups[i].ID < state.SettingGroups[j].ID })
	state.AuthorityStateSHA256, _ = authoritystate.HashV2(state)
	if err := authoritystate.ValidateCurrent(state); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(options.AuthorityState, canonicalTestJSON(t, state), 0600); err != nil {
		t.Fatal(err)
	}
	for _, target := range []string{"registry", "connectivity", "controller-identity"} {
		for _, connectivity := range []string{"non-controller", "active-controller"} {
			options.MigrationTarget, options.ConnectivityTarget = target, connectivity
			final, err := Build(options, testEngine)
			if err != nil || !final.Deployable {
				t.Fatalf("final %s/%s: %v %#v", target, connectivity, err, final.Refusals)
			}
			for _, g := range final.ActionGroups {
				if g.Operation != "verify_instance_authority" {
					t.Fatalf("adopted group retained legacy source: %#v", g)
				}
			}
			for _, assignment := range final.Authorities {
				if assignment.Authority == authoritystate.LegacyRegistrySource && assignment.Disposition == "continuing" {
					t.Fatalf("adopted registry retained legacy authority: %#v", assignment)
				}
			}
			if final.LegacyRemovalReady {
				t.Fatal("execution inventory dependency was lost")
			}
		}
	}

	// Inventory selection is explicit and consumes the pending runner as one
	// atomic source group, while preserving all completed migrations.
	options.MigrationTarget = "inventory"
	inventoryPlan, err := Build(options, testEngine)
	if err != nil || !inventoryPlan.Deployable || inventoryPlan.SchemaVersion != 7 || inventoryPlan.Inventory == nil {
		t.Fatalf("inventory preparation failed: %v %#v", err, inventoryPlan.Refusals)
	}
	inventoryGroup := actionGroup(inventoryPlan, authoritystate.InventoryGroupID)
	if inventoryGroup.Executor != "inventory_source_v1" || inventoryGroup.Operation != "adopt_instance_specification" || len(inventoryGroup.Scopes) != 2 {
		t.Fatalf("incomplete inventory group: %#v", inventoryGroup)
	}
	for _, g := range inventoryPlan.ActionGroups {
		if g.ID != authoritystate.InventoryGroupID && (g.Executor != "none" || g.Operation != "verify_instance_authority") {
			t.Fatalf("inventory selection changed an existing source group: %#v", g)
		}
	}
	state.Kind, state.SchemaVersion, state.PriorStateKind = authoritystate.KindV5, 5, authoritystate.KindV4
	state.PriorStateSHA256, state.TransitionID, state.SignedIntentSHA256 = state.AuthorityStateSHA256, "inventory-test-anchor", strings.Repeat("f", 64)
	state.InventoryAdoption = &authoritystate.IdentityAdoption{Nonce: state.TransitionID, IntentSHA256: state.SignedIntentSHA256, PlanSHA256: inventoryPlan.PlanSHA256}
	state.SettingGroups = append(state.SettingGroups, authoritystate.SettingGroup{ID: authoritystate.InventoryGroupID, Scopes: inventoryGroup.Scopes, Source: authoritystate.InstanceAuthority})
	sort.Slice(state.SettingGroups, func(i, j int) bool { return state.SettingGroups[i].ID < state.SettingGroups[j].ID })
	state.AuthorityStateSHA256, _ = authoritystate.HashV2(state)
	if err := authoritystate.ValidateCurrent(state); err != nil {
		t.Fatal(err)
	}
	writeFile(t, filepath.Dir(options.AuthorityState), filepath.Base(options.AuthorityState), string(canonicalTestJSON(t, state)))
	for _, target := range []string{"inventory", "registry", "controller-identity", "connectivity"} {
		for _, connectivity := range []string{"non-controller", "active-controller"} {
			options.MigrationTarget, options.ConnectivityTarget = target, connectivity
			final, err := Build(options, testEngine)
			if err != nil || !final.Deployable || final.SchemaVersion != 7 {
				t.Fatalf("final inventory %s/%s: %v %#v", target, connectivity, err, final.Refusals)
			}
			for _, group := range final.ActionGroups {
				if group.Operation != "verify_instance_authority" {
					t.Fatalf("adopted group retained legacy ownership: %#v", group)
				}
			}
			for _, action := range final.Actions {
				if action.Executor == "unimplemented_action" || (action.Operation == "retain_legacy" && (action.Scope == authoritystate.InventoryScope || strings.HasPrefix(action.Scope, authoritystate.RunnerScopePrefix))) {
					t.Fatalf("inventory migration left pending authority: %#v", action)
				}
			}
			for _, assignment := range final.Authorities {
				if assignment.ID == "legacy_engine_inventory" {
					t.Fatal("inventory still uses legacy authority")
				}
			}
			if final.LegacyRemovalReady {
				t.Fatal("inventory adoption authorized legacy deletion")
			}
		}
	}
	for _, badScopes := range [][]string{{authoritystate.InventoryScope}, {authoritystate.InventoryScope, "shell"}, {authoritystate.RunnerScopePrefix + "boxa-ops", authoritystate.InventoryScope}} {
		bad := state
		bad.SettingGroups = append([]authoritystate.SettingGroup{}, state.SettingGroups...)
		for i := range bad.SettingGroups {
			if bad.SettingGroups[i].ID == authoritystate.InventoryGroupID {
				bad.SettingGroups[i].Scopes = badScopes
			}
		}
		bad.AuthorityStateSHA256, _ = authoritystate.HashV2(bad)
		if err := authoritystate.ValidateCurrent(bad); err == nil {
			t.Fatal("invalid inventory scope accepted")
		}
	}
	for _, oldKind := range []string{authoritystate.KindV2, authoritystate.KindV3, authoritystate.KindV4} {
		var raw map[string]any
		json.Unmarshal(canonicalTestJSON(t, state), &raw)
		raw["kind"], raw["inventory_adoption"] = oldKind, nil
		writeFile(t, filepath.Dir(options.AuthorityState), filepath.Base(options.AuthorityState), string(canonicalTestJSON(t, raw)))
		if _, err := authoritystate.LoadCurrent(options.AuthorityState); err == nil {
			t.Fatal("older source accepted null inventory adoption")
		}
	}
	for _, oldKind := range []string{authoritystate.KindV2, authoritystate.KindV3} {
		var raw map[string]any
		if err := json.Unmarshal(canonicalTestJSON(t, state), &raw); err != nil {
			t.Fatal(err)
		}
		raw["kind"], raw["registry_adoption"] = oldKind, nil
		if err := os.WriteFile(options.AuthorityState, canonicalTestJSON(t, raw), 0600); err != nil {
			t.Fatal(err)
		}
		if _, err := authoritystate.LoadCurrent(options.AuthorityState); err == nil {
			t.Fatal("older source accepted null registry adoption")
		}
	}
}
