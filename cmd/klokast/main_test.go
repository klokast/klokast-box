package main

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestVersionJSON(t *testing.T) {
	var stdout, stderr bytes.Buffer
	if got := run([]string{"version", "--json"}, &stdout, &stderr); got != 0 {
		t.Fatalf("run(version) = %d, stderr=%q", got, stderr.String())
	}
	var result versionResult
	if err := json.Unmarshal(stdout.Bytes(), &result); err != nil {
		t.Fatal(err)
	}
	if result.EngineRepository != engineRepository || result.EngineRef != engineRef ||
		result.EngineCommit != engineCommit || result.Name != "klokast" {
		t.Fatalf("unexpected version result: %#v", result)
	}
}

func TestInitUsageIsValidationFailure(t *testing.T) {
	var stdout, stderr bytes.Buffer
	if got := run([]string{"init"}, &stdout, &stderr); got != 2 {
		t.Fatalf("run(init) = %d, want 2", got)
	}
	if !strings.Contains(stderr.String(), "init --instance") {
		t.Fatalf("usage omits init command: %q", stderr.String())
	}
}

func TestInitJSONAndExistingDestination(t *testing.T) {
	priorRepository, priorRef, priorCommit := engineRepository, engineRef, engineCommit
	engineRepository = "https://github.com/klokast/klokast-box"
	engineRef = "main"
	engineCommit = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
	t.Cleanup(func() {
		engineRepository, engineRef, engineCommit = priorRepository, priorRef, priorCommit
	})
	parent := t.TempDir()
	values := filepath.Join(parent, "values.json")
	content := `{
  "schema_version": 1,
  "instance": {"name": "family-klokast"},
  "tailnet": {
    "magicdns_suffix": "example.ts.net",
    "groups": {"operators": ["admin@example.com"], "family": []}
  },
  "site": {"country": "FR"},
  "box": {"hostname_prefix": "k001"}
}`
	if err := os.WriteFile(values, []byte(content), 0o600); err != nil {
		t.Fatal(err)
	}
	destination := filepath.Join(parent, "instance")
	arguments := []string{"init", "--instance", destination, "--profile", "single-box", "--values", values, "--json"}
	var stdout, stderr bytes.Buffer
	if got := run(arguments, &stdout, &stderr); got != 0 {
		t.Fatalf("run(init) = %d, stderr=%q", got, stderr.String())
	}
	var result struct {
		Created      bool   `json:"created"`
		InstancePath string `json:"instance_path"`
	}
	if err := json.Unmarshal(stdout.Bytes(), &result); err != nil {
		t.Fatal(err)
	}
	if !result.Created || result.InstancePath != destination {
		t.Fatalf("unexpected init result: %#v", result)
	}
	stdout.Reset()
	stderr.Reset()
	if got := run(arguments, &stdout, &stderr); got != 2 {
		t.Fatalf("second run(init) = %d, stderr=%q", got, stderr.String())
	}
	if !strings.Contains(stdout.String(), `"code":"path.exists"`) || strings.Contains(stdout.String(), "admin@example.com") {
		t.Fatalf("unexpected validation result: %q", stdout.String())
	}
}

func TestCheckUsageIsValidationFailure(t *testing.T) {
	var stdout, stderr bytes.Buffer
	if got := run([]string{"check"}, &stdout, &stderr); got != 2 {
		t.Fatalf("run(check) = %d, want 2", got)
	}
}
