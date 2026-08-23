package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
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
	if !strings.Contains(stderr.String(), "--compatibility-registry") {
		t.Fatalf("usage omits compatibility registry: %q", stderr.String())
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
	valuesContent := mainInstanceValues()
	if err := os.WriteFile(values, []byte(valuesContent), 0o600); err != nil {
		t.Fatal(err)
	}
	instancePath := filepath.Join(parent, "instance")
	var stdout, stderr bytes.Buffer
	if got := run([]string{"init", "--instance", instancePath, "--values", values}, &stdout, &stderr); got != 0 {
		t.Fatalf("run(init) = %d, stderr=%q", got, stderr.String())
	}
	registry := filepath.Join(parent, "platform-resources.yml")
	registryContent := `---
schema_version: 1
boxes:
  boxa:
    access:
      available_capabilities: [overlay]
      enabled_capabilities: [overlay]
      prohibited_capabilities: [ap-uplink, direct-egress, direct-ingress, edge-ingress, local-lan, rg-lan, vpn-egress]
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
	deployment := filepath.Join(parent, "deployment.yml")
	if err := os.WriteFile(deployment, []byte(`---
schema_version: 1
tailnet:
  magicdns_suffix: example.ts.net
  groups:
    operators: [admin@example.com]
    family: [admin@example.com]
boxes:
  boxa:
    site: site-001
`), 0o600); err != nil {
		t.Fatal(err)
	}
	controller := filepath.Join(parent, "controller-ha.yml")
	if err := os.WriteFile(controller, []byte(`---
schema_version: 1
controllers:
  - box: boxa
    hostname: boxa-ops
`), 0o600); err != nil {
		t.Fatal(err)
	}
	observation := writeMainObservation(t, parent)
	sourceReceipt := writeMainSourceReceipt(t, parent, instancePath)
	arguments := []string{
		"plan", "--instance", instancePath,
		"--compatibility-deployment", deployment,
		"--compatibility-registry", registry,
		"--compatibility-controller-ha", controller,
		"--observation", observation,
		"--instance-source-receipt", sourceReceipt, "--json",
	}
	if got := run(arguments, &stdout, &stderr); got != 2 {
		t.Fatalf("run(plan) = %d, want non-deployable status 2; stderr=%q, stdout=%q", got, stderr.String(), stdout.String())
	}
	var result struct {
		Valid      bool `json:"valid"`
		Compatible bool `json:"compatible"`
		Deployable bool `json:"deployable"`
		PlanSHA256 string `json:"plan_sha256"`
		Projection struct {
			ControlPlane struct {
				Airunners []string `json:"airunners"`
			} `json:"control_plane"`
		} `json:"projection"`
	}
	if err := json.Unmarshal(stdout.Bytes(), &result); err != nil {
		t.Fatal(err)
	}
	if !result.Valid || !result.Compatible || result.Deployable || len(result.PlanSHA256) != 64 || result.Projection.ControlPlane.Airunners[0] != "boxa-ops-airunner" {
		t.Fatalf("unexpected plan result: %#v", result)
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

func writeMainSourceReceipt(t *testing.T, directory, instancePath string) string {
	t.Helper()
	command := exec.Command("git", "-C", instancePath, "rev-parse", "HEAD")
	output, err := command.Output()
	commit := strings.TrimSpace(string(output))
	if err != nil {
		// An unborn repository still needs a syntactically valid receipt. Its
		// commit cannot match, so the plan remains non-deployable.
		commit = strings.Repeat("c", 40)
	}
	repository := "family/klokast"
	repositoryDigest := sha256.Sum256([]byte(repository))
	value := map[string]any{
		"schema_version": 1,
		"kind": "klokast.instance-source.v1",
		"repository": repository,
		"repository_sha256": fmt.Sprintf("%x", repositoryDigest[:]),
		"repository_id": 123456,
		"remote_ref": "refs/heads/main",
		"commit": commit,
		"fetched_at": time.Now().UTC().Truncate(time.Second).Format(time.RFC3339),
		"deploy_key_fingerprint": "SHA256:abcdefghijklmnopqrstuvwxyzABCDEFGH123456",
		"anonymous_readable": false,
		"authenticated_readable": true,
	}
	canonical, marshalErr := json.Marshal(value)
	if marshalErr != nil {
		t.Fatal(marshalErr)
	}
	digest := sha256.Sum256(canonical)
	value["receipt_sha256"] = fmt.Sprintf("%x", digest[:])
	content, marshalErr := json.Marshal(value)
	if marshalErr != nil {
		t.Fatal(marshalErr)
	}
	path := filepath.Join(directory, "instance-source.json")
	if err := os.WriteFile(path, append(content, '\n'), 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

func writeMainObservation(t *testing.T, directory string) string {
	t.Helper()
	guests := []any{"bak", "dmz", "iot", "ops", "router"}
	value := map[string]any{
		"schema_version": 1,
		"observed_at": time.Now().UTC().Format(time.RFC3339),
		"source_controller": "boxa-ops",
		"source_map_sha256": strings.Repeat("b", 64),
		"tailnet_machines": []any{
			map[string]any{"hostname": "boxa-bak", "online": true, "tags": []any{"tag:vm"}},
			map[string]any{"hostname": "boxa-dmz", "online": true, "tags": []any{"tag:vm"}},
			map[string]any{"hostname": "boxa-dom0", "online": true, "tags": []any{"tag:dom0"}},
			map[string]any{"hostname": "boxa-iot", "online": true, "tags": []any{"tag:vm"}},
			map[string]any{"hostname": "boxa-ops", "online": true, "tags": []any{"tag:ops"}},
			map[string]any{"hostname": "boxa-ops-airunner", "online": true, "tags": []any{"tag:airunner"}},
			map[string]any{"hostname": "boxa-router", "online": true, "tags": []any{"tag:vm"}},
		},
		"boxes": []any{map[string]any{
			"hostname_prefix": "boxa", "dom0_reachable": true, "xen_available": true,
			"running_guests": guests, "configured_guests": guests, "autostart_guests": guests,
		}},
	}
	canonical, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	digest := sha256.Sum256(canonical)
	value["generation_sha256"] = fmt.Sprintf("%x", digest[:])
	content, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(directory, "observation.json")
	if err := os.WriteFile(path, append(content, '\n'), 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}
