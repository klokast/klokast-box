package instance

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
	"strings"

	klokastbox "klokast-box"
	"klokast-box/internal/contract"
)

const maximumValuesFile = 64 * 1024

var (
	engineCommitPattern = regexp.MustCompile(`^[0-9a-f]{40}$`)
	engineRefPattern    = regexp.MustCompile(`^[A-Za-z0-9](?:[A-Za-z0-9._/-]{0,253}[A-Za-z0-9])?$`)
)

type Options struct {
	InstancePath string
	ValuesPath   string
}

type Result struct {
	Created      bool            `json:"created"`
	InstancePath string          `json:"instance_path"`
	Engine       EngineSelection `json:"engine"`
}

type EngineSelection struct {
	Repository string `json:"repository"`
	Ref        string `json:"ref"`
	Commit     string `json:"commit"`
}

type ValidationError struct {
	Diagnostics []contract.Diagnostic
}

func (e *ValidationError) Error() string {
	return "instance values or destination failed validation"
}

type duplicateJSONKeyError struct {
	path string
}

func (e *duplicateJSONKeyError) Error() string {
	return "duplicate JSON object key"
}

func Init(options Options, engine contract.Engine) (result Result, returnedErr error) {
	if err := validateEngine(engine); err != nil {
		return Result{}, err
	}
	if _, err := exec.LookPath("git"); err != nil {
		return Result{}, fmt.Errorf("git is required: %w", err)
	}
	destination, err := resolveDestination(options.InstancePath)
	if err != nil {
		return Result{}, err
	}
	values, err := loadValues(options.ValuesPath)
	if err != nil {
		return Result{}, err
	}
	if err := rejectGitWorktree(filepath.Dir(destination)); err != nil {
		return Result{}, err
	}

	staging, err := os.MkdirTemp(filepath.Dir(destination), "."+filepath.Base(destination)+".klokast-init-")
	if err != nil {
		return Result{}, fmt.Errorf("create private staging directory: %w", err)
	}
	if err := os.Chmod(staging, 0o700); err != nil {
		_ = os.RemoveAll(staging)
		return Result{}, fmt.Errorf("set staging directory permissions: %w", err)
	}
	renamed := false
	defer func() {
		if renamed {
			return
		}
		if cleanupErr := os.RemoveAll(staging); cleanupErr != nil {
			if returnedErr == nil {
				returnedErr = fmt.Errorf("remove failed staging directory %s: %w", staging, cleanupErr)
			} else {
				returnedErr = fmt.Errorf("%w; failed staging directory remains at %s: %v", returnedErr, staging, cleanupErr)
			}
		}
	}()

	if err := copyTemplate(staging); err != nil {
		return Result{}, fmt.Errorf("copy embedded instance template: %w", err)
	}
	if err := writeGeneratedDocuments(staging, values, engine); err != nil {
		return Result{}, err
	}
	if err := initializeRepository(staging); err != nil {
		return Result{}, err
	}
	report, err := contract.Check(staging, engine)
	if err != nil {
		return Result{}, fmt.Errorf("check generated instance: %w", err)
	}
	if !report.Valid {
		return Result{}, &ValidationError{Diagnostics: report.Diagnostics}
	}
	if err := publishNoReplace(staging, destination); err != nil {
		return Result{}, fmt.Errorf("publish generated instance: %w", err)
	}
	renamed = true
	return Result{
		Created:      true,
		InstancePath: destination,
		Engine: EngineSelection{
			Repository: engine.Repository,
			Ref:        engine.Ref,
			Commit:     engine.Commit,
		},
	}, nil
}

func validateEngine(engine contract.Engine) error {
	if engine.Repository != "https://github.com/klokast/klokast-box" ||
		!engineRefPattern.MatchString(engine.Ref) || strings.Contains(engine.Ref, "//") ||
		strings.Contains(engine.Ref, "..") || strings.Contains(engine.Ref, "@{") ||
		!engineCommitPattern.MatchString(engine.Commit) || strings.Trim(engine.Commit, "0") == "" {
		return fmt.Errorf("running binary does not identify a builder-approved engine commit")
	}
	return nil
}

func resolveDestination(path string) (string, error) {
	if path == "" {
		return "", validation("instance", "path.required", "instance path is required")
	}
	abs, err := filepath.Abs(path)
	if err != nil {
		return "", fmt.Errorf("resolve instance path: %w", err)
	}
	base := filepath.Base(abs)
	if base == "." || base == string(filepath.Separator) || base == "" {
		return "", validation("instance", "path.unsafe", "instance path must name a new directory")
	}
	parent, err := filepath.EvalSymlinks(filepath.Dir(abs))
	if err != nil {
		return "", fmt.Errorf("resolve instance parent: %w", err)
	}
	if info, err := os.Stat(parent); err != nil || !info.IsDir() {
		return "", validation("instance", "path.parent", "instance parent must be an existing directory")
	}
	destination := filepath.Join(parent, base)
	if _, err := os.Lstat(destination); err == nil {
		return "", validation("instance", "path.exists", "instance destination already exists")
	} else if !errors.Is(err, fs.ErrNotExist) {
		return "", fmt.Errorf("inspect instance destination: %w", err)
	}
	return destination, nil
}

func rejectGitWorktree(parent string) error {
	for current := parent; ; current = filepath.Dir(current) {
		if _, err := os.Lstat(filepath.Join(current, ".git")); err == nil {
			return validation("instance", "git.nested", "instance destination must not be inside an existing Git worktree")
		} else if !errors.Is(err, fs.ErrNotExist) {
			return fmt.Errorf("inspect ancestor Git marker: %w", err)
		}
		next := filepath.Dir(current)
		if next == current {
			break
		}
	}
	command := isolatedGitCommand(parent, "rev-parse", "--is-inside-work-tree")
	output, err := command.Output()
	if err == nil && strings.TrimSpace(string(output)) == "true" {
		return validation("instance", "git.nested", "instance destination must not be inside an existing Git worktree")
	}
	if err != nil {
		var exitError *exec.ExitError
		if !errors.As(err, &exitError) || exitError.ExitCode() != 128 {
			return fmt.Errorf("inspect instance parent Git state: %w", err)
		}
	}
	return nil
}

func loadValues(path string) (any, error) {
	if path == "" {
		return nil, validation("values", "path.required", "values path is required")
	}
	abs, err := filepath.Abs(path)
	if err != nil {
		return nil, fmt.Errorf("resolve values path: %w", err)
	}
	linkInfo, err := os.Lstat(abs)
	if err != nil {
		return nil, fmt.Errorf("inspect values file: %w", err)
	}
	if linkInfo.Mode()&os.ModeSymlink != 0 {
		return nil, validation("values", "path.symlink", "values file must not be a symbolic link")
	}
	if !linkInfo.Mode().IsRegular() {
		return nil, validation("values", "path.type", "values file must be a regular file")
	}
	if linkInfo.Size() <= 0 || linkInfo.Size() > maximumValuesFile {
		return nil, validation("values", "file.size", "values file must be non-empty and no larger than 64 KiB")
	}
	file, err := os.Open(abs)
	if err != nil {
		return nil, fmt.Errorf("open values file: %w", err)
	}
	defer file.Close()
	openInfo, err := file.Stat()
	if err != nil || !os.SameFile(linkInfo, openInfo) || !openInfo.Mode().IsRegular() {
		return nil, validation("values", "path.changed", "values file changed during inspection")
	}
	content, err := io.ReadAll(io.LimitReader(file, maximumValuesFile+1))
	if err != nil {
		return nil, fmt.Errorf("read values file: %w", err)
	}
	if len(content) > maximumValuesFile {
		return nil, validation("values", "file.size", "values file exceeds the 64-KiB limit")
	}
	if lines := contract.RawSecretLines(content); len(lines) != 0 {
		return nil, validation(fmt.Sprintf("values:%d", lines[0]), "secret.raw", "possible raw secret value is present")
	}
	value, err := decodeUniqueJSON(content)
	if err != nil {
		var duplicate *duplicateJSONKeyError
		if errors.As(err, &duplicate) {
			return nil, validation("values"+duplicate.path, "json.duplicate", "duplicate JSON object key is forbidden")
		}
		return nil, validation("values", "json.syntax", "values file is not one valid JSON document")
	}
	return value, nil
}

func decodeUniqueJSON(content []byte) (any, error) {
	decoder := json.NewDecoder(bytes.NewReader(content))
	decoder.UseNumber()
	value, err := decodeJSONValue(decoder, "$")
	if err != nil {
		return nil, err
	}
	var extra any
	if err := decoder.Decode(&extra); err != io.EOF {
		if err == nil {
			return nil, errors.New("multiple JSON values")
		}
		return nil, err
	}
	return value, nil
}

func decodeJSONValue(decoder *json.Decoder, path string) (any, error) {
	token, err := decoder.Token()
	if err != nil {
		return nil, err
	}
	delimiter, ok := token.(json.Delim)
	if !ok {
		return token, nil
	}
	switch delimiter {
	case '{':
		object := map[string]any{}
		seen := map[string]bool{}
		for decoder.More() {
			keyToken, err := decoder.Token()
			if err != nil {
				return nil, err
			}
			key, ok := keyToken.(string)
			if !ok {
				return nil, errors.New("JSON object key is not a string")
			}
			childPath := path + "." + key
			if seen[key] {
				return nil, &duplicateJSONKeyError{path: childPath}
			}
			seen[key] = true
			value, err := decodeJSONValue(decoder, childPath)
			if err != nil {
				return nil, err
			}
			object[key] = value
		}
		if token, err = decoder.Token(); err != nil || token != json.Delim('}') {
			return nil, errors.New("unterminated JSON object")
		}
		return object, nil
	case '[':
		var array []any
		for decoder.More() {
			value, err := decodeJSONValue(decoder, path)
			if err != nil {
				return nil, err
			}
			array = append(array, value)
		}
		if token, err = decoder.Token(); err != nil || token != json.Delim(']') {
			return nil, errors.New("unterminated JSON array")
		}
		return array, nil
	default:
		return nil, errors.New("unexpected JSON delimiter")
	}
}

func copyTemplate(destination string) error {
	return fs.WalkDir(klokastbox.Assets, "templates/instance", func(path string, entry fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		relative, err := filepath.Rel("templates/instance", path)
		if err != nil || relative == "." {
			return err
		}
		target := filepath.Join(destination, relative)
		if entry.Type()&os.ModeSymlink != 0 {
			return fmt.Errorf("embedded template contains a symbolic link")
		}
		if entry.IsDir() {
			return os.MkdirAll(target, 0o750)
		}
		if !entry.Type().IsRegular() {
			return fmt.Errorf("embedded template contains a non-regular file")
		}
		content, err := klokastbox.Assets.ReadFile(path)
		if err != nil {
			return err
		}
		return os.WriteFile(target, content, 0o640)
	})
}

func writeGeneratedDocuments(root string, values any, engine contract.Engine) error {
	instanceContent, err := json.MarshalIndent(values, "", "  ")
	if err != nil {
		return fmt.Errorf("encode %s: %w", contract.InstancePath, err)
	}
	instanceContent = append(instanceContent, '\n')
	lock := contract.LockDocument{
		Schema:        schemaURL(engine.Commit, "klokast-lock-v1.schema.json"),
		SchemaVersion: 1,
	}
	lock.Engine.Repository = engine.Repository
	lock.Engine.Ref = engine.Ref
	lock.Engine.Commit = engine.Commit
	lockContent, err := json.MarshalIndent(lock, "", "  ")
	if err != nil {
		return fmt.Errorf("encode %s: %w", contract.LockPath, err)
	}
	lockContent = append(lockContent, '\n')
	for path, content := range map[string][]byte{
		contract.InstancePath: instanceContent,
		contract.LockPath:     lockContent,
	} {
		if err := os.WriteFile(filepath.Join(root, path), content, 0o640); err != nil {
			return fmt.Errorf("write %s: %w", path, err)
		}
	}
	return nil
}

func schemaURL(commit, name string) string {
	return "https://raw.githubusercontent.com/klokast/klokast-box/" + commit + "/schemas/" + name
}

func initializeRepository(root string) error {
	if output, err := isolatedGitCommand(root, "init", "-q", "--initial-branch=main").CombinedOutput(); err != nil {
		return fmt.Errorf("initialize instance Git repository: %w: %s", err, strings.TrimSpace(string(output)))
	}
	if output, err := isolatedGitCommand(root, "add", "-A").CombinedOutput(); err != nil {
		return fmt.Errorf("stage authoritative instance inputs: %w: %s", err, strings.TrimSpace(string(output)))
	}
	return nil
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

func validation(path, code, message string) error {
	return &ValidationError{Diagnostics: []contract.Diagnostic{{Path: path, Code: code, Message: message}}}
}
