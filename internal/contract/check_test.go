package contract

import (
	"fmt"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	klokastbox "klokast-box"
)

const testCommit = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

func TestCanonicalSingleBoxAndTwoBoxFixtures(t *testing.T) {
	for _, fixture := range []string{"single", "two"} {
		t.Run(fixture, func(t *testing.T) {
			root := prepareInstance(t, fixture, nil)
			report, err := Check(root, testCommit)
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
	report, err := Check(root, testCommit)
	if err != nil || !report.Valid {
		t.Fatalf("dirty worktree rejected: err=%v diagnostics=%#v", err, report.Diagnostics)
	}
}

func TestInvalidLockAndAuthoritativeTracking(t *testing.T) {
	t.Run("mismatch", func(t *testing.T) {
		root := prepareInstance(t, "single", func(root string) {
			replaceInFile(t, filepath.Join(root, lockPath), testCommit, strings.Repeat("b", 40))
		})
		requireCode(t, root, testCommit, "engine.mismatch")
	})
	t.Run("abbreviated", func(t *testing.T) {
		root := prepareInstance(t, "single", func(root string) {
			replaceInFile(t, filepath.Join(root, lockPath), testCommit, "abc123")
		})
		requireCode(t, root, testCommit, "schema.invalid")
	})
	t.Run("untracked", func(t *testing.T) {
		root := prepareInstance(t, "single", nil)
		runGit(t, root, "rm", "--cached", "ops/deployment.yml")
		requireCode(t, root, testCommit, "git.untracked")
	})
}

func TestIdentityReferencesAndCardinality(t *testing.T) {
	tests := []struct {
		name string
		old  string
		new  string
		code string
	}{
		{"duplicate-prefix", "hostname_prefix: k002", "hostname_prefix: k001", "identity.prefix"},
		{"broken-site", "site: site-002", "site: missing-site", "reference.site"},
		{"broken-controller", "active_box: box-001", "active_box: missing-box", "reference.box"},
		{"controller-cardinality", "standby_box: box-002", "standby_box: box-001", "cardinality.controller"},
		{"duplicate-runner-id", "id: airunner-cloud-001", "id: airunner-001", "identity.airunner"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			root := prepareInstance(t, "two", func(root string) {
				replaceInFile(t, filepath.Join(root, "ops/deployment.yml"), test.old, test.new)
			})
			requireCode(t, root, testCommit, test.code)
		})
	}
}

func TestRunnerUnionUnknownFieldsAndYAMLSafety(t *testing.T) {
	t.Run("runner-union", func(t *testing.T) {
		root := prepareInstance(t, "single", func(root string) {
			replaceInFile(t, filepath.Join(root, "ops/deployment.yml"), "      box: box-001", "      box: box-001\n      hostname: forbidden.example")
		})
		requireCode(t, root, testCommit, "schema.invalid")
	})
	t.Run("unknown-field", func(t *testing.T) {
		root := prepareInstance(t, "single", func(root string) {
			replaceInFile(t, filepath.Join(root, "klokast.yml"), "contract: 1", "contract: 1\nunknown: true")
		})
		requireCode(t, root, testCommit, "schema.invalid")
	})
	t.Run("duplicate-key", func(t *testing.T) {
		root := prepareInstance(t, "single", func(root string) {
			replaceInFile(t, filepath.Join(root, "klokast.yml"), "contract: 1", "contract: 1\ncontract: 1")
		})
		requireCode(t, root, testCommit, "yaml.duplicate")
	})
	t.Run("custom-tag", func(t *testing.T) {
		root := prepareInstance(t, "single", func(root string) {
			replaceInFile(t, filepath.Join(root, "klokast.yml"), "contract: 1", "contract: !unsafe 1")
		})
		requireCode(t, root, testCommit, "yaml.tag")
	})
}

func TestSafePathsAndStandaloneRepository(t *testing.T) {
	t.Run("traversal", func(t *testing.T) {
		root := prepareInstance(t, "single", func(root string) {
			replaceInFile(t, filepath.Join(root, "klokast.yml"), "ops/deployment.yml", "../deployment.yml")
		})
		requireCode(t, root, testCommit, "path.unsafe")
	})
	t.Run("symlink", func(t *testing.T) {
		root := prepareInstance(t, "single", nil)
		outside := filepath.Join(t.TempDir(), "deployment.yml")
		writeTestFile(t, outside, "schema_version: 1\n")
		if err := os.Remove(filepath.Join(root, "ops/deployment.yml")); err != nil {
			t.Fatal(err)
		}
		if err := os.Symlink(outside, filepath.Join(root, "ops/deployment.yml")); err != nil {
			t.Fatal(err)
		}
		runGit(t, root, "add", "ops/deployment.yml")
		requireCode(t, root, testCommit, "path.symlink")
	})
	t.Run("nested", func(t *testing.T) {
		outer := t.TempDir()
		runGit(t, outer, "init", "-q")
		root := filepath.Join(outer, "instance")
		if err := os.Mkdir(root, 0o755); err != nil {
			t.Fatal(err)
		}
		report, err := Check(root, testCommit)
		if err != nil {
			t.Fatal(err)
		}
		if !hasCode(report, "git.repository") {
			t.Fatalf("missing git.repository: %#v", report.Diagnostics)
		}
	})
}

func TestCapabilityAndPlacementRules(t *testing.T) {
	t.Run("enabled-undeclared", func(t *testing.T) {
		root := prepareInstance(t, "single", func(root string) {
			replaceInFile(t, filepath.Join(root, "ops/platform-resources.yml"), "enabled_capabilities:\n        - overlay", "enabled_capabilities:\n        - overlay\n        - local-lan")
		})
		requireCode(t, root, testCommit, "capability.undeclared")
	})
	t.Run("enabled-prohibited", func(t *testing.T) {
		root := prepareInstance(t, "single", func(root string) {
			replaceInFile(t, filepath.Join(root, "ops/platform-resources.yml"), "        - direct-ingress\n      policy:", "        - direct-ingress\n        - overlay\n      policy:")
		})
		requireCode(t, root, testCommit, "capability.conflict")
	})
	t.Run("policy-disabled", func(t *testing.T) {
		root := prepareInstance(t, "single", func(root string) {
			replaceInFile(t, filepath.Join(root, "ops/platform-resources.yml"), "public-ingress: none", "public-ingress: direct-ingress")
		})
		requireCode(t, root, testCommit, "capability.policy")
	})
	t.Run("broken-placement", func(t *testing.T) {
		root := prepareInstance(t, "single", func(root string) {
			replaceInFile(t, filepath.Join(root, "ops/platform-resources.yml"), "box: box-001", "box: missing-box")
		})
		requireCode(t, root, testCommit, "reference.box")
	})
	t.Run("manifest-placement-mode", func(t *testing.T) {
		root := prepareInstance(t, "single", func(root string) {
			platform := filepath.Join(root, "ops/platform-resources.yml")
			replaceInFile(t, platform, "nextcloud:", "torrent:")
			replaceInFile(t, platform, "cloudflare-tunnel-egress", "vpn-egress")
			replaceInFile(t, platform, "mode: single_box\n      box: box-001", "mode: multi_box\n      boxes: [box-001]")
		})
		requireCode(t, root, testCommit, "placement.mode")
	})
}

func TestSecretAndGeneratedStateDetectionDoesNotEchoValues(t *testing.T) {
	secret := "super-secret-token-value"
	root := prepareInstance(t, "single", func(root string) {
		writeTestFile(t, filepath.Join(root, "notes.yml"), "token: "+secret+"\n")
		writeTestFile(t, filepath.Join(root, ".klokast/plan.json"), "{}\n")
	})
	report, err := Check(root, testCommit)
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

func TestOperationalFailureForMissingInstance(t *testing.T) {
	if _, err := Check(filepath.Join(t.TempDir(), "missing"), testCommit); err == nil {
		t.Fatal("missing instance did not produce operational failure")
	}
}

func prepareInstance(t *testing.T, fixture string, mutate func(string)) string {
	t.Helper()
	root := t.TempDir()
	if err := fs.WalkDir(klokastbox.Assets, "templates/instance", func(path string, entry fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		relative, err := filepath.Rel("templates/instance", path)
		if err != nil || relative == "." {
			return err
		}
		destination := filepath.Join(root, relative)
		if entry.IsDir() {
			return os.MkdirAll(destination, 0o755)
		}
		content, err := klokastbox.Assets.ReadFile(path)
		if err != nil {
			return err
		}
		return os.WriteFile(destination, content, 0o644)
	}); err != nil {
		t.Fatal(err)
	}
	writeTestFile(t, filepath.Join(root, lockPath), fmt.Sprintf("---\nschema_version: 1\nengine:\n  repository: https://github.com/klokast/klokast-box\n  ref: main\n  commit: %s\n", testCommit))
	if fixture == "two" {
		for source, destination := range map[string]string{
			"tests/fixtures/contract/valid-two/deployment.yml":         "ops/deployment.yml",
			"tests/fixtures/contract/valid-two/platform-resources.yml": "ops/platform-resources.yml",
		} {
			content, err := os.ReadFile(filepath.Join(repositoryRoot(t), source))
			if err != nil {
				t.Fatal(err)
			}
			writeTestFile(t, filepath.Join(root, destination), string(content))
		}
	}
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

func requireCode(t *testing.T, root, commit, code string) {
	t.Helper()
	report, err := Check(root, commit)
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
	content, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Count(string(content), old) != 1 {
		t.Fatalf("%q does not occur exactly once in %s", old, path)
	}
	writeTestFile(t, path, strings.Replace(string(content), old, replacement, 1))
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
	if output, err := command.CombinedOutput(); err != nil {
		t.Fatalf("git %v: %v: %s", args, err, output)
	}
}
