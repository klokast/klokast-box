package contract

import (
	"bytes"
	"crypto/sha256"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"strings"

	"gopkg.in/yaml.v3"
)

// RootDocument is the validated Contract v1 root document.
type RootDocument struct {
	Contract int `yaml:"contract"`
	Paths    struct {
		Deployment        string `yaml:"deployment"`
		PlatformResources string `yaml:"platform_resources"`
	} `yaml:"paths"`
}

// LockDocument is the validated Contract v1 engine lock.
type LockDocument struct {
	SchemaVersion int `yaml:"schema_version"`
	Engine        struct {
		Repository string `yaml:"repository"`
		Ref        string `yaml:"ref"`
		Commit     string `yaml:"commit"`
	} `yaml:"engine"`
}

// DeploymentDocument is the validated Contract v1 deployment document.
type DeploymentDocument struct {
	SchemaVersion int `yaml:"schema_version"`
	Instance      struct {
		Name string `yaml:"name"`
	} `yaml:"instance"`
	Tailnet struct {
		MagicDNSSuffix string              `yaml:"magicdns_suffix"`
		Groups         map[string][]string `yaml:"groups"`
	} `yaml:"tailnet"`
	Sites        map[string]SiteDocument `yaml:"sites"`
	Boxes        map[string]BoxDocument  `yaml:"boxes"`
	ControlPlane struct {
		Controller ControllerDocument `yaml:"controller"`
		Airunners  []AirunnerDocument `yaml:"airunners"`
	} `yaml:"control_plane"`
}

type SiteDocument struct {
	Country          string `yaml:"country"`
	Timezone         string `yaml:"timezone"`
	PhysicalLocation string `yaml:"physical_location"`
}

type BoxDocument struct {
	HostnamePrefix string `yaml:"hostname_prefix"`
	Site           string `yaml:"site"`
}

type ControllerDocument struct {
	ActiveBox  string `yaml:"active_box"`
	StandbyBox string `yaml:"standby_box"`
}

type AirunnerDocument struct {
	ID       string `yaml:"id"`
	Kind     string `yaml:"kind"`
	Box      string `yaml:"box"`
	Hostname string `yaml:"hostname"`
}

// PlatformDocument is the validated Contract v1 platform-resources document.
type PlatformDocument struct {
	SchemaVersion int                    `yaml:"schema_version"`
	Boxes         map[string]PlatformBox `yaml:"boxes"`
	Apps          map[string]AppBinding  `yaml:"apps"`
}

type PlatformBox struct {
	Access AccessDocument `yaml:"access"`
}

type AccessDocument struct {
	Declared   []string          `yaml:"declared_capabilities"`
	Enabled    []string          `yaml:"enabled_capabilities"`
	Prohibited []string          `yaml:"prohibited_capabilities"`
	Policy     map[string]string `yaml:"policy"`
}

type AppBinding struct {
	Enabled   bool              `yaml:"enabled"`
	Placement PlacementDocument `yaml:"placement"`
	Resources map[string]any    `yaml:"resources"`
}

type PlacementDocument struct {
	Mode          string   `yaml:"mode"`
	Box           string   `yaml:"box"`
	ActiveMaster  string   `yaml:"active_master"`
	PassiveBackup string   `yaml:"passive_backup"`
	Boxes         []string `yaml:"boxes"`
}

// Input contains one authoritative file and its exact worktree content hash.
type Input struct {
	Path    string
	Content []byte
	SHA256  string
}

// Snapshot contains the checked Contract v1 documents and exact input bytes.
// The caller must take another snapshot before acting if concurrent changes
// matter. Snapshot never changes the instance repository.
type Snapshot struct {
	Root       string
	RootConfig RootDocument
	Lock       LockDocument
	Deployment DeploymentDocument
	Platform   PlatformDocument
	Inputs     []Input
}

// Load checks and then reads a coherent Contract v1 worktree snapshot.
func Load(instancePath string, engine Engine) (Snapshot, Report, error) {
	report, err := Check(instancePath, engine)
	if err != nil || !report.Valid {
		return Snapshot{}, report, err
	}
	root, err := filepath.Abs(instancePath)
	if err != nil {
		return Snapshot{}, Report{}, fmt.Errorf("resolve instance path: %w", err)
	}
	root, err = filepath.EvalSymlinks(root)
	if err != nil {
		return Snapshot{}, Report{}, fmt.Errorf("resolve instance path: %w", err)
	}

	rootContent, err := readRegularNoSymlinks(root, "klokast.yml")
	if err != nil {
		return Snapshot{}, Report{}, fmt.Errorf("read checked root contract: %w", err)
	}
	var rootConfig RootDocument
	if err := yaml.Unmarshal(rootContent, &rootConfig); err != nil {
		return Snapshot{}, Report{}, fmt.Errorf("decode checked root contract: %w", err)
	}

	paths := []string{"klokast.yml", lockPath, rootConfig.Paths.Deployment, rootConfig.Paths.PlatformResources}
	contents := make(map[string][]byte, len(paths))
	contents["klokast.yml"] = rootContent
	for _, path := range paths[1:] {
		content, readErr := readRegularNoSymlinks(root, path)
		if readErr != nil {
			return Snapshot{}, Report{}, fmt.Errorf("read checked authoritative input %s: %w", path, readErr)
		}
		contents[path] = content
	}

	var lock LockDocument
	var deployment DeploymentDocument
	var platform PlatformDocument
	for _, item := range []struct {
		path   string
		target any
	}{
		{lockPath, &lock},
		{rootConfig.Paths.Deployment, &deployment},
		{rootConfig.Paths.PlatformResources, &platform},
	} {
		if err := yaml.Unmarshal(contents[item.path], item.target); err != nil {
			return Snapshot{}, Report{}, fmt.Errorf("decode checked authoritative input %s: %w", item.path, err)
		}
	}

	inputs := make([]Input, 0, len(paths))
	for _, path := range paths {
		digest := sha256.Sum256(contents[path])
		inputs = append(inputs, Input{Path: path, Content: contents[path], SHA256: fmt.Sprintf("%x", digest[:])})
	}
	return Snapshot{
		Root:       root,
		RootConfig: rootConfig,
		Lock:       lock,
		Deployment: deployment,
		Platform:   platform,
		Inputs:     inputs,
	}, report, nil
}

// ParseSafeYAML converts one safe YAML document to JSON-compatible values.
func ParseSafeYAML(content []byte) (any, []Diagnostic) {
	_, value, diagnostics := parseYAML(content)
	return value, diagnostics
}

// RawSecretLines returns line numbers that look like raw secret assignments.
// It never returns the suspected values.
func RawSecretLines(content []byte) []int {
	var lines []int
	for index, line := range bytes.Split(content, []byte{'\n'}) {
		if secretAssignment.Match(line) || matchesAny(secretTokens, line) {
			lines = append(lines, index+1)
		}
	}
	return lines
}

func readRegularNoSymlinks(root, path string) ([]byte, error) {
	clean := filepath.ToSlash(filepath.Clean(filepath.FromSlash(path)))
	if path == "" || strings.Contains(path, "\\") || filepath.IsAbs(path) || clean != path || clean == "." || clean == ".." || strings.HasPrefix(clean, "../") {
		return nil, fmt.Errorf("unsafe relative path")
	}
	current := root
	for _, component := range strings.Split(clean, "/") {
		current = filepath.Join(current, component)
		info, err := os.Lstat(current)
		if err != nil {
			return nil, err
		}
		if info.Mode()&os.ModeSymlink != 0 {
			return nil, fmt.Errorf("symbolic link is not permitted")
		}
	}
	info, err := os.Stat(current)
	if err != nil {
		return nil, err
	}
	if !info.Mode().IsRegular() {
		return nil, fmt.Errorf("path is not a regular file")
	}
	if info.Size() > maximumTrackedFile {
		return nil, fmt.Errorf("file exceeds the one-MiB limit")
	}
	content, err := os.ReadFile(current)
	if err != nil {
		return nil, err
	}
	if bytes.IndexByte(content, 0) >= 0 {
		return nil, fs.ErrInvalid
	}
	return content, nil
}
