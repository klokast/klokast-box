package deploymentplan

import (
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"klokast-box/internal/authoritystate"
	"klokast-box/internal/toolchain"
)

func checkInstanceOnly(t *testing.T, previous Options, state authoritystate.StateV2) {
	t.Helper()
	receipt, err := toolchain.Load(previous.ControllerToolchainReceipt, testCommit)
	if err != nil {
		t.Fatal(err)
	}
	receipt.SchemaVersion, receipt.Kind = 7, toolchain.KindV7
	receipt.ReceiptSHA256, _ = toolchain.Hash(receipt)
	receiptPath := filepath.Join(t.TempDir(), "toolchain.json")
	content := canonicalTestJSON(t, receipt)
	if err := os.WriteFile(receiptPath, content, 0600); err != nil {
		t.Fatal(err)
	}
	options := Options{InstanceOnly: true, InstancePath: previous.InstancePath, ObservationPath: previous.ObservationPath,
		InstanceSourceReceipt: previous.InstanceSourceReceipt, AuthorityState: previous.AuthorityState, ControllerToolchainReceipt: receiptPath}
	first, err := Build(options, testEngine)
	if err != nil || !first.Deployable || first.SchemaVersion != 8 || len(first.ActionGroups) != 6 || first.AuthorityState.AuthorityStateSHA256 != state.AuthorityStateSHA256 || first.LegacyRemovalReady {
		t.Fatalf("instance-only: %v %#v", err, first)
	}
	encoded, err := json.Marshal(first)
	if err != nil {
		t.Fatal(err)
	}
	var fields map[string]any
	json.Unmarshal(encoded, &fields)
	for _, key := range []string{"compatibility", "compatibility_inputs", "compatible", "migration_target", "connectivity_target", "selected_box"} {
		if _, exists := fields[key]; exists {
			t.Fatalf("instance-only retained %s", key)
		}
	}
	for _, group := range first.ActionGroups {
		if group.Operation != "verify_instance_authority" || group.Executor != VerificationExecutor {
			t.Fatalf("non-verification group: %#v", group)
		}
	}
	// These files belong to this test's isolated temporary filesystem only.
	for _, path := range []string{previous.CompatibilityDeployment, previous.CompatibilityRegistry, previous.CompatibilityControllerHA} {
		if err := os.Rename(path, path+".retained"); err != nil {
			t.Fatal(err)
		}
		defer os.Rename(path+".retained", path)
	}
	second, err := Build(options, testEngine)
	if err != nil || !reflect.DeepEqual(first, second) {
		t.Fatalf("legacy absence changed Plan v8: %v", err)
	}
	for _, mutate := range []func(*Options){func(o *Options) { o.MigrationTarget = "connectivity" }, func(o *Options) { o.ConnectivityTarget = "non-controller" }, func(o *Options) { o.CompatibilityRegistry = "absent" }, func(o *Options) { o.CompatibilityDeployment = "absent" }, func(o *Options) { o.CompatibilityControllerHA = "absent" }} {
		bad := options
		mutate(&bad)
		if _, err := Build(bad, testEngine); err == nil {
			t.Fatal("instance-only accepted compatibility selection")
		}
	}
	for _, source := range []string{"legacy_platform_resources", "unsupported"} {
		bad := state
		bad.SettingGroups = append([]authoritystate.SettingGroup{}, state.SettingGroups...)
		bad.SettingGroups[0].Source = source
		bad.AuthorityStateSHA256, _ = authoritystate.HashV2(bad)
		content := canonicalTestJSON(t, bad)
		os.WriteFile(options.AuthorityState, content, 0600)
		if _, err := Build(options, testEngine); err == nil {
			t.Fatal("instance-only accepted unsupported ownership")
		}
	}
	bad := state
	bad.SettingGroups = append([]authoritystate.SettingGroup{}, state.SettingGroups...)
	for i := range bad.SettingGroups {
		if bad.SettingGroups[i].ID == authoritystate.InventoryGroupID {
			bad.SettingGroups[i].Scopes = []string{authoritystate.RunnerScopePrefix + "boxb-ops-airunner", authoritystate.InventoryScope}
		}
	}
	bad.AuthorityStateSHA256, _ = authoritystate.HashV2(bad)
	content = canonicalTestJSON(t, bad)
	os.WriteFile(options.AuthorityState, content, 0600)
	if _, err := Build(options, testEngine); err == nil || !strings.Contains(err.Error(), "scope") {
		t.Fatalf("changed membership accepted: %v", err)
	}
	content = canonicalTestJSON(t, state)
	os.WriteFile(options.AuthorityState, content, 0600)
}
