package deploymentplan

import (
	"bytes"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"sort"

	"klokast-box/internal/contract"
	"klokast-box/internal/strictjson"
)

const (
	LegacyRetirementExecutor = "legacy_input_retirement_v1"
	LegacyRetirementGroupID  = "legacy-input-retirement-v1"
	RetirementEvidenceKind   = "klokast.legacy-retirement-evidence.v1"
)

var legacyInputPaths = []string{
	"/home/smith/private/klokast/deployment.yml",
	"/home/smith/private/klokast/platform-resources.yml",
	"/home/smith/private/klokast/controller-ha.yml",
}

var obsoleteBackupNames = []string{
	"platform-resources.yml.20260611T010611Z.bak",
	"platform-resources.yml.20260611T011900Z.bak",
	"platform-resources.yml.20260611T015423Z.bak",
}

type RetirementFileReference struct {
	Path   string `json:"path"`
	SHA256 string `json:"sha256"`
	Size   int64  `json:"size"`
	UID    int    `json:"uid"`
	GID    int    `json:"gid"`
	Mode   string `json:"mode"`
	NLink  uint64 `json:"nlink"`
}

type RetirementEvidence struct {
	SchemaVersion                  int                       `json:"schema_version"`
	Kind                           string                    `json:"kind"`
	Phase                          string                    `json:"phase"`
	ConsumerMatrixSHA256           string                    `json:"consumer_matrix_sha256"`
	SettingsSHA256                 string                    `json:"settings_sha256"`
	RecoveryArchivePath            string                    `json:"recovery_archive_path"`
	RecoveryArchiveSHA256          string                    `json:"recovery_archive_sha256"`
	RecoveryManifestSHA256         string                    `json:"recovery_manifest_sha256"`
	DetachedReconstructionSHA256   string                    `json:"detached_reconstruction_sha256"`
	ConsumerAbsenceComplete        bool                      `json:"consumer_absence_complete"`
	RecoveryReconstructionVerified bool                     `json:"recovery_reconstruction_verified"`
	SettingsUnchanged              bool                      `json:"settings_unchanged"`
	LiveInputsState                string                    `json:"live_inputs_state"`
	ApprovedBackupsState           string                    `json:"approved_backups_state"`
	LiveInputs                     []RetirementFileReference `json:"live_inputs"`
	ApprovedBackups                []RetirementFileReference `json:"approved_backups"`
	ExerciseReceiptSHA256          string                    `json:"exercise_receipt_sha256"`
	RetirementReceiptSHA256        string                    `json:"retirement_receipt_sha256"`
	EvidenceSHA256                 string                    `json:"evidence_sha256"`
}

type LegacyRetirementReference struct {
	Phase                          string                    `json:"phase"`
	EvidenceSHA256                 string                    `json:"evidence_sha256"`
	ConsumerMatrixSHA256           string                    `json:"consumer_matrix_sha256"`
	SettingsSHA256                 string                    `json:"settings_sha256"`
	RecoveryArchivePath            string                    `json:"recovery_archive_path"`
	RecoveryArchiveSHA256          string                    `json:"recovery_archive_sha256"`
	RecoveryManifestSHA256         string                    `json:"recovery_manifest_sha256"`
	DetachedReconstructionSHA256   string                    `json:"detached_reconstruction_sha256"`
	ConsumerAbsenceComplete        bool                      `json:"consumer_absence_complete"`
	RecoveryReconstructionVerified bool                     `json:"recovery_reconstruction_verified"`
	SettingsUnchanged              bool                      `json:"settings_unchanged"`
	LiveInputsState                string                    `json:"live_inputs_state"`
	ApprovedBackupsState           string                    `json:"approved_backups_state"`
	LiveInputs                     []RetirementFileReference `json:"live_inputs"`
	ApprovedBackups                []RetirementFileReference `json:"approved_backups"`
	ExerciseReceiptSHA256          string                    `json:"exercise_receipt_sha256"`
	RetirementReceiptSHA256        string                    `json:"retirement_receipt_sha256"`
}

func retirementEvidenceHash(value RetirementEvidence) (string, error) {
	content, err := json.Marshal(value)
	if err != nil {
		return "", err
	}
	var fields map[string]any
	decoder := json.NewDecoder(bytes.NewReader(content))
	decoder.UseNumber()
	if err := decoder.Decode(&fields); err != nil {
		return "", err
	}
	delete(fields, "evidence_sha256")
	canonical, err := json.Marshal(fields)
	if err != nil {
		return "", err
	}
	digest := sha256.Sum256(canonical)
	return fmt.Sprintf("%x", digest[:]), nil
}

func isRetirementDigest(value string) bool {
	matched, _ := regexp.MatchString(`^[0-9a-f]{64}$`, value)
	return matched
}

func loadRetirementEvidence(path, phase string) (RetirementEvidence, error) {
	if path == "" {
		return RetirementEvidence{}, fmt.Errorf("legacy retirement requires --retirement-evidence")
	}
	info, err := os.Lstat(path)
	if err != nil {
		return RetirementEvidence{}, fmt.Errorf("inspect retirement evidence: %w", err)
	}
	if !info.Mode().IsRegular() || info.Mode()&os.ModeSymlink != 0 || info.Size() <= 0 || info.Size() > 1024*1024 {
		return RetirementEvidence{}, fmt.Errorf("retirement evidence must be one non-empty bounded regular file")
	}
	content, err := os.ReadFile(path)
	if err != nil {
		return RetirementEvidence{}, fmt.Errorf("read retirement evidence: %w", err)
	}
	var evidence RetirementEvidence
	if err := strictjson.Decode(content, &evidence, true); err != nil {
		return RetirementEvidence{}, fmt.Errorf("decode retirement evidence: %w", err)
	}
	if evidence.SchemaVersion != 1 || evidence.Kind != RetirementEvidenceKind || evidence.Phase != phase {
		return RetirementEvidence{}, fmt.Errorf("retirement evidence identity or phase does not match Plan v9")
	}
	want, err := retirementEvidenceHash(evidence)
	if err != nil || evidence.EvidenceSHA256 != want {
		return RetirementEvidence{}, fmt.Errorf("retirement evidence hash does not match canonical content")
	}
	for _, digest := range []string{evidence.ConsumerMatrixSHA256, evidence.SettingsSHA256, evidence.RecoveryArchiveSHA256, evidence.RecoveryManifestSHA256, evidence.DetachedReconstructionSHA256} {
		if !isRetirementDigest(digest) {
			return RetirementEvidence{}, fmt.Errorf("retirement evidence contains an invalid required digest")
		}
	}
	archiveRoot := "/var/lib/klokast/legacy-input-recovery"
	if !filepath.IsAbs(evidence.RecoveryArchivePath) || filepath.Clean(evidence.RecoveryArchivePath) != evidence.RecoveryArchivePath || filepath.Dir(evidence.RecoveryArchivePath) != archiveRoot || !isRetirementDigest(filepath.Base(evidence.RecoveryArchivePath)) {
		return RetirementEvidence{}, fmt.Errorf("retirement recovery archive path must be one content-addressed directory under %s", archiveRoot)
	}
	if err := validateRetirementFiles(evidence.LiveInputs, legacyInputPaths); err != nil {
		return RetirementEvidence{}, err
	}
	wantedBackups := make([]string, len(obsoleteBackupNames))
	for index, name := range obsoleteBackupNames {
		wantedBackups[index] = filepath.Join("/home/smith/private/klokast", name)
	}
	if err := validateRetirementFiles(evidence.ApprovedBackups, wantedBackups); err != nil {
		return RetirementEvidence{}, err
	}
	if !evidence.ConsumerAbsenceComplete || !evidence.RecoveryReconstructionVerified || !evidence.SettingsUnchanged {
		return RetirementEvidence{}, fmt.Errorf("retirement evidence does not prove consumer closure, reconstruction, and unchanged settings")
	}
	if phase == "exercise" {
		if evidence.LiveInputsState != "present" || evidence.ApprovedBackupsState != "present" || evidence.ExerciseReceiptSHA256 != "" || evidence.RetirementReceiptSHA256 != "" {
			return RetirementEvidence{}, fmt.Errorf("exercise evidence must describe present inputs without prior retirement receipts")
		}
	} else if phase == "retire" {
		if evidence.LiveInputsState != "present" || evidence.ApprovedBackupsState != "present" || !isRetirementDigest(evidence.ExerciseReceiptSHA256) || evidence.RetirementReceiptSHA256 != "" {
			return RetirementEvidence{}, fmt.Errorf("retire evidence requires restored live inputs and one valid exercise receipt")
		}
	} else if phase == "verify" {
		if evidence.LiveInputsState != "absent" || evidence.ApprovedBackupsState != "absent" || !isRetirementDigest(evidence.ExerciseReceiptSHA256) || !isRetirementDigest(evidence.RetirementReceiptSHA256) {
			return RetirementEvidence{}, fmt.Errorf("verify evidence requires absent inputs and both prior execution receipts")
		}
	} else {
		return RetirementEvidence{}, fmt.Errorf("retirement phase must be exercise, retire, or verify")
	}
	return evidence, nil
}

func validateRetirementFiles(files []RetirementFileReference, wanted []string) error {
	paths := make([]string, len(files))
	for index, file := range files {
		paths[index] = file.Path
		if !isRetirementDigest(file.SHA256) || file.Size <= 0 || file.UID < 0 || file.GID < 0 || !regexp.MustCompile(`^0[0-7]{3}$`).MatchString(file.Mode) || file.NLink != 1 {
			return fmt.Errorf("retirement file metadata is incomplete or unsafe: %s", file.Path)
		}
	}
	if !reflect.DeepEqual(paths, wanted) {
		return fmt.Errorf("retirement file set differs from the closed approved list")
	}
	return nil
}

func buildLegacyRetirement(options Options, engine contract.Engine) (Artifact, error) {
	if options.InstanceOnly || options.RetirementPhase == "" {
		return Artifact{}, fmt.Errorf("legacy retirement requires its separate mode and phase")
	}
	a, err := buildCompleteInstancePlan(options, engine, 9, "klokast.plan.v9", 8)
	if err != nil {
		return a, err
	}
	evidence, err := loadRetirementEvidence(options.RetirementEvidence, options.RetirementPhase)
	if err != nil {
		return a, err
	}
	reference := LegacyRetirementReference{
		Phase: evidence.Phase, EvidenceSHA256: evidence.EvidenceSHA256,
		ConsumerMatrixSHA256: evidence.ConsumerMatrixSHA256, SettingsSHA256: evidence.SettingsSHA256,
		RecoveryArchivePath: evidence.RecoveryArchivePath, RecoveryArchiveSHA256: evidence.RecoveryArchiveSHA256,
		RecoveryManifestSHA256: evidence.RecoveryManifestSHA256, DetachedReconstructionSHA256: evidence.DetachedReconstructionSHA256,
		ConsumerAbsenceComplete: evidence.ConsumerAbsenceComplete, RecoveryReconstructionVerified: evidence.RecoveryReconstructionVerified,
		SettingsUnchanged: evidence.SettingsUnchanged, LiveInputsState: evidence.LiveInputsState,
		ApprovedBackupsState: evidence.ApprovedBackupsState, LiveInputs: evidence.LiveInputs,
		ApprovedBackups: evidence.ApprovedBackups, ExerciseReceiptSHA256: evidence.ExerciseReceiptSHA256,
		RetirementReceiptSHA256: evidence.RetirementReceiptSHA256,
	}
	a.LegacyRetirement = &reference
	operations := map[string]string{"exercise": "exercise_legacy_input_retirement", "retire": "retire_legacy_inputs", "verify": "verify_legacy_retirement"}
	rollback := map[string]string{"exercise": "restore_exact_legacy_inputs", "retire": "recovery_archive_only", "verify": "no_mutation"}
	a.ActionGroups = append(a.ActionGroups, ActionGroup{ID: LegacyRetirementGroupID, Operation: operations[evidence.Phase], Scopes: []string{"legacy_private_inputs"}, Executor: LegacyRetirementExecutor, RollbackType: rollback[evidence.Phase]})
	a.Actions = append(a.Actions, Action{
		ID: actionID(operations[evidence.Phase], "legacy_private_inputs"), Operation: operations[evidence.Phase], Scope: "legacy_private_inputs",
		AuthorityBefore: "retained_legacy_inputs", AuthorityAfter: map[string]string{"exercise": "retained_legacy_inputs", "retire": "immutable_recovery_archive", "verify": "immutable_recovery_archive"}[evidence.Phase],
		Executor: LegacyRetirementExecutor,
		Preconditions: []string{"exact_plan_v9_revalidated", "complete_instance_ownership", "consumer_absence_verified", "recovery_reconstruction_verified", "single_use_approval"},
		Rollback: Rollback{Strategy: rollback[evidence.Phase], Authority: "immutable_recovery_archive", SourceSHA256: evidence.RecoveryArchiveSHA256},
	})
	a.Authorities = append(a.Authorities, AuthorityAssignment{ID: "legacy_retirement_evidence_v1", Scope: "legacy_private_inputs", Disposition: "evidence", SourceSHA256: evidence.EvidenceSHA256})
	sort.Slice(a.Actions, func(i, j int) bool { return a.Actions[i].ID < a.Actions[j].ID })
	sort.Slice(a.Authorities, func(i, j int) bool { return a.Authorities[i].ID < a.Authorities[j].ID })
	a.LegacyRemovalReady = evidence.Phase != "exercise"
	a.PlanSHA256, err = Hash(a)
	return a, err
}
