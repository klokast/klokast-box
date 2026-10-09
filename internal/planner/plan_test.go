package planner

import (
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

func TestConnectivityCapabilityMapping(t *testing.T) {
	access := accessForCapabilities([]string{
		"overlay", "local-ap-uplink", "direct-wan-egress",
		"edge-tunnel-ingress", "direct-wan-ingress",
	})
	if strings.Join(access.Enabled, ",") != "ap-uplink,direct-egress,direct-ingress,edge-ingress,overlay" {
		t.Fatalf("elementary capabilities did not map exactly: %#v", access)
	}
	if strings.Join(access.Prohibited, ",") != "local-lan,rg-lan,vpn-egress" {
		t.Fatalf("prohibited capabilities are not the exact complement: %#v", access)
	}
}

func TestProjectionIsDeterministicAcrossRepositoryPaths(t *testing.T) {
	first, err := Plan(Options{InstancePath: prepareInstance(t, nil)}, testEngine)
	if err != nil {
		t.Fatal(err)
	}
	second, err := Plan(Options{InstancePath: prepareInstance(t, nil)}, testEngine)
	if err != nil {
		t.Fatal(err)
	}
	if first.ProjectionHash != second.ProjectionHash {
		t.Fatalf("projection depends on repository path: %s != %s", first.ProjectionHash, second.ProjectionHash)
	}
}

func TestTwoBoxProjectionPreservesOrderedRuntimeIDs(t *testing.T) {
	root := prepareTwoBoxInstance(t, nil)
	result, err := Plan(Options{InstancePath: root}, testEngine)
	if err != nil {
		t.Fatal(err)
	}
	if !result.Valid || !result.Compatible || result.Projection.ControlPlane.StandbyController == nil {
		t.Fatalf("two-box projection failed: %#v", result)
	}
	if result.Projection.ControlPlane.StandbyController.Hostname != "boxb-ops" {
		t.Fatalf("unexpected standby controller: %#v", result.Projection.ControlPlane.StandbyController)
	}
	if len(result.Projection.Sites) != 2 || result.Projection.Sites[0].ID != "site-a" ||
		result.Projection.Sites[0].Country != "XA" || result.Projection.Sites[1].ID != "site-b" ||
		result.Projection.Sites[1].Country != "XB" {
		t.Fatalf("box metadata did not produce the expected site projection: %#v", result.Projection.Sites)
	}
	runners := result.Projection.ControlPlane.Airunners
	want := "boxb-ops-airunner,boxa-ops-airunner,vultr-ops,hetzner-ops"
	if strings.Join(runners, ",") != want {
		t.Fatalf("unexpected runners: %#v", runners)
	}
}

func TestProjectionHashChangesWhenAirunnerPriorityChanges(t *testing.T) {
	first, err := Plan(Options{InstancePath: prepareTwoBoxInstance(t, nil)}, testEngine)
	if err != nil {
		t.Fatal(err)
	}
	secondRoot := prepareTwoBoxInstance(t, func(root string) {
		replaceInFile(t, filepath.Join(root, contract.InstancePath),
			`"boxb-ops-airunner",
    "boxa-ops-airunner"`,
			`"boxa-ops-airunner",
    "boxb-ops-airunner"`)
	})
	second, err := Plan(Options{InstancePath: secondRoot}, testEngine)
	if err != nil {
		t.Fatal(err)
	}
	if !first.Valid || !second.Valid || first.ProjectionHash == second.ProjectionHash {
		t.Fatalf("ordered airunner priority did not affect projection hash: %q == %q", first.ProjectionHash, second.ProjectionHash)
	}
	if strings.Join(second.Projection.ControlPlane.Airunners, ",") != "boxa-ops-airunner,boxb-ops-airunner,vultr-ops,hetzner-ops" {
		t.Fatalf("projection did not preserve changed priority: %#v", second.Projection.ControlPlane.Airunners)
	}
}

func canonicalRegistry() string {
	return `---
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
}

func canonicalTwoBoxRegistry() string {
	return `---
schema_version: 1
boxes:
  boxa:
    access:
      available_capabilities: [overlay, ap-uplink, direct-egress]
      enabled_capabilities: [overlay, ap-uplink, direct-egress]
      prohibited_capabilities: [direct-ingress, edge-ingress, local-lan, rg-lan, vpn-egress]
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
`
}

func prepareInstance(t *testing.T, mutate func(string)) string {
	return prepareFixtureInstance(t, "tests/fixtures/contract/init-single.json", mutate)
}

func prepareTwoBoxInstance(t *testing.T, mutate func(string)) string {
	return prepareFixtureInstance(t, "tests/fixtures/contract/valid-two/klokast-instance.json", mutate)
}

func prepareFixtureInstance(t *testing.T, fixture string, mutate func(string)) string {
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
	content, err := os.ReadFile(filepath.Join(repositoryRoot(t), fixture))
	if err != nil {
		t.Fatal(err)
	}
	writeFile(t, filepath.Join(root, contract.InstancePath), string(content))
	writeFile(t, filepath.Join(root, contract.LockPath), fmt.Sprintf(`{
  "$schema": "https://raw.githubusercontent.com/klokast/klokast-box/%s/schemas/klokast-lock-v1.schema.json",
  "engine": {"commit": "%s", "ref": "main", "repository": "https://github.com/klokast/klokast-box"},
  "schema-version": 1
}
`, testCommit, testCommit))
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

func hasDiagnostic(result Result, code string) bool {
	for _, diagnostic := range result.Diagnostics {
		if diagnostic.Code == code {
			return true
		}
	}
	return false
}

func TestVPNEgressCapabilityMapping(t *testing.T) {
	access := accessForCapabilities([]string{"overlay", "vpn-wan-egress"})
	found := false
	for _, capability := range access.Enabled {
		found = found || capability == "vpn-egress"
	}
	if !found {
		t.Fatal("VPN capability did not enable the existing network capability")
	}
	for _, capability := range access.Prohibited {
		if capability == "vpn-egress" {
			t.Fatal("enabled VPN capability remains prohibited")
		}
	}
}
