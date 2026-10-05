// Package planner resolves Instance intent. It is read-only.
package planner

import (
	"bytes"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"os/exec"
	"reflect"
	"sort"
	"strings"

	klokastbox "klokast-box"
	"klokast-box/internal/contract"
)

const maximumRegistryFile = 1024 * 1024

type Options struct {
	InstancePath string
}

type Result struct {
	SchemaVersion  int                   `json:"schema_version"`
	Valid          bool                  `json:"valid"`
	Compatible     bool                  `json:"compatible"`
	Deployable     bool                  `json:"deployable"`
	AuthorityReady bool                  `json:"authority_ready"`
	Engine         Engine                `json:"engine"`
	Repository     Repository            `json:"repository"`
	Inputs         []InputDigest         `json:"inputs"`
	Projection     *Projection           `json:"projection,omitempty"`
	ProjectionHash string                `json:"projection_sha256,omitempty"`
	Diagnostics    []contract.Diagnostic `json:"diagnostics"`
}

type Engine struct {
	Repository string `json:"repository"`
	Ref        string `json:"ref"`
	Commit     string `json:"commit"`
}

type Repository struct {
	Branch     string   `json:"branch,omitempty"`
	HeadCommit string   `json:"head_commit,omitempty"`
	Clean      bool     `json:"clean"`
	Reasons    []string `json:"reasons"`
}

type InputDigest struct {
	Path   string `json:"path"`
	SHA256 string `json:"sha256"`
}

type Projection struct {
	VMUpdates     *contract.VMUpdatePolicy `json:"vm_updates,omitempty"`
	Registry      *RegistryProjection      `json:"registry,omitempty"`
	SchemaVersion int                      `json:"schema_version"`
	Engine        Engine                   `json:"engine"`
	Tailnet       Tailnet                  `json:"tailnet"`
	Sites         []Site                   `json:"sites"`
	Boxes         []Box                    `json:"boxes"`
	ControlPlane  ControlPlane             `json:"control_plane"`
	Apps          []App                    `json:"apps"`
}

type Tailnet struct {
	MagicDNSSuffix string         `json:"magicdns_suffix"`
	Groups         []TailnetGroup `json:"groups"`
}

type TailnetGroup struct {
	Name    string   `json:"name"`
	Members []string `json:"members"`
}

type Site struct {
	ID               string `json:"id"`
	Country          string `json:"country"`
	Timezone         string `json:"timezone"`
	PhysicalLocation string `json:"physical_location,omitempty"`
}

type Box struct {
	ID             string       `json:"id"`
	HostnamePrefix string       `json:"hostname_prefix"`
	SiteID         string       `json:"site_id"`
	Connectivity   []string     `json:"connectivity"`
	Runtime        RuntimeNames `json:"runtime"`
	Access         Access       `json:"access"`
}

type RuntimeNames struct {
	Dom0   string `json:"dom0"`
	Router string `json:"router"`
	Backup string `json:"backup"`
	DMZ    string `json:"dmz"`
	IoT    string `json:"iot"`
	Ops    string `json:"ops"`
}

type Access struct {
	Declared        []string `json:"declared_capabilities"`
	LegacyAvailable []string `json:"legacy_available_capabilities"`
	Enabled         []string `json:"enabled_capabilities"`
	Prohibited      []string `json:"prohibited_capabilities"`
}

type ControlPlane struct {
	ActiveController  Controller  `json:"active_controller"`
	StandbyController *Controller `json:"standby_controller,omitempty"`
	Airunners         []string    `json:"airunners"`
}

type Controller struct {
	BoxID    string `json:"box_id"`
	Hostname string `json:"hostname"`
}

type App struct {
	ID           string            `json:"id"`
	DesiredState string            `json:"desired_state"`
	Enabled      bool              `json:"enabled"`
	Placement    Placement         `json:"placement,omitempty"`
	Resources    []ResourceBinding `json:"features"`
	Data         []DataBinding     `json:"data"`
}

type DataBinding struct {
	ID         string `json:"id"`
	BoxID      string `json:"box_id"`
	RuntimeBox string `json:"runtime_box"`
	Retention  string `json:"retention"`
}

type Placement struct {
	Mode              string   `json:"mode"`
	BoxID             string   `json:"box_id,omitempty"`
	RuntimeBox        string   `json:"runtime_box,omitempty"`
	ActiveBoxID       string   `json:"active_box_id,omitempty"`
	ActiveRuntimeBox  string   `json:"active_runtime_box,omitempty"`
	PassiveBoxID      string   `json:"passive_box_id,omitempty"`
	PassiveRuntimeBox string   `json:"passive_runtime_box,omitempty"`
	BoxIDs            []string `json:"box_ids,omitempty"`
	RuntimeBoxes      []string `json:"runtime_boxes,omitempty"`
}

type ResourceBinding struct {
	ID    string `json:"id"`
	Value any    `json:"value"`
}

type manifest struct {
	PlacementMode string
	Features      map[string]manifestFeature
}

type manifestFeature struct {
	Kind             string
	Values           map[string]bool
	ResourceBindings map[string][]string
}

func Plan(options Options, engine contract.Engine) (Result, error) {
	result := Result{SchemaVersion: 1, Engine: Engine{Repository: engine.Repository, Ref: engine.Ref, Commit: engine.Commit}, Diagnostics: []contract.Diagnostic{}}
	snapshot, report, err := contract.Load(options.InstancePath, engine)
	if err != nil {
		return result, err
	}
	if !report.Valid {
		result.Diagnostics = report.Diagnostics
		return result, nil
	}
	projection := Resolve(snapshot)
	registry, err := ResolveRegistry(snapshot)
	if err != nil {
		return result, err
	}
	projection.Registry = &registry
	hash, err := ProjectionHash(projection)
	if err != nil {
		return result, err
	}
	repository, err := inspectRepository(snapshot.Root)
	if err != nil {
		return result, err
	}
	second, checked, err := contract.Load(options.InstancePath, engine)
	if err != nil || !checked.Valid || !sameInputs(snapshot.Inputs, second.Inputs) {
		return result, fmt.Errorf("instance changed during planning")
	}
	result.Valid, result.Compatible, result.Deployable = true, true, true
	result.Repository, result.Inputs, result.Projection, result.ProjectionHash = repository, inputDigests(snapshot.Inputs), &projection, hash
	return result, nil
}

// Resolve produces the deterministic Instance Specification v1 projection. Plan and all
// offline observers use this resolver so runtime identities cannot diverge.
func Resolve(snapshot contract.Snapshot) Projection {
	result := Projection{
		SchemaVersion: 1,
		Engine: Engine{
			Repository: snapshot.Engine.Repository,
			Ref:        snapshot.Engine.Ref,
			Commit:     snapshot.Engine.Commit,
		},
		Tailnet: Tailnet{
			MagicDNSSuffix: snapshot.Instance.Tailscale.DNSName,
			Groups:         []TailnetGroup{},
		},
		Sites: []Site{},
		Boxes: []Box{},
		ControlPlane: ControlPlane{
			Airunners: []string{},
		},
		Apps: []App{},
	}
	if policy := snapshot.Instance.VMUpdates; policy != nil {
		copy := *policy
		copy.Targets = make(map[string][]string, len(policy.Targets))
		for box, roles := range policy.Targets {
			copy.Targets[box] = sortedCopy(roles)
		}
		copy.Exclusions = append([]contract.VMUpdateExclusion{}, policy.Exclusions...)
		sort.Slice(copy.Exclusions, func(i, j int) bool {
			a, b := copy.Exclusions[i], copy.Exclusions[j]
			if a.Box != b.Box {
				return a.Box < b.Box
			}
			return a.Role < b.Role
		})
		result.VMUpdates = &copy
	}
	groups := map[string][]string{"operators": {}, "family": {}}
	for login, member := range snapshot.Instance.Tailscale.Members {
		for _, role := range member.Roles {
			if role == "operator" {
				groups["operators"] = append(groups["operators"], login)
			} else if role == "family" {
				groups["family"] = append(groups["family"], login)
			}
		}
	}
	for _, name := range []string{"family", "operators"} {
		result.Tailnet.Groups = append(result.Tailnet.Groups, TailnetGroup{Name: name, Members: sortedCopy(groups[name])})
	}
	projectedSites := map[string]Site{}
	for _, box := range snapshot.Instance.Boxes {
		projectedSites[box.Site] = Site{
			ID: box.Site, Country: box.Country, Timezone: "Etc/UTC", PhysicalLocation: box.Description,
		}
	}
	siteIDs := sortedKeys(projectedSites)
	for _, id := range siteIDs {
		result.Sites = append(result.Sites, projectedSites[id])
	}
	boxIDs := sortedKeys(snapshot.Instance.Boxes)
	for _, id := range boxIDs {
		box := snapshot.Instance.Boxes[id]
		prefix := id
		result.Boxes = append(result.Boxes, Box{
			ID: id, HostnamePrefix: prefix, SiteID: box.Site, Connectivity: sortedCopy(box.Connectivity),
			Runtime: RuntimeNames{
				Dom0: prefix + "-dom0", Router: prefix + "-router", Backup: prefix + "-bak",
				DMZ: prefix + "-dmz", IoT: prefix + "-iot", Ops: prefix + "-ops",
			},
			Access: accessForCapabilities(box.Connectivity),
		})
	}
	active := snapshot.Instance.Controllers.Active
	result.ControlPlane.ActiveController = Controller{BoxID: active, Hostname: active + "-ops"}
	if standby := snapshot.Instance.Controllers.Standby; standby != "" {
		result.ControlPlane.StandbyController = &Controller{BoxID: standby, Hostname: standby + "-ops"}
	}
	result.ControlPlane.Airunners = append(result.ControlPlane.Airunners, snapshot.Instance.Airunners...)
	appIDs := sortedKeys(snapshot.Instance.Apps)
	for _, id := range appIDs {
		binding := snapshot.Instance.Apps[id]
		resolved := App{ID: id, DesiredState: binding.DesiredState, Enabled: binding.DesiredState == "present", Resources: resourceBindings(binding.Features), Data: []DataBinding{}}
		if binding.Placement != nil {
			resolved.Placement = resolvePlacement(*binding.Placement)
		}
		for _, dataID := range sortedKeys(binding.Data) {
			data := binding.Data[dataID]
			resolved.Data = append(resolved.Data, DataBinding{ID: dataID, BoxID: data.Box, RuntimeBox: data.Box, Retention: data.Retention})
		}
		result.Apps = append(result.Apps, resolved)
	}
	if registry, err := ResolveRegistry(snapshot); err == nil {
		result.Registry = &registry
	}
	return result
}

// ProjectionHash returns the SHA-256 hash of the deterministic JSON projection.
func ProjectionHash(projection Projection) (string, error) {
	content, err := json.Marshal(projection)
	if err != nil {
		return "", fmt.Errorf("encode deterministic projection: %w", err)
	}
	digest := sha256.Sum256(content)
	return fmt.Sprintf("%x", digest[:]), nil
}

func resolvePlacement(value contract.PlacementDocument) Placement {
	result := Placement{Mode: value.Mode}
	switch value.Mode {
	case "single-box":
		result.BoxID = value.Box
		result.RuntimeBox = value.Box
	case "active-passive":
		result.ActiveBoxID = value.Active
		result.ActiveRuntimeBox = value.Active
		result.PassiveBoxID = value.Passive
		result.PassiveRuntimeBox = value.Passive
	case "multi-box":
		result.BoxIDs = sortedCopy(value.Boxes)
		for _, id := range result.BoxIDs {
			result.RuntimeBoxes = append(result.RuntimeBoxes, id)
		}
	}
	return result
}

func inspectRepository(root string) (Repository, error) {
	result := Repository{Reasons: []string{}}
	if output, err := git(root, "symbolic-ref", "--quiet", "--short", "HEAD").Output(); err == nil {
		result.Branch = strings.TrimSpace(string(output))
	}
	if output, err := git(root, "rev-parse", "--verify", "HEAD^{commit}").Output(); err == nil {
		result.HeadCommit = strings.TrimSpace(string(output))
	} else {
		var exitError *exec.ExitError
		if !errors.As(err, &exitError) {
			return Repository{}, fmt.Errorf("inspect instance HEAD: %w", err)
		}
		result.Reasons = append(result.Reasons, "repository.unborn")
	}
	status, err := git(root, "status", "--porcelain=v1", "--untracked-files=all").Output()
	if err != nil {
		return Repository{}, fmt.Errorf("inspect instance worktree: %w", err)
	}
	result.Clean = len(status) == 0
	if !result.Clean {
		result.Reasons = append(result.Reasons, "repository.dirty")
	}
	return result, nil
}

func loadManifests() (map[string]manifest, error) {
	paths, err := fs.Glob(klokastbox.Assets, "apps/*/platform-resources.yml")
	if err != nil {
		return nil, err
	}
	result := map[string]manifest{}
	for _, path := range paths {
		content, err := klokastbox.Assets.ReadFile(path)
		if err != nil {
			return nil, err
		}
		value, diagnostics := contract.ParseSafeYAML(content)
		if len(diagnostics) != 0 {
			return nil, fmt.Errorf("%s is not safe YAML", path)
		}
		object, ok := value.(map[string]any)
		if !ok {
			return nil, fmt.Errorf("%s is not an object", path)
		}
		id, _ := object["app"].(string)
		if id == "" {
			return nil, fmt.Errorf("%s has no app ID", path)
		}
		mode, _ := object["placement_mode"].(string)
		item := manifest{PlacementMode: mode, Features: map[string]manifestFeature{}}
		if features, ok := object["features"].([]any); ok {
			for _, rawFeature := range features {
				feature, ok := rawFeature.(map[string]any)
				if !ok {
					continue
				}
				featureID, _ := feature["id"].(string)
				kind, _ := feature["type"].(string)
				definition := manifestFeature{Kind: kind, Values: map[string]bool{}, ResourceBindings: map[string][]string{}}
				for _, value := range stringsFromAny(feature["values"]) {
					definition.Values[value] = true
				}
				if bindings, ok := feature["resource_bindings"].(map[string]any); ok {
					for value, rawResources := range bindings {
						definition.ResourceBindings[value] = stringsFromAny(rawResources)
					}
				}
				if featureID != "" {
					item.Features[featureID] = definition
				}
			}
		}
		result[id] = item
	}
	return result, nil
}

func inputDigests(inputs []contract.Input) []InputDigest {
	result := make([]InputDigest, 0, len(inputs))
	for _, input := range inputs {
		result = append(result, InputDigest{Path: input.Path, SHA256: input.SHA256})
	}
	return result
}

func sameInputs(left, right []contract.Input) bool {
	if len(left) != len(right) {
		return false
	}
	for index := range left {
		if left[index].Path != right[index].Path || left[index].SHA256 != right[index].SHA256 {
			return false
		}
	}
	return true
}

func accessForCapabilities(capabilities []string) Access {
	legacyVocabulary := []string{
		"ap-uplink", "direct-egress", "direct-ingress", "edge-ingress",
		"local-lan", "overlay", "rg-lan", "vpn-egress",
	}
	declared := map[string]bool{}
	enabled := map[string]bool{}
	for _, capability := range capabilities {
		switch capability {
		case "overlay":
			enabled["overlay"] = true
		case "local-ap-uplink":
			enabled["ap-uplink"] = true
		case "direct-wan-egress":
			enabled["direct-egress"] = true
		case "edge-tunnel-ingress":
			enabled["edge-ingress"] = true
		case "direct-wan-ingress":
			enabled["direct-ingress"] = true
		}
	}
	prohibited := map[string]bool{}
	for _, capability := range legacyVocabulary {
		if !enabled[capability] {
			prohibited[capability] = true
		}
	}
	for capability := range enabled {
		declared[capability] = true
	}
	return Access{
		Declared: sortedCopy(capabilities), LegacyAvailable: sortedBoolKeys(declared),
		Enabled: sortedBoolKeys(enabled), Prohibited: sortedBoolKeys(prohibited),
	}
}

func sortedBoolKeys(values map[string]bool) []string {
	result := make([]string, 0, len(values))
	for value := range values {
		result = append(result, value)
	}
	sort.Strings(result)
	return result
}

func resourceBindings(values map[string]any) []ResourceBinding {
	result := make([]ResourceBinding, 0, len(values))
	for _, key := range sortedKeys(values) {
		result = append(result, ResourceBinding{ID: key, Value: values[key]})
	}
	return result
}

func resourceMapForManifest(manifest manifest, values []ResourceBinding) map[string]any {
	result := map[string]any{}
	for _, binding := range values {
		definition, ok := manifest.Features[binding.ID]
		if !ok {
			continue
		}
		if boolean, ok := binding.Value.(bool); ok {
			if !boolean {
				continue
			}
			for _, resource := range definition.ResourceBindings["true"] {
				result[resource] = true
			}
			continue
		}
		if value, ok := binding.Value.(string); ok {
			for _, resource := range definition.ResourceBindings[value] {
				result[resource] = true
			}
		}
	}
	return result
}

func stringsFromAny(value any) []string {
	items, _ := value.([]any)
	result := make([]string, 0, len(items))
	for _, item := range items {
		if text, ok := item.(string); ok {
			result = append(result, text)
		}
	}
	return result
}

func legacyPlacement(value Placement) map[string]any {
	switch value.Mode {
	case "single-box":
		return map[string]any{"active_master": value.RuntimeBox}
	case "active-passive":
		return map[string]any{"active_master": value.ActiveRuntimeBox, "passive_backup": value.PassiveRuntimeBox}
	case "multi-box":
		return map[string]any{"boxes": value.RuntimeBoxes}
	default:
		return map[string]any{}
	}
}

func placementHasTarget(value any) bool {
	object, ok := asMap(value)
	if !ok {
		return false
	}
	for _, value := range object {
		switch current := value.(type) {
		case string:
			if current != "" {
				return true
			}
		case []any:
			if len(current) != 0 {
				return true
			}
		}
	}
	return false
}

func equivalent(left, right any) bool {
	return reflect.DeepEqual(normalize(left), normalize(right))
}

func normalize(value any) any {
	encoded, err := json.Marshal(value)
	if err != nil {
		return value
	}
	decoder := json.NewDecoder(bytes.NewReader(encoded))
	decoder.UseNumber()
	var result any
	if err := decoder.Decode(&result); err != nil {
		return value
	}
	return result
}

func integer(value any) (int64, bool) {
	switch current := value.(type) {
	case json.Number:
		result, err := current.Int64()
		return result, err == nil
	case int:
		return int64(current), true
	case int64:
		return current, true
	default:
		return 0, false
	}
}

func asMap(value any) (map[string]any, bool) {
	result, ok := value.(map[string]any)
	return result, ok
}

func isMap(value any) bool {
	_, ok := asMap(value)
	return ok
}

func equivalentStringSet(left, right any) bool {
	toStrings := func(value any) ([]string, bool) {
		normalized, ok := normalize(value).([]any)
		if !ok {
			return nil, false
		}
		result := make([]string, 0, len(normalized))
		for _, item := range normalized {
			text, ok := item.(string)
			if !ok {
				return nil, false
			}
			result = append(result, text)
		}
		sort.Strings(result)
		return result, true
	}
	leftStrings, leftOK := toStrings(left)
	rightStrings, rightOK := toStrings(right)
	return leftOK && rightOK && reflect.DeepEqual(leftStrings, rightStrings)
}

func sortedCopy(values []string) []string {
	result := make([]string, len(values))
	copy(result, values)
	sort.Strings(result)
	return result
}

func sortedKeys[V any](values map[string]V) []string {
	result := make([]string, 0, len(values))
	for key := range values {
		result = append(result, key)
	}
	sort.Strings(result)
	return result
}

func git(root string, arguments ...string) *exec.Cmd {
	command := exec.Command("git", append([]string{"-C", root}, arguments...)...)
	environment := make([]string, 0, len(os.Environ())+2)
	for _, entry := range os.Environ() {
		name, _, _ := strings.Cut(entry, "=")
		if strings.HasPrefix(name, "GIT_") {
			continue
		}
		environment = append(environment, entry)
	}
	command.Env = append(environment, "GIT_CONFIG_NOSYSTEM=1", "GIT_CONFIG_GLOBAL=/dev/null")
	return command
}
