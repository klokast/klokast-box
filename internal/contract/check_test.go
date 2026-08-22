package contract

import (
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

const testCommit = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

var testEngine = Engine{
	Repository: "https://github.com/klokast/klokast-box",
	Ref:        "main",
	Commit:     testCommit,
}

func TestCanonicalSingleBoxAndTwoBoxFixtures(t *testing.T) {
	for _, fixture := range []string{"single", "two"} {
		t.Run(fixture, func(t *testing.T) {
			root := prepareInstance(t, fixture, nil)
			report, err := Check(root, testEngine)
			if err != nil {
				t.Fatal(err)
			}
			if !report.Valid {
				t.Fatalf("fixture is invalid: %#v", report.Diagnostics)
			}
		})
	}
}

func TestDirtyWorktreeIsAccepted(t *testing.T) {
	root := prepareInstance(t, "single", nil)
	writeTestFile(t, filepath.Join(root, "README.md"), "dirty but permitted\n")
	report, err := Check(root, testEngine)
	if err != nil || !report.Valid {
		t.Fatalf("dirty worktree rejected: err=%v diagnostics=%#v", err, report.Diagnostics)
	}
}

func TestStrictJSONAndAuthoritativeTracking(t *testing.T) {
	t.Run("duplicate", func(t *testing.T) {
		root := prepareInstance(t, "single", func(root string) {
			replaceInFile(t, filepath.Join(root, InstancePath), `"schema-version": 1`, `"schema-version": 1, "schema-version": 1`)
		})
		requireCode(t, root, "json.duplicate")
	})
	t.Run("unknown", func(t *testing.T) {
		root := prepareInstance(t, "single", func(root string) {
			replaceInFile(t, filepath.Join(root, InstancePath), `"apps": {}`, `"apps": {}, "datasets": {}`)
		})
		requireCode(t, root, "schema.invalid")
	})
	t.Run("untracked", func(t *testing.T) {
		root := prepareInstance(t, "single", nil)
		runGit(t, root, "rm", "--cached", InstancePath)
		requireCode(t, root, "git.untracked")
	})
	t.Run("obsolete-yaml", func(t *testing.T) {
		root := prepareInstance(t, "single", func(root string) {
			writeTestFile(t, filepath.Join(root, "klokast.yml"), "contract: 1\n")
		})
		requireCode(t, root, "tracked.obsolete")
	})
}

func TestEngineAndSchemaPins(t *testing.T) {
	t.Run("lock-commit", func(t *testing.T) {
		root := prepareInstance(t, "single", func(root string) {
			replaceInFile(t, filepath.Join(root, LockPath), `"commit": "`+testCommit+`"`, `"commit": "`+strings.Repeat("b", 40)+`"`)
		})
		requireCode(t, root, "engine.mismatch")
	})
	t.Run("instance-schema", func(t *testing.T) {
		root := prepareInstance(t, "single", func(root string) {
			replaceInFile(t, filepath.Join(root, InstancePath), testCommit, strings.Repeat("b", 40))
		})
		requireCode(t, root, "schema.engine")
	})
	t.Run("repository", func(t *testing.T) {
		root := prepareInstance(t, "single", func(root string) {
			replaceInFile(t, filepath.Join(root, LockPath), "https://github.com/klokast/klokast-box", "https://github.com/example/fork")
		})
		requireCode(t, root, "schema.invalid")
	})
}

func TestIdentityReferencesAndAirunners(t *testing.T) {
	tests := []struct {
		name string
		old  string
		new  string
		code string
	}{
		{"controller", `"active": "k001"`, `"active": "missing"`, "reference.box"},
		{"controller-cardinality", `"standby": "k002"`, `"standby": "k001"`, "cardinality.controller"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			root := prepareInstance(t, "two", func(root string) {
				replaceInFile(t, filepath.Join(root, InstancePath), test.old, test.new)
			})
			requireCode(t, root, test.code)
		})
	}
}

func TestInlineSiteMetadata(t *testing.T) {
	t.Run("top-level-sites", func(t *testing.T) {
		root := prepareInstance(t, "single", func(root string) {
			mutateInstanceJSON(t, root, func(value map[string]any) {
				value["sites"] = map[string]any{
					"milla": map[string]any{"country": "FR", "description": "Example home"},
				}
			})
		})
		requireCode(t, root, "schema.invalid")
	})
	t.Run("missing-country", func(t *testing.T) {
		root := prepareInstance(t, "single", func(root string) {
			mutateInstanceJSON(t, root, func(value map[string]any) {
				box := value["boxes"].(map[string]any)["k001"].(map[string]any)
				delete(box, "country")
			})
		})
		requireCode(t, root, "schema.invalid")
	})
	t.Run("shared-site-metadata", func(t *testing.T) {
		root := prepareInstance(t, "two", func(root string) {
			replaceInFile(t, filepath.Join(root, InstancePath), `"site": "milla"`, `"site": "mingdu"`)
		})
		requireCode(t, root, "site.inconsistent")
	})
}

func TestAirunnerRuntimeIdentityContract(t *testing.T) {
	tests := []struct {
		name   string
		mutate func(map[string]any)
		code   string
	}{
		{"old-object", func(value map[string]any) {
			value["airunners"] = map[string]any{
				"preferred": "k001-ops-airunner",
				"authorized": map[string]any{"k001-ops-airunner": map[string]any{"kind": "controller-container", "box": "k001"}},
			}
		}, "schema.invalid"},
		{"empty", func(value map[string]any) { value["airunners"] = []any{} }, "schema.invalid"},
		{"duplicate", func(value map[string]any) {
			value["airunners"] = []any{"k001-ops-airunner", "k001-ops-airunner"}
		}, "schema.invalid"},
		{"unknown-provider", func(value map[string]any) {
			value["airunners"] = []any{"digitalocean-ops"}
		}, "reference.cloud-provider"},
		{"invalid-suffix", func(value map[string]any) {
			value["airunners"] = []any{"vultr-runner"}
		}, "identity.airunner"},
		{"removed-box-guest", func(value map[string]any) {
			value["airunners"] = []any{"k001-airunner"}
		}, "identity.airunner"},
		{"non-controller-box", func(value map[string]any) {
			value["boxes"].(map[string]any)["k003"] = map[string]any{
				"site": "milla", "country": "FR", "description": "", "connectivity-profiles": []any{"tailscale"},
			}
			value["airunners"] = []any{"k003-ops-airunner"}
		}, "reference.controller"},
		{"box-cloud-collision", func(value map[string]any) {
			value["boxes"].(map[string]any)["vultr"] = map[string]any{
				"site": "milla", "country": "FR", "description": "", "connectivity-profiles": []any{"tailscale"},
			}
			value["airunners"] = []any{"vultr-ops"}
		}, "identity.cloud-collision"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			root := prepareInstance(t, "two", func(root string) {
				mutateInstanceJSON(t, root, test.mutate)
			})
			requireCode(t, root, test.code)
		})
	}
}

func TestEmbeddedCloudProviderCatalog(t *testing.T) {
	providers, err := loadCloudProviders()
	if err != nil {
		t.Fatal(err)
	}
	if len(providers) != 2 || providers["hetzner"].Domain != "hetzner.com" || providers["vultr"].Domain != "vultr.com" {
		t.Fatalf("unexpected embedded cloud-provider catalog: %#v", providers)
	}
	invalid := []string{
		`{"schema-version":1}`,
		`{"Hetzner":{"name":"Hetzner","domain":"hetzner.com","comment":""}}`,
		`{"hetzner":{"name":"other","domain":"hetzner.com","comment":""}}`,
		`{"hetzner":{"name":"hetzner","domain":"Hetzner.com","comment":""}}`,
		`{"hetzner":{"name":"hetzner","domain":"hetzner.com","comment":"","region":"hel1"}}`,
	}
	for index, content := range invalid {
		if _, err := validateCloudProviders([]byte(content)); err == nil {
			t.Errorf("invalid catalog %d was accepted", index)
		}
	}
}

func TestVersionOneBoxRequiresTailscaleConnectivity(t *testing.T) {
	root := prepareInstance(t, "two", func(root string) {
		replaceInFile(t, filepath.Join(root, InstancePath),
			`"connectivity-profiles": [
        "local-ap-direct-egress",
        "tailscale"
      ]`,
			`"connectivity-profiles": [
        "local-ap-direct-egress"
      ]`)
	})
	requireCode(t, root, "connectivity.tailscale")
}

func TestAppLifecycleAndDataRules(t *testing.T) {
	t.Run("absent-needs-data", func(t *testing.T) {
		root := prepareInstance(t, "two", func(root string) {
			path := filepath.Join(root, InstancePath)
			var value map[string]any
			if err := json.Unmarshal([]byte(readTestFile(t, path)), &value); err != nil {
				t.Fatal(err)
			}
			music := value["apps"].(map[string]any)["music"].(map[string]any)
			delete(music, "data")
			content, err := json.MarshalIndent(value, "", "  ")
			if err != nil {
				t.Fatal(err)
			}
			writeTestFile(t, path, string(content)+"\n")
		})
		requireCode(t, root, "schema.invalid")
	})
	t.Run("unknown-data", func(t *testing.T) {
		root := prepareInstance(t, "two", func(root string) {
			replaceInFile(t, filepath.Join(root, InstancePath), `"library": {`, `"unknown": {`)
		})
		requireCode(t, root, "data.unknown")
	})
	t.Run("data-box", func(t *testing.T) {
		root := prepareInstance(t, "two", func(root string) {
			replaceInFile(t, filepath.Join(root, InstancePath), `"box": "k002",`, `"box": "missing",`)
		})
		requireCode(t, root, "reference.box")
	})
}

func TestSecretAndGeneratedStateDetectionDoesNotEchoValues(t *testing.T) {
	secret := "super-secret-token-value"
	root := prepareInstance(t, "single", func(root string) {
		writeTestFile(t, filepath.Join(root, "notes.json"), `{"token":"`+secret+`"}`+"\n")
		writeTestFile(t, filepath.Join(root, ".klokast/plan.json"), "{}\n")
	})
	runGit(t, root, "add", "-f", ".klokast/plan.json")
	report, err := Check(root, testEngine)
	if err != nil {
		t.Fatal(err)
	}
	if !hasCode(report, "secret.raw") || !hasCode(report, "tracked.forbidden") {
		t.Fatalf("missing tracked-content diagnostics: %#v", report.Diagnostics)
	}
	for _, diagnostic := range report.Diagnostics {
		if strings.Contains(diagnostic.Message, secret) {
			t.Fatalf("secret leaked in diagnostic: %#v", diagnostic)
		}
	}
}

func TestTrackedContentLimits(t *testing.T) {
	t.Run("binary", func(t *testing.T) {
		root := prepareInstance(t, "single", func(root string) {
			if err := os.WriteFile(filepath.Join(root, "binary.dat"), []byte{'a', 0, 'b'}, 0o644); err != nil {
				t.Fatal(err)
			}
		})
		requireCode(t, root, "tracked.binary")
	})
	t.Run("size", func(t *testing.T) {
		root := prepareInstance(t, "single", func(root string) {
			if err := os.WriteFile(filepath.Join(root, "large.txt"), []byte(strings.Repeat("a", maximumTrackedFile+1)), 0o644); err != nil {
				t.Fatal(err)
			}
		})
		requireCode(t, root, "tracked.size")
	})
}

func TestCompatibilityYAMLRequiresStringKeys(t *testing.T) {
	_, diagnostics := ParseSafeYAML([]byte("1: value\n"))
	if len(diagnostics) == 0 || diagnostics[0].Code != "yaml.key" {
		t.Fatalf("non-string YAML key was accepted: %#v", diagnostics)
	}
}

func TestSafePathsAndStandaloneRepository(t *testing.T) {
	t.Run("symlink", func(t *testing.T) {
		root := prepareInstance(t, "single", nil)
		outside := filepath.Join(t.TempDir(), "instance.json")
		writeTestFile(t, outside, "{}\n")
		if err := os.Remove(filepath.Join(root, InstancePath)); err != nil {
			t.Fatal(err)
		}
		if err := os.Symlink(outside, filepath.Join(root, InstancePath)); err != nil {
			t.Fatal(err)
		}
		runGit(t, root, "add", InstancePath)
		requireCode(t, root, "path.symlink")
	})
	t.Run("nested", func(t *testing.T) {
		outer := t.TempDir()
		runGit(t, outer, "init", "-q")
		root := filepath.Join(outer, "instance")
		if err := os.Mkdir(root, 0o755); err != nil {
			t.Fatal(err)
		}
		report, err := Check(root, testEngine)
		if err != nil {
			t.Fatal(err)
		}
		if !hasCode(report, "git.repository") {
			t.Fatalf("missing git.repository: %#v", report.Diagnostics)
		}
	})
}

func TestOperationalFailureForMissingInstance(t *testing.T) {
	if _, err := Check(filepath.Join(t.TempDir(), "missing"), testEngine); err == nil {
		t.Fatal("missing instance did not produce operational failure")
	}
}

func prepareInstance(t *testing.T, fixture string, mutate func(string)) string {
	t.Helper()
	root := t.TempDir()
	for _, support := range []string{"README.md", "AGENTS.md", ".gitignore"} {
		content, err := os.ReadFile(filepath.Join(repositoryRoot(t), "templates", "instance", support))
		if err != nil {
			t.Fatal(err)
		}
		writeTestFile(t, filepath.Join(root, support), string(content))
	}
	source := filepath.Join(repositoryRoot(t), "tests", "fixtures", "contract", "init-single.json")
	if fixture == "two" {
		source = filepath.Join(repositoryRoot(t), "tests", "fixtures", "contract", "valid-two", InstancePath)
	}
	content, err := os.ReadFile(source)
	if err != nil {
		t.Fatal(err)
	}
	writeTestFile(t, filepath.Join(root, InstancePath), string(content))
	writeTestFile(t, filepath.Join(root, LockPath), fmt.Sprintf(`{
  "$schema": "https://raw.githubusercontent.com/klokast/klokast-box/%s/schemas/klokast-lock-v1.schema.json",
  "engine": {
    "commit": "%s",
    "ref": "main",
    "repository": "https://github.com/klokast/klokast-box"
  },
  "schema-version": 1
}
`, testCommit, testCommit))
	if mutate != nil {
		mutate(root)
	}
	runGit(t, root, "init", "-q")
	runGit(t, root, "add", "-A")
	return root
}

func repositoryRoot(t *testing.T) string {
	t.Helper()
	root, err := filepath.Abs(filepath.Join("..", ".."))
	if err != nil {
		t.Fatal(err)
	}
	return root
}

func requireCode(t *testing.T, root, code string) {
	t.Helper()
	report, err := Check(root, testEngine)
	if err != nil {
		t.Fatal(err)
	}
	if !hasCode(report, code) {
		t.Fatalf("missing %s: %#v", code, report.Diagnostics)
	}
}

func hasCode(report Report, code string) bool {
	for _, diagnostic := range report.Diagnostics {
		if diagnostic.Code == code {
			return true
		}
	}
	return false
}

func replaceInFile(t *testing.T, path, old, replacement string) {
	t.Helper()
	content := readTestFile(t, path)
	if strings.Count(content, old) != 1 {
		t.Fatalf("%q does not occur exactly once in %s", old, path)
	}
	writeTestFile(t, path, strings.Replace(content, old, replacement, 1))
}

func mutateInstanceJSON(t *testing.T, root string, mutate func(map[string]any)) {
	t.Helper()
	path := filepath.Join(root, InstancePath)
	var value map[string]any
	if err := json.Unmarshal([]byte(readTestFile(t, path)), &value); err != nil {
		t.Fatal(err)
	}
	mutate(value)
	content, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		t.Fatal(err)
	}
	writeTestFile(t, path, string(content)+"\n")
}

func readTestFile(t *testing.T, path string) string {
	t.Helper()
	content, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	return string(content)
}

func writeTestFile(t *testing.T, path, content string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
}

func runGit(t *testing.T, root string, args ...string) {
	t.Helper()
	command := exec.Command("git", append([]string{"-C", root}, args...)...)
	command.Env = append(os.Environ(), "GIT_CONFIG_NOSYSTEM=1", "GIT_CONFIG_GLOBAL=/dev/null")
	if output, err := command.CombinedOutput(); err != nil {
		t.Fatalf("git %v: %v: %s", args, err, output)
	}
}
