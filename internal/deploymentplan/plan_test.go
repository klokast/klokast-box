package deploymentplan

import (
	"bytes"
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
	"klokast-box/internal/authoritystate"
	"klokast-box/internal/contract"
	"klokast-box/internal/doctor"
	"klokast-box/internal/planner"
	"klokast-box/internal/toolchain"
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
	if first.SchemaVersion != 3 || first.Kind != "klokast.plan.v3" || first.SelectedBox != "boxb" {
		t.Fatalf("Plan v3 did not select the unique non-controller box: %#v", first)
	}
	selectedGroup := actionGroup(first, authoritystate.BoxConnectivityPrefix+"boxb")
	if selectedGroup.Operation != "adopt_instance_specification" || selectedGroup.Executor != "box_connectivity_v1" || !equalStrings(selectedGroup.Scopes, authoritystate.BoxConnectivityScopes("boxb")) {
		t.Fatalf("selected box group is not closed: %#v", selectedGroup)
	}
	controllerGroup := actionGroup(first, authoritystate.BoxConnectivityPrefix+"boxa")
	if controllerGroup.Operation != "retain_legacy" || controllerGroup.Executor != "none" {
		t.Fatalf("active-controller box was exposed to Apply: %#v", controllerGroup)
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
	encoded, err := json.Marshal(first)
	if err != nil {
		t.Fatal(err)
	}
	if first.InstanceSource.Commit != first.Instance.Commit || first.InstanceSource.ReceiptSHA256 == "" || strings.Contains(string(encoded), "family/klokast") {
		t.Fatalf("instance source provenance is incomplete or disclosed its repository name: %#v", first.InstanceSource)
	}
	for _, finding := range first.Compatibility.Findings {
		if finding.ID == "" {
			t.Fatalf("compatibility finding has no canonical ID: %#v", finding)
		}
		if finding.Class == "compatibility_only" && finding.Authority == "" {
			t.Fatalf("compatibility-only finding has no authority: %#v", finding)
		}
	}
	if !hasOperation(first, "retain_legacy") || !hasOperation(first, "verify_substrate") {
		t.Fatalf("required actions are absent: %#v", first.Actions)
	}
}

func TestSelectNonControllerBoxRefusesAbsentAndAmbiguousChoices(t *testing.T) {
	one := &planner.Projection{
		Boxes:        []planner.Box{{ID: "boxa"}},
		ControlPlane: planner.ControlPlane{ActiveController: planner.Controller{BoxID: "boxa"}},
	}
	if selected := selectNonControllerBox(one); selected != "" {
		t.Fatalf("one-box projection selected %q", selected)
	}
	two := &planner.Projection{
		Boxes:        []planner.Box{{ID: "boxa"}, {ID: "boxb"}},
		ControlPlane: planner.ControlPlane{ActiveController: planner.Controller{BoxID: "boxa"}},
	}
	if selected := selectNonControllerBox(two); selected != "boxb" {
		t.Fatalf("two-box projection selected %q", selected)
	}
	three := &planner.Projection{
		Boxes:        []planner.Box{{ID: "boxa"}, {ID: "boxb"}, {ID: "boxc"}},
		ControlPlane: planner.ControlPlane{ActiveController: planner.Controller{BoxID: "boxa"}},
	}
	if selected := selectNonControllerBox(three); selected != "" {
		t.Fatalf("ambiguous projection selected %q", selected)
	}
}

func TestExactFindingActionAuthorityCoverageRejectsTampering(t *testing.T) {
	instance := prepareInstance(t)
	artifact, err := Build(compatibilityOptions(t, instance), testEngine)
	if err != nil || !artifact.AuthorityReady {
		t.Fatalf("baseline plan is not authority-ready: err=%v artifact=%#v", err, artifact)
	}
	digests := compatibilityDigests(artifact.CompatibilityInputs)
	compatibilityFindingID := ""
	for _, finding := range artifact.Compatibility.Findings {
		if finding.Class == "compatibility_only" {
			compatibilityFindingID = finding.ID
			break
		}
	}
	if compatibilityFindingID == "" {
		t.Fatal("baseline plan has no compatibility-only finding")
	}
	tests := []struct {
		name   string
		mutate func(*Artifact)
	}{
		{"duplicate-action", func(value *Artifact) {
			for _, action := range value.Actions {
				if action.FindingID == compatibilityFindingID {
					value.Actions = append(value.Actions, action)
					return
				}
			}
		}},
		{"missing-action", func(value *Artifact) {
			filtered := []Action{}
			for _, action := range value.Actions {
				if action.FindingID != compatibilityFindingID {
					filtered = append(filtered, action)
				}
			}
			value.Actions = filtered
		}},
		{"wrong-digest", func(value *Artifact) {
			for index := range value.Authorities {
				if value.Authorities[index].FindingID == compatibilityFindingID {
					value.Authorities[index].SourceSHA256 = strings.Repeat("f", 64)
				}
			}
		}},
		{"altered-finding-id", func(value *Artifact) {
			for index := range value.Compatibility.Findings {
				if value.Compatibility.Findings[index].ID == compatibilityFindingID {
					value.Compatibility.Findings[index].ID = "finding-altered"
				}
			}
		}},
		{"extra-authority", func(value *Artifact) {
			value.Authorities = append(value.Authorities, AuthorityAssignment{
				ID: "extra", FindingID: compatibilityFindingID, Authority: "legacy_platform_resources",
				Scope: "extra", Disposition: "continuing", SourceSHA256: strings.Repeat("e", 64),
			})
		}},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			encoded, _ := json.Marshal(artifact)
			var candidate Artifact
			if err := json.Unmarshal(encoded, &candidate); err != nil {
				t.Fatal(err)
			}
			test.mutate(&candidate)
			ready, refusals := exactCoverage(candidate, digests)
			if ready || len(refusals) == 0 {
				t.Fatalf("tampered coverage was accepted: %#v", candidate)
			}
		})
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

func TestBuildUsesVerificationActionsAfterBoxAdoption(t *testing.T) {
	instance := prepareInstance(t)
	options := compatibilityOptions(t, instance)
	state, err := authoritystate.LoadV2(options.AuthorityState)
	if err != nil {
		t.Fatal(err)
	}
	adopted, err := authoritystate.TransitionGroup(
		state, authoritystate.BoxConnectivityPrefix+"boxb",
		authoritystate.InstanceAuthority, strings.Repeat("d", 64), "adopt-test",
	)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(options.AuthorityState, canonicalTestJSON(t, adopted), 0o600); err != nil {
		t.Fatal(err)
	}
	artifact, err := Build(options, testEngine)
	if err != nil {
		t.Fatal(err)
	}
	group := actionGroup(artifact, authoritystate.BoxConnectivityPrefix+"boxb")
	if !artifact.Deployable || group.Operation != "verify_instance_authority" {
		t.Fatalf("migrated Plan v3 did not become verification-only: %#v", group)
	}
	for _, scope := range authoritystate.BoxConnectivityScopes("boxb") {
		found := false
		for _, action := range artifact.Actions {
			if action.Scope == scope {
				found = true
				if action.Operation != "verify_instance_authority" || action.AuthorityBefore != authoritystate.InstanceAuthority || action.AuthorityAfter != authoritystate.InstanceAuthority {
					t.Fatalf("scope %s proposed another adoption: %#v", scope, action)
				}
			}
		}
		if !found {
			t.Fatalf("scope %s has no verification action", scope)
		}
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
    family: [admin@example.com]
boxes:
  boxa:
    site: site-001
    physical_location: Example location
  boxb:
    site: site-002
    physical_location: Second example location
`)
	registry := writeFile(t, directory, "platform-resources.yml", `---
schema_version: 1
boxes:
  boxa:
    access:
      available_capabilities: [overlay]
      enabled_capabilities: [overlay]
      prohibited_capabilities: [ap-uplink, direct-egress, direct-ingress, edge-ingress, local-lan, rg-lan, vpn-egress]
  boxb:
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
`)
	controller := writeFile(t, directory, "controller-ha.yml", `---
schema_version: 1
remote_user: smith
repo_dir: ~/src/klokast/klokast-box
controllers:
  - box: boxa
    hostname: boxa-ops
`)
	return Options{
		InstancePath: instance, CompatibilityDeployment: deployment,
		CompatibilityRegistry: registry, CompatibilityControllerHA: controller,
		ObservationPath:            writeObservation(t, directory),
		InstanceSourceReceipt:      writeSourceReceipt(t, directory, instance),
		AuthorityState:             writeAuthorityState(t, directory),
		ControllerToolchainReceipt: writeToolchainReceipt(t, directory),
	}
}

func writeAuthorityState(t *testing.T, directory string) string {
	t.Helper()
	state, err := authoritystate.Initial()
	if err != nil {
		t.Fatal(err)
	}
	state, err = authoritystate.Transition(
		state, authoritystate.InstanceAuthority, strings.Repeat("a", 64), "tailnet-adopt",
	)
	if err != nil {
		t.Fatal(err)
	}
	converted, err := authoritystate.ConvertV1(
		state, []string{"boxa", "boxb"}, strings.Repeat("b", 64), "convert-v2",
	)
	if err != nil {
		t.Fatal(err)
	}
	return writeFile(t, directory, "authority-state.json", string(canonicalTestJSON(t, converted)))
}

func writeToolchainReceipt(t *testing.T, directory string) string {
	t.Helper()
	receipt := toolchain.Receipt{
		SchemaVersion: 3, Kind: toolchain.Kind, EngineCommit: testCommit,
		PublicCheckoutClean: true, PublicCheckoutCommit: testCommit,
		Components: []toolchain.Component{},
	}
	for index, name := range toolchain.Components {
		digest := fmt.Sprintf("%064x", index+1)
		receipt.Components = append(receipt.Components, toolchain.Component{
			Name: name, SourceSHA256: digest, InstalledSHA256: digest,
		})
	}
	digest, err := toolchain.Hash(receipt)
	if err != nil {
		t.Fatal(err)
	}
	receipt.ReceiptSHA256 = digest
	return writeFile(t, directory, "controller-toolchain.json", string(canonicalTestJSON(t, receipt)))
}

func writeSourceReceipt(t *testing.T, directory, instance string) string {
	t.Helper()
	commit := gitOutput(t, instance, "rev-parse", "HEAD")
	repository := "family/klokast"
	repositoryDigest := sha256.Sum256([]byte(repository))
	value := map[string]any{
		"schema_version":         1,
		"kind":                   "klokast.instance-source.v1",
		"repository":             repository,
		"repository_sha256":      fmt.Sprintf("%x", repositoryDigest[:]),
		"repository_id":          123456,
		"remote_ref":             "refs/heads/main",
		"commit":                 commit,
		"fetched_at":             time.Now().UTC().Truncate(time.Second).Format(time.RFC3339),
		"deploy_key_fingerprint": "SHA256:abcdefghijklmnopqrstuvwxyzABCDEFGH123456",
		"anonymous_readable":     false,
		"authenticated_readable": true,
	}
	canonical, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	digest := sha256.Sum256(canonical)
	value["receipt_sha256"] = fmt.Sprintf("%x", digest[:])
	content, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	return writeFile(t, directory, "instance-source.json", string(content)+"\n")
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
	content, err := os.ReadFile(filepath.Join(repositoryRoot(t), "tests", "fixtures", "contract", "init-single.json"))
	if err != nil {
		t.Fatal(err)
	}
	var instance map[string]any
	if err := json.Unmarshal(content, &instance); err != nil {
		t.Fatal(err)
	}
	boxes := instance["boxes"].(map[string]any)
	boxes["boxb"] = map[string]any{
		"site": "site-c", "country": "XC", "description": "Second example home",
		"connectivity": []any{"overlay"},
	}
	writeFile(t, root, contract.InstancePath, string(canonicalTestJSON(t, instance)))
	writeFile(t, root, contract.LockPath, fmt.Sprintf(`{
  "$schema": "https://raw.githubusercontent.com/klokast/klokast-box/%s/schemas/klokast-lock-v1.schema.json",
  "engine": {"commit": "%s", "ref": "main", "repository": "https://github.com/klokast/klokast-box"},
  "schema-version": 1
}
`, testCommit, testCommit))
	runGit(t, root, "init", "-q", "--initial-branch=main")
	runGit(t, root, "add", "-A")
	runGit(t, root, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "instance")
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

func writeObservation(t *testing.T, directory string) string {
	t.Helper()
	machines := []doctor.TailnetMachine{
		{Hostname: "boxa-bak", Online: true, Tags: []string{"tag:vm"}},
		{Hostname: "boxa-dmz", Online: true, Tags: []string{"tag:vm"}},
		{Hostname: "boxa-dom0", Online: true, Tags: []string{"tag:dom0"}},
		{Hostname: "boxa-iot", Online: true, Tags: []string{"tag:vm"}},
		{Hostname: "boxa-ops", Online: true, Tags: []string{"tag:ops"}},
		{Hostname: "boxa-ops-airunner", Online: true, Tags: []string{"tag:airunner"}},
		{Hostname: "boxa-router", Online: true, Tags: []string{"tag:vm"}},
		{Hostname: "boxb-bak", Online: true, Tags: []string{"tag:vm"}},
		{Hostname: "boxb-dmz", Online: true, Tags: []string{"tag:vm"}},
		{Hostname: "boxb-dom0", Online: true, Tags: []string{"tag:dom0"}},
		{Hostname: "boxb-iot", Online: true, Tags: []string{"tag:vm"}},
		{Hostname: "boxb-router", Online: true, Tags: []string{"tag:vm"}},
	}
	guests := []string{"bak", "dmz", "iot", "ops", "router"}
	observation := doctor.Observation{
		SchemaVersion: 1, ObservedAt: time.Now().UTC().Format(time.RFC3339), SourceController: "boxa-ops",
		SourceMapSHA256: strings.Repeat("b", 64), TailnetMachines: machines,
		Boxes: []doctor.ObservedBox{
			{
				HostnamePrefix: "boxa", Dom0Reachable: true, XenAvailable: true,
				RunningGuests: append([]string{}, guests...), ConfiguredGuests: append([]string{}, guests...), AutostartGuests: append([]string{}, guests...),
			},
			{
				HostnamePrefix: "boxb", Dom0Reachable: true, XenAvailable: true,
				RunningGuests:    []string{"bak", "dmz", "iot", "router"},
				ConfiguredGuests: []string{"bak", "dmz", "iot", "router"},
				AutostartGuests:  []string{"bak", "dmz", "iot", "router"},
			},
		},
	}
	sort.Slice(observation.TailnetMachines, func(i, j int) bool {
		return observation.TailnetMachines[i].Hostname < observation.TailnetMachines[j].Hostname
	})
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

func gitOutput(t *testing.T, root string, arguments ...string) string {
	t.Helper()
	command := exec.Command("git", append([]string{"-C", root}, arguments...)...)
	output, err := command.CombinedOutput()
	if err != nil {
		t.Fatalf("git %v: %v: %s", arguments, err, output)
	}
	return strings.TrimSpace(string(output))
}

func hasOperation(artifact Artifact, operation string) bool {
	for _, action := range artifact.Actions {
		if action.Operation == operation {
			return true
		}
	}
	return false
}

func actionGroup(artifact Artifact, id string) ActionGroup {
	for _, group := range artifact.ActionGroups {
		if group.ID == id {
			return group
		}
	}
	return ActionGroup{}
}

func canonicalTestJSON(t *testing.T, value any) []byte {
	t.Helper()
	content, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	var generic any
	decoder := json.NewDecoder(bytes.NewReader(content))
	decoder.UseNumber()
	if err := decoder.Decode(&generic); err != nil {
		t.Fatal(err)
	}
	canonical, err := json.Marshal(generic)
	if err != nil {
		t.Fatal(err)
	}
	return append(canonical, '\n')
}

func equalStrings(first, second []string) bool {
	if len(first) != len(second) {
		return false
	}
	for index := range first {
		if first[index] != second[index] {
			return false
		}
	}
	return true
}
