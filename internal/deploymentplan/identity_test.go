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

func TestControllerIdentityMigrationAndExistingTargets(t *testing.T) {
	instance := prepareInstance(t)
	content, err := os.ReadFile(filepath.Join(instance, contract.InstancePath))
	if err != nil {
		t.Fatal(err)
	}
	var desired map[string]any
	if err := json.Unmarshal(content, &desired); err != nil {
		t.Fatal(err)
	}
	desired["controllers"].(map[string]any)["standby"] = "boxb"
	writeFile(t, instance, contract.InstancePath, string(canonicalTestJSON(t, desired)))
	runGit(t, instance, "add", contract.InstancePath)
	runGit(t, instance, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "declare standby")
	options := compatibilityOptions(t, instance)
	content, err = os.ReadFile(options.CompatibilityControllerHA)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(options.CompatibilityControllerHA, append(content, []byte("  - box: boxb\n    hostname: boxb-ops\n")...), 0600); err != nil {
		t.Fatal(err)
	}
	content, err = os.ReadFile(options.ObservationPath)
	if err != nil {
		t.Fatal(err)
	}
	var observation doctor.Observation
	if err := json.Unmarshal(content, &observation); err != nil {
		t.Fatal(err)
	}
	observation.TailnetMachines = append(observation.TailnetMachines, doctor.TailnetMachine{Hostname: "boxb-ops", Online: true, Tags: []string{"tag:ops"}})
	sort.Slice(observation.TailnetMachines, func(i, j int) bool {
		return observation.TailnetMachines[i].Hostname < observation.TailnetMachines[j].Hostname
	})
	for i := range observation.Boxes {
		if observation.Boxes[i].HostnamePrefix == "boxb" {
			box := &observation.Boxes[i]
			box.RunningGuests = append(box.RunningGuests, "ops")
			box.ConfiguredGuests = append(box.ConfiguredGuests, "ops")
			box.AutostartGuests = append(box.AutostartGuests, "ops")
			sort.Strings(box.RunningGuests)
			sort.Strings(box.ConfiguredGuests)
			sort.Strings(box.AutostartGuests)
		}
	}
	// Use a fresh map; instance-only fields must not enter the observation hash.
	desired = map[string]any{}
	if err := json.Unmarshal(canonicalTestJSON(t, observation), &desired); err != nil {
		t.Fatal(err)
	}
	delete(desired, "generation_sha256")
	hashContent, err := json.Marshal(desired)
	if err != nil {
		t.Fatal(err)
	}
	observation.GenerationSHA256 = fmt.Sprintf("%x", sha256.Sum256(hashContent))
	if err := os.WriteFile(options.ObservationPath, canonicalTestJSON(t, observation), 0600); err != nil {
		t.Fatal(err)
	}
	options.MigrationTarget = "controller-identity"
	before, err := Build(options, testEngine)
	if err != nil {
		t.Fatal(err)
	}
	if before.Deployable {
		t.Fatal("controller identity accepted legacy connectivity sources")
	}
	state, err := authoritystate.LoadV2(options.AuthorityState)
	if err != nil {
		t.Fatal(err)
	}
	for i := range state.SettingGroups {
		state.SettingGroups[i].Source = authoritystate.InstanceAuthority
	}
	state.AuthorityStateSHA256, _ = authoritystate.HashV2(state)
	if err := os.WriteFile(options.AuthorityState, canonicalTestJSON(t, state), 0600); err != nil {
		t.Fatal(err)
	}
	plan, err := Build(options, testEngine)
	if err != nil || !plan.Deployable {
		t.Fatalf("controller identity plan failed: %v diagnostics=%#v refusals=%#v", err, plan.Diagnostics, plan.Refusals)
	}
	group := actionGroup(plan, authoritystate.ControllerIdentityGroupID)
	if group.Executor != "controller_identity_source_v1" || group.Operation != "adopt_instance_specification" || !equalStrings(group.Scopes, authoritystate.ControllerIdentityScopes) {
		t.Fatalf("identity group is not closed: %#v", group)
	}
	for _, box := range []string{"boxa", "boxb"} {
		g := actionGroup(plan, authoritystate.BoxConnectivityPrefix+box)
		if g.Executor != "none" || g.Operation != "verify_instance_authority" {
			t.Fatalf("identity Plan exposed router executor: %#v", g)
		}
	}
	for _, action := range plan.Actions {
		if action.Scope == "deployment.control_plane.controller" && action.AuthorityBefore != "controller_ha_markers" {
			t.Fatal("derived controller placement was assigned a fictitious legacy file source")
		}
	}
	prior := state.AuthorityStateSHA256
	state.Kind, state.SchemaVersion, state.PriorStateKind = authoritystate.KindV3, 3, authoritystate.KindV2
	state.PriorStateSHA256, state.TransitionID, state.SignedIntentSHA256 = prior, "identity-adoption-test", strings.Repeat("c", 64)
	state.ControllerIdentityAdoption = &authoritystate.IdentityAdoption{Nonce: state.TransitionID, IntentSHA256: state.SignedIntentSHA256, PlanSHA256: plan.PlanSHA256}
	state.SettingGroups = append(state.SettingGroups, authoritystate.SettingGroup{ID: authoritystate.ControllerIdentityGroupID, Scopes: authoritystate.ControllerIdentityScopes, Source: authoritystate.InstanceAuthority})
	sort.Slice(state.SettingGroups, func(i, j int) bool { return state.SettingGroups[i].ID < state.SettingGroups[j].ID })
	state.AuthorityStateSHA256, _ = authoritystate.HashV2(state)
	if err := authoritystate.ValidateCurrent(state); err != nil {
		t.Fatal(err)
	}
	if err := authoritystate.ValidateV2(state); err == nil {
		t.Fatal("historical v2 validator accepted v3")
	}
	if err := os.WriteFile(options.AuthorityState, canonicalTestJSON(t, state), 0600); err != nil {
		t.Fatal(err)
	}
	for _, target := range []string{"controller-identity", "connectivity"} {
		for _, connectivity := range []string{"non-controller", "active-controller"} {
			options.MigrationTarget, options.ConnectivityTarget = target, connectivity
			final, err := Build(options, testEngine)
			if err != nil || !final.Deployable {
				t.Fatalf("final target failed: %s %s %v %#v", target, connectivity, err, final.Refusals)
			}
			for _, g := range final.ActionGroups {
				if g.Operation != "verify_instance_authority" {
					t.Fatalf("adopted group retained legacy ownership: %#v", g)
				}
			}
			identity := actionGroup(final, authoritystate.ControllerIdentityGroupID)
			if target == "connectivity" && identity.Executor != "none" {
				t.Fatal("unselected identity group exposes executor")
			}
		}
	}
	state.ControllerIdentityAdoption.PlanSHA256 = "bad"
	state.AuthorityStateSHA256, _ = authoritystate.HashV2(state)
	if err := authoritystate.ValidateCurrent(state); err == nil {
		t.Fatal("malformed adoption reference accepted")
	}
}
