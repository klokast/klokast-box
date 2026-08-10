package doctor

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
)

const testCommit = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

var testEngine = contract.Engine{
	Repository: "https://github.com/klokast/klokast-box",
	Ref:        "main",
	Commit:     testCommit,
}

var testNow = time.Date(2026, 8, 10, 12, 10, 0, 0, time.UTC)

func TestHealthySingleBoxAndDirtyWorktree(t *testing.T) {
	instance := prepareInstance(t, false)
	observation := singleBoxObservation()
	path := writeObservation(t, observation)
	result, err := Doctor(Options{InstancePath: instance, ObservationPath: path, Now: func() time.Time { return testNow }}, testEngine)
	if err != nil {
		t.Fatal(err)
	}
	if !result.Valid || !result.Healthy || result.Summary.Drift != 0 || result.ProjectionHash == "" || len(result.Inputs) != 4 {
		t.Fatalf("unexpected doctor result: %#v", result)
	}
	if len(result.ObservationGeneration) != 64 {
		t.Fatalf("observation provenance is absent: %#v", result)
	}
	appendFile(t, filepath.Join(instance, "ops/deployment.yml"), "\n")
	dirty, err := Doctor(Options{InstancePath: instance, ObservationPath: path, Now: func() time.Time { return testNow }}, testEngine)
	if err != nil || !dirty.Valid || !dirty.Healthy {
		t.Fatalf("dirty checked worktree must remain accepted: result=%#v err=%v", dirty, err)
	}
}

func TestHealthyTwoBoxWithStandbyAndExternalAirunner(t *testing.T) {
	instance := prepareInstance(t, true)
	observation := twoBoxObservation()
	result, err := Doctor(Options{InstancePath: instance, ObservationPath: writeObservation(t, observation), Now: func() time.Time { return testNow }}, testEngine)
	if err != nil {
		t.Fatal(err)
	}
	if !result.Valid || !result.Healthy {
		t.Fatalf("two-box observation is not healthy: %#v", result)
	}
}

func TestObservedDriftIsRedacted(t *testing.T) {
	tests := []struct {
		name string
		mutate func(*Observation)
		code string
	}{
		{"missing-machine", func(o *Observation) { o.TailnetMachines = o.TailnetMachines[1:] }, "tailnet.missing"},
		{"offline-machine", func(o *Observation) { o.TailnetMachines[0].Online = false }, "tailnet.offline"},
		{"wrong-tag", func(o *Observation) { o.TailnetMachines[0].Tags = []string{"tag:redacted-wrong-role"} }, "tailnet.tag"},
		{"dom0-unreachable", func(o *Observation) { o.Boxes[0].Dom0Reachable = false }, "dom0.unreachable"},
		{"xen-unavailable", func(o *Observation) { o.Boxes[0].XenAvailable = false }, "xen.unavailable"},
		{"guest-not-running", func(o *Observation) { o.Boxes[0].RunningGuests = remove(o.Boxes[0].RunningGuests, "router") }, "xen.not-running"},
		{"guest-not-configured", func(o *Observation) { o.Boxes[0].ConfiguredGuests = remove(o.Boxes[0].ConfiguredGuests, "router") }, "xen.not-configured"},
		{"guest-no-autostart", func(o *Observation) { o.Boxes[0].AutostartGuests = remove(o.Boxes[0].AutostartGuests, "router") }, "xen.no-autostart"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			observation := singleBoxObservation()
			test.mutate(&observation)
			path := writeObservation(t, observation)
			result, err := Doctor(Options{InstancePath: prepareInstance(t, false), ObservationPath: path, Now: func() time.Time { return testNow }}, testEngine)
			if err != nil {
				t.Fatal(err)
			}
			if !result.Valid || result.Healthy || !hasFinding(result, test.code) {
				t.Fatalf("drift was not reported: %#v", result)
			}
			encoded, _ := json.Marshal(result)
			if strings.Contains(string(encoded), "redacted-wrong-role") {
				t.Fatalf("observed scalar leaked in report: %s", encoded)
			}
		})
	}
}

func TestExtraLegacyResourcesDoNotAffectAuthorityDecision(t *testing.T) {
	observation := singleBoxObservation()
	observation.TailnetMachines = append(observation.TailnetMachines, TailnetMachine{Hostname: "k001-ops-airunner", Online: true, Tags: []string{"tag:airunner"}})
	sortObservation(&observation)
	observation.Boxes[0].RunningGuests = append(observation.Boxes[0].RunningGuests, "legacy")
	observation.Boxes[0].ConfiguredGuests = append(observation.Boxes[0].ConfiguredGuests, "legacy")
	observation.Boxes[0].AutostartGuests = append(observation.Boxes[0].AutostartGuests, "legacy")
	sortObservation(&observation)
	result, err := Doctor(Options{InstancePath: prepareInstance(t, false), ObservationPath: writeObservation(t, observation), Now: func() time.Time { return testNow }}, testEngine)
	if err != nil || !result.Valid || !result.Healthy {
		t.Fatalf("extra legacy resources must be ignored: result=%#v err=%v", result, err)
	}
}

func TestObservationValidation(t *testing.T) {
	tests := []struct {
		name string
		mutate func(*Observation)
		code string
	}{
		{"stale", func(o *Observation) { o.ObservedAt = "2026-08-10T11:39:59Z" }, "time.stale"},
		{"future", func(o *Observation) { o.ObservedAt = "2026-08-10T12:15:01Z" }, "time.future"},
		{"non-utc", func(o *Observation) { o.ObservedAt = "2026-08-10T12:00:00+00:00" }, "time.utc"},
		{"duplicate-machine", func(o *Observation) { o.TailnetMachines = append(o.TailnetMachines, o.TailnetMachines[0]) }, "identity.duplicate"},
		{"duplicate-box", func(o *Observation) { o.Boxes = append(o.Boxes, o.Boxes[0]) }, "identity.duplicate"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			observation := singleBoxObservation()
			test.mutate(&observation)
			result, err := Doctor(Options{InstancePath: prepareInstance(t, false), ObservationPath: writeObservation(t, observation), Now: func() time.Time { return testNow }}, testEngine)
			if err != nil {
				t.Fatal(err)
			}
			if result.Valid || !hasDiagnostic(result, test.code) {
				t.Fatalf("invalid observation was accepted: %#v", result)
			}
		})
	}
}

func TestObservationHashUnknownFieldsSymlinkAndSize(t *testing.T) {
	instance := prepareInstance(t, false)
	t.Run("hash", func(t *testing.T) {
		observation := singleBoxObservation()
		path := writeObservation(t, observation)
		content, _ := os.ReadFile(path)
		content = []byte(strings.Replace(string(content), `"online":true`, `"online":false`, 1))
		if err := os.WriteFile(path, content, 0o600); err != nil { t.Fatal(err) }
		result, err := Doctor(Options{InstancePath: instance, ObservationPath: path, Now: func() time.Time { return testNow }}, testEngine)
		if err != nil || result.Valid || !hasDiagnostic(result, "hash.mismatch") { t.Fatalf("result=%#v err=%v", result, err) }
	})
	t.Run("unknown", func(t *testing.T) {
		observation := singleBoxObservation()
		content, _ := json.Marshal(observation)
		content = []byte(strings.Replace(string(content), `"schema_version":1`, `"schema_version":1,"private_value":"must-not-leak"`, 1))
		path := filepath.Join(t.TempDir(), "observation.json")
		if err := os.WriteFile(path, content, 0o600); err != nil { t.Fatal(err) }
		result, err := Doctor(Options{InstancePath: instance, ObservationPath: path, Now: func() time.Time { return testNow }}, testEngine)
		encoded, _ := json.Marshal(result)
		if err != nil || result.Valid || !hasDiagnostic(result, "json.invalid") || strings.Contains(string(encoded), "must-not-leak") { t.Fatalf("result=%s err=%v", encoded, err) }
	})
	t.Run("symlink", func(t *testing.T) {
		target := writeObservation(t, singleBoxObservation())
		link := filepath.Join(t.TempDir(), "observation.json")
		if err := os.Symlink(target, link); err != nil { t.Fatal(err) }
		result, err := Doctor(Options{InstancePath: instance, ObservationPath: link, Now: func() time.Time { return testNow }}, testEngine)
		if err != nil || result.Valid || !hasDiagnostic(result, "path.symlink") { t.Fatalf("result=%#v err=%v", result, err) }
	})
	t.Run("oversized", func(t *testing.T) {
		path := filepath.Join(t.TempDir(), "observation.json")
		if err := os.WriteFile(path, make([]byte, maximumObservationFile+1), 0o600); err != nil { t.Fatal(err) }
		result, err := Doctor(Options{InstancePath: instance, ObservationPath: path, Now: func() time.Time { return testNow }}, testEngine)
		if err != nil || result.Valid || !hasDiagnostic(result, "path.size") { t.Fatalf("result=%#v err=%v", result, err) }
	})
}

func singleBoxObservation() Observation {
	machines := []TailnetMachine{
		{Hostname: "k001-airunner", Online: true, Tags: []string{"tag:airunner"}},
		{Hostname: "k001-bak", Online: true, Tags: []string{"tag:vm"}},
		{Hostname: "k001-dmz", Online: true, Tags: []string{"tag:vm"}},
		{Hostname: "k001-dom0", Online: true, Tags: []string{"tag:dom0"}},
		{Hostname: "k001-iot", Online: true, Tags: []string{"tag:vm"}},
		{Hostname: "k001-ops", Online: true, Tags: []string{"tag:ops"}},
		{Hostname: "k001-router", Online: true, Tags: []string{"tag:vm"}},
	}
	guests := []string{"airunner", "bak", "dmz", "iot", "ops", "router"}
	observation := Observation{
		SchemaVersion: 1, ObservedAt: "2026-08-10T12:00:00Z", SourceController: "k001-ops",
		SourceMapSHA256: strings.Repeat("b", 64), TailnetMachines: machines,
		Boxes: []ObservedBox{{HostnamePrefix: "k001", Dom0Reachable: true, XenAvailable: true, RunningGuests: append([]string{}, guests...), ConfiguredGuests: append([]string{}, guests...), AutostartGuests: append([]string{}, guests...)}},
	}
	sortObservation(&observation)
	return observation
}

func twoBoxObservation() Observation {
	observation := singleBoxObservation()
	observation.TailnetMachines = append(observation.TailnetMachines, TailnetMachine{Hostname: "vultr-ops-airunner", Online: true, Tags: []string{"tag:airunner"}})
	for _, role := range []string{"bak", "dmz", "dom0", "iot", "ops", "router"} {
		tag := "tag:vm"
		if role == "dom0" { tag = "tag:dom0" }
		if role == "ops" { tag = "tag:ops" }
		observation.TailnetMachines = append(observation.TailnetMachines, TailnetMachine{Hostname: "k002-" + role, Online: true, Tags: []string{tag}})
	}
	guests := []string{"bak", "dmz", "iot", "ops", "router"}
	observation.Boxes = append(observation.Boxes, ObservedBox{HostnamePrefix: "k002", Dom0Reachable: true, XenAvailable: true, RunningGuests: append([]string{}, guests...), ConfiguredGuests: append([]string{}, guests...), AutostartGuests: append([]string{}, guests...)})
	sortObservation(&observation)
	return observation
}

func sortObservation(observation *Observation) {
	sort.Slice(observation.TailnetMachines, func(i, j int) bool { return observation.TailnetMachines[i].Hostname < observation.TailnetMachines[j].Hostname })
	for index := range observation.TailnetMachines { sort.Strings(observation.TailnetMachines[index].Tags) }
	sort.Slice(observation.Boxes, func(i, j int) bool { return observation.Boxes[i].HostnamePrefix < observation.Boxes[j].HostnamePrefix })
	for index := range observation.Boxes {
		sort.Strings(observation.Boxes[index].RunningGuests)
		sort.Strings(observation.Boxes[index].ConfiguredGuests)
		sort.Strings(observation.Boxes[index].AutostartGuests)
	}
}

func writeObservation(t *testing.T, observation Observation) string {
	t.Helper()
	observation.GenerationSHA256 = ""
	content, err := json.Marshal(observation)
	if err != nil { t.Fatal(err) }
	var value map[string]any
	if err := json.Unmarshal(content, &value); err != nil { t.Fatal(err) }
	delete(value, "generation_sha256")
	canonical, err := json.Marshal(value)
	if err != nil { t.Fatal(err) }
	digest := sha256.Sum256(canonical)
	observation.GenerationSHA256 = fmt.Sprintf("%x", digest[:])
	content, err = json.Marshal(observation)
	if err != nil { t.Fatal(err) }
	path := filepath.Join(t.TempDir(), "observation.json")
	if err := os.WriteFile(path, content, 0o600); err != nil { t.Fatal(err) }
	return path
}

func prepareInstance(t *testing.T, twoBox bool) string {
	t.Helper()
	root := t.TempDir()
	if err := fs.WalkDir(klokastbox.Assets, "templates/instance", func(path string, entry fs.DirEntry, err error) error {
		if err != nil { return err }
		relative, err := filepath.Rel("templates/instance", path)
		if err != nil || relative == "." { return err }
		destination := filepath.Join(root, relative)
		if entry.IsDir() { return os.MkdirAll(destination, 0o755) }
		content, err := klokastbox.Assets.ReadFile(path)
		if err != nil { return err }
		return os.WriteFile(destination, content, 0o644)
	}); err != nil { t.Fatal(err) }
	if twoBox {
		for source, destination := range map[string]string{
			"tests/fixtures/contract/valid-two/deployment.yml": "ops/deployment.yml",
			"tests/fixtures/contract/valid-two/platform-resources.yml": "ops/platform-resources.yml",
		} {
			content, err := os.ReadFile(filepath.Join(repositoryRoot(t), source))
			if err != nil { t.Fatal(err) }
			if err := os.WriteFile(filepath.Join(root, destination), content, 0o644); err != nil { t.Fatal(err) }
		}
	}
	lock := fmt.Sprintf("---\nschema_version: 1\nengine:\n  repository: https://github.com/klokast/klokast-box\n  ref: main\n  commit: %s\n", testCommit)
	if err := os.WriteFile(filepath.Join(root, "klokast.lock.yml"), []byte(lock), 0o644); err != nil { t.Fatal(err) }
	runGit(t, root, "init", "-q")
	runGit(t, root, "add", "-A")
	return root
}

func repositoryRoot(t *testing.T) string {
	t.Helper()
	root, err := filepath.Abs(filepath.Join("..", ".."))
	if err != nil { t.Fatal(err) }
	return root
}

func runGit(t *testing.T, root string, args ...string) {
	t.Helper()
	command := exec.Command("git", args...)
	command.Dir = root
	command.Env = append(os.Environ(), "GIT_CONFIG_NOSYSTEM=1", "HOME="+t.TempDir())
	if output, err := command.CombinedOutput(); err != nil { t.Fatalf("git %v: %v: %s", args, err, output) }
}

func appendFile(t *testing.T, path, content string) {
	t.Helper()
	file, err := os.OpenFile(path, os.O_APPEND|os.O_WRONLY, 0)
	if err != nil { t.Fatal(err) }
	if _, err := file.WriteString(content); err != nil { file.Close(); t.Fatal(err) }
	if err := file.Close(); err != nil { t.Fatal(err) }
}

func remove(values []string, wanted string) []string {
	result := []string{}
	for _, value := range values { if value != wanted { result = append(result, value) } }
	return result
}

func removeMachine(values []TailnetMachine, hostname string) []TailnetMachine {
	result := []TailnetMachine{}
	for _, value := range values { if value.Hostname != hostname { result = append(result, value) } }
	return result
}

func hasFinding(result Result, code string) bool {
	for _, finding := range result.Findings { if finding.Code == code { return true } }
	return false
}

func hasDiagnostic(result Result, code string) bool {
	for _, diagnostic := range result.Diagnostics { if diagnostic.Code == code { return true } }
	return false
}
