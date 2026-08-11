package planner

import (
	"encoding/json"
	"fmt"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	klokastbox "klokast-box"
	"klokast-box/internal/contract"
)

const testCommit = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

var testEngine = contract.Engine{
	Repository: "https://github.com/klokast/klokast-box",
	Ref:        "main",
	Commit:     testCommit,
}

func TestPlanResolvesCanonicalInstanceWithoutRequiringCommit(t *testing.T) {
	root := prepareInstance(t, nil)
	registry := writeRegistry(t, canonicalRegistry())
	result, err := Plan(Options{InstancePath: root, CompatibilityRegistry: registry}, testEngine)
	if err != nil {
		t.Fatal(err)
	}
	if !result.Valid || !result.Compatible || result.Deployable || result.AuthorityReady {
		t.Fatalf("unexpected plan gates: %#v", result)
	}
	if result.Projection.ControlPlane.ActiveController.Hostname != "k001-ops" {
		t.Fatalf("unexpected controller projection: %#v", result.Projection.ControlPlane)
	}
	runner := result.Projection.ControlPlane.Airunners[0]
	if runner.Kind != "controller_container" || runner.RuntimeHostname != "k001-ops-airunner" || runner.BoxID != "box-001" {
		t.Fatalf("unexpected airunner projection: %#v", runner)
	}
	box := result.Projection.Boxes[0]
	if strings.Join(box.Access.LegacyAvailable, ",") != "overlay" {
		t.Fatalf("unexpected legacy capabilities: %#v", box.Access)
	}
	if len(result.Inputs) != 4 || len(result.ProjectionHash) != 64 || len(result.Compatibility.RegistrySHA256) != 64 {
		t.Fatalf("missing provenance: %#v", result)
	}
	if result.Compatibility.Summary.Conflict != 0 || result.Compatibility.Summary.Unsupported != 0 || result.Compatibility.Summary.CompatibilityOnly != 0 {
		t.Fatalf("unexpected compatibility findings: %#v", result.Compatibility)
	}
}

func TestCommittedCleanAndDirtyDeployability(t *testing.T) {
	root := prepareInstance(t, nil)
	registry := writeRegistry(t, canonicalRegistry())
	runGit(t, root, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "instance")
	clean, err := Plan(Options{InstancePath: root, CompatibilityRegistry: registry}, testEngine)
	if err != nil {
		t.Fatal(err)
	}
	if !clean.Deployable || !clean.AuthorityReady || clean.Repository.HeadCommit == "" {
		t.Fatalf("clean committed instance is not authority-ready: %#v", clean)
	}
	appendFile(t, filepath.Join(root, "ops/deployment.yml"), "\n")
	dirty, err := Plan(Options{InstancePath: root, CompatibilityRegistry: registry}, testEngine)
	if err != nil {
		t.Fatal(err)
	}
	if !dirty.Valid || !dirty.Compatible || dirty.Deployable || dirty.Repository.Clean {
		t.Fatalf("dirty read-only plan has wrong gates: %#v", dirty)
	}
}

func TestCompatibilityOnlyFieldsRemainVisible(t *testing.T) {
	root := prepareInstance(t, nil)
	legacy := strings.Replace(canonicalRegistry(), "    access:\n", "    dom0_bridge_ports:\n      lan: [eth2]\n    access:\n", 1)
	result, err := Plan(Options{InstancePath: root, CompatibilityRegistry: writeRegistry(t, legacy)}, testEngine)
	if err != nil {
		t.Fatal(err)
	}
	if !result.Compatible || result.AuthorityReady || result.Compatibility.Summary.CompatibilityOnly != 1 {
		t.Fatalf("compatibility-only field was not gated: %#v", result.Compatibility)
	}
	if !hasFinding(result, "boxes.k001.dom0_bridge_ports", "compatibility_only") {
		t.Fatalf("compatibility-only field path is absent: %#v", result.Compatibility.Findings)
	}
}

func TestSanitizedRegistryFixtureReportsEveryRealFieldClass(t *testing.T) {
	root := prepareInstance(t, nil)
	content, err := os.ReadFile(filepath.Join(repositoryRoot(t), "tests/fixtures/compatibility/registry-field-classes.yml"))
	if err != nil {
		t.Fatal(err)
	}
	result, err := Plan(Options{InstancePath: root, CompatibilityRegistry: writeRegistry(t, string(content))}, testEngine)
	if err != nil {
		t.Fatal(err)
	}
	expected := map[string]string{
		"schema_version": "matched",
		"boxes.k001": "derived",
		"boxes.k001.shared_guests": "compatibility_only",
		"boxes.k001.dom0_bridge_ports": "compatibility_only",
		"boxes.k001.dhcp_reservations": "compatibility_only",
		"apps.nextcloud.runtime_state": "compatibility_only",
		"apps.nextcloud.isolation": "compatibility_only",
		"apps.nextcloud.users": "compatibility_only",
		"apps.nextcloud.devices": "compatibility_only",
		"apps.nextcloud.app_vms": "compatibility_only",
		"apps.nextcloud.ingress_mode": "compatibility_only",
		"apps.nextcloud.ephemeral": "compatibility_only",
		"apps.nextcloud.resources": "conflict",
		"compatibility_marker": "unsupported",
	}
	for path, class := range expected {
		if !hasFinding(result, path, class) {
			t.Errorf("missing classification %s=%s", path, class)
		}
	}
	if result.Compatibility.Summary.Matched == 0 || result.Compatibility.Summary.Derived == 0 ||
		result.Compatibility.Summary.CompatibilityOnly == 0 || result.Compatibility.Summary.Conflict == 0 ||
		result.Compatibility.Summary.Unsupported == 0 {
		t.Fatalf("fixture does not exercise every classification: %#v", result.Compatibility.Summary)
	}
	encoded, _ := json.Marshal(result)
	for _, scalar := range []string{"person-example", "device-example", "192.0.2.10", "2099-01-01T00:00:00Z"} {
		if strings.Contains(string(encoded), scalar) {
			t.Fatalf("compatibility report disclosed a sanitized scalar: %s", scalar)
		}
	}
}

func TestUnrepresentedAppFieldsAreNotSilentlyOmitted(t *testing.T) {
	root := prepareInstance(t, nil)
	legacy := canonicalRegistry() + `  legacy-example:
    enabled: false
    runtime_state: stopped
    users: [person-example]
    devices: {device-example: {}}
    app_vms: {vm-example: {}}
    ingress_mode: overlay
    ephemeral: {privileged_approval: false, cleanup_required: true}
    placement: {active_master: ""}
    resources: {}
`
	result, err := Plan(Options{InstancePath: root, CompatibilityRegistry: writeRegistry(t, legacy)}, testEngine)
	if err != nil {
		t.Fatal(err)
	}
	for _, field := range []string{"enabled", "runtime_state", "users", "devices", "app_vms", "ingress_mode", "ephemeral", "placement", "resources"} {
		if !hasFinding(result, "apps.legacy-example."+field, "compatibility_only") {
			t.Errorf("unrepresented app field was omitted: %s", field)
		}
	}
}

func TestConflictsAndUnsupportedFieldsFailCompatibility(t *testing.T) {
	tests := []struct {
		name     string
		registry string
		path     string
		class    string
	}{
		{
			name:     "capability-conflict",
			registry: strings.Replace(canonicalRegistry(), "available_capabilities: [overlay]", "available_capabilities: [overlay, direct-ingress]", 1),
			path:     "boxes.k001.access.available_capabilities",
			class:    "conflict",
		},
		{
			name:     "unknown-root",
			registry: canonicalRegistry() + "unknown: true\n",
			path:     "unknown",
			class:    "unsupported",
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			root := prepareInstance(t, nil)
			result, err := Plan(Options{InstancePath: root, CompatibilityRegistry: writeRegistry(t, test.registry)}, testEngine)
			if err != nil {
				t.Fatal(err)
			}
			if !result.Valid || result.Compatible || !hasFinding(result, test.path, test.class) {
				t.Fatalf("conflict was not reported: %#v", result)
			}
		})
	}
}

func TestEnabledAppMustMatchLegacyManifestPlacement(t *testing.T) {
	root := prepareInstance(t, func(root string) {
		replaceInFile(t, filepath.Join(root, "ops/platform-resources.yml"), "enabled: false", "enabled: true")
	})
	legacy := strings.Replace(canonicalRegistry(), "enabled: false", "enabled: true", 1)
	legacy = strings.Replace(legacy, "active_master: \"\"\n      passive_backup: \"\"", "active_master: k001", 1)
	result, err := Plan(Options{InstancePath: root, CompatibilityRegistry: writeRegistry(t, legacy)}, testEngine)
	if err != nil {
		t.Fatal(err)
	}
	if result.Compatible || !hasCode(result, "placement.mode") {
		t.Fatalf("legacy placement-mode conflict is absent: %#v", result.Compatibility)
	}
}

func TestRegistryYAMLSafetyAndSecretRedaction(t *testing.T) {
	t.Run("duplicate", func(t *testing.T) {
		root := prepareInstance(t, nil)
		registry := writeRegistry(t, strings.Replace(canonicalRegistry(), "schema_version: 1", "schema_version: 1\nschema_version: 1", 1))
		result, err := Plan(Options{InstancePath: root, CompatibilityRegistry: registry}, testEngine)
		if err != nil {
			t.Fatal(err)
		}
		if result.Valid || !hasDiagnostic(result, "yaml.duplicate") {
			t.Fatalf("duplicate YAML key was not rejected: %#v", result.Diagnostics)
		}
	})
	t.Run("secret", func(t *testing.T) {
		root := prepareInstance(t, nil)
		secret := "never-print-this-secret"
		registry := writeRegistry(t, canonicalRegistry()+"token: "+secret+"\n")
		result, err := Plan(Options{InstancePath: root, CompatibilityRegistry: registry}, testEngine)
		if err != nil {
			t.Fatal(err)
		}
		if result.Valid || !hasDiagnostic(result, "secret.raw") || strings.Contains(fmt.Sprintf("%#v", result.Diagnostics), secret) {
			t.Fatalf("secret diagnostic is unsafe: %#v", result.Diagnostics)
		}
	})
	t.Run("symlink", func(t *testing.T) {
		root := prepareInstance(t, nil)
		target := writeRegistry(t, canonicalRegistry())
		link := filepath.Join(t.TempDir(), "registry.yml")
		if err := os.Symlink(target, link); err != nil {
			t.Fatal(err)
		}
		result, err := Plan(Options{InstancePath: root, CompatibilityRegistry: link}, testEngine)
		if err != nil {
			t.Fatal(err)
		}
		if result.Valid || !hasDiagnostic(result, "path.symlink") {
			t.Fatalf("registry symlink was not rejected: %#v", result.Diagnostics)
		}
	})
}

func TestProjectionIsDeterministicAcrossRepositoryPaths(t *testing.T) {
	registry := writeRegistry(t, canonicalRegistry())
	first, err := Plan(Options{InstancePath: prepareInstance(t, nil), CompatibilityRegistry: registry}, testEngine)
	if err != nil {
		t.Fatal(err)
	}
	second, err := Plan(Options{InstancePath: prepareInstance(t, nil), CompatibilityRegistry: registry}, testEngine)
	if err != nil {
		t.Fatal(err)
	}
	if first.ProjectionHash != second.ProjectionHash {
		t.Fatalf("projection depends on repository path: %s != %s", first.ProjectionHash, second.ProjectionHash)
	}
}

func TestTwoBoxProjectionResolvesStandbyAndExternalRunner(t *testing.T) {
	root := prepareInstance(t, nil)
	for source, destination := range map[string]string{
		"tests/fixtures/contract/valid-two/deployment.yml":         "ops/deployment.yml",
		"tests/fixtures/contract/valid-two/platform-resources.yml": "ops/platform-resources.yml",
	} {
		content, err := os.ReadFile(filepath.Join(repositoryRoot(t), source))
		if err != nil {
			t.Fatal(err)
		}
		writeFile(t, filepath.Join(root, destination), string(content))
	}
	runGit(t, root, "add", "-A")
	registry := writeRegistry(t, `---
schema_version: 1
boxes:
  k001:
    access:
      available_capabilities: [overlay]
      enabled_capabilities: [overlay]
      prohibited_capabilities: [direct-ingress]
      policy: {private-service-ingress: overlay, public-ingress: none}
  k002:
    access:
      available_capabilities: [overlay]
      enabled_capabilities: [overlay]
      prohibited_capabilities: [direct-ingress]
      policy: {private-service-ingress: overlay, public-ingress: none}
apps:
  nextcloud:
    enabled: false
    placement: {active_master: "", passive_backup: ""}
    resources: {cloudflare-tunnel-egress: false}
`)
	result, err := Plan(Options{InstancePath: root, CompatibilityRegistry: registry}, testEngine)
	if err != nil {
		t.Fatal(err)
	}
	if !result.Valid || !result.Compatible || result.Projection.ControlPlane.StandbyController == nil {
		t.Fatalf("two-box projection failed: %#v", result)
	}
	if result.Projection.ControlPlane.StandbyController.Hostname != "k002-ops" {
		t.Fatalf("unexpected standby controller: %#v", result.Projection.ControlPlane.StandbyController)
	}
	runners := result.Projection.ControlPlane.Airunners
	if len(runners) != 2 || runners[1].RuntimeHostname != "vultr-ops-airunner" {
		t.Fatalf("unexpected runners: %#v", runners)
	}
}

func canonicalRegistry() string {
	return `---
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
}

func prepareInstance(t *testing.T, mutate func(string)) string {
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
	writeFile(t, filepath.Join(root, "klokast.lock.yml"), fmt.Sprintf("---\nschema_version: 1\nengine:\n  repository: https://github.com/klokast/klokast-box\n  ref: main\n  commit: %s\n", testCommit))
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

func writeRegistry(t *testing.T, content string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "platform-resources.yml")
	writeFile(t, path, content)
	return path
}

func writeFile(t *testing.T, path, content string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
		t.Fatal(err)
	}
}

func appendFile(t *testing.T, path, content string) {
	t.Helper()
	file, err := os.OpenFile(path, os.O_APPEND|os.O_WRONLY, 0)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := file.WriteString(content); err != nil {
		_ = file.Close()
		t.Fatal(err)
	}
	if err := file.Close(); err != nil {
		t.Fatal(err)
	}
}

func replaceInFile(t *testing.T, path, old, replacement string) {
	t.Helper()
	content, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Count(string(content), old) != 1 {
		t.Fatalf("%q does not occur exactly once", old)
	}
	writeFile(t, path, strings.Replace(string(content), old, replacement, 1))
}

func runGit(t *testing.T, root string, arguments ...string) {
	t.Helper()
	command := exec.Command("git", append([]string{"-C", root}, arguments...)...)
	command.Env = append(os.Environ(), "GIT_CONFIG_NOSYSTEM=1", "GIT_CONFIG_GLOBAL=/dev/null")
	if output, err := command.CombinedOutput(); err != nil {
		t.Fatalf("git %v: %v: %s", arguments, err, output)
	}
}

func hasFinding(result Result, path, class string) bool {
	if result.Compatibility == nil {
		return false
	}
	for _, finding := range result.Compatibility.Findings {
		if finding.Path == path && finding.Class == class {
			return true
		}
	}
	return false
}

func hasCode(result Result, code string) bool {
	if result.Compatibility == nil {
		return false
	}
	for _, finding := range result.Compatibility.Findings {
		if finding.Code == code {
			return true
		}
	}
	return false
}

func hasDiagnostic(result Result, code string) bool {
	for _, diagnostic := range result.Diagnostics {
		if diagnostic.Code == code {
			return true
		}
	}
	return false
}
