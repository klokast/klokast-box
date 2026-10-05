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
	content := mainInstanceValues()
	if err := os.WriteFile(values, []byte(content), 0o600); err != nil {
		t.Fatal(err)
	}
	destination := filepath.Join(parent, "instance")
	arguments := []string{"init", "--instance", destination, "--values", values, "--json"}
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

func TestPlanUsageIsValidationFailure(t *testing.T) {
	var stdout, stderr bytes.Buffer
	if got := run([]string{"plan"}, &stdout, &stderr); got != 2 {
		t.Fatalf("run(plan) = %d, want 2", got)
	}
	if !strings.Contains(stderr.String(), "--instance") || strings.Contains(stderr.String(), "receipt") {
		t.Fatalf("usage does not describe the Instance preview: %q", stderr.String())
	}
}

func TestDoctorUsageIsValidationFailure(t *testing.T) {
	var stdout, stderr bytes.Buffer
	if got := run([]string{"doctor"}, &stdout, &stderr); got != 2 {
		t.Fatalf("run(doctor) = %d, want 2", got)
	}
	if !strings.Contains(stderr.String(), "--observation") {
		t.Fatalf("usage omits observation file: %q", stderr.String())
	}
}

func TestInventoryRejectsCallerSelectionAndCommands(t *testing.T) {
	for _, arguments := range [][]string{
		{"inventory"},
		{"inventory", "--instance", "/unused"},
		{"inventory", "--instance", "/unused", "--json", "--box", "boxa"},
		{"inventory", "--instance", "/unused", "--json", "--compatibility-registry", "/unused"},
		{"inventory", "--instance", "/unused", "--json", "apply"},
	} {
		var stdout, stderr bytes.Buffer
		if code := run(arguments, &stdout, &stderr); code != 2 || stdout.Len() != 0 || !strings.Contains(stderr.String(), "klokast inventory --instance PATH --json") {
			t.Fatalf("inventory accepted caller selection or command: %v code=%d stdout=%q stderr=%q", arguments, code, stdout.String(), stderr.String())
		}
	}
}

func mainInstanceValues() string {
	return `{
  "$schema": "https://raw.githubusercontent.com/klokast/klokast-box/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/schemas/klokast-instance-v1.schema.json",
  "schema-version": 1,
  "tailscale": {
    "tailnet-dns-name": "example.ts.net",
    "members": {"admin@example.com": {"roles": ["operator", "family"]}}
  },
  "boxes": {"boxa": {"site": "site-b", "country": "XB", "description": "Example home", "connectivity": ["overlay"]}},
  "controllers": {"active": "boxa"},
  "airunners": ["boxa-ops-airunner"],
  "apps": {}
}`
}
