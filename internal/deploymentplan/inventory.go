package deploymentplan

import (
	"klokast-box/internal/authoritystate"
	"klokast-box/internal/planner"
)

var inventoryPreconditions = []string{"configured_controller_pair", "exact_plan_v7_revalidated", "equal_complete_inventory",
	"equal_complete_registry", "equal_compiler_and_router_variables", "declared_runners_verified", "routers_verified_before_source_publication"}

func addInventoryGroup(artifact *Artifact, state authoritystate.StateV2, findings map[string]planner.Finding) {
	selected, adopted := artifact.MigrationTarget == "inventory", state.Kind == authoritystate.KindV5
	operation, executor := "retain_legacy", "none"
	if adopted {
		operation = "verify_instance_authority"
	}
	if selected {
		executor = "inventory_source_v1"
		if !adopted {
			operation = "adopt_instance_specification"
		}
		if state.Kind != authoritystate.KindV4 && !adopted {
			artifact.Refusals = append(artifact.Refusals, refusal("inventory.prerequisite", "inventory", "inventory adoption requires all five prior groups adopted"))
		}
	}
	artifact.ActionGroups = append(artifact.ActionGroups, ActionGroup{ID: authoritystate.InventoryGroupID,
		Operation: operation, Executor: executor, Scopes: append([]string{}, artifact.Inventory.Scopes...), RollbackType: "no_mutation"})
	for _, scope := range artifact.Inventory.Scopes {
		before := "none"
		findingID, id := "", operation+"-execution-inventory"
		if scope == authoritystate.InventoryScope {
			before = "legacy_engine_inventory"
		} else {
			finding, ok := findings[scope]
			if !ok || finding.Code != "airunner.instance-specification" || finding.Class != "derived" {
				artifact.Refusals = append(artifact.Refusals, refusal("inventory.runner", scope, "runner scope lacks its exact private identity finding"))
				continue
			}
			findingID, id = finding.ID, actionID(operation, finding.ID)
		}
		if adopted {
			before = authoritystate.InstanceAuthority
		}
		artifact.Actions = append(artifact.Actions, Action{ID: id, FindingID: findingID, Scope: scope,
			Operation: operation, Executor: executor, AuthorityBefore: before, AuthorityAfter: authoritystate.InstanceAuthority,
			Preconditions: append([]string{}, inventoryPreconditions...), Rollback: Rollback{Strategy: "no_mutation", Authority: before}})
	}
}
