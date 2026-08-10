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

func TestPlanUsageIsValidationFailure(t *testing.T) {
	var stdout, stderr bytes.Buffer
	if got := run([]string{"plan"}, &stdout, &stderr); got != 2 {
		t.Fatalf("run(plan) = %d, want 2", got)
	}
	if !strings.Contains(stderr.String(), "--compatibility-registry") {
		t.Fatalf("usage omits compatibility registry: %q", stderr.String())
	}
}

func TestPlanJSONIsReadOnlyAndReportsUnbornRepository(t *testing.T) {
	priorRepository, priorRef, priorCommit := engineRepository, engineRef, engineCommit
	engineRepository = "https://github.com/klokast/klokast-box"
	engineRef = "main"
	engineCommit = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
	t.Cleanup(func() {
		engineRepository, engineRef, engineCommit = priorRepository, priorRef, priorCommit
	})
	parent := t.TempDir()
	values := filepath.Join(parent, "values.json")
	valuesContent := `{
  "schema_version": 1,
  "instance": {"name": "family-klokast"},
  "tailnet": {
    "magicdns_suffix": "example.ts.net",
    "groups": {"operators": ["admin@example.com"], "family": []}
  },
  "site": {"country": "FR"},
  "box": {"hostname_prefix": "k001"}
}`
	if err := os.WriteFile(values, []byte(valuesContent), 0o600); err != nil {
		t.Fatal(err)
	}
	instancePath := filepath.Join(parent, "instance")
	var stdout, stderr bytes.Buffer
	if got := run([]string{"init", "--instance", instancePath, "--profile", "single-box", "--values", values}, &stdout, &stderr); got != 0 {
		t.Fatalf("run(init) = %d, stderr=%q", got, stderr.String())
	}
	registry := filepath.Join(parent, "platform-resources.yml")
	registryContent := `---
schema_version: 1
boxes:
  k001:
    access:
      available_capabilities: [overlay]
      enabled_capabilities: [overlay]
      prohibited_capabilities: [rg-lan, direct-egress, direct-ingress]
      policy:
        local-presence-control: overlay
        private-service-ingress: overlay
        file-upload: overlay
        household-wan-egress: none
        public-ingress: none
apps:
  nextcloud:
    enabled: false
    placement:
      active_master: ""
      passive_backup: ""
    resources:
      cloudflare-tunnel-egress: false
`
	if err := os.WriteFile(registry, []byte(registryContent), 0o600); err != nil {
		t.Fatal(err)
	}
	stdout.Reset()
	stderr.Reset()
	if got := run([]string{"plan", "--instance", instancePath, "--compatibility-registry", registry, "--json"}, &stdout, &stderr); got != 0 {
		t.Fatalf("run(plan) = %d, stderr=%q, stdout=%q", got, stderr.String(), stdout.String())
	}
	var result struct {
		Valid      bool `json:"valid"`
		Compatible bool `json:"compatible"`
		Deployable bool `json:"deployable"`
		Projection struct {
			ControlPlane struct {
				Airunners []struct {
					RuntimeHostname string `json:"runtime_hostname"`
				} `json:"airunners"`
			} `json:"control_plane"`
		} `json:"projection"`
	}
	if err := json.Unmarshal(stdout.Bytes(), &result); err != nil {
		t.Fatal(err)
	}
	if !result.Valid || !result.Compatible || result.Deployable || result.Projection.ControlPlane.Airunners[0].RuntimeHostname != "k001-airunner" {
		t.Fatalf("unexpected plan result: %#v", result)
	}
}
