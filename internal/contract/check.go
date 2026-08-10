package contract

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"
	_ "time/tzdata"

	klokastbox "klokast-box"

	"github.com/santhosh-tekuri/jsonschema/v6"
	"gopkg.in/yaml.v3"
)

const (
	lockPath           = "klokast.lock.yml"
	maximumTrackedFile = 1024 * 1024
)

var (
	allowedYAMLTags = map[string]bool{
		"": true, "!!map": true, "!!seq": true, "!!str": true,
		"!!int": true, "!!bool": true, "!!null": true, "!!float": true,
	}
	identifierPattern = regexp.MustCompile(`^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$`)
	secretAssignment  = regexp.MustCompile(`(?i)^[ \t]*(?:password|passwd|secret|token|api[_-]?key|auth[_-]?key|private[_-]?key)[ \t]*:[ \t]*['"]?([^#'" \t\r\n]{8,})`)
	secretTokens      = []*regexp.Regexp{
		regexp.MustCompile(`tskey-[A-Za-z0-9_-]{8,}`),
		regexp.MustCompile(`gh[pousr]_[A-Za-z0-9_]{8,}`),
		regexp.MustCompile(`-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----`),
	}
	reservedRuntimeSuffixes = []string{
		"bootstrap", "dom0", "router", "bak", "dmz", "iot", "usr", "ops", "airunner", "builder",
	}
)

type Diagnostic struct {
	Path    string `json:"path"`
	Code    string `json:"code"`
	Message string `json:"message"`
}

type Report struct {
	Valid       bool         `json:"valid"`
	Diagnostics []Diagnostic `json:"diagnostics"`
}

type Engine struct {
	Repository string
	Ref        string
	Commit     string
}

type rootDocument struct {
	Contract int `yaml:"contract"`
	Paths    struct {
		Deployment        string `yaml:"deployment"`
		PlatformResources string `yaml:"platform_resources"`
	} `yaml:"paths"`
}

type lockDocument struct {
	SchemaVersion int `yaml:"schema_version"`
	Engine        struct {
		Repository string `yaml:"repository"`
		Ref        string `yaml:"ref"`
		Commit     string `yaml:"commit"`
	} `yaml:"engine"`
}

type deploymentDocument struct {
	SchemaVersion int `yaml:"schema_version"`
	Instance      struct {
		Name string `yaml:"name"`
	} `yaml:"instance"`
	Tailnet struct {
		MagicDNSSuffix string              `yaml:"magicdns_suffix"`
		Groups         map[string][]string `yaml:"groups"`
	} `yaml:"tailnet"`
	Sites map[string]struct {
		Country          string `yaml:"country"`
		Timezone         string `yaml:"timezone"`
		PhysicalLocation string `yaml:"physical_location"`
	} `yaml:"sites"`
	Boxes map[string]struct {
		HostnamePrefix string `yaml:"hostname_prefix"`
		Site           string `yaml:"site"`
	} `yaml:"boxes"`
	ControlPlane struct {
		Controller struct {
			ActiveBox  string `yaml:"active_box"`
			StandbyBox string `yaml:"standby_box"`
		} `yaml:"controller"`
		Airunners []struct {
			ID       string `yaml:"id"`
			Kind     string `yaml:"kind"`
			Box      string `yaml:"box"`
			Hostname string `yaml:"hostname"`
		} `yaml:"airunners"`
	} `yaml:"control_plane"`
}

type platformDocument struct {
	SchemaVersion int `yaml:"schema_version"`
	Boxes         map[string]struct {
		Access struct {
			Declared   []string          `yaml:"declared_capabilities"`
			Enabled    []string          `yaml:"enabled_capabilities"`
			Prohibited []string          `yaml:"prohibited_capabilities"`
			Policy     map[string]string `yaml:"policy"`
		} `yaml:"access"`
	} `yaml:"boxes"`
	Apps map[string]struct {
		Enabled   bool `yaml:"enabled"`
		Placement struct {
			Mode          string   `yaml:"mode"`
			Box           string   `yaml:"box"`
			ActiveMaster  string   `yaml:"active_master"`
			PassiveBackup string   `yaml:"passive_backup"`
			Boxes         []string `yaml:"boxes"`
		} `yaml:"placement"`
		Resources map[string]any `yaml:"resources"`
	} `yaml:"apps"`
}

type appManifest struct {
	PlacementMode string
	Capabilities  map[string]bool
	Resources     map[string]bool
}

type checker struct {
	root        string
	engine      Engine
	diagnostics []Diagnostic
	tracked     map[string]bool
}

func Check(instancePath string, engine Engine) (Report, error) {
	root, err := filepath.Abs(instancePath)
	if err != nil {
		return Report{}, fmt.Errorf("resolve instance path: %w", err)
	}
	root, err = filepath.EvalSymlinks(root)
	if err != nil {
		return Report{}, fmt.Errorf("resolve instance path: %w", err)
	}
	info, err := os.Stat(root)
	if err != nil {
		return Report{}, fmt.Errorf("inspect instance path: %w", err)
	}
	if !info.IsDir() {
		return Report{}, fmt.Errorf("instance path is not a directory")
	}
	if _, err := exec.LookPath("git"); err != nil {
		return Report{}, fmt.Errorf("git is required: %w", err)
	}

	c := &checker{root: root, engine: engine, tracked: map[string]bool{}}
	if !c.inspectRepository() {
		return c.report(), nil
	}
	c.inspectTrackedFiles()

	rootValue, rootNode, rootOK := c.loadAndValidate("klokast.yml", "schemas/instance-contract-v1.json")
	var rootConfig rootDocument
	if rootOK {
		rootOK = decodeNode(rootNode, &rootConfig, func(message string) {
			c.add("klokast.yml", "yaml.decode", message)
		})
		_ = rootValue
	}
	if !rootOK {
		return c.report(), nil
	}

	paths := []string{lockPath, rootConfig.Paths.Deployment, rootConfig.Paths.PlatformResources}
	seen := map[string]bool{"klokast.yml": true}
	for _, path := range paths {
		if seen[path] {
			c.add(path, "path.duplicate", "authoritative files must use distinct paths")
		}
		seen[path] = true
	}

	_, lockNode, lockOK := c.loadAndValidate(lockPath, "schemas/engine-lock-v1.json")
	_, deploymentNode, deploymentOK := c.loadAndValidate(rootConfig.Paths.Deployment, "schemas/deployment-v1.json")
	_, platformNode, platformOK := c.loadAndValidate(rootConfig.Paths.PlatformResources, "schemas/platform-resources-v1.json")

	var lock lockDocument
	var deployment deploymentDocument
	var platform platformDocument
	if lockOK {
		lockOK = decodeNode(lockNode, &lock, func(message string) { c.add(lockPath, "yaml.decode", message) })
	}
	if deploymentOK {
		deploymentOK = decodeNode(deploymentNode, &deployment, func(message string) {
			c.add(rootConfig.Paths.Deployment, "yaml.decode", message)
		})
	}
	if platformOK {
		platformOK = decodeNode(platformNode, &platform, func(message string) {
			c.add(rootConfig.Paths.PlatformResources, "yaml.decode", message)
		})
	}
	if lockOK {
		c.validateLock(lock)
	}
	if deploymentOK {
		c.validateDeployment(rootConfig.Paths.Deployment, deployment)
	}
	if deploymentOK && platformOK {
		manifests, manifestErr := loadAppManifests()
		if manifestErr != nil {
			return Report{}, fmt.Errorf("load embedded application manifests: %w", manifestErr)
		}
		c.validatePlatform(rootConfig.Paths.PlatformResources, deployment, platform, manifests)
	}
	return c.report(), nil
}

func (c *checker) inspectRepository() bool {
	command := exec.Command("git", "-C", c.root, "rev-parse", "--show-toplevel")
	output, err := command.Output()
	if err != nil {
		c.add(".", "git.repository", "instance must be a standalone Git repository")
		return false
	}
	top, err := filepath.EvalSymlinks(strings.TrimSpace(string(output)))
	if err != nil || top != c.root {
		c.add(".", "git.repository", "instance must be the root of a standalone Git repository")
		return false
	}
	super, err := exec.Command("git", "-C", c.root, "rev-parse", "--show-superproject-working-tree").Output()
	if err == nil && strings.TrimSpace(string(super)) != "" {
		c.add(".", "git.repository", "instance must not be embedded as a Git submodule")
	}
	tracked, err := exec.Command("git", "-C", c.root, "ls-files", "-z").Output()
	if err != nil {
		c.add(".", "git.tracked", "cannot enumerate tracked instance inputs")
		return false
	}
	for _, name := range bytes.Split(tracked, []byte{0}) {
		if len(name) > 0 {
			c.tracked[string(name)] = true
		}
	}
	return true
}

func (c *checker) inspectTrackedFiles() {
	paths := make([]string, 0, len(c.tracked))
	for path := range c.tracked {
		paths = append(paths, path)
	}
	sort.Strings(paths)
	for _, path := range paths {
		if forbiddenTrackedPath(path) {
			c.add(path, "tracked.forbidden", "generated state, secret paths, and key material must not be tracked")
		}
		full := filepath.Join(c.root, filepath.FromSlash(path))
		info, err := os.Lstat(full)
		if err != nil {
			c.add(path, "tracked.missing", "tracked path is not readable from the worktree")
			continue
		}
		if info.Mode()&os.ModeSymlink != 0 {
			c.add(path, "path.symlink", "tracked symbolic links are not permitted")
			continue
		}
		if !info.Mode().IsRegular() {
			continue
		}
		if info.Size() > maximumTrackedFile {
			c.add(path, "tracked.size", "tracked file exceeds the one-MiB validation limit")
			continue
		}
		content, err := os.ReadFile(full)
		if err != nil {
			c.add(path, "tracked.read", "tracked file cannot be read")
			continue
		}
		if bytes.IndexByte(content, 0) >= 0 {
			c.add(path, "tracked.binary", "binary content is not permitted in the instance repository")
			continue
		}
		for number, line := range bytes.Split(content, []byte{'\n'}) {
			if secretAssignment.Match(line) || matchesAny(secretTokens, line) {
				c.add(fmt.Sprintf("%s:%d", path, number+1), "secret.raw", "possible raw secret value is tracked")
			}
		}
	}
}

func (c *checker) loadAndValidate(path, schemaPath string) (any, *yaml.Node, bool) {
	full, clean, ok := c.safeAuthoritativePath(path)
	if !ok {
		return nil, nil, false
	}
	if !c.tracked[clean] {
		c.add(clean, "git.untracked", "authoritative input must be tracked by Git")
		return nil, nil, false
	}
	content, err := os.ReadFile(full)
	if err != nil {
		if errors.Is(err, fs.ErrNotExist) {
			c.add(clean, "path.missing", "authoritative input does not exist")
			return nil, nil, false
		}
		c.add(clean, "path.read", "authoritative input cannot be read")
		return nil, nil, false
	}
	node, value, parseDiagnostics := parseYAML(content)
	for _, diagnostic := range parseDiagnostics {
		diagnostic.Path = clean
		c.diagnostics = append(c.diagnostics, diagnostic)
	}
	if len(parseDiagnostics) != 0 {
		return nil, node, false
	}
	if err := validateSchema(schemaPath, value); err != nil {
		var validationError *jsonschema.ValidationError
		if errors.As(err, &validationError) {
			locations := leafLocations(validationError)
			if len(locations) == 0 {
				locations = []string{"$"}
			}
			for _, location := range locations {
				c.add(clean+location, "schema.invalid", "document does not satisfy its Contract v1 schema")
			}
		} else {
			c.add(clean, "schema.invalid", "document does not satisfy its Contract v1 schema")
		}
		return value, node, false
	}
	return value, node, true
}

func (c *checker) safeAuthoritativePath(path string) (string, string, bool) {
	clean := filepath.ToSlash(filepath.Clean(filepath.FromSlash(path)))
	if path == "" || strings.Contains(path, "\\") || filepath.IsAbs(path) || clean != path || clean == "." || clean == ".." || strings.HasPrefix(clean, "../") {
		c.add(path, "path.unsafe", "authoritative path must be a clean relative path inside the instance repository")
		return "", clean, false
	}
	current := c.root
	for _, component := range strings.Split(clean, "/") {
		current = filepath.Join(current, component)
		info, err := os.Lstat(current)
		if err != nil {
			if errors.Is(err, fs.ErrNotExist) {
				c.add(clean, "path.missing", "authoritative input does not exist")
			} else {
				c.add(clean, "path.read", "authoritative path cannot be inspected")
			}
			return "", clean, false
		}
		if info.Mode()&os.ModeSymlink != 0 {
			c.add(clean, "path.symlink", "authoritative paths may not contain symbolic links")
			return "", clean, false
		}
	}
	info, err := os.Stat(current)
	if err != nil || !info.Mode().IsRegular() {
		c.add(clean, "path.type", "authoritative input must be a regular file")
		return "", clean, false
	}
	return current, clean, true
}

func (c *checker) validateLock(lock lockDocument) {
	if c.engine.Repository != "https://github.com/klokast/klokast-box" || c.engine.Ref == "" ||
		!regexp.MustCompile(`^[A-Za-z0-9](?:[A-Za-z0-9._/-]{0,253}[A-Za-z0-9])?$`).MatchString(c.engine.Ref) ||
		strings.Contains(c.engine.Ref, "//") || strings.Contains(c.engine.Ref, "..") ||
		strings.Contains(c.engine.Ref, "@{") || !regexp.MustCompile(`^[0-9a-f]{40}$`).MatchString(c.engine.Commit) {
		c.add(lockPath, "engine.binary", "running binary does not identify a full engine commit")
		return
	}
	if lock.Engine.Repository != c.engine.Repository {
		c.add(lockPath, "engine.repository", "engine lock repository does not match the running builder-approved engine")
	}
	if lock.Engine.Ref != c.engine.Ref {
		c.add(lockPath, "engine.ref", "engine lock ref does not match the running builder-approved engine")
	}
	if lock.Engine.Commit != c.engine.Commit {
		c.add(lockPath, "engine.mismatch", "engine lock commit does not match the running builder-approved engine commit")
	}
}

func (c *checker) validateDeployment(path string, deployment deploymentDocument) {
	prefixes := map[string]string{}
	generated := map[string]string{}
	for site, value := range deployment.Sites {
		if _, err := time.LoadLocation(value.Timezone); err != nil {
			c.add(path+"$.sites."+site+".timezone", "timezone.invalid", "timezone is not in the embedded IANA database")
		}
	}
	for box, value := range deployment.Boxes {
		if _, ok := deployment.Sites[value.Site]; !ok {
			c.add(path+"$.boxes."+box+".site", "reference.site", "box references an unknown site")
		}
		if prior, exists := prefixes[value.HostnamePrefix]; exists {
			c.add(path+"$.boxes."+box+".hostname_prefix", "identity.prefix", "hostname prefix duplicates box "+prior)
		}
		prefixes[value.HostnamePrefix] = box
		for _, suffix := range reservedRuntimeSuffixes {
			if value.HostnamePrefix == suffix || strings.HasSuffix(value.HostnamePrefix, "-"+suffix) {
				c.add(path+"$.boxes."+box+".hostname_prefix", "identity.prefix", "hostname prefix ends in a reserved runtime role")
				break
			}
		}
		for _, suffix := range []string{"dom0", "router", "bak", "dmz", "iot", "ops", "airunner"} {
			name := value.HostnamePrefix + "-" + suffix
			if len(name) > 63 || !identifierPattern.MatchString(name) {
				c.add(path+"$.boxes."+box+".hostname_prefix", "identity.runtime", "hostname prefix cannot produce safe runtime names")
			}
			if prior, exists := generated[name]; exists {
				c.add(path+"$.boxes."+box+".hostname_prefix", "identity.runtime", "generated runtime name collides with "+prior)
			}
			generated[name] = box
		}
	}
	controller := deployment.ControlPlane.Controller
	if _, ok := deployment.Boxes[controller.ActiveBox]; !ok {
		c.add(path+"$.control_plane.controller.active_box", "reference.box", "active controller references an unknown box")
	}
	if controller.StandbyBox != "" {
		if _, ok := deployment.Boxes[controller.StandbyBox]; !ok {
			c.add(path+"$.control_plane.controller.standby_box", "reference.box", "standby controller references an unknown box")
		}
		if controller.StandbyBox == controller.ActiveBox {
			c.add(path+"$.control_plane.controller.standby_box", "cardinality.controller", "active and standby controllers must use different boxes")
		}
	}
	runnerIDs := map[string]bool{}
	runnerBoxes := map[string]bool{}
	externalHosts := map[string]bool{}
	for index, runner := range deployment.ControlPlane.Airunners {
		location := fmt.Sprintf("%s$.control_plane.airunners[%d]", path, index)
		if runnerIDs[runner.ID] {
			c.add(location+".id", "identity.airunner", "airunner ID must be unique")
		}
		runnerIDs[runner.ID] = true
		switch runner.Kind {
		case "box":
			if _, ok := deployment.Boxes[runner.Box]; !ok {
				c.add(location+".box", "reference.box", "airunner references an unknown box")
			}
			if runnerBoxes[runner.Box] {
				c.add(location+".box", "cardinality.airunner", "a box may host only one declared airunner")
			}
			runnerBoxes[runner.Box] = true
		case "external":
			if externalHosts[runner.Hostname] {
				c.add(location+".hostname", "identity.airunner", "external airunner hostname must be unique")
			}
			externalHosts[runner.Hostname] = true
			if owner, exists := generated[runner.Hostname]; exists {
				c.add(location+".hostname", "identity.runtime", "external hostname collides with generated names for "+owner)
			}
		}
	}
}

func (c *checker) validatePlatform(path string, deployment deploymentDocument, platform platformDocument, manifests map[string]appManifest) {
	for box := range deployment.Boxes {
		if _, ok := platform.Boxes[box]; !ok {
			c.add(path+"$.boxes", "reference.box", "platform resources omit deployment box "+box)
		}
	}
	for box, config := range platform.Boxes {
		if _, ok := deployment.Boxes[box]; !ok {
			c.add(path+"$.boxes."+box, "reference.box", "platform resources reference an unknown box")
		}
		declared := stringSet(config.Access.Declared)
		enabled := stringSet(config.Access.Enabled)
		prohibited := stringSet(config.Access.Prohibited)
		if declared["none"] || enabled["none"] || prohibited["none"] {
			c.add(path+"$.boxes."+box+".access", "capability.none", "none is a policy value, not a capability")
		}
		for capability := range enabled {
			if !declared[capability] {
				c.add(path+"$.boxes."+box+".access.enabled_capabilities", "capability.undeclared", "enabled capability must be declared")
			}
			if prohibited[capability] {
				c.add(path+"$.boxes."+box+".access", "capability.conflict", "enabled and prohibited capabilities must be disjoint")
			}
		}
		for intent, capability := range config.Access.Policy {
			if capability != "none" && !enabled[capability] {
				c.add(path+"$.boxes."+box+".access.policy."+intent, "capability.policy", "policy must select an enabled capability or none")
			}
		}
	}
	for app, binding := range platform.Apps {
		manifest, ok := manifests[app]
		location := path + "$.apps." + app
		if !ok {
			c.add(location, "app.unsupported", "application has no embedded public manifest")
			continue
		}
		boxes := placementBoxes(binding.Placement.Mode, binding.Placement.Box, binding.Placement.ActiveMaster, binding.Placement.PassiveBackup, binding.Placement.Boxes)
		for _, box := range boxes {
			if _, ok := deployment.Boxes[box]; !ok {
				c.add(location+".placement", "reference.box", "application placement references an unknown box")
			}
		}
		if binding.Placement.Mode == "active_passive" && binding.Placement.ActiveMaster == binding.Placement.PassiveBackup {
			c.add(location+".placement", "placement.cardinality", "active and passive placements must use different boxes")
		}
		if manifest.PlacementMode != "" && manifest.PlacementMode != binding.Placement.Mode {
			c.add(location+".placement.mode", "placement.mode", "placement mode does not match the embedded public manifest")
		}
		for resource := range binding.Resources {
			if !manifest.Resources[resource] {
				c.add(location+".resources."+resource, "resource.unknown", "resource binding is not declared by the embedded public manifest")
			}
		}
		if binding.Enabled {
			for _, box := range boxes {
				config, exists := platform.Boxes[box]
				if !exists {
					continue
				}
				enabled := stringSet(config.Access.Enabled)
				for capability := range manifest.Capabilities {
					if !enabled[capability] {
						c.add(location, "capability.required", "enabled application requires capability "+capability+" on box "+box)
					}
				}
			}
		}
	}
}

func parseYAML(content []byte) (*yaml.Node, any, []Diagnostic) {
	decoder := yaml.NewDecoder(bytes.NewReader(content))
	var document yaml.Node
	if err := decoder.Decode(&document); err != nil {
		return nil, nil, []Diagnostic{{Code: "yaml.syntax", Message: "YAML cannot be parsed"}}
	}
	if len(document.Content) != 1 {
		return &document, nil, []Diagnostic{{Code: "yaml.document", Message: "YAML must contain exactly one document"}}
	}
	var extra yaml.Node
	if err := decoder.Decode(&extra); err != io.EOF {
		return &document, nil, []Diagnostic{{Code: "yaml.document", Message: "YAML must contain exactly one document"}}
	}
	diagnostics := inspectYAMLNode(document.Content[0])
	if len(diagnostics) != 0 {
		return &document, nil, diagnostics
	}
	var value any
	if err := document.Content[0].Decode(&value); err != nil {
		return &document, nil, []Diagnostic{{Code: "yaml.decode", Message: "YAML value cannot be decoded"}}
	}
	encoded, err := json.Marshal(value)
	if err != nil {
		return &document, nil, []Diagnostic{{Code: "yaml.json", Message: "YAML value is not JSON-compatible"}}
	}
	decoderJSON := json.NewDecoder(bytes.NewReader(encoded))
	decoderJSON.UseNumber()
	if err := decoderJSON.Decode(&value); err != nil {
		return &document, nil, []Diagnostic{{Code: "yaml.json", Message: "YAML value is not JSON-compatible"}}
	}
	return &document, value, nil
}

func inspectYAMLNode(node *yaml.Node) []Diagnostic {
	var diagnostics []Diagnostic
	if !allowedYAMLTags[node.Tag] {
		diagnostics = append(diagnostics, Diagnostic{Code: "yaml.tag", Message: "custom or unsupported YAML tags are forbidden"})
	}
	if node.Kind == yaml.MappingNode {
		keys := map[string]bool{}
		for index := 0; index < len(node.Content); index += 2 {
			key := node.Content[index]
			if key.Kind != yaml.ScalarNode || key.Tag != "!!str" {
				diagnostics = append(diagnostics, Diagnostic{Code: "yaml.key", Message: "mapping keys must be plain strings"})
			} else if keys[key.Value] {
				diagnostics = append(diagnostics, Diagnostic{Code: "yaml.duplicate", Message: "duplicate mapping key is forbidden"})
			} else {
				keys[key.Value] = true
			}
		}
	}
	for _, child := range node.Content {
		diagnostics = append(diagnostics, inspectYAMLNode(child)...)
	}
	return diagnostics
}

func validateSchema(schemaPath string, value any) error {
	content, err := klokastbox.Assets.ReadFile(schemaPath)
	if err != nil {
		return err
	}
	document, err := jsonschema.UnmarshalJSON(bytes.NewReader(content))
	if err != nil {
		return err
	}
	compiler := jsonschema.NewCompiler()
	compiler.AssertFormat()
	const location = "embedded:///schema.json"
	if err := compiler.AddResource(location, document); err != nil {
		return err
	}
	schema, err := compiler.Compile(location)
	if err != nil {
		return err
	}
	return schema.Validate(value)
}

func loadAppManifests() (map[string]appManifest, error) {
	paths, err := fs.Glob(klokastbox.Assets, "apps/*/platform-resources.yml")
	if err != nil {
		return nil, err
	}
	manifests := map[string]appManifest{}
	for _, path := range paths {
		content, err := klokastbox.Assets.ReadFile(path)
		if err != nil {
			return nil, err
		}
		_, value, diagnostics := parseYAML(content)
		if len(diagnostics) != 0 {
			return nil, fmt.Errorf("%s is not safe YAML", path)
		}
		object, ok := value.(map[string]any)
		if !ok {
			return nil, fmt.Errorf("%s is not an object", path)
		}
		name, ok := object["app"].(string)
		if !ok || name == "" {
			return nil, fmt.Errorf("%s has no app ID", path)
		}
		manifest := appManifest{Capabilities: map[string]bool{}, Resources: map[string]bool{}}
		manifest.PlacementMode, _ = object["placement_mode"].(string)
		collectManifestFields(object, manifest.Capabilities, manifest.Resources)
		if _, exists := manifests[name]; exists {
			return nil, fmt.Errorf("duplicate app manifest %s", name)
		}
		manifests[name] = manifest
	}
	return manifests, nil
}

func collectManifestFields(value any, capabilities, resources map[string]bool) {
	switch current := value.(type) {
	case map[string]any:
		if id, ok := current["id"].(string); ok {
			resources[id] = true
		}
		if access, ok := current["access"].(map[string]any); ok {
			if capability, ok := access["capability"].(string); ok {
				capabilities[capability] = true
			}
		}
		for _, child := range current {
			collectManifestFields(child, capabilities, resources)
		}
	case []any:
		for _, child := range current {
			collectManifestFields(child, capabilities, resources)
		}
	}
}

func decodeNode(node *yaml.Node, target any, add func(string)) bool {
	if len(node.Content) != 1 {
		add("YAML document cannot be decoded")
		return false
	}
	if err := node.Content[0].Decode(target); err != nil {
		add("YAML document cannot be decoded")
		return false
	}
	return true
}

func leafLocations(validationError *jsonschema.ValidationError) []string {
	if len(validationError.Causes) == 0 {
		return []string{jsonLocation(validationError.InstanceLocation)}
	}
	var locations []string
	for _, cause := range validationError.Causes {
		locations = append(locations, leafLocations(cause)...)
	}
	sort.Strings(locations)
	return uniqueStrings(locations)
}

func jsonLocation(tokens []string) string {
	var result strings.Builder
	result.WriteString("$")
	for _, token := range tokens {
		if identifierPattern.MatchString(token) || regexp.MustCompile(`^[A-Za-z_][A-Za-z0-9_-]*$`).MatchString(token) {
			result.WriteString(".")
			result.WriteString(token)
		} else {
			result.WriteString("[")
			encoded, _ := json.Marshal(token)
			result.Write(encoded)
			result.WriteString("]")
		}
	}
	return result.String()
}

func placementBoxes(mode, box, active, passive string, boxes []string) []string {
	switch mode {
	case "single_box":
		return []string{box}
	case "active_passive":
		return []string{active, passive}
	case "multi_box":
		return boxes
	default:
		return nil
	}
}

func forbiddenTrackedPath(path string) bool {
	parts := strings.Split(filepath.ToSlash(path), "/")
	for _, part := range parts {
		switch part {
		case ".klokast", ".generated", "generated", "state", "runtime", "secrets":
			return true
		}
	}
	base := parts[len(parts)-1]
	if base == ".env" || strings.HasSuffix(base, ".tfstate") || strings.HasSuffix(base, ".tfstate.backup") || strings.HasSuffix(base, ".key") || strings.HasSuffix(base, ".pem") || strings.HasSuffix(base, ".p12") {
		return true
	}
	return false
}

func matchesAny(patterns []*regexp.Regexp, value []byte) bool {
	for _, pattern := range patterns {
		if pattern.Match(value) {
			return true
		}
	}
	return false
}

func stringSet(values []string) map[string]bool {
	result := make(map[string]bool, len(values))
	for _, value := range values {
		result[value] = true
	}
	return result
}

func uniqueStrings(values []string) []string {
	result := values[:0]
	for _, value := range values {
		if len(result) == 0 || result[len(result)-1] != value {
			result = append(result, value)
		}
	}
	return result
}

func (c *checker) add(path, code, message string) {
	if path == "" {
		path = "."
	}
	c.diagnostics = append(c.diagnostics, Diagnostic{Path: path, Code: code, Message: message})
}

func (c *checker) report() Report {
	sort.Slice(c.diagnostics, func(i, j int) bool {
		left, right := c.diagnostics[i], c.diagnostics[j]
		if left.Path != right.Path {
			return left.Path < right.Path
		}
		if left.Code != right.Code {
			return left.Code < right.Code
		}
		return left.Message < right.Message
	})
	diagnostics := c.diagnostics[:0]
	for _, diagnostic := range c.diagnostics {
		if len(diagnostics) == 0 || diagnostics[len(diagnostics)-1] != diagnostic {
			diagnostics = append(diagnostics, diagnostic)
		}
	}
	c.diagnostics = diagnostics
	return Report{Valid: len(diagnostics) == 0, Diagnostics: diagnostics}
}
