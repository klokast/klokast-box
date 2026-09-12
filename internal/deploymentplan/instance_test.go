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
	checkLegacyRetirement(t, previous, state)
}

func checkLegacyRetirement(t *testing.T, previous Options, state authoritystate.StateV2) {
	t.Helper()
	receipt, err := toolchain.Load(previous.ControllerToolchainReceipt, testCommit)
	if err != nil {
		t.Fatal(err)
	}
	receipt.SchemaVersion, receipt.Kind = 8, toolchain.KindV8
	receipt.ReceiptSHA256, _ = toolchain.Hash(receipt)
	receiptPath := filepath.Join(t.TempDir(), "toolchain-v8.json")
	if err := os.WriteFile(receiptPath, canonicalTestJSON(t, receipt), 0600); err != nil {
		t.Fatal(err)
	}
	file := func(path string, index int) RetirementFileReference {
		return RetirementFileReference{Path: path, SHA256: fmt.Sprintf("%064x", index), Size: int64(index), UID: 1000, GID: 1000, Mode: "0600", NLink: 1}
	}
	evidence := RetirementEvidence{
		SchemaVersion: 1, Kind: RetirementEvidenceKind, Phase: "exercise",
		ConsumerMatrixSHA256: strings.Repeat("1", 64), SettingsSHA256: strings.Repeat("2", 64),
		RecoveryArchivePath: "/var/lib/klokast/legacy-input-recovery/" + strings.Repeat("3", 64),
		RecoveryArchiveSHA256: strings.Repeat("3", 64), RecoveryManifestSHA256: strings.Repeat("4", 64),
		DetachedReconstructionSHA256: strings.Repeat("5", 64), ConsumerAbsenceComplete: true,
		RecoveryReconstructionVerified: true, SettingsUnchanged: true, LiveInputsState: "present", ApprovedBackupsState: "present",
	}
	for index, path := range legacyInputPaths {
		evidence.LiveInputs = append(evidence.LiveInputs, file(path, index+1))
	}
	for index, name := range obsoleteBackupNames {
		evidence.ApprovedBackups = append(evidence.ApprovedBackups, file(filepath.Join("/home/smith/private/klokast", name), index+10))
	}
	writeEvidence := func(value RetirementEvidence) string {
		value.EvidenceSHA256, err = retirementEvidenceHash(value)
		if err != nil {
			t.Fatal(err)
		}
		path := filepath.Join(t.TempDir(), value.Phase+".json")
		if err := os.WriteFile(path, canonicalTestJSON(t, value), 0600); err != nil {
			t.Fatal(err)
		}
		return path
	}
	base := Options{LegacyRetirement: true, InstancePath: previous.InstancePath, ObservationPath: previous.ObservationPath,
		InstanceSourceReceipt: previous.InstanceSourceReceipt, AuthorityState: previous.AuthorityState, ControllerToolchainReceipt: receiptPath}
	base.RetirementPhase, base.RetirementEvidence = "exercise", writeEvidence(evidence)
	exercise, err := Build(base, testEngine)
	if err != nil || !exercise.Deployable || exercise.SchemaVersion != 9 || exercise.LegacyRemovalReady || len(exercise.ActionGroups) != 7 {
		t.Fatalf("exercise Plan v9: %v %#v", err, exercise)
	}
	group := actionGroup(exercise, LegacyRetirementGroupID)
	if group.Operation != "exercise_legacy_input_retirement" || group.Executor != LegacyRetirementExecutor {
		t.Fatalf("exercise lifecycle group is not closed: %#v", group)
	}
	evidence.Phase, evidence.ExerciseReceiptSHA256 = "retire", strings.Repeat("6", 64)
	base.RetirementPhase, base.RetirementEvidence = "retire", writeEvidence(evidence)
	retire, err := Build(base, testEngine)
	if err != nil || !retire.LegacyRemovalReady || actionGroup(retire, LegacyRetirementGroupID).Operation != "retire_legacy_inputs" {
		t.Fatalf("retire Plan v9: %v %#v", err, retire)
	}
	evidence.Phase, evidence.LiveInputsState, evidence.ApprovedBackupsState = "verify", "absent", "absent"
	evidence.RetirementReceiptSHA256 = strings.Repeat("7", 64)
	base.RetirementPhase, base.RetirementEvidence = "verify", writeEvidence(evidence)
	verify, err := Build(base, testEngine)
	if err != nil || !verify.LegacyRemovalReady || actionGroup(verify, LegacyRetirementGroupID).Operation != "verify_legacy_retirement" {
		t.Fatalf("verify Plan v9: %v %#v", err, verify)
	}
	bad := base
	bad.MigrationTarget = "inventory"
	if _, err := Build(bad, testEngine); err == nil {
		t.Fatal("Plan v9 accepted a migration target")
	}
	bad = base
	bad.RetirementPhase = "exercise"
	if _, err := Build(bad, testEngine); err == nil {
		t.Fatal("Plan v9 accepted evidence from another phase")
	}
}
