package planner

import (
	"path/filepath"
	"testing"

	"klokast-box/internal/contract"
)

func TestSchemaCheckpointRefusesRegistryExtensions(t *testing.T) {
	for _, extension := range []string{"inactive-apps", "substrate"} {
		t.Run(extension, func(t *testing.T) {
			root := prepareInstance(t, func(root string) {
				path := filepath.Join(root, contract.InstancePath)
				if extension == "inactive-apps" {
					replaceInFile(t, path, `"apps": {}`, `"apps": {}, "inactive-apps": {}`)
				} else {
					replaceInFile(t, path, `"boxa": {`, `"boxa": {"substrate": {},`)
				}
			})
			runGit(t, root, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "checkpoint input")
			options := Options{InstancePath: root, CompatibilityRegistry: writeRegistry(t, canonicalRegistry())}
			result, err := Plan(options, testEngine)
			if err != nil {
				t.Fatal(err)
			}
			if !result.Valid || result.Compatible || result.Deployable || result.AuthorityReady || !hasCode(result, "registry.checkpoint-only") {
				t.Fatalf("accepted checkpoint input was silently ignored: %#v", result)
			}
			second, err := Plan(options, testEngine)
			if err != nil || second.Compatibility.Findings[0].ID != result.Compatibility.Findings[0].ID {
				t.Fatal("checkpoint refusal is not deterministic")
			}
		})
	}
}
