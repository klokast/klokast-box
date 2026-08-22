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

func TestInitCreatesCheckedStandaloneInstance(t *testing.T) {
	parent := t.TempDir()
	valuesPath := writeValues(t, parent, validValues(t))
	destination := filepath.Join(parent, "private-instance")
	result, err := Init(Options{InstancePath: destination, ValuesPath: valuesPath}, approvedTestEngine)
	if err != nil {
		t.Fatal(err)
	}
	if !result.Created || result.InstancePath != destination || result.Engine.Commit != approvedTestCommit {
		t.Fatalf("unexpected result: %#v", result)
	}
	info, err := os.Stat(destination)
	if err != nil {
		t.Fatal(err)
	}
	if info.Mode().Perm() != 0o700 {
		t.Fatalf("instance mode = %o, want 700", info.Mode().Perm())
	}
	instance := readTestFile(t, filepath.Join(destination, contract.InstancePath))
	if strings.Contains(instance, "timezone") || !strings.Contains(instance, `"tailnet-dns-name": "example.ts.net"`) {
		t.Fatalf("generated instance has unexpected content:\n%s", instance)
	}
	lock := readTestFile(t, filepath.Join(destination, contract.LockPath))
	if !strings.Contains(lock, `"commit": "`+approvedTestCommit+`"`) || !strings.Contains(lock, `"ref": "main"`) {
		t.Fatalf("lock does not bind the approved engine:\n%s", lock)
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
	tracked := strings.Fields(runGitOutput(t, destination, "ls-files"))
	for _, expected := range []string{".gitignore", "AGENTS.md", "README.md", contract.InstancePath, contract.LockPath} {
		if !containsString(tracked, expected) {
			t.Fatalf("tracked inputs omit %s: %v", expected, tracked)
		}
	}
	for _, obsolete := range []string{"klokast.yml", "klokast.lock.yml", "ops/deployment.yml", "ops/platform-resources.yml"} {
		if containsString(tracked, obsolete) {
			t.Fatalf("obsolete input remains tracked: %s", obsolete)
		}
	}
	report, err := contract.Check(destination, approvedTestEngine)
	if err != nil || !report.Valid {
		t.Fatalf("generated instance is invalid: err=%v diagnostics=%#v", err, report.Diagnostics)
	}
}

func TestInitCanonicalizesObjectKeyOrder(t *testing.T) {
	parent := t.TempDir()
	result, err := Init(Options{
		InstancePath: filepath.Join(parent, "instance"),
		ValuesPath:   writeValues(t, parent, validValues(t)),
	}, approvedTestEngine)
	if err != nil {
		t.Fatal(err)
	}
	content := readTestFile(t, filepath.Join(result.InstancePath, contract.InstancePath))
	if strings.Index(content, `"$schema"`) > strings.Index(content, `"tailscale"`) {
		t.Fatalf("instance JSON is not deterministically encoded:\n%s", content)
	}
}

func TestInitRejectsInvalidInputsAndCleansStaging(t *testing.T) {
	tests := []struct {
		name   string
		code   string
		mutate func(map[string]any)
	}{
		{"timezone-field", "schema.invalid", func(value map[string]any) { value["timezone"] = "Europe/Paris" }},
		{"empty-members", "schema.invalid", func(value map[string]any) { value["tailscale"].(map[string]any)["members"] = map[string]any{} }},
		{"reserved-box", "identity.box", func(value map[string]any) {
			value["boxes"].(map[string]any)["builder"] = value["boxes"].(map[string]any)["boxa"]
			value["controllers"].(map[string]any)["active"] = "builder"
			value["airunners"] = []any{"builder-ops-airunner"}
		}},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			parent := t.TempDir()
			values := validValues(t)
			test.mutate(values)
			requireValidationCode(t, Options{InstancePath: filepath.Join(parent, "instance"), ValuesPath: writeValues(t, parent, values)}, approvedTestEngine, test.code)
			requireNoStaging(t, parent)
		})
	}

	t.Run("duplicate-json-key", func(t *testing.T) {
		parent := t.TempDir()
		path := filepath.Join(parent, "values.json")
		if err := os.WriteFile(path, []byte(`{"schema-version":1,"schema-version":1}`), 0o600); err != nil {
			t.Fatal(err)
		}
		requireValidationCode(t, Options{InstancePath: filepath.Join(parent, "instance"), ValuesPath: path}, approvedTestEngine, "json.duplicate")
	})
	t.Run("multiple-json-documents", func(t *testing.T) {
		parent := t.TempDir()
		path := writeValues(t, parent, validValues(t))
		file, err := os.OpenFile(path, os.O_APPEND|os.O_WRONLY, 0)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := file.WriteString("{}\n"); err != nil {
			_ = file.Close()
			t.Fatal(err)
		}
		if err := file.Close(); err != nil {
			t.Fatal(err)
		}
		requireValidationCode(t, Options{InstancePath: filepath.Join(parent, "instance"), ValuesPath: path}, approvedTestEngine, "json.syntax")
	})
	t.Run("oversized-values", func(t *testing.T) {
		parent := t.TempDir()
		path := filepath.Join(parent, "values.json")
		if err := os.WriteFile(path, make([]byte, maximumValuesFile+1), 0o600); err != nil {
			t.Fatal(err)
		}
		requireValidationCode(t, Options{InstancePath: filepath.Join(parent, "instance"), ValuesPath: path}, approvedTestEngine, "file.size")
	})
	t.Run("values-symlink", func(t *testing.T) {
		parent := t.TempDir()
		target := writeValues(t, parent, validValues(t))
		link := filepath.Join(parent, "link.json")
		if err := os.Symlink(target, link); err != nil {
			t.Fatal(err)
		}
		requireValidationCode(t, Options{InstancePath: filepath.Join(parent, "instance"), ValuesPath: link}, approvedTestEngine, "path.symlink")
	})
	t.Run("existing-destination", func(t *testing.T) {
		parent := t.TempDir()
		destination := filepath.Join(parent, "instance")
		if err := os.Mkdir(destination, 0o700); err != nil {
			t.Fatal(err)
		}
		requireValidationCode(t, Options{InstancePath: destination, ValuesPath: writeValues(t, parent, validValues(t))}, approvedTestEngine, "path.exists")
	})
	t.Run("nested-worktree", func(t *testing.T) {
		parent := t.TempDir()
		runGitOutput(t, parent, "init", "-q")
		requireValidationCode(t, Options{InstancePath: filepath.Join(parent, "instance"), ValuesPath: writeValues(t, parent, validValues(t))}, approvedTestEngine, "git.nested")
	})
	t.Run("secret-like-content", func(t *testing.T) {
		parent := t.TempDir()
		secret := "ghp_1234567890abcdef"
		values := validValues(t)
		values["boxes"].(map[string]any)["boxa"].(map[string]any)["description"] = secret
		_, err := Init(Options{InstancePath: filepath.Join(parent, "instance"), ValuesPath: writeValues(t, parent, values)}, approvedTestEngine)
		var validationError *ValidationError
		if !errors.As(err, &validationError) {
			t.Fatalf("Init() error = %v, want validation error", err)
		}
		if !hasDiagnostic(validationError.Diagnostics, "secret.raw") || strings.Contains(validationError.Error(), secret) {
			t.Fatalf("secret rejection is missing or unsafe: %#v", validationError.Diagnostics)
		}
		requireNoStaging(t, parent)
	})
}

func TestInitRejectsUnapprovedBinaryBeforeCreatingDestination(t *testing.T) {
	parent := t.TempDir()
	_, err := Init(Options{
		InstancePath: filepath.Join(parent, "instance"),
		ValuesPath:   writeValues(t, parent, validValues(t)),
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

func validValues(t *testing.T) map[string]any {
	t.Helper()
	path := filepath.Join(repositoryRoot(t), "tests", "fixtures", "contract", "init-single.json")
	content, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var value map[string]any
	if err := json.Unmarshal(content, &value); err != nil {
		t.Fatal(err)
	}
	return value
}

func repositoryRoot(t *testing.T) string {
	t.Helper()
	root, err := filepath.Abs(filepath.Join("..", ".."))
	if err != nil {
		t.Fatal(err)
	}
	return root
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
	if !hasDiagnostic(validationError.Diagnostics, code) {
		t.Fatalf("missing %s: %#v", code, validationError.Diagnostics)
	}
}

func hasDiagnostic(diagnostics []contract.Diagnostic, code string) bool {
	for _, diagnostic := range diagnostics {
		if diagnostic.Code == code {
			return true
		}
	}
	return false
}

func containsString(values []string, wanted string) bool {
	for _, value := range values {
		if value == wanted {
			return true
		}
	}
	return false
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
