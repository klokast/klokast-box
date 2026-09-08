package deploymentplan

import (
	"strings"

	"klokast-box/internal/authoritystate"
	"klokast-box/internal/planner"
)

func addRegistryGroup(artifact *Artifact, state authoritystate.StateV2, findings map[string]planner.Finding) {
	selected := artifact.MigrationTarget == "registry"
	adopted := state.Kind == authoritystate.KindV4 || state.Kind == authoritystate.KindV5
	operation, executor := "retain_legacy", "none"
	if adopted {
		operation = "verify_instance_authority"
	}
	if selected {
		executor = "registry_source_v1"
		if !adopted {
			operation = "adopt_instance_specification"
		}
		if state.Kind != authoritystate.KindV3 && !adopted {
			artifact.Refusals = append(artifact.Refusals, refusal("registry.prerequisite", "registry", "registry adoption requires adopted controller identity and connectivity"))
		}
		for _, group := range state.SettingGroups {
			if group.Source != authoritystate.InstanceAuthority {
				artifact.Refusals = append(artifact.Refusals, refusal("registry.prerequisite", group.ID, "registry adoption requires all prior groups to use the instance"))
			}
		}
	} else if !adopted {
		artifact.Refusals = append(artifact.Refusals, refusal("registry.selection", "registry", "complete registry inputs require explicit registry adoption before other migrations"))
	}
	artifact.ActionGroups = append(artifact.ActionGroups, ActionGroup{ID: authoritystate.RegistryGroupID, Operation: operation,
		Executor: executor, Scopes: append([]string{}, artifact.Projection.Registry.Scopes...), RollbackType: "no_mutation"})
	for _, scope := range artifact.Projection.Registry.Scopes {
		finding, ok := findings[scope]
		if !ok || (finding.Class != "matched" && finding.Class != "derived") {
			artifact.Refusals = append(artifact.Refusals, refusal("registry.compatibility", scope, "registry field requires exact saved-value or omitted-default evidence"))
			if ok {
				artifact.Actions = append(artifact.Actions, refusalAction(finding))
			}
			continue
		}
		before, after := authoritystate.LegacyRegistrySource, authoritystate.InstanceAuthority
		if adopted || strings.Contains(scope, ".data.") {
			before = authoritystate.InstanceAuthority
		}
		if operation == "retain_legacy" {
			after = before
		}
		artifact.Actions = append(artifact.Actions, Action{
			ID: actionID(operation, finding.ID), FindingID: finding.ID, Scope: scope, Operation: operation,
			AuthorityBefore: before, AuthorityAfter: after, Executor: executor,
			Preconditions: []string{"configured_controller_pair", "exact_plan_v6_revalidated", "equal_complete_registry", "equal_compiler_and_router_variables", "routers_verified_before_source_publication"},
			Rollback:      Rollback{Strategy: "no_mutation", Authority: before},
		})
	}
}
