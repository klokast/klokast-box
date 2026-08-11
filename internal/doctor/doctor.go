// Package doctor compares a checked Contract v1 projection with one redacted,
// recent Observation v1 document. It performs no discovery and changes no state.
package doctor

import (
	"bytes"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"

	"klokast-box/internal/contract"
	"klokast-box/internal/planner"
)

const maximumObservationFile = 1024 * 1024

var (
	hostnamePattern = regexp.MustCompile(`^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$`)
	digestPattern   = regexp.MustCompile(`^[0-9a-f]{64}$`)
)

type Options struct {
	InstancePath    string
	ObservationPath string
	Now             func() time.Time
}

type Result struct {
	SchemaVersion         int                   `json:"schema_version"`
	Valid                 bool                  `json:"valid"`
	Healthy               bool                  `json:"healthy"`
	HealthScope           string                `json:"health_scope"`
	Engine                planner.Engine        `json:"engine"`
	Inputs                []planner.InputDigest `json:"inputs"`
	ProjectionHash        string                `json:"projection_sha256,omitempty"`
	ObservationSource     string                `json:"observation_source,omitempty"`
	ObservedAt            string                `json:"observed_at,omitempty"`
	ObservationGeneration string               `json:"observation_generation_sha256,omitempty"`
	Summary               FindingSummary        `json:"summary"`
	Findings              []Finding             `json:"findings"`
	Diagnostics           []contract.Diagnostic `json:"diagnostics"`
}

type FindingSummary struct {
	Checks int `json:"checks"`
	Passed int `json:"passed"`
	Drift  int `json:"drift"`
}

type Finding struct {
	Path    string `json:"path"`
	Code    string `json:"code"`
	Message string `json:"message"`
}

type Observation struct {
	SchemaVersion    int               `json:"schema_version"`
	ObservedAt       string            `json:"observed_at"`
	SourceController string            `json:"source_controller"`
	SourceMapSHA256  string            `json:"source_map_sha256"`
	TailnetMachines  []TailnetMachine  `json:"tailnet_machines"`
	Boxes            []ObservedBox     `json:"boxes"`
	GenerationSHA256 string            `json:"generation_sha256"`
}

type TailnetMachine struct {
	Hostname string   `json:"hostname"`
	Online   bool     `json:"online"`
	Tags     []string `json:"tags"`
}

type ObservedBox struct {
	HostnamePrefix  string   `json:"hostname_prefix"`
	Dom0Reachable   bool     `json:"dom0_reachable"`
	XenAvailable    bool     `json:"xen_available"`
	RunningGuests   []string `json:"running_guests"`
	ConfiguredGuests []string `json:"configured_guests"`
	AutostartGuests []string `json:"autostart_guests"`
}

func Doctor(options Options, engine contract.Engine) (Result, error) {
	result := Result{
		SchemaVersion: 1,
		HealthScope:   "standard_substrate_v1",
		Engine: planner.Engine{Repository: engine.Repository, Ref: engine.Ref, Commit: engine.Commit},
		Inputs: []planner.InputDigest{}, Findings: []Finding{}, Diagnostics: []contract.Diagnostic{},
	}
	snapshot, report, err := contract.Load(options.InstancePath, engine)
	if err != nil {
		return Result{}, err
	}
	if !report.Valid {
		result.Diagnostics = report.Diagnostics
		return result, nil
	}
	for _, input := range snapshot.Inputs {
		result.Inputs = append(result.Inputs, planner.InputDigest{Path: input.Path, SHA256: input.SHA256})
	}
	projection := planner.Resolve(snapshot)
	result.ProjectionHash, err = planner.ProjectionHash(projection)
	if err != nil {
		return Result{}, err
	}

	now := time.Now().UTC()
	if options.Now != nil {
		now = options.Now().UTC()
	}
	observation, diagnostics, err := loadObservation(options.ObservationPath, now)
	if err != nil {
		return Result{}, err
	}
	if len(diagnostics) != 0 {
		result.Diagnostics = diagnostics
		return result, nil
	}
	result.Valid = true
	result.ObservationSource = observation.SourceController
	result.ObservedAt = observation.ObservedAt
	result.ObservationGeneration = observation.GenerationSHA256
	checkProjection(&result, projection, observation)
	sort.Slice(result.Findings, func(i, j int) bool {
		if result.Findings[i].Path != result.Findings[j].Path {
			return result.Findings[i].Path < result.Findings[j].Path
		}
		return result.Findings[i].Code < result.Findings[j].Code
	})
	result.Summary.Drift = len(result.Findings)
	result.Summary.Passed = result.Summary.Checks - result.Summary.Drift
	result.Healthy = len(result.Findings) == 0
	return result, nil
}

func loadObservation(path string, now time.Time) (Observation, []contract.Diagnostic, error) {
	add := func(code, message string) []contract.Diagnostic {
		return []contract.Diagnostic{{Path: "observation", Code: code, Message: message}}
	}
	if path == "" {
		return Observation{}, add("path.required", "observation file path is required"), nil
	}
	absolute, err := filepath.Abs(path)
	if err != nil {
		return Observation{}, nil, fmt.Errorf("resolve observation path: %w", err)
	}
	info, err := os.Lstat(absolute)
	if err != nil {
		if os.IsNotExist(err) {
			return Observation{}, add("path.missing", "observation file does not exist"), nil
		}
		return Observation{}, nil, fmt.Errorf("inspect observation file: %w", err)
	}
	if info.Mode()&os.ModeSymlink != 0 {
		return Observation{}, add("path.symlink", "observation must not be a symbolic link"), nil
	}
	if !info.Mode().IsRegular() {
		return Observation{}, add("path.type", "observation must be a regular file"), nil
	}
	if info.Size() <= 0 || info.Size() > maximumObservationFile {
		return Observation{}, add("path.size", "observation must be non-empty and no larger than one MiB"), nil
	}
	content, err := os.ReadFile(absolute)
	if err != nil {
		return Observation{}, nil, fmt.Errorf("read observation file: %w", err)
	}
	if bytes.IndexByte(content, 0) >= 0 {
		return Observation{}, add("json.invalid", "observation is not a valid JSON document"), nil
	}
	decoder := json.NewDecoder(bytes.NewReader(content))
	decoder.DisallowUnknownFields()
	var observation Observation
	if err := decoder.Decode(&observation); err != nil {
		return Observation{}, add("json.invalid", "observation is not a strict Observation v1 document"), nil
	}
	var extra any
	if err := decoder.Decode(&extra); err != io.EOF {
		return Observation{}, add("json.multiple", "observation must contain one JSON document"), nil
	}
	if observation.SchemaVersion != 1 {
		return Observation{}, add("schema.version", "observation schema_version must be 1"), nil
	}
	if !digestPattern.MatchString(observation.SourceMapSHA256) || !digestPattern.MatchString(observation.GenerationSHA256) {
		return Observation{}, add("hash.format", "observation hashes must be lowercase SHA-256 values"), nil
	}
	actualHash, err := observationHash(content)
	if err != nil {
		return Observation{}, add("json.invalid", "observation cannot be canonicalized"), nil
	}
	if actualHash != observation.GenerationSHA256 {
		return Observation{}, add("hash.mismatch", "observation generation hash does not match its content"), nil
	}
	observedAt, err := time.Parse("2006-01-02T15:04:05Z", observation.ObservedAt)
	if err != nil || !strings.HasSuffix(observation.ObservedAt, "Z") {
		return Observation{}, add("time.utc", "observed_at must be a whole-second UTC timestamp"), nil
	}
	if observedAt.Before(now.Add(-30 * time.Minute)) {
		return Observation{}, add("time.stale", "observation is older than 30 minutes"), nil
	}
	if observedAt.After(now.Add(5 * time.Minute)) {
		return Observation{}, add("time.future", "observation is more than five minutes in the future"), nil
	}
	if !hostnamePattern.MatchString(observation.SourceController) {
		return Observation{}, add("identity.controller", "source_controller is not a normalized hostname"), nil
	}
	if diagnostic := validateObservationCollections(observation); diagnostic != nil {
		return Observation{}, []contract.Diagnostic{*diagnostic}, nil
	}
	return observation, nil, nil
}

func observationHash(content []byte) (string, error) {
	var value map[string]any
	decoder := json.NewDecoder(bytes.NewReader(content))
	decoder.UseNumber()
	if err := decoder.Decode(&value); err != nil {
		return "", err
	}
	delete(value, "generation_sha256")
	canonical, err := json.Marshal(value)
	if err != nil {
		return "", err
	}
	digest := sha256.Sum256(canonical)
	return fmt.Sprintf("%x", digest[:]), nil
}

func validateObservationCollections(observation Observation) *contract.Diagnostic {
	pathDiagnostic := func(path, code, message string) *contract.Diagnostic {
		return &contract.Diagnostic{Path: "observation." + path, Code: code, Message: message}
	}
	if observation.TailnetMachines == nil {
		return pathDiagnostic("tailnet_machines", "schema.required", "collection must be present")
	}
	if observation.Boxes == nil {
		return pathDiagnostic("boxes", "schema.required", "collection must be present")
	}
	last := ""
	for index, machine := range observation.TailnetMachines {
		path := fmt.Sprintf("tailnet_machines[%d]", index)
		if !hostnamePattern.MatchString(machine.Hostname) {
			return pathDiagnostic(path+".hostname", "identity.hostname", "Tailnet hostname is not one normalized DNS label")
		}
		if machine.Hostname <= last {
			return pathDiagnostic("tailnet_machines", "identity.duplicate", "Tailnet machine identities must be unique and sorted")
		}
		last = machine.Hostname
		if diagnostic := validateSortedStrings(machine.Tags, path+".tags", `^tag:[a-z0-9](?:[a-z0-9-]{0,62})$`); diagnostic != nil {
			return diagnostic
		}
	}
	last = ""
	for index, box := range observation.Boxes {
		path := fmt.Sprintf("boxes[%d]", index)
		if !hostnamePattern.MatchString(box.HostnamePrefix) || box.HostnamePrefix <= last {
			return pathDiagnostic("boxes", "identity.duplicate", "box prefixes must be valid, unique, and sorted")
		}
		last = box.HostnamePrefix
		for name, values := range map[string][]string{
			"running_guests": box.RunningGuests, "configured_guests": box.ConfiguredGuests, "autostart_guests": box.AutostartGuests,
		} {
			if diagnostic := validateSortedStrings(values, path+"."+name, `^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$`); diagnostic != nil {
				return diagnostic
			}
		}
	}
	return nil
}

func validateSortedStrings(values []string, path, pattern string) *contract.Diagnostic {
	if values == nil {
		return &contract.Diagnostic{Path: "observation." + path, Code: "schema.required", Message: "collection must be present"}
	}
	valid := regexp.MustCompile(pattern)
	last := ""
	for _, value := range values {
		if !valid.MatchString(value) || value <= last {
			return &contract.Diagnostic{Path: "observation." + path, Code: "identity.duplicate", Message: "identities must be valid, unique, and sorted"}
		}
		last = value
	}
	return nil
}

func checkProjection(result *Result, projection planner.Projection, observation Observation) {
	machines := map[string]TailnetMachine{}
	for _, machine := range observation.TailnetMachines {
		machines[machine.Hostname] = machine
	}
	boxes := map[string]ObservedBox{}
	for _, box := range observation.Boxes {
		boxes[box.HostnamePrefix] = box
	}
	add := func(path, code, message string) {
		result.Findings = append(result.Findings, Finding{Path: path, Code: code, Message: message})
	}
	check := func(ok bool, path, code, message string) {
		result.Summary.Checks++
		if !ok {
			add(path, code, message)
		}
	}
	checkMachine := func(hostname, tag string) {
		machine, present := machines[hostname]
		check(present, "tailnet."+hostname, "tailnet.missing", "required Tailnet identity is absent")
		if !present {
			return
		}
		check(machine.Online, "tailnet."+hostname, "tailnet.offline", "required Tailnet identity is offline")
		check(contains(machine.Tags, tag), "tailnet."+hostname, "tailnet.tag", "required Tailnet identity does not have its role tag")
	}
	checkGuest := func(box ObservedBox, boxPresent bool, prefix, guest string) {
		if !boxPresent {
			return
		}
		path := "boxes." + prefix + ".guests." + guest
		check(contains(box.RunningGuests, guest), path, "xen.not-running", "required Xen guest is not running")
		check(contains(box.ConfiguredGuests, guest), path, "xen.not-configured", "required Xen guest configuration is absent")
		check(contains(box.AutostartGuests, guest), path, "xen.no-autostart", "required Xen guest has no autostart entry")
	}

	check(observation.SourceController == projection.ControlPlane.ActiveController.Hostname,
		"source_controller", "controller.inactive", "observation did not come from the declared active controller")

	for _, projectedBox := range projection.Boxes {
		prefix := projectedBox.HostnamePrefix
		box, present := boxes[prefix]
		check(present, "boxes."+prefix, "box.missing", "Contract box is absent from the observation")
		checkMachine(projectedBox.Runtime.Dom0, "tag:dom0")
		for _, role := range []struct{ hostname, guest string }{
			{projectedBox.Runtime.Router, "router"}, {projectedBox.Runtime.Backup, "bak"},
			{projectedBox.Runtime.DMZ, "dmz"}, {projectedBox.Runtime.IoT, "iot"},
		} {
			checkMachine(role.hostname, "tag:vm")
			checkGuest(box, present, prefix, role.guest)
		}
		if present {
			check(box.Dom0Reachable, "boxes."+prefix+".dom0", "dom0.unreachable", "dom0 was not reachable during observation")
			check(box.XenAvailable, "boxes."+prefix+".xen", "xen.unavailable", "Xen facts were not available during observation")
		}
	}

	controllers := []planner.Controller{projection.ControlPlane.ActiveController}
	if projection.ControlPlane.StandbyController != nil {
		controllers = append(controllers, *projection.ControlPlane.StandbyController)
	}
	for _, controller := range controllers {
		checkMachine(controller.Hostname, "tag:ops")
		prefix := prefixForBoxID(projection, controller.BoxID)
		box, present := boxes[prefix]
		checkGuest(box, present, prefix, "ops")
	}
	for _, runner := range projection.ControlPlane.Airunners {
		checkMachine(runner.RuntimeHostname, "tag:airunner")
		if runner.Kind == "box" {
			prefix := prefixForBoxID(projection, runner.BoxID)
			box, present := boxes[prefix]
			checkGuest(box, present, prefix, "airunner")
		}
	}
}

func prefixForBoxID(projection planner.Projection, id string) string {
	for _, box := range projection.Boxes {
		if box.ID == id {
			return box.HostnamePrefix
		}
	}
	return ""
}

func contains(values []string, wanted string) bool {
	index := sort.SearchStrings(values, wanted)
	return index < len(values) && values[index] == wanted
}
