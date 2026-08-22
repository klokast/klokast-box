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

	klokastbox "klokast-box"

	"github.com/santhosh-tekuri/jsonschema/v6"
	"gopkg.in/yaml.v3"
)

const maximumTrackedFile = 1024 * 1024

var (
	allowedYAMLTags = map[string]bool{
		"": true, "!!map": true, "!!seq": true, "!!str": true,
		"!!int": true, "!!bool": true, "!!null": true, "!!float": true,
	}
	identifierPattern = regexp.MustCompile(`^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$`)
	secretAssignment  = regexp.MustCompile(`(?i)['"]?(?:password|passwd|secret|token|api[_-]?key|auth[_-]?key|private[_-]?key)['"]?[ \t]*[:=][ \t]*['"]?([^#'" \t\r\n]{8,})`)
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

type featureDefinition struct {
	Kind   string
	Values map[string]bool
}

type appManifest struct {
	PlacementMode string
	Features      map[string]featureDefinition
	Data          map[string]bool
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

	instanceValue, instanceOK := c.loadAndValidateJSON(InstancePath, "schemas/klokast-instance-v1.schema.json")
	lockValue, lockOK := c.loadAndValidateJSON(LockPath, "schemas/klokast-lock-v1.schema.json")
	var instance InstanceDocument
	var lock LockDocument
	if instanceOK {
		content, _ := json.Marshal(instanceValue)
		if err := json.Unmarshal(content, &instance); err != nil {
			c.add(InstancePath, "json.decode", "instance JSON cannot be decoded")
			instanceOK = false
		}
	}
	if lockOK {
		content, _ := json.Marshal(lockValue)
		if err := json.Unmarshal(content, &lock); err != nil {
			c.add(LockPath, "json.decode", "lock JSON cannot be decoded")
			lockOK = false
		}
	}
	if lockOK {
		c.validateLock(lock)
	}
	if instanceOK {
		providers, providerErr := loadCloudProviders()
		if providerErr != nil {
			return Report{}, fmt.Errorf("load embedded cloud-provider catalog: %w", providerErr)
		}
		manifests, manifestErr := loadAppManifests()
		if manifestErr != nil {
			return Report{}, fmt.Errorf("load embedded application manifests: %w", manifestErr)
		}
		c.validateInstance(instance, providers, manifests)
	}
	return c.report(), nil
}

func (c *checker) inspectRepository() bool {
	output, err := isolatedGitCommand(c.root, "rev-parse", "--show-toplevel").Output()
	if err != nil {
		c.add(".", "git.repository", "instance must be a standalone Git repository")
		return false
	}
	top, err := filepath.EvalSymlinks(strings.TrimSpace(string(output)))
	if err != nil || top != c.root {
		c.add(".", "git.repository", "instance must be the root of a standalone Git repository")
		return false
	}
	if output, err := isolatedGitCommand(c.root, "rev-parse", "--show-superproject-working-tree").Output(); err == nil && strings.TrimSpace(string(output)) != "" {
		c.add(".", "git.repository", "instance must not be embedded as a Git submodule")
	}
	tracked, err := isolatedGitCommand(c.root, "ls-files", "-z").Output()
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
	obsolete := map[string]bool{
		"klokast.yml": true, "klokast.lock.yml": true,
		"ops/deployment.yml": true, "ops/platform-resources.yml": true,
	}
	paths := make([]string, 0, len(c.tracked))
	for path := range c.tracked {
		paths = append(paths, path)
	}
	sort.Strings(paths)
	for _, path := range paths {
		if obsolete[path] {
			c.add(path, "tracked.obsolete", "the unreleased YAML Contract input is not permitted by the Instance Specification")
		}
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

func (c *checker) loadAndValidateJSON(path, schemaPath string) (any, bool) {
	if !c.tracked[path] {
		c.add(path, "git.untracked", "authoritative input must be tracked by Git")
		return nil, false
	}
	content, err := readRegularNoSymlinks(c.root, path)
	if err != nil {
		if errors.Is(err, fs.ErrNotExist) {
			c.add(path, "path.missing", "authoritative input does not exist")
		} else {
			c.add(path, "path.read", "authoritative input cannot be read")
		}
		return nil, false
	}
	value, duplicatePath, err := decodeUniqueJSON(content)
	if err != nil {
		if duplicatePath != "" {
			c.add(path+duplicatePath, "json.duplicate", "duplicate JSON object key is forbidden")
		} else {
			c.add(path, "json.syntax", "authoritative input must be one valid JSON document")
		}
		return nil, false
	}
	if err := validateSchema(schemaPath, value); err != nil {
		var validationError *jsonschema.ValidationError
		if errors.As(err, &validationError) {
			locations := leafLocations(validationError)
			if len(locations) == 0 {
				locations = []string{"$"}
			}
			for _, location := range locations {
				c.add(path+location, "schema.invalid", "document does not satisfy the Instance Specification v1 schema")
			}
		} else {
			c.add(path, "schema.invalid", "document does not satisfy the Instance Specification v1 schema")
		}
		return value, false
	}
	return value, true
}

func (c *checker) validateLock(lock LockDocument) {
	expectedSchema := schemaURL(c.engine.Commit, "klokast-lock-v1.schema.json")
	if lock.Schema != expectedSchema {
		c.add(LockPath+"$.$schema", "schema.engine", "lock schema URL must use the exact approved engine commit")
	}
	if c.engine.Repository != "https://github.com/klokast/klokast-box" || c.engine.Ref == "" ||
		!regexp.MustCompile(`^[A-Za-z0-9](?:[A-Za-z0-9._/-]{0,253}[A-Za-z0-9])?$`).MatchString(c.engine.Ref) ||
		strings.Contains(c.engine.Ref, "//") || strings.Contains(c.engine.Ref, "..") || strings.Contains(c.engine.Ref, "@{") ||
		!regexp.MustCompile(`^[0-9a-f]{40}$`).MatchString(c.engine.Commit) {
		c.add(LockPath, "engine.binary", "running binary does not identify a full engine commit")
		return
	}
	if lock.Engine.Repository != c.engine.Repository {
		c.add(LockPath, "engine.repository", "engine lock repository does not match the running builder-approved engine")
	}
	if lock.Engine.Ref != c.engine.Ref {
		c.add(LockPath, "engine.ref", "engine lock ref does not match the running builder-approved engine ref")
	}
	if lock.Engine.Commit != c.engine.Commit {
		c.add(LockPath, "engine.mismatch", "engine lock commit does not match the running builder-approved engine commit")
	}
}

func (c *checker) validateInstance(instance InstanceDocument, providers map[string]cloudProvider, manifests map[string]appManifest) {
	if instance.Schema != schemaURL(c.engine.Commit, "klokast-instance-v1.schema.json") {
		c.add(InstancePath+"$.$schema", "schema.engine", "instance schema URL must use the exact approved engine commit")
	}
	hasOperator, hasFamily, hasOperatorFamily := false, false, false
	for _, member := range instance.Tailscale.Members {
		memberOperator, memberFamily := false, false
		for _, role := range member.Roles {
			memberOperator = memberOperator || role == "operator"
			memberFamily = memberFamily || role == "family"
		}
		hasOperator = hasOperator || memberOperator
		hasFamily = hasFamily || memberFamily
		hasOperatorFamily = hasOperatorFamily || (memberOperator && memberFamily)
	}
	if !hasOperator || !hasFamily || !hasOperatorFamily {
		c.add(InstancePath+"$.tailscale.members", "tailscale.operator-family", "one member must have both operator and family roles")
	}

	generated := map[string]string{}
	sites := map[string]BoxDocument{}
	boxIDs := make([]string, 0, len(instance.Boxes))
	for box := range instance.Boxes {
		boxIDs = append(boxIDs, box)
	}
	sort.Strings(boxIDs)
	for _, box := range boxIDs {
		value := instance.Boxes[box]
		if prior, ok := sites[value.Site]; ok &&
			(prior.Country != value.Country || prior.Description != value.Description) {
			c.add(InstancePath+"$.boxes."+box+".site", "site.inconsistent", "boxes with the same site label must use the same country and description")
		} else if !ok {
			sites[value.Site] = value
		}
		if !hasConnectivityProfile(value, "tailscale") {
			c.add(InstancePath+"$.boxes."+box+".connectivity", "connectivity.tailscale", "an Instance Specification v1 box must use the tailscale connectivity profile")
		}
		for _, suffix := range reservedRuntimeSuffixes {
			if box == suffix || strings.HasSuffix(box, "-"+suffix) {
				c.add(InstancePath+"$.boxes."+box, "identity.box", "box ID ends in a reserved runtime role")
				break
			}
		}
		if _, collision := providers[box]; collision {
			c.add(InstancePath+"$.boxes."+box, "identity.cloud-collision", "box ID collides with a cloud-provider runtime prefix")
		}
		for _, suffix := range []string{"dom0", "router", "bak", "dmz", "iot", "ops", "ops-airunner"} {
			name := box + "-" + suffix
			if len(name) > 63 || !identifierPattern.MatchString(name) {
				c.add(InstancePath+"$.boxes."+box, "identity.runtime", "box ID cannot produce safe runtime names")
			}
			if prior, exists := generated[name]; exists {
				c.add(InstancePath+"$.boxes."+box, "identity.runtime", "generated runtime name collides with "+prior)
			}
			generated[name] = box
		}
	}
	if _, ok := instance.Boxes[instance.Controllers.Active]; !ok {
		c.add(InstancePath+"$.controllers.active", "reference.box", "active controller references an unknown box")
	}
	if instance.Controllers.Standby != "" {
		if _, ok := instance.Boxes[instance.Controllers.Standby]; !ok {
			c.add(InstancePath+"$.controllers.standby", "reference.box", "standby controller references an unknown box")
		}
		if instance.Controllers.Standby == instance.Controllers.Active {
			c.add(InstancePath+"$.controllers.standby", "cardinality.controller", "active and standby controllers must use different boxes")
		}
	}
	for index, id := range instance.Airunners {
		location := fmt.Sprintf("%s$.airunners[%d]", InstancePath, index)
		if box, ok := strings.CutSuffix(id, "-ops-airunner"); ok && box != "" {
			if box != instance.Controllers.Active && box != instance.Controllers.Standby {
				c.add(location, "reference.controller", "airunner container must run in an active or standby controller VM")
			}
			continue
		}
		if provider, ok := strings.CutSuffix(id, "-ops"); ok && provider != "" {
			if _, collision := instance.Boxes[provider]; collision {
				c.add(location, "identity.cloud-collision", "cloud airunner runtime name collides with a box ops runtime name")
				continue
			}
			if _, supported := providers[provider]; !supported {
				c.add(location, "reference.cloud-provider", "cloud airunner references an unsupported cloud provider")
			}
			continue
		}
		c.add(location, "identity.airunner", "airunner ID must be <box>-ops-airunner or <cloud>-ops")
	}

	for appID, app := range instance.Apps {
		location := InstancePath + "$.apps." + appID
		manifest, ok := manifests[appID]
		if !ok {
			c.add(location, "app.unsupported", "application has no embedded public manifest")
			continue
		}
		if app.Placement != nil {
			for _, box := range placementBoxes(*app.Placement) {
				if _, ok := instance.Boxes[box]; !ok {
					c.add(location+".placement", "reference.box", "application placement references an unknown box")
				}
			}
			if app.Placement.Mode == "active-passive" && app.Placement.Active == app.Placement.Passive {
				c.add(location+".placement", "placement.cardinality", "active and passive placements must use different boxes")
			}
			if manifest.PlacementMode != "" && manifest.PlacementMode != app.Placement.Mode {
				c.add(location+".placement.mode", "placement.mode", "placement mode does not match the embedded public manifest")
			}
		}
		for feature, value := range app.Features {
			definition, ok := manifest.Features[feature]
			if !ok {
				c.add(location+".features."+feature, "feature.unknown", "feature is not declared by the embedded public manifest")
				continue
			}
			if definition.Kind == "boolean" {
				if _, ok := value.(bool); !ok {
					c.add(location+".features."+feature, "feature.type", "feature must be Boolean")
				}
			} else if stringValue, ok := value.(string); !ok || !definition.Values[stringValue] {
				c.add(location+".features."+feature, "feature.value", "feature must use one declared value")
			}
		}
		for dataID, data := range app.Data {
			if !manifest.Data[dataID] {
				c.add(location+".data."+dataID, "data.unknown", "data ID is not declared by the embedded public manifest")
			}
			if _, ok := instance.Boxes[data.Box]; !ok {
				c.add(location+".data."+dataID+".box", "reference.box", "data references an unknown box")
			}
		}
	}
}

func hasConnectivityProfile(box BoxDocument, expected string) bool {
	for _, profile := range box.Connectivity {
		if profile == expected {
			return true
		}
	}
	return false
}

func schemaURL(commit, name string) string {
	return "https://raw.githubusercontent.com/klokast/klokast-box/" + commit + "/schemas/" + name
}

func placementBoxes(value PlacementDocument) []string {
	switch value.Mode {
	case "single-box":
		return []string{value.Box}
	case "active-passive":
		return []string{value.Active, value.Passive}
	case "multi-box":
		return value.Boxes
	default:
		return nil
	}
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
		name, _ := object["app"].(string)
		if name == "" {
			return nil, fmt.Errorf("%s has no app ID", path)
		}
		placement, _ := object["placement_mode"].(string)
		manifest := appManifest{
			PlacementMode: strings.ReplaceAll(placement, "_", "-"),
			Features:      map[string]featureDefinition{},
			Data:          map[string]bool{},
		}
		if values, ok := object["features"].([]any); ok {
			for _, raw := range values {
				entry, ok := raw.(map[string]any)
				if !ok {
					continue
				}
				id, _ := entry["id"].(string)
				kind, _ := entry["type"].(string)
				definition := featureDefinition{Kind: kind, Values: map[string]bool{}}
				for _, value := range asStrings(entry["values"]) {
					definition.Values[value] = true
				}
				if id != "" {
					manifest.Features[id] = definition
				}
			}
		}
		if values, ok := object["datasets"].([]any); ok {
			for _, raw := range values {
				if entry, ok := raw.(map[string]any); ok {
					if id, ok := entry["id"].(string); ok && id != "" {
						manifest.Data[id] = true
					}
				}
			}
		}
		if _, exists := manifests[name]; exists {
			return nil, fmt.Errorf("duplicate app manifest %s", name)
		}
		manifests[name] = manifest
	}
	return manifests, nil
}

func asStrings(value any) []string {
	items, _ := value.([]any)
	result := make([]string, 0, len(items))
	for _, item := range items {
		if text, ok := item.(string); ok {
			result = append(result, text)
		}
	}
	return result
}

func decodeUniqueJSON(content []byte) (any, string, error) {
	decoder := json.NewDecoder(bytes.NewReader(content))
	decoder.UseNumber()
	value, duplicate, err := decodeJSONValue(decoder, "$")
	if err != nil {
		return nil, duplicate, err
	}
	var extra any
	if err := decoder.Decode(&extra); err != io.EOF {
		if err == nil {
			err = errors.New("multiple JSON values")
		}
		return nil, "", err
	}
	return value, "", nil
}

func decodeJSONValue(decoder *json.Decoder, path string) (any, string, error) {
	token, err := decoder.Token()
	if err != nil {
		return nil, "", err
	}
	delimiter, ok := token.(json.Delim)
	if !ok {
		return token, "", nil
	}
	switch delimiter {
	case '{':
		object := map[string]any{}
		seen := map[string]bool{}
		for decoder.More() {
			keyToken, err := decoder.Token()
			if err != nil {
				return nil, "", err
			}
			key, ok := keyToken.(string)
			if !ok {
				return nil, "", errors.New("JSON object key is not a string")
			}
			childPath := path + "." + key
			if seen[key] {
				return nil, childPath, errors.New("duplicate JSON object key")
			}
			seen[key] = true
			value, duplicate, err := decodeJSONValue(decoder, childPath)
			if err != nil {
				return nil, duplicate, err
			}
			object[key] = value
		}
		if token, err = decoder.Token(); err != nil || token != json.Delim('}') {
			return nil, "", errors.New("unterminated JSON object")
		}
		return object, "", nil
	case '[':
		var array []any
		for decoder.More() {
			value, duplicate, err := decodeJSONValue(decoder, path)
			if err != nil {
				return nil, duplicate, err
			}
			array = append(array, value)
		}
		if token, err = decoder.Token(); err != nil || token != json.Delim(']') {
			return nil, "", errors.New("unterminated JSON array")
		}
		return array, "", nil
	default:
		return nil, "", errors.New("unexpected JSON delimiter")
	}
}

// ParseSafeYAML converts one compatibility YAML document to JSON-compatible values.
func ParseSafeYAML(content []byte) (any, []Diagnostic) {
	_, value, diagnostics := parseYAML(content)
	return value, diagnostics
}

// RawSecretLines returns suspect line numbers without returning values.
func RawSecretLines(content []byte) []int {
	var lines []int
	for index, line := range bytes.Split(content, []byte{'\n'}) {
		if secretAssignment.Match(line) || matchesAny(secretTokens, line) {
			lines = append(lines, index+1)
		}
	}
	return lines
}

func parseYAML(content []byte) (*yaml.Node, any, []Diagnostic) {
	decoder := yaml.NewDecoder(bytes.NewReader(content))
	var node yaml.Node
	if err := decoder.Decode(&node); err != nil {
		return nil, nil, []Diagnostic{{Code: "yaml.syntax", Message: "document is not valid safe YAML"}}
	}
	var extra yaml.Node
	if err := decoder.Decode(&extra); err != io.EOF {
		return &node, nil, []Diagnostic{{Code: "yaml.multiple", Message: "document must contain one YAML document"}}
	}
	diagnostics := inspectYAMLNode(&node)
	if len(diagnostics) != 0 {
		return &node, nil, diagnostics
	}
	var value any
	if err := node.Decode(&value); err != nil {
		return &node, nil, []Diagnostic{{Code: "yaml.decode", Message: "document cannot be decoded"}}
	}
	encoded, err := json.Marshal(value)
	if err != nil {
		return &node, nil, []Diagnostic{{Code: "yaml.json", Message: "document is not JSON-compatible"}}
	}
	jsonDecoder := json.NewDecoder(bytes.NewReader(encoded))
	jsonDecoder.UseNumber()
	if err := jsonDecoder.Decode(&value); err != nil {
		return &node, nil, []Diagnostic{{Code: "yaml.json", Message: "document is not JSON-compatible"}}
	}
	return &node, value, nil
}

func inspectYAMLNode(node *yaml.Node) []Diagnostic {
	var diagnostics []Diagnostic
	if !allowedYAMLTags[node.Tag] {
		diagnostics = append(diagnostics, Diagnostic{Code: "yaml.tag", Message: "custom YAML tags are forbidden"})
	}
	if node.Kind == yaml.AliasNode || node.Anchor != "" {
		diagnostics = append(diagnostics, Diagnostic{Code: "yaml.alias", Message: "YAML aliases and anchors are forbidden"})
	}
	if node.Kind == yaml.MappingNode {
		seen := map[string]bool{}
		for index := 0; index+1 < len(node.Content); index += 2 {
			key := node.Content[index]
			if key.Kind != yaml.ScalarNode || key.Tag != "!!str" {
				diagnostics = append(diagnostics, Diagnostic{Code: "yaml.key", Message: "YAML mapping keys must be strings"})
			} else if seen[key.Value] {
				diagnostics = append(diagnostics, Diagnostic{Code: "yaml.duplicate", Message: "duplicate YAML mapping key is forbidden"})
			}
			seen[key.Value] = true
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
		if identifierPattern.MatchString(token) || regexp.MustCompile(`^[A-Za-z_$][A-Za-z0-9_$-]*$`).MatchString(token) {
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

func forbiddenTrackedPath(path string) bool {
	parts := strings.Split(filepath.ToSlash(path), "/")
	for _, part := range parts {
		switch part {
		case ".klokast", ".generated", "generated", "state", "runtime", "secrets":
			return true
		}
	}
	base := parts[len(parts)-1]
	return base == ".env" || strings.HasSuffix(base, ".tfstate") || strings.HasSuffix(base, ".tfstate.backup") ||
		strings.HasSuffix(base, ".key") || strings.HasSuffix(base, ".pem") || strings.HasSuffix(base, ".p12")
}

func matchesAny(patterns []*regexp.Regexp, value []byte) bool {
	for _, pattern := range patterns {
		if pattern.Match(value) {
			return true
		}
	}
	return false
}

func isolatedGitCommand(root string, arguments ...string) *exec.Cmd {
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
