package deploymentplan

import (
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"testing"
	"time"

	klokastbox "klokast-box"
	"klokast-box/internal/contract"
	"klokast-box/internal/doctor"
)

const testCommit = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

var testEngine = contract.Engine{
	Repository: "https://github.com/klokast/klokast-box",
	Ref:        "main",
	Commit:     testCommit,
}

func TestBuildProducesStableDeployablePlanWithRetainedAuthority(t *testing.T) {
	instance := prepareInstance(t)
	options := compatibilityOptions(t, instance)
	first, err := Build(options, testEngine)
	if err != nil {
		t.Fatal(err)
	}
	second, err := Build(options, testEngine)
	if err != nil {
		t.Fatal(err)
	}
	if !first.Valid || !first.Compatible || !first.SubstrateHealthy || !first.Deployable || !first.AuthorityReady {
		t.Fatalf("unexpected plan gates: %#v", first)
	}
	if first.LegacyRemovalReady {
		t.Fatal("legacy removal became ready while controller compatibility fields remain")
	}
	if len(first.PlanSHA256) != 64 || first.PlanSHA256 != second.PlanSHA256 {
		t.Fatalf("plan hash is not stable: %q != %q", first.PlanSHA256, second.PlanSHA256)
	}
	want, err := Hash(first)
	if err != nil || want != first.PlanSHA256 {
		t.Fatalf("plan hash does not verify: want=%q got=%q err=%v", want, first.PlanSHA256, err)
	}
	if len(first.CompatibilityInputs) != 3 || first.HealthScope != "standard_substrate_v1" {
		t.Fatalf("provenance or health scope is incomplete: %#v", first)
	}
	for _, finding := range first.Compatibility.Findings {
		if finding.Class == "compatibility_only" && finding.Authority == "" {
			t.Fatalf("compatibility-only finding has no authority: %#v", finding)
		}
	}
	if !hasOperation(first, "retain_legacy") || !hasOperation(first, "verify_substrate") {
		t.Fatalf("required actions are absent: %#v", first.Actions)
	}
}

func TestBuildRefusesCompatibilityConflict(t *testing.T) {
	instance := prepareInstance(t)
	options := compatibilityOptions(t, instance)
	replaceFile(t, options.CompatibilityDeployment, "magicdns_suffix: example.ts.net", "magicdns_suffix: other.ts.net")
	artifact, err := Build(options, testEngine)
	if err != nil {
		t.Fatal(err)
	}
	if !artifact.Valid || artifact.Compatible || artifact.Deployable || len(artifact.Refusals) == 0 {
		t.Fatalf("conflict was not refused: %#v", artifact)
	}
}

func compatibilityOptions(t *testing.T, instance string) Options {
	t.Helper()
	directory := t.TempDir()
	deployment := writeFile(t, directory, "deployment.yml", `---
schema_version: 1
tailnet:
  magicdns_suffix: example.ts.net
  groups:
    operators: [admin@example.com]
    family: [admin@example.com, family@example.com]
boxes:
  k001:
    site: site-001
    physical_location: Example location
`)
	registry := writeFile(t, directory, "platform-resources.yml", `---
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
`)
	controller := writeFile(t, directory, "controller-ha.yml", `---
schema_version: 1
remote_user: smith
repo_dir: ~/src/klokast/klokast-box
controllers:
  - box: k001
    hostname: k001-ops
`)
	return Options{
		InstancePath: instance, CompatibilityDeployment: deployment,
		CompatibilityRegistry: registry, CompatibilityControllerHA: controller,
		ObservationPath: writeObservation(t, directory),
	}
}

func prepareInstance(t *testing.T) string {
	t.Helper()
	root := t.TempDir()
	if err := fs.WalkDir(klokastbox.Assets, "templates/instance", func(path string, entry fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
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
	writeFile(t, root, "klokast.lock.yml", fmt.Sprintf(`---
schema_version: 1
engine:
  repository: https://github.com/klokast/klokast-box
  ref: main
  commit: %s
`, testCommit))
	runGit(t, root, "init", "-q", "--initial-branch=main")
	runGit(t, root, "add", "-A")
	runGit(t, root, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "instance")
	return root
}

func writeObservation(t *testing.T, directory string) string {
	t.Helper()
	machines := []doctor.TailnetMachine{
		{Hostname: "k001-bak", Online: true, Tags: []string{"tag:vm"}},
		{Hostname: "k001-dmz", Online: true, Tags: []string{"tag:vm"}},
		{Hostname: "k001-dom0", Online: true, Tags: []string{"tag:dom0"}},
		{Hostname: "k001-iot", Online: true, Tags: []string{"tag:vm"}},
		{Hostname: "k001-ops", Online: true, Tags: []string{"tag:ops"}},
		{Hostname: "k001-ops-airunner", Online: true, Tags: []string{"tag:airunner"}},
		{Hostname: "k001-router", Online: true, Tags: []string{"tag:vm"}},
	}
	guests := []string{"bak", "dmz", "iot", "ops", "router"}
	observation := doctor.Observation{
		SchemaVersion: 1, ObservedAt: time.Now().UTC().Format(time.RFC3339), SourceController: "k001-ops",
		SourceMapSHA256: strings.Repeat("b", 64), TailnetMachines: machines,
		Boxes: []doctor.ObservedBox{{
			HostnamePrefix: "k001", Dom0Reachable: true, XenAvailable: true,
			RunningGuests: append([]string{}, guests...), ConfiguredGuests: append([]string{}, guests...), AutostartGuests: append([]string{}, guests...),
		}},
	}
	sort.Slice(observation.TailnetMachines, func(i, j int) bool { return observation.TailnetMachines[i].Hostname < observation.TailnetMachines[j].Hostname })
	content, err := json.Marshal(observation)
	if err != nil {
		t.Fatal(err)
	}
	var value map[string]any
	if err := json.Unmarshal(content, &value); err != nil {
		t.Fatal(err)
	}
	delete(value, "generation_sha256")
	canonical, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	digest := sha256.Sum256(canonical)
	observation.GenerationSHA256 = fmt.Sprintf("%x", digest[:])
	encoded, err := json.Marshal(observation)
	if err != nil {
		t.Fatal(err)
	}
	return writeFile(t, directory, "observation.json", string(encoded)+"\n")
}

func writeFile(t *testing.T, directory, name, content string) string {
	t.Helper()
	path := filepath.Join(directory, name)
	if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

func replaceFile(t *testing.T, path, old, replacement string) {
	t.Helper()
	content, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Count(string(content), old) != 1 {
		t.Fatalf("%q does not occur exactly once", old)
	}
	if err := os.WriteFile(path, []byte(strings.Replace(string(content), old, replacement, 1)), 0o600); err != nil {
		t.Fatal(err)
	}
}

func runGit(t *testing.T, root string, arguments ...string) {
	t.Helper()
	command := exec.Command("git", append([]string{"-C", root}, arguments...)...)
	if output, err := command.CombinedOutput(); err != nil {
		t.Fatalf("git %v: %v: %s", arguments, err, output)
	}
}

func hasOperation(artifact Artifact, operation string) bool {
	for _, action := range artifact.Actions {
		if action.Operation == operation {
			return true
		}
	}
	return false
}
