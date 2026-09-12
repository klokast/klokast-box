// Package deploymentplan creates a deterministic, read-only Plan v6 artifact.
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
	InstanceOnly               bool
	LegacyRetirement           bool
	RetirementPhase            string
	RetirementEvidence         string
	MigrationTarget            string
	ConnectivityTarget         string
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
	MigrationTarget     string                       `json:"migration_target"`
	ConnectivityTarget  string                       `json:"connectivity_target"`
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
	LegacyRetirement   *LegacyRetirementReference     `json:"legacy_retirement,omitempty"`
	Inputs              []planner.InputDigest        `json:"inputs"`
	CompatibilityInputs []planner.CompatibilityInput `json:"compatibility_inputs"`
	Projection          *planner.Projection          `json:"projection,omitempty"`
	Inventory           *planner.InventoryProjection `json:"inventory,omitempty"`
	ProjectionHash      string                       `json:"projection_sha256,omitempty"`
	Observation         ObservationReference         `json:"observation"`
	Compatibility       *planner.Compatibility       `json:"compatibility,omitempty"`
	Authorities         []AuthorityAssignment        `json:"authorities"`
	Actions             []Action                     `json:"actions"`
	ActionGroups        []ActionGroup                `json:"action_groups"`
	SelectedBox         string                       `json:"selected_box,omitempty"`
	Refusals            []Refusal                    `json:"refusals"`
	Diagnostics         []contract.Diagnostic        `json:"diagnostics"`
	PlanSHA256          string                       `json:"plan_sha256,omitempty"`
}

type AuthorityStateReference struct {
	AuthorityStateSHA256 string                  `json:"authority_state_sha256"`
	Kind                 string                  `json:"kind"`
	SettingGroups        []SettingGroupReference `json:"setting_groups"`
}

type SettingGroupReference struct {
	ID     string   `json:"id"`
	Scopes []string `json:"scopes"`
	Source string   `json:"source"`
}

type ToolchainReference struct {
	ReceiptSHA256 string `json:"receipt_sha256"`
	EngineCommit  string `json:"engine_commit"`
}

type ActionGroup struct {
	ID           string   `json:"id"`
	Operation    string   `json:"operation"`
	Scopes       []string `json:"scopes"`
	Executor     string   `json:"executor"`
	RollbackType string   `json:"rollback_type"`
	Box          string   `json:"box,omitempty"`
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
	if options.LegacyRetirement {
		return buildLegacyRetirement(options, engine)
	}
	if options.InstanceOnly {
		return buildInstanceOnly(options, engine)
	}
	if options.MigrationTarget == "" {
		options.MigrationTarget = "connectivity"
	}
	if options.MigrationTarget != "connectivity" && options.MigrationTarget != "controller-identity" && options.MigrationTarget != "registry" && options.MigrationTarget != "inventory" {
		return Artifact{}, fmt.Errorf("migration target must be connectivity, controller-identity, registry, or inventory")
	}
	if options.ConnectivityTarget == "" {
		options.ConnectivityTarget = "non-controller"
	}
	if options.ConnectivityTarget != "non-controller" && options.ConnectivityTarget != "active-controller" {
		return Artifact{}, fmt.Errorf("connectivity target must be non-controller or active-controller")
	}
	artifact := Artifact{
		MigrationTarget:    options.MigrationTarget,
		ConnectivityTarget: options.ConnectivityTarget,
		SchemaVersion:      6,
		Kind:               "klokast.plan.v6",
		HealthScope:        "standard_substrate_v1",
		Engine:             planner.Engine{Repository: engine.Repository, Ref: engine.Ref, Commit: engine.Commit},
		Inputs:             []planner.InputDigest{}, CompatibilityInputs: []planner.CompatibilityInput{},
		Authorities: []AuthorityAssignment{}, Actions: []Action{}, ActionGroups: []ActionGroup{}, Refusals: []Refusal{},
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
	state, err := authoritystate.LoadCurrent(options.AuthorityState)
	if err != nil {
		return Artifact{}, err
	}
	toolchainReceipt, err := toolchain.Load(options.ControllerToolchainReceipt, engine.Commit)
	if err != nil {
		return Artifact{}, err
	}
	if options.MigrationTarget == "inventory" || state.Kind == authoritystate.KindV5 {
		artifact.SchemaVersion, artifact.Kind = 7, "klokast.plan.v7"
		inventory, err := planner.Inventory(options.InstancePath, engine)
		if err != nil {
			return Artifact{}, err
		}
		if !inventory.Valid || inventory.Projection == nil || !reflect.DeepEqual(inventory.Inputs, report.Inputs) || !reflect.DeepEqual(inventory.Repository, report.Repository) {
			return Artifact{}, fmt.Errorf("inventory projection is incomplete or differs from the checked private inputs")
		}
		artifact.Inventory = inventory.Projection
	}
	if (artifact.SchemaVersion == 7 && toolchainReceipt.SchemaVersion != 6 && toolchainReceipt.SchemaVersion != 7) || (artifact.SchemaVersion == 6 && toolchainReceipt.SchemaVersion != 5 && toolchainReceipt.SchemaVersion != 6 && toolchainReceipt.SchemaVersion != 7) {
		return Artifact{}, fmt.Errorf("Plan v7 requires Toolchain v6; Plan v6 requires Toolchain v5 or v6")
	}
	artifact.AuthorityState = AuthorityStateReference{
		AuthorityStateSHA256: state.AuthorityStateSHA256,
		Kind:                 state.Kind,
		SettingGroups:        []SettingGroupReference{},
	}
	for _, group := range state.SettingGroups {
		artifact.AuthorityState.SettingGroups = append(
			artifact.AuthorityState.SettingGroups,
			SettingGroupReference{ID: group.ID, Scopes: append([]string{}, group.Scopes...), Source: group.Source},
		)
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
	if !authorityGroupsMatchProjection(state, artifact.Projection) {
		artifact.Refusals = append(artifact.Refusals, refusal(
			"authority-state.groups", "authority_state.setting_groups",
			"Authority State v2 groups do not match the complete instance box set",
		))
	}
	if state.Kind == authoritystate.KindV5 {
		scopes := []string{}
		for _, group := range state.SettingGroups {
			if group.ID == authoritystate.InventoryGroupID {
				scopes = group.Scopes
			}
		}
		if artifact.Inventory == nil || !reflect.DeepEqual(scopes, artifact.Inventory.Scopes) {
			artifact.Refusals = append(artifact.Refusals, refusal("inventory.scopes", "inventory", "inventory source differs from the complete private scope set"))
		}
	}
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
	groupFindings := map[string]planner.Finding{}
	groupScopes := map[string]string{}
	for _, scope := range authoritystate.TailnetScopes {
		groupScopes[scope] = authoritystate.TailnetGroupID
	}
	for _, box := range artifact.Projection.Boxes {
		groupID := authoritystate.BoxConnectivityPrefix + box.ID
		for _, scope := range authoritystate.BoxConnectivityScopes(box.ID) {
			groupScopes[scope] = groupID
		}
	}
	identityGrouped := options.MigrationTarget == "controller-identity" || state.Kind == authoritystate.KindV3 || state.Kind == authoritystate.KindV4 || state.Kind == authoritystate.KindV5
	if identityGrouped {
		for _, scope := range authoritystate.ControllerIdentityScopes {
			groupScopes[scope] = authoritystate.ControllerIdentityGroupID
		}
	}
	registryGrouped := artifact.Projection.Registry != nil
	if registryGrouped {
		for _, scope := range artifact.Projection.Registry.Scopes {
			groupScopes[scope] = authoritystate.RegistryGroupID
		}
	} else if options.MigrationTarget == "registry" || state.Kind == authoritystate.KindV4 || state.Kind == authoritystate.KindV5 {
		artifact.Refusals = append(artifact.Refusals, refusal("registry.incomplete", "registry", "registry source requires a complete instance-derived registry"))
	}
	if artifact.Inventory != nil {
		for _, scope := range artifact.Inventory.Scopes {
			groupScopes[scope] = authoritystate.InventoryGroupID
		}
	}
	for _, finding := range artifact.Compatibility.Findings {
		if _, grouped := groupScopes[finding.Path]; grouped {
			groupFindings[finding.Path] = finding
			continue
		}
		switch finding.Class {
		case "matched", "derived":
			if finding.Code == "registry.schema" || finding.Code == "deployment.schema" || finding.Code == "controller.schema" || finding.Code == "controller.engine-policy" {
				artifact.Actions = append(artifact.Actions, Action{
					ID: actionID("verify_engine_policy", finding.ID), FindingID: finding.ID, Scope: finding.Path,
					Operation: "verify_engine_policy", AuthorityBefore: "engine_policy", AuthorityAfter: "engine_policy", Executor: "none",
					Preconditions: []string{"exact_plan_v6_revalidated"}, Rollback: Rollback{Strategy: "no_mutation", Authority: "engine_policy"},
				})
				continue
			}
			before := sourceAuthority(finding)
			if finding.Code == "controller.instance-specification" {
				before = "controller_ha_markers"
			}
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
	tailnetSource, err := authoritystate.CurrentGroupSource(state, authoritystate.TailnetGroupID)
	if err != nil {
		return Artifact{}, err
	}
	artifact.ActionGroups = append(artifact.ActionGroups, ActionGroup{
		ID:           authoritystate.TailnetGroupID,
		Operation:    "verify_instance_authority",
		Scopes:       append([]string{}, authoritystate.TailnetScopes...),
		Executor:     "none",
		RollbackType: "no_mutation",
	})
	legacyDigest := digests[authoritystate.LegacyAuthority]
	for _, scope := range authoritystate.TailnetScopes {
		finding, present := groupFindings[scope]
		if !present || finding.Class != "matched" || legacyDigest == "" || tailnetSource != authoritystate.InstanceAuthority {
			artifact.Refusals = append(artifact.Refusals, refusal(
				"tailnet-group.not-verifiable", scope,
				"the completed Tailnet group requires matched evidence and instance authority",
			))
			continue
		}
		artifact.Actions = append(artifact.Actions, Action{
			ID: actionID("verify_instance_authority", finding.ID), FindingID: finding.ID,
			Operation: "verify_instance_authority", Scope: scope,
			AuthorityBefore: authoritystate.InstanceAuthority,
			AuthorityAfter:  authoritystate.InstanceAuthority, Executor: "none",
			Preconditions: []string{"active_controller_fenced", "exact_plan_v6_revalidated"},
			Rollback:      Rollback{Strategy: "no_mutation", Authority: authoritystate.InstanceAuthority},
		})
	}
	artifact.SelectedBox = selectNonControllerBox(artifact.Projection)
	active := artifact.Projection.ControlPlane.ActiveController
	if artifact.SelectedBox == "" || active.Hostname != active.BoxID+"-ops" || artifact.Observation.SourceController != active.Hostname {
		artifact.SelectedBox = ""
		artifact.Refusals = append(artifact.Refusals, refusal(
			"box-connectivity.selection", "boxes",
			"connectivity requires two instance boxes and the matching active controller observation",
		))
	} else if options.ConnectivityTarget == "active-controller" {
		peerSource, _ := authoritystate.CurrentGroupSource(state, authoritystate.BoxConnectivityPrefix+artifact.SelectedBox)
		if peerSource != authoritystate.InstanceAuthority || tailnetSource != authoritystate.InstanceAuthority {
			artifact.Refusals = append(artifact.Refusals, refusal(
				"box-connectivity.prerequisite", "boxes",
				"controller box adoption requires instance authority for the peer and Tailnet groups",
			))
		}
		artifact.SelectedBox = active.BoxID
	}
	registryDigest := digests[authoritystate.LegacyRegistrySource]
	for _, box := range artifact.Projection.Boxes {
		groupID := authoritystate.BoxConnectivityPrefix + box.ID
		source, sourceErr := authoritystate.CurrentGroupSource(state, groupID)
		if sourceErr != nil {
			artifact.Refusals = append(artifact.Refusals, refusal("box-connectivity.authority", groupID, sourceErr.Error()))
			continue
		}
		operation, executor := "retain_legacy", "none"
		if source == authoritystate.InstanceAuthority {
			operation = "verify_instance_authority"
		}
		rollbackType := "box_connectivity_registry_v1"
		preconditions := []string{"active_controller_fenced", "exact_plan_v6_revalidated", "effective_registry_compiles_equal", "one_router_rollback_prepared"}
		if box.ID == artifact.SelectedBox && options.MigrationTarget == "connectivity" {
			operation, executor = "adopt_instance_specification", "box_connectivity_v1"
			if options.ConnectivityTarget == "active-controller" {
				executor = "controller_box_connectivity_source_v1"
				rollbackType = "no_mutation"
				preconditions = []string{"active_controller_fenced", "exact_plan_v6_revalidated", "effective_registry_compiles_equal", "router_verified_before_source_publication"}
			}
			if source == authoritystate.InstanceAuthority {
				operation = "verify_instance_authority"
			}
		}
		artifact.ActionGroups = append(artifact.ActionGroups, ActionGroup{
			ID: groupID, Operation: operation,
			Scopes: authoritystate.BoxConnectivityScopes(box.ID), Executor: executor,
			RollbackType: rollbackType, Box: box.ID,
		})
		for _, scope := range authoritystate.BoxConnectivityScopes(box.ID) {
			finding, present := groupFindings[scope]
			expectedClass := "matched"
			if scope == "boxes."+box.ID || scope == "boxes."+box.ID+".connectivity" {
				expectedClass = "derived"
			}
			if !present || finding.Class != expectedClass || registryDigest == "" {
				artifact.Refusals = append(artifact.Refusals, refusal(
					"box-connectivity.not-matched", scope,
					"the complete box connectivity group requires exact legacy registry evidence",
				))
				continue
			}
			before, after := source, source
			if operation == "adopt_instance_specification" || operation == "verify_instance_authority" {
				after = authoritystate.InstanceAuthority
			}
			artifact.Actions = append(artifact.Actions, Action{
				ID: actionID(operation, finding.ID), FindingID: finding.ID,
				Operation: operation, Scope: scope, AuthorityBefore: before,
				AuthorityAfter: after, Executor: executor,
				Preconditions: preconditions,
				Rollback: Rollback{
					Strategy:     rollbackType,
					Authority:    authoritystate.LegacyRegistrySource,
					SourceSHA256: registryDigest,
				},
			})
		}
	}
	if identityGrouped {
		addControllerIdentityGroup(&artifact, state, groupFindings, digests)
	}
	if registryGrouped {
		addRegistryGroup(&artifact, state, groupFindings)
	}
	if artifact.Inventory != nil {
		addInventoryGroup(&artifact, state, groupFindings)
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
	if artifact.SchemaVersion == 7 {
		for i := range artifact.Actions {
			for j, precondition := range artifact.Actions[i].Preconditions {
				if precondition == "exact_plan_v6_revalidated" {
					artifact.Actions[i].Preconditions[j] = "exact_plan_v7_revalidated"
				}
			}
		}
	}
	sort.Slice(artifact.Actions, func(i, j int) bool { return artifact.Actions[i].ID < artifact.Actions[j].ID })
	sort.Slice(artifact.ActionGroups, func(i, j int) bool { return artifact.ActionGroups[i].ID < artifact.ActionGroups[j].ID })
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
		return "", fmt.Errorf("encode Plan v5 artifact: %w", err)
	}
	var value any
	decoder := json.NewDecoder(bytes.NewReader(encoded))
	decoder.UseNumber()
	if err := decoder.Decode(&value); err != nil {
		return "", fmt.Errorf("canonicalize Plan v5 artifact: %w", err)
	}
	var canonical bytes.Buffer
	encoder := json.NewEncoder(&canonical)
	encoder.SetEscapeHTML(false)
	if err := encoder.Encode(value); err != nil {
		return "", fmt.Errorf("canonicalize Plan v5 artifact: %w", err)
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

func selectNonControllerBox(projection *planner.Projection) string {
	if projection == nil || len(projection.Boxes) != 2 {
		return ""
	}
	activeFound := false
	for _, box := range projection.Boxes {
		activeFound = activeFound || box.ID == projection.ControlPlane.ActiveController.BoxID
	}
	if !activeFound {
		return ""
	}
	selected := ""
	for _, box := range projection.Boxes {
		if box.ID == projection.ControlPlane.ActiveController.BoxID {
			continue
		}
		if selected != "" {
			return ""
		}
		selected = box.ID
	}
	return selected
}

func authorityGroupsMatchProjection(state authoritystate.StateV2, projection *planner.Projection) bool {
	if projection == nil {
		return false
	}
	expected := map[string][]string{
		authoritystate.TailnetGroupID: authoritystate.TailnetScopes,
	}
	for _, box := range projection.Boxes {
		expected[authoritystate.BoxConnectivityPrefix+box.ID] = authoritystate.BoxConnectivityScopes(box.ID)
	}
	if state.Kind == authoritystate.KindV3 || state.Kind == authoritystate.KindV4 || state.Kind == authoritystate.KindV5 {
		expected[authoritystate.ControllerIdentityGroupID] = authoritystate.ControllerIdentityScopes
	}
	if state.Kind == authoritystate.KindV4 || state.Kind == authoritystate.KindV5 {
		if projection.Registry == nil {
			return false
		}
		expected[authoritystate.RegistryGroupID] = projection.Registry.Scopes
	}
	extra := 0
	if state.Kind == authoritystate.KindV5 {
		extra = 1
	}
	if len(state.SettingGroups) != len(expected)+extra {
		return false
	}
	for _, group := range state.SettingGroups {
		if state.Kind == authoritystate.KindV5 && group.ID == authoritystate.InventoryGroupID {
			continue // Checked against the independent sealed inventory projection.
		}
		scopes, present := expected[group.ID]
		if !present || !reflect.DeepEqual(group.Scopes, scopes) {
			return false
		}
	}
	return true
}

func authorityAssignments(artifact Artifact, digests map[string]string) []AuthorityAssignment {
	result := []AuthorityAssignment{
		{ID: "instance_specification_v1", Scope: "candidate_desired_state", Disposition: "candidate", SourceCommit: artifact.Instance.Commit},
		{ID: "legacy_engine_inventory", Scope: "execution_inventory", Disposition: "continuing", SourceCommit: artifact.Engine.Commit},
		{ID: "observation_v1", Scope: "standard_substrate_health", Disposition: "evidence", SourceSHA256: artifact.Observation.GenerationSHA256},
	}
	if artifact.AuthorityState.Kind == authoritystate.KindV5 && artifact.Inventory != nil {
		result[1] = AuthorityAssignment{ID: "instance_execution_inventory_v1", Scope: authoritystate.InventoryScope,
			Authority: authoritystate.InstanceAuthority, Disposition: "active", SourceCommit: artifact.Instance.Commit,
			SourceSHA256: artifact.Inventory.InventorySHA256}
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
		AuthorityBefore: before, AuthorityAfter: "instance_specification_v1", Executor: "unimplemented_action",
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
