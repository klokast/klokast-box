package deploymentplan

import (
	"encoding/json"
	"fmt"
	"reflect"
	"sort"
	"time"

	"klokast-box/internal/authoritystate"
	"klokast-box/internal/contract"
	"klokast-box/internal/doctor"
	"klokast-box/internal/instancesource"
	"klokast-box/internal/planner"
	"klokast-box/internal/toolchain"
)

const VerificationExecutor = "instance_verification_v1"

// MarshalJSON preserves historical artifacts while excluding comparison claims
// and migration selection from the independent verification contract.
func (a Artifact) MarshalJSON() ([]byte, error) {
	type plain Artifact
	content, err := json.Marshal(plain(a))
	if err != nil || a.SchemaVersion != 8 {
		return content, err
	}
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(content, &fields); err != nil {
		return nil, err
	}
	for _, field := range []string{"compatible", "compatibility", "compatibility_inputs", "migration_target", "connectivity_target", "selected_box"} {
		delete(fields, field)
	}
	return json.Marshal(fields)
}

func buildInstanceOnly(options Options, engine contract.Engine) (Artifact, error) {
	a := Artifact{SchemaVersion: 8, Kind: "klokast.plan.v8", HealthScope: "standard_substrate_v1",
		Engine: planner.Engine{Repository: engine.Repository, Ref: engine.Ref, Commit: engine.Commit},
		Inputs: []planner.InputDigest{}, Authorities: []AuthorityAssignment{}, Actions: []Action{},
		ActionGroups: []ActionGroup{}, Refusals: []Refusal{}, Diagnostics: []contract.Diagnostic{}}
	if options.MigrationTarget != "" || options.ConnectivityTarget != "" || options.CompatibilityDeployment != "" || options.CompatibilityRegistry != "" || options.CompatibilityControllerHA != "" {
		return a, fmt.Errorf("instance-only planning rejects compatibility inputs and migration targets")
	}
	snapshot, checked, err := contract.Load(options.InstancePath, engine)
	if err != nil {
		return a, err
	}
	if !checked.Valid {
		a.Diagnostics = checked.Diagnostics
		return a, nil
	}
	projection := planner.Resolve(snapshot)
	a.Projection = &projection
	a.ProjectionHash, err = planner.ProjectionHash(projection)
	if err != nil {
		return a, err
	}
	inventory, err := planner.Inventory(options.InstancePath, engine)
	if err != nil {
		return a, err
	}
	if !inventory.Valid {
		a.Diagnostics = inventory.Diagnostics
		return a, nil
	}
	a.Inventory, a.Inputs = inventory.Projection, inventory.Inputs
	for _, input := range snapshot.Inputs {
		found := false
		for _, current := range a.Inputs {
			if current.Path == input.Path && current.SHA256 == input.SHA256 {
				found = true
			}
		}
		if !found {
			return a, fmt.Errorf("instance inputs changed before inventory rendering")
		}
	}
	state, err := authoritystate.LoadCurrent(options.AuthorityState)
	if err != nil {
		return a, err
	}
	receipt, err := toolchain.Load(options.ControllerToolchainReceipt, engine.Commit)
	if err != nil {
		return a, err
	}
	if receipt.SchemaVersion != 7 {
		return a, fmt.Errorf("Plan v8 requires Controller Toolchain v7")
	}
	a.ControllerToolchain = ToolchainReference{ReceiptSHA256: receipt.ReceiptSHA256, EngineCommit: receipt.EngineCommit}
	if state.Kind != authoritystate.KindV5 || !authorityGroupsMatchProjection(state, a.Projection) || len(state.SettingGroups) != 6 || a.Inventory == nil {
		return a, fmt.Errorf("instance-only planning requires complete Authority State v5 for all six instance groups")
	}
	a.AuthorityState = AuthorityStateReference{AuthorityStateSHA256: state.AuthorityStateSHA256, Kind: state.Kind, SettingGroups: []SettingGroupReference{}}
	for _, group := range state.SettingGroups {
		if group.Source != authoritystate.InstanceAuthority || (group.ID == authoritystate.InventoryGroupID && !reflect.DeepEqual(group.Scopes, a.Inventory.Scopes)) {
			return a, fmt.Errorf("instance-only ownership or scope membership changed: %s", group.ID)
		}
		a.AuthorityState.SettingGroups = append(a.AuthorityState.SettingGroups, SettingGroupReference{ID: group.ID, Scopes: group.Scopes, Source: group.Source})
		a.ActionGroups = append(a.ActionGroups, ActionGroup{ID: group.ID, Operation: "verify_instance_authority", Scopes: group.Scopes, Executor: VerificationExecutor, RollbackType: "no_mutation"})
		for _, scope := range group.Scopes {
			a.Actions = append(a.Actions, Action{ID: actionID("verify_instance_authority", scope), Scope: scope, Operation: "verify_instance_authority", AuthorityBefore: group.Source, AuthorityAfter: group.Source, Executor: VerificationExecutor,
				Preconditions: []string{"exact_plan_v8_revalidated", "complete_instance_ownership", "read_only_consumers_verified"}, Rollback: Rollback{Strategy: "no_mutation", Authority: group.Source}})
		}
	}
	source, diagnostics, err := instancesource.Load(options.InstanceSourceReceipt, time.Now().UTC())
	if err != nil {
		return a, err
	}
	if len(diagnostics) != 0 {
		a.Diagnostics = diagnostics
		return a, nil
	}
	a.InstanceSource = source.Reference()
	a.Instance = InstanceIdentity{Branch: inventory.Repository.Branch, Commit: inventory.Repository.HeadCommit}
	if source.Commit != a.Instance.Commit || source.RemoteRef != "refs/heads/"+a.Instance.Branch {
		a.Refusals = append(a.Refusals, refusal("instance-source.mismatch", "instance.repository", "source receipt differs from the checked instance"))
	}
	for _, reason := range inventory.Repository.Reasons {
		a.Refusals = append(a.Refusals, refusal(reason, "instance.repository", "instance repository must be clean and committed"))
	}
	health, err := doctor.Doctor(doctor.Options{InstancePath: options.InstancePath, ObservationPath: options.ObservationPath}, engine)
	if err != nil {
		return a, err
	}
	if !health.Valid {
		a.Diagnostics = health.Diagnostics
		return a, nil
	}
	if health.ProjectionHash != a.ProjectionHash || !reflect.DeepEqual(health.Inputs, a.Inputs) {
		return a, fmt.Errorf("instance changed during observation verification")
	}
	a.SubstrateHealthy = health.Healthy
	a.Observation = ObservationReference{SourceController: health.ObservationSource, ObservedAt: health.ObservedAt, GenerationSHA256: health.ObservationGeneration}
	for _, finding := range health.Findings {
		a.Refusals = append(a.Refusals, refusal("observation."+finding.Code, finding.Path, finding.Message))
	}
	second, err := planner.Inventory(options.InstancePath, engine)
	if err != nil {
		return a, err
	}
	secondState, err := authoritystate.LoadCurrent(options.AuthorityState)
	if err != nil {
		return a, err
	}
	secondSource, diagnostics, err := instancesource.Load(options.InstanceSourceReceipt, time.Now().UTC())
	if err != nil {
		return a, err
	}
	secondToolchain, err := toolchain.Load(options.ControllerToolchainReceipt, engine.Commit)
	if err != nil {
		return a, err
	}
	if !reflect.DeepEqual(second, inventory) || !reflect.DeepEqual(state, secondState) || len(diagnostics) != 0 || !reflect.DeepEqual(source, secondSource) || !reflect.DeepEqual(receipt, secondToolchain) {
		return a, fmt.Errorf("inputs or evidence changed during instance-only planning")
	}
	a.Authorities = []AuthorityAssignment{{ID: authoritystate.InstanceAuthority, Authority: authoritystate.InstanceAuthority, Scope: "desired_state", Disposition: "active", SourceCommit: a.Instance.Commit}, {ID: "observation_v1", Scope: "standard_substrate_health", Disposition: "evidence", SourceSHA256: health.ObservationGeneration}}
	sort.Slice(a.Actions, func(i, j int) bool { return a.Actions[i].ID < a.Actions[j].ID })
	a.Valid = true
	a.Deployable = inventory.Repository.Clean && a.Instance.Commit != "" && a.SubstrateHealthy && len(a.Refusals) == 0
	a.AuthorityReady = a.Deployable
	a.LegacyRemovalReady = false
	a.PlanSHA256, err = Hash(a)
	return a, err
}
