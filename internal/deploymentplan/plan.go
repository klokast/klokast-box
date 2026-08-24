// Package deploymentplan creates a deterministic, read-only Plan v2 artifact.
package deploymentplan

import (
	"bytes"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"reflect"
	"sort"
	"strings"
	"time"

	"klokast-box/internal/authoritystate"
	"klokast-box/internal/contract"
	"klokast-box/internal/doctor"
	"klokast-box/internal/instancesource"
	"klokast-box/internal/planner"
	"klokast-box/internal/toolchain"
)

type Options struct {
	InstancePath               string
	CompatibilityDeployment    string
	CompatibilityRegistry      string
	CompatibilityControllerHA  string
	ObservationPath            string
	InstanceSourceReceipt      string
	AuthorityState             string
	ControllerToolchainReceipt string
}

type Artifact struct {
	SchemaVersion       int                          `json:"schema_version"`
	Kind                string                       `json:"kind"`
	Valid               bool                         `json:"valid"`
	Compatible          bool                         `json:"compatible"`
	SubstrateHealthy    bool                         `json:"substrate_healthy"`
	Deployable          bool                         `json:"deployable"`
	AuthorityReady      bool                         `json:"authority_ready"`
	LegacyRemovalReady  bool                         `json:"legacy_removal_ready"`
	HealthScope         string                       `json:"health_scope"`
	Engine              planner.Engine               `json:"engine"`
	Instance            InstanceIdentity             `json:"instance"`
	InstanceSource      instancesource.Reference     `json:"instance_source"`
	AuthorityState      AuthorityStateReference      `json:"authority_state"`
	ControllerToolchain ToolchainReference           `json:"controller_toolchain"`
	Inputs              []planner.InputDigest        `json:"inputs"`
	CompatibilityInputs []planner.CompatibilityInput `json:"compatibility_inputs"`
	Projection          *planner.Projection          `json:"projection,omitempty"`
	ProjectionHash      string                       `json:"projection_sha256,omitempty"`
	Observation         ObservationReference         `json:"observation"`
	Compatibility       *planner.Compatibility       `json:"compatibility,omitempty"`
	Authorities         []AuthorityAssignment        `json:"authorities"`
	Actions             []Action                     `json:"actions"`
	AtomicActionGroup   AtomicActionGroup            `json:"atomic_action_group"`
	Refusals            []Refusal                    `json:"refusals"`
	Diagnostics         []contract.Diagnostic        `json:"diagnostics"`
	PlanSHA256          string                       `json:"plan_sha256,omitempty"`
}

type AuthorityStateReference struct {
	AuthorityStateSHA256 string `json:"authority_state_sha256"`
	TailnetAuthority     string `json:"tailnet_authority"`
}

type ToolchainReference struct {
	ReceiptSHA256 string `json:"receipt_sha256"`
	EngineCommit  string `json:"engine_commit"`
}

type AtomicActionGroup struct {
	ID           string   `json:"id"`
	Operation    string   `json:"operation"`
	Scopes       []string `json:"scopes"`
	Executor     string   `json:"executor"`
	RollbackType string   `json:"rollback_type"`
}

type InstanceIdentity struct {
	Branch string `json:"branch,omitempty"`
	Commit string `json:"commit,omitempty"`
}

type ObservationReference struct {
	SourceController string `json:"source_controller,omitempty"`
	ObservedAt       string `json:"observed_at,omitempty"`
	GenerationSHA256 string `json:"generation_sha256,omitempty"`
}

type AuthorityAssignment struct {
	ID           string `json:"id"`
	FindingID    string `json:"finding_id,omitempty"`
	Authority    string `json:"authority,omitempty"`
	Scope        string `json:"scope"`
	Disposition  string `json:"disposition"`
	SourceSHA256 string `json:"source_sha256,omitempty"`
	SourceCommit string `json:"source_commit,omitempty"`
}

type Action struct {
	ID                    string   `json:"id"`
	FindingID             string   `json:"finding_id,omitempty"`
	AuthorityAssignmentID string   `json:"authority_assignment_id,omitempty"`
	Operation             string   `json:"operation"`
	Scope                 string   `json:"scope"`
	AuthorityBefore       string   `json:"authority_before"`
	AuthorityAfter        string   `json:"authority_after"`
	Executor              string   `json:"executor"`
	Preconditions         []string `json:"preconditions"`
	Rollback              Rollback `json:"rollback"`
}

type Rollback struct {
	Strategy     string `json:"strategy"`
	Authority    string `json:"authority"`
	SourceSHA256 string `json:"source_sha256,omitempty"`
}

type Refusal struct {
	Code    string `json:"code"`
	Scope   string `json:"scope"`
	Message string `json:"message"`
}

func Build(options Options, engine contract.Engine) (Artifact, error) {
	artifact := Artifact{
		SchemaVersion: 2,
		Kind:          "klokast.plan.v2",
		HealthScope:   "standard_substrate_v1",
		Engine:        planner.Engine{Repository: engine.Repository, Ref: engine.Ref, Commit: engine.Commit},
		Inputs:        []planner.InputDigest{}, CompatibilityInputs: []planner.CompatibilityInput{},
		Authorities: []AuthorityAssignment{}, Actions: []Action{}, Refusals: []Refusal{},
		Diagnostics: []contract.Diagnostic{},
	}
	plannerOptions := planner.Options{
		InstancePath:              options.InstancePath,
		CompatibilityDeployment:   options.CompatibilityDeployment,
		CompatibilityRegistry:     options.CompatibilityRegistry,
		CompatibilityControllerHA: options.CompatibilityControllerHA,
	}
	report, err := planner.Plan(plannerOptions, engine)
	if err != nil {
		return Artifact{}, err
	}
	if !report.Valid {
		artifact.Diagnostics = report.Diagnostics
		return artifact, nil
	}
	state, err := authoritystate.Load(options.AuthorityState)
	if err != nil {
		return Artifact{}, err
	}
	tailnetAuthority, err := authoritystate.Authority(state)
	if err != nil {
		return Artifact{}, err
	}
	toolchainReceipt, err := toolchain.Load(options.ControllerToolchainReceipt, engine.Commit)
	if err != nil {
		return Artifact{}, err
	}
	artifact.AuthorityState = AuthorityStateReference{
		AuthorityStateSHA256: state.AuthorityStateSHA256,
		TailnetAuthority:     tailnetAuthority,
	}
	artifact.ControllerToolchain = ToolchainReference{
		ReceiptSHA256: toolchainReceipt.ReceiptSHA256,
		EngineCommit:  toolchainReceipt.EngineCommit,
	}

	artifact.Engine = report.Engine
	artifact.Instance = InstanceIdentity{Branch: report.Repository.Branch, Commit: report.Repository.HeadCommit}
	artifact.Inputs = report.Inputs
	artifact.Projection = report.Projection
	artifact.ProjectionHash = report.ProjectionHash
	artifact.Compatibility = report.Compatibility
	artifact.CompatibilityInputs = report.Compatibility.Inputs
	artifact.Compatible = report.Compatible
	receipt, sourceDiagnostics, err := instancesource.Load(options.InstanceSourceReceipt, time.Now().UTC())
	if err != nil {
		return Artifact{}, err
	}
	if len(sourceDiagnostics) != 0 {
		artifact.Diagnostics = sourceDiagnostics
		return artifact, nil
	}
	artifact.InstanceSource = receipt.Reference()
	sourceMatchesRepository := receipt.Commit == report.Repository.HeadCommit && receipt.RemoteRef == "refs/heads/"+report.Repository.Branch

	health, err := doctor.Doctor(doctor.Options{InstancePath: options.InstancePath, ObservationPath: options.ObservationPath}, engine)
	if err != nil {
		return Artifact{}, err
	}
	if !health.Valid {
		artifact.Diagnostics = health.Diagnostics
		return artifact, nil
	}
	if health.Engine != report.Engine || !reflect.DeepEqual(health.Inputs, report.Inputs) || health.ProjectionHash != report.ProjectionHash {
		return Artifact{}, fmt.Errorf("instance inputs changed between compatibility planning and observation checks")
	}
	artifact.Observation = ObservationReference{
		SourceController: health.ObservationSource,
		ObservedAt:       health.ObservedAt,
		GenerationSHA256: health.ObservationGeneration,
	}
	artifact.SubstrateHealthy = health.Healthy

	second, err := planner.Plan(plannerOptions, engine)
	if err != nil {
		return Artifact{}, err
	}
	if !sameReportInputs(report, second) {
		return Artifact{}, fmt.Errorf("desired-state or compatibility inputs changed while the plan was created")
	}

	digests := compatibilityDigests(artifact.CompatibilityInputs)
	artifact.Authorities = authorityAssignments(artifact, digests)
	tailnetFindings := map[string]planner.Finding{}
	for _, finding := range artifact.Compatibility.Findings {
		if isTailnetPilotScope(finding.Path) {
			tailnetFindings[finding.Path] = finding
			continue
		}
		switch finding.Class {
		case "matched", "derived":
			before := sourceAuthority(finding)
			if finding.Code == "airunner.instance-specification" {
				before = "none"
			}
			artifact.Actions = append(artifact.Actions, adoptionAction(finding, before, digests[before]))
		case "compatibility_only":
			if finding.Authority == "" || digests[finding.Authority] == "" {
				artifact.Refusals = append(artifact.Refusals, refusal("authority.missing", finding.Path, "a compatibility-only field has no continuing authority"))
				continue
			}
			artifact.Actions = append(artifact.Actions, retainedAction(finding, digests[finding.Authority]))
		case "conflict", "unsupported":
			artifact.Actions = append(artifact.Actions, refusalAction(finding))
			artifact.Refusals = append(artifact.Refusals, refusal("compatibility."+finding.Class, finding.Path, finding.Message))
		default:
			artifact.Refusals = append(artifact.Refusals, refusal("compatibility.class", finding.Path, "the compatibility finding class is not supported"))
		}
	}
	pilotOperation := "adopt_instance_specification"
	if tailnetAuthority == authoritystate.InstanceAuthority {
		pilotOperation = "verify_instance_authority"
	}
	artifact.AtomicActionGroup = AtomicActionGroup{
		ID:           "tailnet-policy-inputs-v1",
		Operation:    pilotOperation,
		Scopes:       append([]string{}, authoritystate.TailnetScopes...),
		Executor:     "tailnet_policy_inputs_v1",
		RollbackType: "tailnet_policy_preimage_v1",
	}
	legacyDigest := digests[authoritystate.LegacyAuthority]
	for _, scope := range authoritystate.TailnetScopes {
		finding, present := tailnetFindings[scope]
		if !present || finding.Class != "matched" || legacyDigest == "" {
			artifact.Refusals = append(artifact.Refusals, refusal(
				"tailnet-pilot.not-matched", scope,
				"the complete Tailnet pilot requires matched legacy deployment evidence",
			))
			continue
		}
		before, after := authoritystate.LegacyAuthority, authoritystate.InstanceAuthority
		if pilotOperation == "verify_instance_authority" {
			before, after = authoritystate.InstanceAuthority, authoritystate.InstanceAuthority
		}
		artifact.Actions = append(artifact.Actions, Action{
			ID: actionID(pilotOperation, finding.ID), FindingID: finding.ID,
			Operation: pilotOperation, Scope: scope, AuthorityBefore: before,
			AuthorityAfter: after, Executor: "tailnet_policy_inputs_v1",
			Preconditions: []string{"active_controller_fenced", "exact_plan_v2_revalidated", "byte_equal_policy", "tailnet_policy_preimage_prepared"},
			Rollback:      Rollback{Strategy: "tailnet_policy_preimage_v1", Authority: authoritystate.LegacyAuthority, SourceSHA256: legacyDigest},
		})
	}
	for _, finding := range health.Findings {
		artifact.Refusals = append(artifact.Refusals, refusal("observation."+finding.Code, finding.Path, finding.Message))
	}
	for _, reason := range report.Repository.Reasons {
		artifact.Refusals = append(artifact.Refusals, refusal(reason, "instance.repository", "the instance repository is not a clean committed deployment input"))
	}
	if !sourceMatchesRepository {
		artifact.Refusals = append(artifact.Refusals, refusal(
			"instance-source.mismatch", "instance.repository",
			"the instance source receipt does not match the checked instance branch and commit",
		))
	}
	artifact.Actions = append(artifact.Actions, Action{
		ID: "verify-standard-substrate", Operation: "verify_substrate", Scope: "standard_substrate_v1",
		AuthorityBefore: "observation_v1", AuthorityAfter: "observation_v1", Executor: "read_only_doctor",
		Preconditions: []string{"fresh_observation", "active_controller_source", "exact_projection_hash"},
		Rollback:      Rollback{Strategy: "no_mutation", Authority: "observation_v1", SourceSHA256: health.ObservationGeneration},
	})
	coverageReady, coverageRefusals := exactCoverage(artifact, digests)
	artifact.Refusals = append(artifact.Refusals, coverageRefusals...)
	sort.Slice(artifact.Actions, func(i, j int) bool { return artifact.Actions[i].ID < artifact.Actions[j].ID })
	sort.Slice(artifact.Refusals, func(i, j int) bool {
		if artifact.Refusals[i].Scope != artifact.Refusals[j].Scope {
			return artifact.Refusals[i].Scope < artifact.Refusals[j].Scope
		}
		return artifact.Refusals[i].Code < artifact.Refusals[j].Code
	})

	artifact.Valid = true
	artifact.Deployable = report.Repository.Clean && report.Repository.HeadCommit != "" && artifact.InstanceSource.ReceiptSHA256 != "" && artifact.AuthorityState.AuthorityStateSHA256 != "" && artifact.ControllerToolchain.ReceiptSHA256 != "" && artifact.Compatible && artifact.SubstrateHealthy && len(artifact.Refusals) == 0
	artifact.AuthorityReady = artifact.Deployable && coverageReady
	// The pilot retains the complete legacy shadow even when every compatibility
	// finding is otherwise representable. Removal is a later authorization.
	artifact.LegacyRemovalReady = false
	artifact.PlanSHA256, err = Hash(artifact)
	if err != nil {
		return Artifact{}, err
	}
	return artifact, nil
}

func Hash(artifact Artifact) (string, error) {
	artifact.PlanSHA256 = ""
	encoded, err := json.Marshal(artifact)
	if err != nil {
		return "", fmt.Errorf("encode Plan v2 artifact: %w", err)
	}
	var value any
	decoder := json.NewDecoder(bytes.NewReader(encoded))
	decoder.UseNumber()
	if err := decoder.Decode(&value); err != nil {
		return "", fmt.Errorf("canonicalize Plan v2 artifact: %w", err)
	}
	var canonical bytes.Buffer
	encoder := json.NewEncoder(&canonical)
	encoder.SetEscapeHTML(false)
	if err := encoder.Encode(value); err != nil {
		return "", fmt.Errorf("canonicalize Plan v2 artifact: %w", err)
	}
	content := bytes.TrimSuffix(canonical.Bytes(), []byte{'\n'})
	digest := sha256.Sum256(content)
	return fmt.Sprintf("%x", digest[:]), nil
}

func sameReportInputs(first, second planner.Result) bool {
	return second.Valid && first.Compatibility != nil && second.Compatibility != nil &&
		first.Engine == second.Engine && first.Repository.Branch == second.Repository.Branch &&
		first.Repository.HeadCommit == second.Repository.HeadCommit && first.Repository.Clean == second.Repository.Clean &&
		reflect.DeepEqual(first.Repository.Reasons, second.Repository.Reasons) && reflect.DeepEqual(first.Inputs, second.Inputs) &&
		first.ProjectionHash == second.ProjectionHash && reflect.DeepEqual(first.Compatibility.Inputs, second.Compatibility.Inputs) &&
		reflect.DeepEqual(first.Compatibility.Findings, second.Compatibility.Findings)
}

func compatibilityDigests(inputs []planner.CompatibilityInput) map[string]string {
	result := map[string]string{}
	for _, input := range inputs {
		result[input.Name] = input.SHA256
	}
	return result
}

func isTailnetPilotScope(scope string) bool {
	for _, candidate := range authoritystate.TailnetScopes {
		if scope == candidate {
			return true
		}
	}
	return false
}

func authorityAssignments(artifact Artifact, digests map[string]string) []AuthorityAssignment {
	result := []AuthorityAssignment{
		{ID: "instance_specification_v1", Scope: "candidate_desired_state", Disposition: "candidate", SourceCommit: artifact.Instance.Commit},
		{ID: "legacy_engine_inventory", Scope: "execution_inventory", Disposition: "continuing", SourceCommit: artifact.Engine.Commit},
		{ID: "observation_v1", Scope: "standard_substrate_health", Disposition: "evidence", SourceSHA256: artifact.Observation.GenerationSHA256},
	}
	for _, finding := range artifact.Compatibility.Findings {
		if finding.Class != "compatibility_only" {
			continue
		}
		result = append(result, AuthorityAssignment{
			ID: assignmentID(finding), FindingID: finding.ID, Authority: finding.Authority,
			Scope: finding.Path, Disposition: "continuing", SourceSHA256: digests[finding.Authority],
		})
	}
	sort.Slice(result, func(i, j int) bool { return result[i].ID < result[j].ID })
	return result
}

func adoptionAction(finding planner.Finding, before, digest string) Action {
	return Action{
		ID: actionID("adopt_instance_specification", finding.ID), FindingID: finding.ID, Operation: "adopt_instance_specification", Scope: finding.Path,
		AuthorityBefore: before, AuthorityAfter: "instance_specification_v1", Executor: "future_authorized_apply",
		Preconditions: []string{"active_controller_fenced", "exact_plan_revalidated", "rollback_prepared"},
		Rollback:      Rollback{Strategy: "restore_authority", Authority: before, SourceSHA256: digest},
	}
}

func retainedAction(finding planner.Finding, digest string) Action {
	return Action{
		ID: actionID("retain_legacy", finding.ID), FindingID: finding.ID, AuthorityAssignmentID: assignmentID(finding), Operation: "retain_legacy", Scope: finding.Path,
		AuthorityBefore: finding.Authority, AuthorityAfter: finding.Authority, Executor: "none",
		Preconditions: []string{"legacy_authority_remains_active"},
		Rollback:      Rollback{Strategy: "no_mutation", Authority: finding.Authority, SourceSHA256: digest},
	}
}

func refusalAction(finding planner.Finding) Action {
	return Action{
		ID: actionID("refuse", finding.ID), FindingID: finding.ID, Operation: "refuse", Scope: finding.Path,
		AuthorityBefore: sourceAuthority(finding), AuthorityAfter: sourceAuthority(finding), Executor: "none",
		Preconditions: []string{"compatibility_refusal_resolved"},
		Rollback:      Rollback{Strategy: "no_mutation", Authority: sourceAuthority(finding)},
	}
}

func sourceAuthority(finding planner.Finding) string {
	if strings.HasPrefix(finding.Path, "deployment.") {
		return "legacy_deployment"
	}
	if strings.HasPrefix(finding.Path, "controller_ha.") {
		return "legacy_controller_ha"
	}
	return "legacy_platform_resources"
}

func exactCoverage(artifact Artifact, digests map[string]string) (bool, []Refusal) {
	refusals := []Refusal{}
	add := func(code, scope, message string) { refusals = append(refusals, refusal(code, scope, message)) }
	if len(artifact.CompatibilityInputs) != 3 || artifact.Observation.GenerationSHA256 == "" {
		add("coverage.inputs", "plan.coverage", "exact compatibility inputs and observation evidence are required")
	}
	seenInputs := map[string]bool{}
	for _, input := range artifact.CompatibilityInputs {
		if input.Name == "" || input.SHA256 == "" || seenInputs[input.Name] {
			add("coverage.input", "plan.coverage", "compatibility input names and digests must be non-empty and unique")
		}
		seenInputs[input.Name] = true
	}
	findings := map[string]planner.Finding{}
	for _, finding := range artifact.Compatibility.Findings {
		if finding.ID == "" || finding.ID != planner.CanonicalFindingID(finding) || findings[finding.ID].ID != "" {
			add("coverage.finding-id", finding.Path, "compatibility finding IDs must be non-empty and unique")
			continue
		}
		findings[finding.ID] = finding
	}
	actions := map[string][]Action{}
	for _, action := range artifact.Actions {
		if action.FindingID == "" {
			continue
		}
		if findings[action.FindingID].ID == "" {
			add("coverage.action-extra", action.Scope, "an action references an unknown compatibility finding")
		}
		actions[action.FindingID] = append(actions[action.FindingID], action)
	}
	assignments := map[string][]AuthorityAssignment{}
	assignmentIDs := map[string]bool{}
	for _, assignment := range artifact.Authorities {
		if assignmentIDs[assignment.ID] {
			add("coverage.authority-id", assignment.Scope, "authority assignment IDs must be unique")
		}
		assignmentIDs[assignment.ID] = true
		if assignment.FindingID == "" {
			continue
		}
		if findings[assignment.FindingID].ID == "" {
			add("coverage.authority-extra", assignment.Scope, "an authority assignment references an unknown finding")
		}
		assignments[assignment.FindingID] = append(assignments[assignment.FindingID], assignment)
	}
	for id, finding := range findings {
		if len(actions[id]) != 1 {
			add("coverage.action-count", finding.Path, "each compatibility finding must bind to exactly one action")
			continue
		}
		action := actions[id][0]
		if action.Scope != finding.Path {
			add("coverage.action-scope", finding.Path, "the finding action scope does not match the finding path")
		}
		if finding.Class != "compatibility_only" {
			if len(assignments[id]) != 0 {
				add("coverage.authority-extra", finding.Path, "only compatibility-only findings can bind continuing authority")
			}
			continue
		}
		if len(assignments[id]) != 1 {
			add("coverage.authority-count", finding.Path, "each compatibility-only finding must bind to exactly one continuing authority")
			continue
		}
		assignment := assignments[id][0]
		expectedDigest := digests[finding.Authority]
		if finding.Authority == "" || expectedDigest == "" || assignment.Authority != finding.Authority ||
			assignment.Disposition != "continuing" || assignment.Scope != finding.Path || assignment.SourceSHA256 != expectedDigest ||
			action.AuthorityAssignmentID != assignment.ID || action.AuthorityBefore != finding.Authority ||
			action.AuthorityAfter != finding.Authority || action.Rollback.SourceSHA256 != expectedDigest {
			add("coverage.authority-mismatch", finding.Path, "continuing authority coverage must match the finding and exact source digest")
		}
	}
	return len(refusals) == 0, refusals
}

func refusal(code, scope, message string) Refusal {
	return Refusal{Code: code, Scope: scope, Message: message}
}

func actionID(operation, scope string) string {
	digest := sha256.Sum256([]byte(operation + "\x00" + scope))
	return fmt.Sprintf("%s-%x", operation, digest[:8])
}

func assignmentID(finding planner.Finding) string {
	digest := sha256.Sum256([]byte("continuing_authority\x00" + finding.ID))
	return fmt.Sprintf("authority-%x", digest[:8])
}
