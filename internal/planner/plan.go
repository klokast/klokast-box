// Package planner resolves Instance Specification v1 intent and compares the result
// with transitional desired-state inputs. It is read-only.
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
	"path/filepath"
	"reflect"
	"sort"
	"strings"

	klokastbox "klokast-box"
	"klokast-box/internal/contract"
)

const maximumRegistryFile = 1024 * 1024

type Options struct {
	InstancePath               string
	CompatibilityDeployment    string
	CompatibilityRegistry      string
	CompatibilityControllerHA  string
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
	Compatibility  *Compatibility        `json:"compatibility,omitempty"`
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
	SchemaVersion int          `json:"schema_version"`
	Engine        Engine       `json:"engine"`
	Tailnet       Tailnet      `json:"tailnet"`
	Sites         []Site       `json:"sites"`
	Boxes         []Box        `json:"boxes"`
	ControlPlane  ControlPlane `json:"control_plane"`
	Apps          []App        `json:"apps"`
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
	ID                   string       `json:"id"`
	HostnamePrefix       string       `json:"hostname_prefix"`
	SiteID               string       `json:"site_id"`
	Connectivity         []string     `json:"connectivity"`
	Runtime              RuntimeNames `json:"runtime"`
	Access               Access       `json:"access"`
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
	Declared        []string        `json:"declared_capabilities"`
	LegacyAvailable []string        `json:"legacy_available_capabilities"`
	Enabled         []string        `json:"enabled_capabilities"`
	Prohibited      []string        `json:"prohibited_capabilities"`
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
	ID        string `json:"id"`
	BoxID     string `json:"box_id"`
	RuntimeBox string `json:"runtime_box"`
	Retention string `json:"retention"`
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

type Compatibility struct {
	RegistrySHA256 string               `json:"registry_sha256"`
	Inputs         []CompatibilityInput `json:"inputs"`
	Summary        FindingSummary       `json:"summary"`
	Findings       []Finding            `json:"findings"`
}

type CompatibilityInput struct {
	Name   string `json:"name"`
	SHA256 string `json:"sha256"`
}

type FindingSummary struct {
	Matched           int `json:"matched"`
	Derived           int `json:"derived"`
	CompatibilityOnly int `json:"compatibility_only"`
	Conflict          int `json:"conflict"`
	Unsupported       int `json:"unsupported"`
}

type Finding struct {
	ID        string `json:"id"`
	Path      string `json:"path"`
	Class     string `json:"class"`
	Code      string `json:"code"`
	Authority string `json:"authority,omitempty"`
	Message   string `json:"message"`
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

type registry struct {
	digest string
	root   map[string]any
}

func Plan(options Options, engine contract.Engine) (Result, error) {
	result := Result{
		SchemaVersion: 1,
		Engine:        Engine{Repository: engine.Repository, Ref: engine.Ref, Commit: engine.Commit},
		Repository:    Repository{Reasons: []string{}},
		Inputs:        []InputDigest{},
		Diagnostics:   []contract.Diagnostic{},
	}
	snapshot, report, err := contract.Load(options.InstancePath, engine)
	if err != nil {
		return Result{}, err
	}
	if !report.Valid {
		result.Diagnostics = report.Diagnostics
		return result, nil
	}
	legacy, diagnostics, err := loadRegistry(options.CompatibilityRegistry)
	if err != nil {
		return Result{}, err
	}
	if len(diagnostics) != 0 {
		result.Diagnostics = diagnostics
		return result, nil
	}
	var legacyDeployment, legacyController compatibilityDocument
	if options.CompatibilityDeployment != "" {
		legacyDeployment, diagnostics, err = loadCompatibilityDocument(options.CompatibilityDeployment, "compatibility-deployment")
		if err != nil {
			return Result{}, err
		}
		if len(diagnostics) != 0 {
			result.Diagnostics = diagnostics
			return result, nil
		}
	}
	if options.CompatibilityControllerHA != "" {
		legacyController, diagnostics, err = loadCompatibilityDocument(options.CompatibilityControllerHA, "compatibility-controller-ha")
		if err != nil {
			return Result{}, err
		}
		if len(diagnostics) != 0 {
			result.Diagnostics = diagnostics
			return result, nil
		}
	}

	projection := Resolve(snapshot)
	projectionHash, err := ProjectionHash(projection)
	if err != nil {
		return Result{}, err
	}
	manifests, err := loadManifests()
	if err != nil {
		return Result{}, fmt.Errorf("load embedded application manifests: %w", err)
	}
	compatibility := compare(snapshot, projection, legacy, manifests)
	compatibility.Inputs = []CompatibilityInput{{Name: "legacy_platform_resources", SHA256: legacy.digest}}
	if options.CompatibilityDeployment != "" {
		mergeCompatibility(&compatibility, compareDeployment(projection, legacyDeployment))
		compatibility.Inputs = append(compatibility.Inputs, CompatibilityInput{Name: "legacy_deployment", SHA256: legacyDeployment.digest})
	}
	if options.CompatibilityControllerHA != "" {
		mergeCompatibility(&compatibility, compareControllerHA(projection, legacyController))
		compatibility.Inputs = append(compatibility.Inputs, CompatibilityInput{Name: "legacy_controller_ha", SHA256: legacyController.digest})
	}
	sort.Slice(compatibility.Inputs, func(i, j int) bool { return compatibility.Inputs[i].Name < compatibility.Inputs[j].Name })
	sortCompatibility(&compatibility)

	second, secondReport, err := contract.Load(options.InstancePath, engine)
	if err != nil {
		return Result{}, err
	}
	if !secondReport.Valid || !sameInputs(snapshot.Inputs, second.Inputs) {
		return Result{}, fmt.Errorf("authoritative instance inputs changed during planning")
	}
	secondLegacy, secondDiagnostics, err := loadRegistry(options.CompatibilityRegistry)
	if err != nil {
		return Result{}, err
	}
	if len(secondDiagnostics) != 0 || secondLegacy.digest != legacy.digest {
		return Result{}, fmt.Errorf("compatibility registry changed during planning")
	}
	if options.CompatibilityDeployment != "" {
		secondDeployment, secondDiagnostics, loadErr := loadCompatibilityDocument(options.CompatibilityDeployment, "compatibility-deployment")
		if loadErr != nil {
			return Result{}, loadErr
		}
		if len(secondDiagnostics) != 0 || secondDeployment.digest != legacyDeployment.digest {
			return Result{}, fmt.Errorf("compatibility deployment changed during planning")
		}
	}
	if options.CompatibilityControllerHA != "" {
		secondController, secondDiagnostics, loadErr := loadCompatibilityDocument(options.CompatibilityControllerHA, "compatibility-controller-ha")
		if loadErr != nil {
			return Result{}, loadErr
		}
		if len(secondDiagnostics) != 0 || secondController.digest != legacyController.digest {
			return Result{}, fmt.Errorf("compatibility controller HA input changed during planning")
		}
	}
	repository, err := inspectRepository(snapshot.Root)
	if err != nil {
		return Result{}, err
	}

	result.Valid = true
	result.Compatible = compatibility.Summary.Conflict == 0 && compatibility.Summary.Unsupported == 0
	result.Repository = repository
	result.Deployable = repository.Clean && repository.HeadCommit != ""
	result.AuthorityReady = result.Compatible && result.Deployable && compatibility.Summary.CompatibilityOnly == 0
	result.Inputs = inputDigests(snapshot.Inputs)
	result.Projection = &projection
	result.ProjectionHash = projectionHash
	result.Compatibility = &compatibility
	return result, nil
}

// Resolve produces the deterministic Instance Specification v1 projection. Plan and all
// offline observers use this resolver so runtime identities cannot diverge.
func Resolve(snapshot contract.Snapshot) Projection {
	result := Projection{
		SchemaVersion: 1,
		Engine: Engine{
			Repository: snapshot.Lock.Engine.Repository,
			Ref:        snapshot.Lock.Engine.Ref,
			Commit:     snapshot.Lock.Engine.Commit,
		},
		Tailnet: Tailnet{
			MagicDNSSuffix: snapshot.Instance.Tailscale.DNSName,
			Groups:         []TailnetGroup{},
		},
		Sites:      []Site{},
		Boxes:      []Box{},
		ControlPlane: ControlPlane{
			Airunners: []string{},
		},
		Apps: []App{},
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

func compare(snapshot contract.Snapshot, projection Projection, legacy registry, manifests map[string]manifest) Compatibility {
	findings := []Finding{}
	add := func(path, class, code, message string) {
		authority := ""
		if class == "compatibility_only" {
			authority = "legacy_platform_resources"
		}
		findings = append(findings, Finding{Path: path, Class: class, Code: code, Authority: authority, Message: message})
	}
	add("schema_version", "matched", "registry.schema", "the legacy registry uses the supported compatibility schema")
	for _, field := range sortedKeys(legacy.root) {
		if field != "schema_version" && field != "boxes" && field != "apps" {
			add(field, "unsupported", "registry.field", "the legacy registry root field has no Instance Specification v1 mapping")
		}
	}
	boxes, _ := legacy.root["boxes"].(map[string]any)
	apps, _ := legacy.root["apps"].(map[string]any)
	expectedBoxes := map[string]bool{}
	for _, box := range projection.Boxes {
		legacyID := box.HostnamePrefix
		expectedBoxes[legacyID] = true
		path := "boxes." + legacyID
		add(path, "derived", "box.logical-runtime", "the legacy box key derives from the stable logical box ID and hostname prefix")
		legacyValue, present := boxes[legacyID]
		if !present {
			add(path, "conflict", "box.missing", "the legacy registry has no box for the resolved hostname prefix")
			continue
		}
		legacyBox, objectOK := asMap(legacyValue)
		if !objectOK {
			add(path, "unsupported", "box.type", "the legacy box entry must be an object")
			continue
		}
		accessValue, accessPresent := legacyBox["access"]
		access, accessOK := asMap(accessValue)
		if !accessPresent {
			add(path+".access", "conflict", "access.missing", "the legacy registry has no access object for the resolved box")
		} else if !accessOK {
			add(path+".access", "unsupported", "access.type", "the legacy access entry must be an object")
		} else {
			expected := map[string]any{
				"available_capabilities":  box.Access.LegacyAvailable,
				"enabled_capabilities":    box.Access.Enabled,
				"prohibited_capabilities": box.Access.Prohibited,
			}
			for _, field := range []string{"available_capabilities", "enabled_capabilities", "prohibited_capabilities"} {
				matches := equivalent(expected[field], access[field])
				matches = equivalentStringSet(expected[field], access[field])
				if matches {
					add(path+".access."+field, "matched", "access.capability", "the connectivity capabilities resolve to the legacy access field")
				} else {
					add(path+".access."+field, "conflict", "access.mismatch", "the connectivity capabilities do not resolve to the legacy access field")
				}
			}
			for _, field := range sortedKeys(access) {
				if field == "policy" {
					add(path+".access."+field, "unsupported", "access.policy-removed", "the legacy box-wide access policy is not accepted")
				} else if field != "available_capabilities" && field != "enabled_capabilities" && field != "prohibited_capabilities" {
					add(path+".access."+field, "unsupported", "access.field", "the legacy access field is not accepted by the compatibility adapter")
				}
			}
		}
		add(path+".connectivity", "derived", "connectivity.capabilities", "the Instance Specification declares provider-neutral connectivity capabilities")
		for _, field := range sortedKeys(legacyBox) {
			if field != "access" {
				add(path+"."+field, "compatibility_only", "box.compatibility", "the legacy box field has no Instance Specification v1 representation and must be retained outside the projection")
			}
		}
	}
	for _, id := range sortedKeys(boxes) {
		if !expectedBoxes[id] {
			add("boxes."+id, "conflict", "box.unrepresented", "the legacy box is not represented by an Instance Specification v1 box")
		}
	}

	expectedApps := map[string]bool{}
	for _, app := range projection.Apps {
		expectedApps[app.ID] = true
		path := "apps." + app.ID
		legacyValue, present := apps[app.ID]
		if !present {
			if app.Enabled {
				add(path, "conflict", "app.missing", "the present Instance Specification app is absent from the legacy registry")
			} else {
				add(path, "derived", "app.absent", "the absent app and its retained data do not require a legacy registry entry")
			}
			for _, data := range app.Data {
				add(path+".data."+data.ID, "derived", "data.contract", "retained data is declared only by the Instance Specification")
			}
			continue
		}
		legacyApp, objectOK := asMap(legacyValue)
		if !objectOK {
			add(path, "unsupported", "app.type", "the legacy app entry must be an object")
			continue
		}
		legacyEnabled, enabledOK := legacyApp["enabled"].(bool)
		if !enabledOK || legacyEnabled != app.Enabled {
			add(path+".enabled", "conflict", "app.enabled", "the Instance Specification and legacy enabled states do not match")
		} else {
			add(path+".enabled", "matched", "app.enabled", "the Instance Specification and legacy enabled states match")
		}
		if app.Enabled {
			mode := strings.ReplaceAll(manifests[app.ID].PlacementMode, "_", "-")
			if mode == "" {
				mode = "active-passive"
			}
			if mode != app.Placement.Mode {
				add(path+".placement", "conflict", "placement.mode", "the present Instance Specification placement mode is not supported by the legacy app manifest")
			}
			expected := legacyPlacement(app.Placement)
			if equivalent(expected, legacyApp["placement"]) {
				add(path+".placement", "matched", "placement.match", "the resolved app placement matches the legacy registry")
			} else {
				add(path+".placement", "conflict", "placement.mismatch", "the resolved app placement does not match the legacy registry")
			}
		} else {
			placementValue, placementPresent := legacyApp["placement"]
			if placementPresent && !isMap(placementValue) {
				add(path+".placement", "unsupported", "placement.type", "the legacy app placement must be an object")
			} else if placementHasTarget(placementValue) {
				add(path+".placement", "compatibility_only", "placement.disabled", "disabled legacy cleanup placement is not authoritative Instance Specification v1 intent")
			} else {
				add(path+".placement", "derived", "placement.preselected", "Instance Specification v1 keeps disabled app preselection outside the legacy adapter")
			}
		}
		actualResources := legacyApp["resources"]
		if app.Enabled {
			expectedResources := resourceMapForManifest(manifests[app.ID], app.Resources)
			actualResources = normalizedLegacyResourceFlags(actualResources)
			if equivalent(expectedResources, actualResources) {
				add(path+".features", "matched", "features.match", "the Instance Specification features match legacy resource selections")
			} else {
				add(path+".features", "conflict", "features.mismatch", "the Instance Specification features do not match legacy resource selections")
			}
		} else if actualResources != nil {
			add(path+".resources", "compatibility_only", "resources.absent", "legacy disabled-app resource preselection remains under compatibility authority")
		}
		for _, data := range app.Data {
			add(path+".data."+data.ID, "derived", "data.contract", "retained data is declared only by the Instance Specification")
		}
		for _, field := range sortedKeys(legacyApp) {
			if field != "enabled" && field != "placement" && field != "resources" {
				add(path+"."+field, "compatibility_only", "app.compatibility", "the legacy app field has no Instance Specification v1 representation and must be retained outside the projection")
			}
		}
	}
	for _, id := range sortedKeys(apps) {
		if expectedApps[id] {
			continue
		}
		legacyApp, ok := asMap(apps[id])
		if !ok {
			add("apps."+id, "unsupported", "app.type", "the legacy app entry must be an object")
			continue
		}
		enabled, enabledOK := legacyApp["enabled"].(bool)
		if !enabledOK {
			add("apps."+id+".enabled", "unsupported", "app.enabled-type", "the unrepresented legacy enabled field must be a Boolean")
			continue
		}
		if enabled {
			add("apps."+id, "conflict", "app.unrepresented", "the enabled legacy app is not represented by Instance Specification v1")
		} else {
			add("apps."+id, "derived", "app.omitted", "an omitted Instance Specification app resolves to absent")
			continue
		}
		for _, field := range sortedKeys(legacyApp) {
			class := "compatibility_only"
			code := "app.unrepresented-field"
			message := "the unrepresented legacy app field must remain under compatibility authority"
			if field == "enabled" {
				if enabled, ok := legacyApp[field].(bool); !ok {
					class, code, message = "unsupported", "app.enabled-type", "the unrepresented legacy enabled field must be a boolean"
				} else if enabled {
					class, code, message = "conflict", "app.enabled", "the enabled legacy app has no Instance Specification v1 representation"
				}
			}
			add("apps."+id+"."+field, class, code, message)
		}
	}

	result := Compatibility{RegistrySHA256: legacy.digest, Findings: findings}
	sortCompatibility(&result)
	return result
}

func normalizedLegacyResourceFlags(value any) map[string]any {
	result := map[string]any{}
	resources, ok := value.(map[string]any)
	if !ok {
		return result
	}
	for id, selected := range resources {
		if enabled, ok := selected.(bool); ok && enabled {
			result[id] = true
		}
	}
	return result
}

func loadRegistry(path string) (registry, []contract.Diagnostic, error) {
	if path == "" {
		return registry{}, nil, fmt.Errorf("compatibility registry path is required")
	}
	absolute, err := filepath.Abs(path)
	if err != nil {
		return registry{}, nil, fmt.Errorf("resolve compatibility registry: %w", err)
	}
	info, err := os.Lstat(absolute)
	if err != nil {
		return registry{}, nil, fmt.Errorf("inspect compatibility registry: %w", err)
	}
	if info.Mode()&os.ModeSymlink != 0 {
		return registry{}, []contract.Diagnostic{{Path: "compatibility-registry", Code: "path.symlink", Message: "compatibility registry must not be a symbolic link"}}, nil
	}
	if !info.Mode().IsRegular() {
		return registry{}, []contract.Diagnostic{{Path: "compatibility-registry", Code: "path.type", Message: "compatibility registry must be a regular file"}}, nil
	}
	if info.Size() > maximumRegistryFile {
		return registry{}, []contract.Diagnostic{{Path: "compatibility-registry", Code: "path.size", Message: "compatibility registry exceeds the one-MiB limit"}}, nil
	}
	content, err := os.ReadFile(absolute)
	if err != nil {
		return registry{}, nil, fmt.Errorf("read compatibility registry: %w", err)
	}
	if bytes.IndexByte(content, 0) >= 0 {
		return registry{}, []contract.Diagnostic{{Path: "compatibility-registry", Code: "yaml.binary", Message: "compatibility registry must be text"}}, nil
	}
	var diagnostics []contract.Diagnostic
	for _, line := range contract.RawSecretLines(content) {
		diagnostics = append(diagnostics, contract.Diagnostic{Path: fmt.Sprintf("compatibility-registry:%d", line), Code: "secret.raw", Message: "possible raw secret value is present"})
	}
	value, yamlDiagnostics := contract.ParseSafeYAML(content)
	for _, diagnostic := range yamlDiagnostics {
		diagnostic.Path = "compatibility-registry"
		diagnostics = append(diagnostics, diagnostic)
	}
	root, ok := value.(map[string]any)
	if len(yamlDiagnostics) == 0 && !ok {
		diagnostics = append(diagnostics, contract.Diagnostic{Path: "compatibility-registry", Code: "registry.type", Message: "compatibility registry must be an object"})
	}
	if ok {
		version, versionOK := integer(root["schema_version"])
		if !versionOK || version != 1 {
			diagnostics = append(diagnostics, contract.Diagnostic{Path: "compatibility-registry$.schema_version", Code: "registry.version", Message: "compatibility registry schema_version must be 1"})
		}
		if _, boxesOK := root["boxes"].(map[string]any); !boxesOK {
			diagnostics = append(diagnostics, contract.Diagnostic{Path: "compatibility-registry$.boxes", Code: "registry.boxes", Message: "compatibility registry boxes must be an object"})
		}
		if _, appsOK := root["apps"].(map[string]any); !appsOK {
			diagnostics = append(diagnostics, contract.Diagnostic{Path: "compatibility-registry$.apps", Code: "registry.apps", Message: "compatibility registry apps must be an object"})
		}
	}
	sort.Slice(diagnostics, func(i, j int) bool {
		if diagnostics[i].Path != diagnostics[j].Path {
			return diagnostics[i].Path < diagnostics[j].Path
		}
		return diagnostics[i].Code < diagnostics[j].Code
	})
	digest := sha256.Sum256(content)
	return registry{digest: fmt.Sprintf("%x", digest[:]), root: root}, diagnostics, nil
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
