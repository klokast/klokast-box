package contract

import (
	"bytes"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"strings"
)

const (
	InstancePath = "klokast-instance.json"
	LockPath     = "klokast.lock.json"
)

// InstanceDocument is the validated Klokast Instance Specification v1 input.
type InstanceDocument struct {
	Schema        string `json:"$schema"`
	SchemaVersion int    `json:"schema-version"`
	Tailscale struct {
		DNSName string                    `json:"tailnet-dns-name"`
		Members map[string]MemberDocument `json:"members"`
	} `json:"tailscale"`
	Boxes       map[string]BoxDocument       `json:"boxes"`
	Controllers ControllerDocument           `json:"controllers"`
	Airunners   []string                      `json:"airunners"`
	Apps        map[string]AppBindingDocument `json:"apps"`
}

type MemberDocument struct {
	Roles []string `json:"roles"`
}

type BoxDocument struct {
	Site                 string   `json:"site"`
	Country              string   `json:"country"`
	Description          string   `json:"description"`
	Connectivity         []string `json:"connectivity"`
}

type ControllerDocument struct {
	Active  string `json:"active"`
	Standby string `json:"standby,omitempty"`
}

type AppBindingDocument struct {
	DesiredState string                  `json:"desired-state"`
	Placement    *PlacementDocument      `json:"placement,omitempty"`
	Features     map[string]any          `json:"features,omitempty"`
	Data         map[string]DataDocument `json:"data,omitempty"`
}

type PlacementDocument struct {
	Mode    string   `json:"mode"`
	Box     string   `json:"box,omitempty"`
	Active  string   `json:"active,omitempty"`
	Passive string   `json:"passive,omitempty"`
	Boxes   []string `json:"boxes,omitempty"`
}

type DataDocument struct {
	Box       string `json:"box"`
	Retention string `json:"retention"`
}

// LockDocument selects the exact public engine used with an instance.
type LockDocument struct {
	Schema        string `json:"$schema"`
	SchemaVersion int    `json:"schema-version"`
	Engine        struct {
		Repository string `json:"repository"`
		Ref        string `json:"ref"`
		Commit     string `json:"commit"`
	} `json:"engine"`
}

// Input contains one authoritative file and its exact worktree content hash.
type Input struct {
	Path    string
	Content []byte
	SHA256  string
}

// Snapshot contains the two checked authoritative instance documents.
type Snapshot struct {
	Root     string
	Instance InstanceDocument
	Lock     LockDocument
	Inputs   []Input
}

// Load checks and then reads a coherent Instance Specification v1 snapshot.
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

	contents := make(map[string][]byte, 2)
	for _, path := range []string{InstancePath, LockPath} {
		content, readErr := readRegularNoSymlinks(root, path)
		if readErr != nil {
			return Snapshot{}, Report{}, fmt.Errorf("read checked authoritative input %s: %w", path, readErr)
		}
		contents[path] = content
	}
	var instance InstanceDocument
	var lock LockDocument
	if err := json.Unmarshal(contents[InstancePath], &instance); err != nil {
		return Snapshot{}, Report{}, fmt.Errorf("decode checked %s: %w", InstancePath, err)
	}
	if err := json.Unmarshal(contents[LockPath], &lock); err != nil {
		return Snapshot{}, Report{}, fmt.Errorf("decode checked %s: %w", LockPath, err)
	}
	inputs := make([]Input, 0, 2)
	for _, path := range []string{InstancePath, LockPath} {
		digest := sha256.Sum256(contents[path])
		inputs = append(inputs, Input{Path: path, Content: contents[path], SHA256: fmt.Sprintf("%x", digest[:])})
	}
	return Snapshot{Root: root, Instance: instance, Lock: lock, Inputs: inputs}, report, nil
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
