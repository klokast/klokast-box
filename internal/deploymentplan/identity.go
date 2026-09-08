package deploymentplan

import (
	"klokast-box/internal/authoritystate"
	"klokast-box/internal/planner"
)

func addControllerIdentityGroup(artifact *Artifact, state authoritystate.StateV2, findings map[string]planner.Finding, digests map[string]string) {
	selected := artifact.MigrationTarget == "controller-identity"
	operation, executor := "verify_instance_authority", "none"
	if selected {
		executor = "controller_identity_source_v1"
		if state.Kind == authoritystate.KindV2 {
			operation = "adopt_instance_specification"
		}
		for _, group := range state.SettingGroups {
			if group.Source != authoritystate.InstanceAuthority {
				artifact.Refusals = append(artifact.Refusals, refusal("controller-identity.prerequisite", group.ID, "controller identity adoption requires both box groups and Tailnet to use the instance"))
			}
		}
	}
	active, standby := artifact.Projection.ControlPlane.ActiveController, artifact.Projection.ControlPlane.StandbyController
	if len(artifact.Projection.Boxes) != 2 || standby == nil || standby.BoxID == active.BoxID || standby.Hostname != standby.BoxID+"-ops" {
		artifact.Refusals = append(artifact.Refusals, refusal("controller-identity.pair", "controllers", "controller identity migration requires the current active and standby pair"))
	}
	artifact.ActionGroups = append(artifact.ActionGroups, ActionGroup{
		ID: authoritystate.ControllerIdentityGroupID, Operation: operation,
		Scopes: append([]string{}, authoritystate.ControllerIdentityScopes...), Executor: executor, RollbackType: "no_mutation",
	})
	for _, scope := range authoritystate.ControllerIdentityScopes {
		finding, ok := findings[scope]
		before, class := "legacy_controller_ha", "matched"
		if scope == "deployment.control_plane.controller" {
			before, class = "controller_ha_markers", "derived"
		}
		if state.Kind == authoritystate.KindV3 || state.Kind == authoritystate.KindV4 || state.Kind == authoritystate.KindV5 {
			before = authoritystate.InstanceAuthority
		}
		if !ok || finding.Class != class || digests["legacy_controller_ha"] == "" {
			artifact.Refusals = append(artifact.Refusals, refusal("controller-identity.compatibility", scope, "controller identity scope lacks exact compatibility evidence"))
			continue
		}
		artifact.Actions = append(artifact.Actions, Action{
			ID: actionID(operation, finding.ID), FindingID: finding.ID, Scope: scope, Operation: operation,
			AuthorityBefore: before, AuthorityAfter: authoritystate.InstanceAuthority, Executor: executor,
			Preconditions: []string{"configured_controller_pair", "exact_plan_v6_revalidated", "equal_controller_configuration", "roles_verified_before_source_publication"},
			Rollback:      Rollback{Strategy: "no_mutation", Authority: before},
		})
	}
}
