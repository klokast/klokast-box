package instance

import (
	"encoding/json"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"klokast-box/internal/contract"
)

const approvedTestCommit = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

var approvedTestEngine = contract.Engine{
	Repository: "https://github.com/klokast/klokast-box",
	Ref:        "main",
	Commit:     approvedTestCommit,
}

func TestInitCreatesCheckedStandaloneSingleBoxInstance(t *testing.T) {
	parent := t.TempDir()
	valuesPath := writeValues(t, parent, validValues())
	destination := filepath.Join(parent, "private-instance")
	result, err := Init(Options{
		InstancePath: destination,
		Profile:      ProfileSingleBox,
		ValuesPath:   valuesPath,
	}, approvedTestEngine)
	if err != nil {
		t.Fatal(err)
	}
	if !result.Created || result.InstancePath != destination || result.Profile != ProfileSingleBox {
		t.Fatalf("unexpected result: %#v", result)
	}
	if result.Engine.Commit != approvedTestCommit {
		t.Fatalf("unexpected engine result: %#v", result.Engine)
	}
	info, err := os.Stat(destination)
	if err != nil {
		t.Fatal(err)
	}
	if info.Mode().Perm() != 0o700 {
		t.Fatalf("instance mode = %o, want 700", info.Mode().Perm())
	}
	deployment := readTestFile(t, filepath.Join(destination, "ops/deployment.yml"))
	for _, expected := range []string{
		"name: family-klokast", "magicdns_suffix: example.ts.net", "timezone: Etc/UTC",
		"physical_location: Example home", "hostname_prefix: k001", "active_box: box-001",
		"id: airunner-001", "kind: controller_container",
	} {
		if !strings.Contains(deployment, expected) {
			t.Fatalf("deployment omits %q:\n%s", expected, deployment)
		}
	}
	platform := readTestFile(t, filepath.Join(destination, "ops/platform-resources.yml"))
	if !strings.Contains(platform, "nextcloud:\n    enabled: false") || !strings.Contains(platform, "mode: single_box") {
		t.Fatalf("canonical disabled Nextcloud selection changed:\n%s", platform)
	}
	lock := readTestFile(t, filepath.Join(destination, "klokast.lock.yml"))
	if !strings.Contains(lock, "commit: "+approvedTestCommit) || !strings.Contains(lock, "ref: main") {
		t.Fatalf("lock does not bind approved engine:\n%s", lock)
	}
	if _, err := os.Stat(filepath.Join(destination, filepath.Base(valuesPath))); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("values input was copied into the instance: %v", err)
	}
	if branch := runGitOutput(t, destination, "branch", "--show-current"); branch != "main" {
		t.Fatalf("branch = %q, want main", branch)
	}
	if command := exec.Command("git", "-C", destination, "rev-parse", "--verify", "HEAD"); command.Run() == nil {
		t.Fatal("init created a Git commit")
	}
	if command := exec.Command("git", "-C", destination, "remote"); strings.TrimSpace(runCommandOutput(t, command)) != "" {
		t.Fatal("init configured a Git remote")
	}
	tracked := runGitOutput(t, destination, "ls-files")
	for _, expected := range []string{"klokast.yml", "klokast.lock.yml", "ops/deployment.yml", "ops/platform-resources.yml"} {
		if !strings.Contains(tracked, expected) {
			t.Fatalf("tracked inputs omit %s:\n%s", expected, tracked)
		}
	}
	report, err := contract.Check(destination, approvedTestEngine)
	if err != nil {
		t.Fatal(err)
	}
	if !report.Valid {
		t.Fatalf("generated instance is invalid: %#v", report.Diagnostics)
	}
}

func TestInitOmitsOptionalPhysicalLocation(t *testing.T) {
	parent := t.TempDir()
	values := validValues()
	delete(values["site"].(map[string]any), "physical_location")
	result, err := Init(Options{
		InstancePath: filepath.Join(parent, "instance"),
		Profile:      ProfileSingleBox,
		ValuesPath:   writeValues(t, parent, values),
	}, approvedTestEngine)
	if err != nil {
		t.Fatal(err)
	}
	deployment := readTestFile(t, filepath.Join(result.InstancePath, "ops/deployment.yml"))
	if strings.Contains(deployment, "physical_location") || !strings.Contains(deployment, "family:\n    - admin@example.com") {
		t.Fatalf("unexpected optional fields:\n%s", deployment)
	}
}

func TestInitRejectsInvalidInputsAndCleansStaging(t *testing.T) {
	t.Run("timezone-field", func(t *testing.T) {
		parent := t.TempDir()
		values := validValues()
		values["site"].(map[string]any)["timezone"] = "Europe/Paris"
		requireValidationCode(t, Options{
			InstancePath: filepath.Join(parent, "instance"),
			Profile:      ProfileSingleBox,
			ValuesPath:   writeValues(t, parent, values),
		}, approvedTestEngine, "schema.invalid")
		requireNoStaging(t, parent)
	})
	t.Run("duplicate-json-key", func(t *testing.T) {
		parent := t.TempDir()
		path := filepath.Join(parent, "values.json")
		content := `{"schema_version":1,"schema_version":1}`
		if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
			t.Fatal(err)
		}
		requireValidationCode(t, Options{
			InstancePath: filepath.Join(parent, "instance"),
			Profile:      ProfileSingleBox,
			ValuesPath:   path,
		}, approvedTestEngine, "json.duplicate")
	})
	t.Run("multiple-json-documents", func(t *testing.T) {
		parent := t.TempDir()
		path := writeValues(t, parent, validValues())
		file, err := os.OpenFile(path, os.O_APPEND|os.O_WRONLY, 0)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := file.WriteString("{}\n"); err != nil {
			file.Close()
			t.Fatal(err)
		}
		if err := file.Close(); err != nil {
			t.Fatal(err)
		}
		requireValidationCode(t, Options{
			InstancePath: filepath.Join(parent, "instance"),
			Profile:      ProfileSingleBox,
			ValuesPath:   path,
		}, approvedTestEngine, "json.syntax")
	})
	t.Run("empty-operators", func(t *testing.T) {
		parent := t.TempDir()
		values := validValues()
		values["tailnet"].(map[string]any)["groups"].(map[string]any)["operators"] = []string{}
		requireValidationCode(t, Options{
			InstancePath: filepath.Join(parent, "instance"),
			Profile:      ProfileSingleBox,
			ValuesPath:   writeValues(t, parent, values),
		}, approvedTestEngine, "schema.invalid")
	})
	t.Run("oversized-values", func(t *testing.T) {
		parent := t.TempDir()
		path := filepath.Join(parent, "values.json")
		if err := os.WriteFile(path, make([]byte, maximumValuesFile+1), 0o600); err != nil {
			t.Fatal(err)
		}
		requireValidationCode(t, Options{
			InstancePath: filepath.Join(parent, "instance"),
			Profile:      ProfileSingleBox,
			ValuesPath:   path,
		}, approvedTestEngine, "file.size")
	})
	t.Run("values-symlink", func(t *testing.T) {
		parent := t.TempDir()
		target := writeValues(t, parent, validValues())
		link := filepath.Join(parent, "link.json")
		if err := os.Symlink(target, link); err != nil {
			t.Fatal(err)
		}
		requireValidationCode(t, Options{
			InstancePath: filepath.Join(parent, "instance"),
			Profile:      ProfileSingleBox,
			ValuesPath:   link,
		}, approvedTestEngine, "path.symlink")
	})
	t.Run("existing-destination", func(t *testing.T) {
		parent := t.TempDir()
		destination := filepath.Join(parent, "instance")
		if err := os.Mkdir(destination, 0o700); err != nil {
			t.Fatal(err)
		}
		requireValidationCode(t, Options{
			InstancePath: destination,
			Profile:      ProfileSingleBox,
			ValuesPath:   writeValues(t, parent, validValues()),
		}, approvedTestEngine, "path.exists")
	})
	t.Run("nested-worktree", func(t *testing.T) {
		parent := t.TempDir()
		runGitOutput(t, parent, "init", "-q")
		requireValidationCode(t, Options{
			InstancePath: filepath.Join(parent, "instance"),
			Profile:      ProfileSingleBox,
			ValuesPath:   writeValues(t, parent, validValues()),
		}, approvedTestEngine, "git.nested")
	})
	t.Run("generated-contract", func(t *testing.T) {
		parent := t.TempDir()
		values := validValues()
		values["box"].(map[string]any)["hostname_prefix"] = "builder"
		requireValidationCode(t, Options{
			InstancePath: filepath.Join(parent, "instance"),
			Profile:      ProfileSingleBox,
			ValuesPath:   writeValues(t, parent, values),
		}, approvedTestEngine, "identity.prefix")
		requireNoStaging(t, parent)
	})
	t.Run("secret-like-content", func(t *testing.T) {
		parent := t.TempDir()
		secret := "ghp_1234567890abcdef"
		values := validValues()
		values["tailnet"].(map[string]any)["groups"].(map[string]any)["operators"] = []string{secret}
		_, err := Init(Options{
			InstancePath: filepath.Join(parent, "instance"),
			Profile:      ProfileSingleBox,
			ValuesPath:   writeValues(t, parent, values),
		}, approvedTestEngine)
		var validationError *ValidationError
		if !errors.As(err, &validationError) {
			t.Fatalf("Init() error = %v, want validation error", err)
		}
		found := false
		for _, diagnostic := range validationError.Diagnostics {
			if diagnostic.Code == "secret.raw" {
				found = true
			}
			if strings.Contains(diagnostic.Message, secret) {
				t.Fatalf("diagnostic exposed secret-like content: %#v", diagnostic)
			}
		}
		if !found {
			t.Fatalf("missing secret.raw: %#v", validationError.Diagnostics)
		}
		requireNoStaging(t, parent)
	})
	t.Run("unsupported-profile", func(t *testing.T) {
		parent := t.TempDir()
		requireValidationCode(t, Options{
			InstancePath: filepath.Join(parent, "instance"),
			Profile:      "two-box",
			ValuesPath:   writeValues(t, parent, validValues()),
		}, approvedTestEngine, "profile.unsupported")
	})
}

func TestInitRejectsUnapprovedBinaryBeforeCreatingDestination(t *testing.T) {
	parent := t.TempDir()
	_, err := Init(Options{
		InstancePath: filepath.Join(parent, "instance"),
		Profile:      ProfileSingleBox,
		ValuesPath:   writeValues(t, parent, validValues()),
	}, contract.Engine{Repository: "unverified", Ref: "unverified", Commit: strings.Repeat("0", 40)})
	if err == nil {
		t.Fatal("unapproved engine was accepted")
	}
	var validationError *ValidationError
	if errors.As(err, &validationError) {
		t.Fatalf("unapproved engine was reported as input validation: %v", err)
	}
	if _, statErr := os.Stat(filepath.Join(parent, "instance")); !errors.Is(statErr, os.ErrNotExist) {
		t.Fatalf("destination exists after rejection: %v", statErr)
	}
}

func TestPublishNoReplacePreservesExistingDestination(t *testing.T) {
	parent := t.TempDir()
	source := filepath.Join(parent, "source")
	destination := filepath.Join(parent, "destination")
	if err := os.Mkdir(source, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(destination, []byte("keep\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := publishNoReplace(source, destination); err == nil {
		t.Fatal("publish replaced an existing destination")
	}
	if content := readTestFile(t, destination); content != "keep\n" {
		t.Fatalf("destination content changed: %q", content)
	}
	if info, err := os.Stat(source); err != nil || !info.IsDir() {
		t.Fatalf("source was removed after rejected publication: info=%v err=%v", info, err)
	}
}

func validValues() map[string]any {
	return map[string]any{
		"schema_version": 1,
		"instance":       map[string]any{"name": "family-klokast"},
		"tailnet": map[string]any{
			"magicdns_suffix": "example.ts.net",
			"groups": map[string]any{
				"operators": []string{"admin@example.com"},
				"family":    []string{"admin@example.com"},
			},
		},
		"site": map[string]any{
			"country":           "FR",
			"physical_location": "Example home",
		},
		"box": map[string]any{"hostname_prefix": "k001"},
	}
}

func writeValues(t *testing.T, directory string, value any) string {
	t.Helper()
	content, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(directory, "values.json")
	if err := os.WriteFile(path, append(content, '\n'), 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

func requireValidationCode(t *testing.T, options Options, engine contract.Engine, code string) {
	t.Helper()
	_, err := Init(options, engine)
	var validationError *ValidationError
	if !errors.As(err, &validationError) {
		t.Fatalf("Init() error = %v, want validation error", err)
	}
	for _, diagnostic := range validationError.Diagnostics {
		if diagnostic.Code == code {
			return
		}
	}
	t.Fatalf("missing %s: %#v", code, validationError.Diagnostics)
}

func requireNoStaging(t *testing.T, parent string) {
	t.Helper()
	matches, err := filepath.Glob(filepath.Join(parent, ".*.klokast-init-*"))
	if err != nil {
		t.Fatal(err)
	}
	if len(matches) != 0 {
		t.Fatalf("staging residue remains: %v", matches)
	}
}

func readTestFile(t *testing.T, path string) string {
	t.Helper()
	content, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	return string(content)
}

func runGitOutput(t *testing.T, root string, arguments ...string) string {
	t.Helper()
	return strings.TrimSpace(runCommandOutput(t, exec.Command("git", append([]string{"-C", root}, arguments...)...)))
}

func runCommandOutput(t *testing.T, command *exec.Cmd) string {
	t.Helper()
	output, err := command.Output()
	if err != nil {
		t.Fatal(err)
	}
	return string(output)
}
