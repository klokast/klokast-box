package deploymentplan

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"klokast-box/internal/contract"
)

func TestCheckpointExtensionsCannotAuthorizeExistingExecutors(t *testing.T) {
	instance := prepareInstance(t)
	path := filepath.Join(instance, contract.InstancePath)
	content, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var desired map[string]any
	if err := json.Unmarshal(content, &desired); err != nil {
		t.Fatal(err)
	}
	desired["inactive-apps"] = map[string]any{}
	desired["boxes"].(map[string]any)["boxa"].(map[string]any)["substrate"] = map[string]any{}
	writeFile(t, instance, contract.InstancePath, string(canonicalTestJSON(t, desired)))
	runGit(t, instance, "add", contract.InstancePath)
	runGit(t, instance, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "checkpoint fields")
	options := compatibilityOptions(t, instance)
	for _, target := range []string{"non-controller", "active-controller"} {
		options.ConnectivityTarget = target
		for _, migration := range []string{"connectivity", "controller-identity"} {
			options.MigrationTarget = migration
			plan, err := Build(options, testEngine)
			if err != nil {
				t.Fatal(err)
			}
			if !plan.Valid || plan.Compatible || plan.Deployable || plan.AuthorityReady {
				t.Fatalf("%s/%s accepted checkpoint fields: %#v", migration, target, plan)
			}
			refused := map[string]bool{}
			for _, action := range plan.Actions {
				if action.Scope == "inactive-apps" || action.Scope == "boxes.boxa.substrate" {
					if action.Operation != "refuse" || action.Executor != "none" {
						t.Fatalf("checkpoint field received an executable action: %#v", action)
					}
					refused[action.Scope] = true
				}
			}
			if len(refused) != 2 {
				t.Fatalf("checkpoint scope coverage incomplete: %#v", refused)
			}
		}
	}
}
