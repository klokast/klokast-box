package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"syscall"
	"time"
)

const nextcloudV2AppID = "nextcloud-v2"
const openclawAppID = "openclaw"

var supportedApps = map[string]bool{
	nextcloudV2AppID: true,
	openclawAppID:    true,
}

type desiredBundle struct {
	SchemaVersion       int                    `json:"schema_version"`
	App                 string                 `json:"app"`
	Operation           string                 `json:"operation"`
	RuntimeState        string                 `json:"runtime_state"`
	TargetBox           string                 `json:"target_box"`
	TargetRole          string                 `json:"target_role"`
	SiteRole            string                 `json:"site_role"`
	Placement           map[string]string      `json:"placement"`
	ResourceGrantSHA256 string                 `json:"resource_grant_sha256"`
	WipeData            bool                   `json:"wipe_data"`
	Images              map[string]any         `json:"images"`
	Runtime             map[string]any         `json:"runtime"`
	TimeoutSeconds      int                    `json:"timeout_seconds"`
	Extra               map[string]interface{} `json:"-"`
}

type statusDoc struct {
	SchemaVersion int               `json:"schema_version"`
	App           string            `json:"app"`
	Command       string            `json:"command"`
	Operation     string            `json:"operation"`
	OK            bool              `json:"ok"`
	State         string            `json:"state"`
	StartedAt     string            `json:"started_at"`
	FinishedAt    string            `json:"finished_at"`
	DesiredSHA256 string            `json:"desired_sha256"`
	PodSpecSHA256 string            `json:"pod_spec_sha256,omitempty"`
	Handler       handlerStatus     `json:"handler"`
	Redactions    []string          `json:"redactions,omitempty"`
	Details       map[string]string `json:"details,omitempty"`
}

type handlerStatus struct {
	Path     string `json:"path"`
	ExitCode int    `json:"exit_code"`
	Meaning  string `json:"meaning"`
	Stdout   string `json:"stdout,omitempty"`
	Stderr   string `json:"stderr,omitempty"`
}

type paths struct {
	root        string
	desired     string
	status      string
	renderedDir string
	renderedPod string
	handlerDir  string
	lockPath    string
}

func main() {
	if err := run(os.Args[1:]); err != nil {
		fmt.Fprintln(os.Stderr, "klokast-node:", err)
		os.Exit(1)
	}
}

func run(args []string) error {
	if len(args) != 2 {
		return errors.New("usage: klokast-node apply|verify|remove nextcloud-v2|openclaw")
	}
	command, app := args[0], args[1]
	if !supportedApps[app] {
		return fmt.Errorf("unsupported app %q", app)
	}
	if command != "apply" && command != "verify" && command != "remove" {
		return fmt.Errorf("unsupported command %q", command)
	}

	p := defaultPaths(app)
	unlock, err := lock(p)
	if err != nil {
		return err
	}
	defer unlock()

	started := time.Now().UTC()
	raw, err := os.ReadFile(p.desired)
	if err != nil {
		return fmt.Errorf("read desired bundle: %w", err)
	}
	desiredHash := sha256Hex(raw)
	redactions, sanitizedRaw := redactJSON(raw)
	bundle, err := parseDesired(raw, app)
	if err != nil {
		status := baseStatus(app, command, desiredHash, started, false)
		status.State = "invalid-desired"
		status.Redactions = redactions
		status.Handler.Meaning = err.Error()
		_ = writeStatus(p.status, status)
		return err
	}
	_ = sanitizedRaw

	timeout := time.Duration(bundle.TimeoutSeconds) * time.Second
	if timeout <= 0 {
		timeout = 10 * time.Minute
	}
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()

	status := baseStatus(app, command, desiredHash, started, false)
	status.Operation = bundle.Operation
	status.Redactions = redactions

	renderPod := app == nextcloudV2AppID && (command == "apply" || command == "verify") && bundle.RuntimeState != "stopped"
	var podHash string
	if renderPod {
		pod, err := renderNextcloudV2Pod(bundle)
		if err != nil {
			status.State = "render-error"
			status.Handler.Meaning = err.Error()
			_ = writeStatus(p.status, status)
			return err
		}
		podHash = sha256Hex(pod)
		status.PodSpecSHA256 = podHash
		if command == "apply" {
			if err := atomicWrite(p.renderedPod, pod, 0o640); err != nil {
				status.State = "render-write-error"
				status.Handler.Meaning = err.Error()
				_ = writeStatus(p.status, status)
				return err
			}
		}
	}

	changed := "false"
	if renderPod {
		changed = "true"
		if command == "apply" && previousPodHash(p.status) == podHash {
			changed = "false"
		}
	}
	handler := filepath.Join(p.handlerDir, command)
	result := runHandler(ctx, handler, map[string]string{
		"KLOKAST_APP":                app,
		"KLOKAST_DESIRED_PATH":       p.desired,
		"KLOKAST_RENDERED_KUBE_PATH": p.renderedPod,
		"KLOKAST_POD_SPEC_CHANGED":   changed,
		"KLOKAST_PODMAN_USER":        runtimeString(bundle, "podman_user", "neo"),
	})
	status.Handler = result
	status.OK = result.ExitCode == 0
	status.State = result.Meaning
	if errors.Is(ctx.Err(), context.DeadlineExceeded) {
		status.OK = false
		status.State = "timeout"
		status.Handler.Meaning = "timeout"
	}
	status.FinishedAt = time.Now().UTC().Format(time.RFC3339)
	if err := writeStatus(p.status, status); err != nil {
		return err
	}
	if !status.OK {
		return fmt.Errorf("handler failed: %s", status.State)
	}
	return nil
}

func defaultPaths(app string) paths {
	root := os.Getenv("KLOKAST_NODE_ROOT")
	if root == "" {
		root = "/"
	}
	join := func(elem ...string) string {
		parts := append([]string{root}, elem...)
		return filepath.Join(parts...)
	}
	return paths{
		root:        root,
		desired:     join("var/lib/klokast/desired", app+".json"),
		status:      join("var/lib/klokast/status", app+".json"),
		renderedDir: join("var/lib/klokast/rendered", app),
		renderedPod: join("var/lib/klokast/rendered", app, "pod.yml"),
		handlerDir:  join("usr/lib/klokast/apps", app),
		lockPath:    join("run/lock/klokast-node-" + app + ".lock"),
	}
}

func lock(p paths) (func(), error) {
	if err := os.MkdirAll(filepath.Dir(p.lockPath), 0o755); err != nil {
		return nil, err
	}
	file, err := os.OpenFile(p.lockPath, os.O_CREATE|os.O_RDWR, 0o600)
	if err != nil {
		return nil, err
	}
	if err := syscall.Flock(int(file.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		_ = file.Close()
		return nil, errors.New("another klokast-node run is active")
	}
	return func() {
		_ = syscall.Flock(int(file.Fd()), syscall.LOCK_UN)
		_ = file.Close()
	}, nil
}

func parseDesired(raw []byte, app string) (desiredBundle, error) {
	var bundle desiredBundle
	if err := json.Unmarshal(raw, &bundle); err != nil {
		return bundle, err
	}
	if bundle.SchemaVersion != 1 {
		return bundle, errors.New("desired schema_version must be 1")
	}
	if bundle.App != app {
		return bundle, fmt.Errorf("desired app must be %s", app)
	}
	if bundle.Operation == "" {
		return bundle, errors.New("operation is required")
	}
	switch app {
	case nextcloudV2AppID:
		if err := validateNextcloudV2Desired(bundle); err != nil {
			return bundle, err
		}
	case openclawAppID:
		if err := validateOpenClawDesired(bundle); err != nil {
			return bundle, err
		}
	default:
		return bundle, fmt.Errorf("unsupported app %q", app)
	}
	return bundle, nil
}

func validateNextcloudV2Desired(bundle desiredBundle) error {
	if bundle.RuntimeState == "" {
		bundle.RuntimeState = "running"
	}
	if bundle.RuntimeState != "running" && bundle.RuntimeState != "stopped" {
		return errors.New("runtime_state must be running or stopped")
	}
	if bundle.TargetRole != "backend" && bundle.TargetRole != "dmz" {
		return errors.New("target_role must be backend or dmz")
	}
	switch bundle.SiteRole {
	case "active", "passive", "removed":
	default:
		return errors.New("site_role must be active, passive, or removed")
	}
	if bundle.Operation != "remove" {
		if bundle.Placement["active_master"] == "" || bundle.Placement["passive_backup"] == "" {
			return errors.New("placement active_master and passive_backup are required")
		}
		if bundle.ResourceGrantSHA256 == "" {
			return errors.New("resource_grant_sha256 is required")
		}
	}
	return nil
}

func validateOpenClawDesired(bundle desiredBundle) error {
	if bundle.TargetRole != "agent" {
		return errors.New("target_role must be agent")
	}
	switch bundle.SiteRole {
	case "active", "removed":
	default:
		return errors.New("site_role must be active or removed")
	}
	if bundle.Operation != "remove" {
		if bundle.Placement["active_master"] == "" {
			return errors.New("placement active_master is required")
		}
		if bundle.ResourceGrantSHA256 == "" {
			return errors.New("resource_grant_sha256 is required")
		}
	}
	return nil
}

func renderNextcloudV2Pod(bundle desiredBundle) ([]byte, error) {
	podName := runtimeString(bundle, "pod_name", nextcloudV2AppID)
	siteRole := bundle.SiteRole
	if bundle.TargetRole == "backend" {
		volumes := nextcloudV2Volumes(bundle)
		return []byte(fmt.Sprintf(`apiVersion: v1
kind: Pod
metadata:
  name: %s
  labels:
    app: nextcloud-v2
    site-role: %s
spec:
  containers:
    - name: postgres
      image: %s
      volumeMounts:
        - name: postgres-data
          mountPath: /var/lib/postgresql/data
    - name: redis
      image: %s
      volumeMounts:
        - name: redis-data
          mountPath: /data
    - name: app
      image: %s
      volumeMounts:
        - name: app-html
          mountPath: /var/www/html
        - name: app-config
          mountPath: /var/www/html/config
        - name: app-custom-apps
          mountPath: /var/www/html/custom_apps
        - name: app-data
          mountPath: /var/www/html/data
    - name: web
      image: %s
      ports:
        - containerPort: 8080
          hostIP: %s
          hostPort: %d
      volumeMounts:
        - name: app-html
          mountPath: /var/www/html
          readOnly: true
  volumes:
    - name: postgres-data
      persistentVolumeClaim:
        claimName: %s
    - name: redis-data
      persistentVolumeClaim:
        claimName: %s
    - name: app-html
      persistentVolumeClaim:
        claimName: %s
    - name: app-config
      persistentVolumeClaim:
        claimName: %s
    - name: app-custom-apps
      persistentVolumeClaim:
        claimName: %s
    - name: app-data
      persistentVolumeClaim:
        claimName: %s
    - name: app-backups
      persistentVolumeClaim:
        claimName: %s
`, podName, siteRole, imageRef(bundle, "postgres"), imageRef(bundle, "redis"), builtImage(bundle, "nextcloud-v2-app"), builtImage(bundle, "nextcloud-v2-web"), runtimeString(bundle, "backend_http_bind", "192.168.100.10"), runtimeInt(bundle, "backend_http_port", 8080), volumes["postgres"], volumes["redis"], volumes["html"], volumes["config"], volumes["custom_apps"], volumes["data"], volumes["backups"])), nil
	}
	volumes := nextcloudV2Volumes(bundle)
	return []byte(fmt.Sprintf(`apiVersion: v1
kind: Pod
metadata:
  name: %s
  labels:
    app: nextcloud-v2
    site-role: %s
spec:
  containers:
    - name: proxy
      image: %s
      ports:
        - containerPort: 8443
          hostPort: %d
    - name: tailscale
      image: %s
      env:
        - name: NEXTCLOUD_TAILSCALE_HOSTNAME
          value: %s
        - name: NEXTCLOUD_TAILSCALE_TAG
          value: %s
        - name: NEXTCLOUD_SITE_ROLE
          value: %s
      command:
        - /bin/sh
        - -c
        - |
          set -eu
          if [ "${NEXTCLOUD_SITE_ROLE:-active}" != "active" ]; then
              while :; do sleep 3600; done
          fi
          socket_dir=/tmp/tailscale
          socket="$socket_dir/tailscaled.sock"
          state_dir=/var/lib/tailscale
          authkey_file=/run/secrets/tailscale-authkey
          mkdir -p "$socket_dir" "$state_dir"
          /usr/sbin/tailscaled --statedir="$state_dir" --socket="$socket" --tun=userspace-networking --port=0 &
          ts_pid="$!"
          trap 'kill "$ts_pid" >/dev/null 2>&1 || true; wait "$ts_pid" 2>/dev/null || true' INT TERM
          for _ in $(seq 1 30); do
              [ -S "$socket" ] && break
              sleep 1
          done
          [ -S "$socket" ] || exit 1
          if ! tailscale --socket "$socket" status --json 2>/dev/null | jq -e --arg hostname "$NEXTCLOUD_TAILSCALE_HOSTNAME" --arg tag "$NEXTCLOUD_TAILSCALE_TAG" '.BackendState == "Running" and .Self.HostName == $hostname and (.Self.Online == true) and ((.Self.Tags // []) | index($tag))' >/dev/null; then
              [ -s "$authkey_file" ] || exit 1
              authkey="$(sed -n '1p' "$authkey_file")"
              tailscale --socket "$socket" up --auth-key="$authkey" --hostname="$NEXTCLOUD_TAILSCALE_HOSTNAME" --advertise-tags="$NEXTCLOUD_TAILSCALE_TAG" --accept-dns=false --force-reauth --timeout=30s
          fi
          for _ in $(seq 1 60); do
              nc -z 127.0.0.1 8443 >/dev/null 2>&1 && break
              sleep 1
          done
          nc -z 127.0.0.1 8443 >/dev/null 2>&1 || exit 1
          tailscale --socket "$socket" set --hostname="$NEXTCLOUD_TAILSCALE_HOSTNAME" --accept-dns=false
          tailscale --socket "$socket" serve reset >/dev/null 2>&1 || true
          tailscale --socket "$socket" serve --bg --yes --https=443 http://127.0.0.1:8443 >/dev/null
          wait "$ts_pid"
      volumeMounts:
        - name: tailscale-state
          mountPath: /var/lib/tailscale
        - name: tailscale-authkey
          mountPath: /run/secrets/tailscale-authkey
          readOnly: true
  volumes:
    - name: tailscale-state
      persistentVolumeClaim:
        claimName: %s
    - name: tailscale-authkey
      hostPath:
        path: %s
        type: File
`, podName, siteRole, builtImage(bundle, "nextcloud-v2-dmz-proxy"), runtimeInt(bundle, "dmz_https_port", 8443), builtImage(bundle, "nextcloud-v2-dmz-proxy"), runtimeString(bundle, "tailscale_hostname", "next"), runtimeString(bundle, "tailscale_tag", "tag:nextcloud"), siteRole, volumes["tailscale_state"], runtimeString(bundle, "tailscale_authkey_file", "/etc/klokast/apps/nextcloud-v2/secrets/tailscale-authkey"))), nil
}

func nextcloudV2Volumes(bundle desiredBundle) map[string]string {
	defaults := map[string]string{
		"postgres":        "klokast-nextcloud-v2-postgres",
		"redis":           "klokast-nextcloud-v2-redis",
		"html":            "klokast-nextcloud-v2-html",
		"config":          "klokast-nextcloud-v2-config",
		"custom_apps":     "klokast-nextcloud-v2-custom-apps",
		"data":            "klokast-nextcloud-v2-data",
		"backups":         "klokast-nextcloud-v2-backups",
		"tailscale_state": "klokast-nextcloud-v2-ingress-ts-state",
	}
	values, _ := bundle.Runtime["volumes"].(map[string]any)
	for key, fallback := range defaults {
		value, _ := values[key].(string)
		if value != "" {
			defaults[key] = value
		} else {
			defaults[key] = fallback
		}
	}
	return defaults
}

func imageRef(bundle desiredBundle, name string) string {
	section, _ := bundle.Images["upstream_images"].(map[string]any)
	item, _ := section[name].(map[string]any)
	return fmt.Sprintf("%s@%s", item["canonical"], item["digest"])
}

func builtImage(bundle desiredBundle, name string) string {
	section, _ := bundle.Images["built_images"].(map[string]any)
	item, _ := section[name].(map[string]any)
	image, _ := item["image"].(string)
	digest, _ := item["digest"].(string)
	if image == "" || digest == "" {
		return image
	}
	return image + "@" + digest
}

func runtimeString(bundle desiredBundle, key, fallback string) string {
	value, _ := bundle.Runtime[key].(string)
	if value == "" {
		return fallback
	}
	return value
}

func runtimeInt(bundle desiredBundle, key string, fallback int) int {
	switch value := bundle.Runtime[key].(type) {
	case float64:
		return int(value)
	case int:
		return value
	default:
		return fallback
	}
}

func runHandler(ctx context.Context, path string, env map[string]string) handlerStatus {
	result := handlerStatus{Path: path, ExitCode: 127, Meaning: "handler-missing"}
	if _, err := os.Stat(path); err != nil {
		result.Stderr = err.Error()
		return result
	}
	cmd := exec.CommandContext(ctx, path)
	cmd.Env = os.Environ()
	for key, value := range env {
		cmd.Env = append(cmd.Env, key+"="+value)
	}
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	err := cmd.Run()
	result.Stdout = strings.TrimSpace(stdout.String())
	result.Stderr = strings.TrimSpace(stderr.String())
	if err == nil {
		result.ExitCode = 0
		result.Meaning = "ok"
		return result
	}
	var exitErr *exec.ExitError
	if errors.As(err, &exitErr) {
		result.ExitCode = exitErr.ExitCode()
		result.Meaning = exitMeaning(result.ExitCode)
		return result
	}
	result.ExitCode = 126
	result.Meaning = err.Error()
	return result
}

func exitMeaning(code int) string {
	switch code {
	case 0:
		return "ok"
	case 10:
		return "validation-error"
	case 20:
		return "drift"
	case 30:
		return "transient-error"
	case 40:
		return "permanent-error"
	default:
		return "handler-error"
	}
}

func baseStatus(app, command, desiredHash string, started time.Time, ok bool) statusDoc {
	return statusDoc{
		SchemaVersion: 1,
		App:           app,
		Command:       command,
		OK:            ok,
		State:         "started",
		StartedAt:     started.Format(time.RFC3339),
		FinishedAt:    time.Now().UTC().Format(time.RFC3339),
		DesiredSHA256: desiredHash,
		Details:       map[string]string{},
	}
}

func writeStatus(path string, status statusDoc) error {
	raw, err := json.MarshalIndent(status, "", "  ")
	if err != nil {
		return err
	}
	raw = append(raw, '\n')
	return atomicWrite(path, raw, 0o640)
}

func atomicWrite(path string, data []byte, mode os.FileMode) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	tmp, err := os.CreateTemp(filepath.Dir(path), "."+filepath.Base(path)+".")
	if err != nil {
		return err
	}
	tmpName := tmp.Name()
	defer os.Remove(tmpName)
	if _, err := tmp.Write(data); err != nil {
		_ = tmp.Close()
		return err
	}
	if err := tmp.Chmod(mode); err != nil {
		_ = tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	return os.Rename(tmpName, path)
}

func previousPodHash(path string) string {
	raw, err := os.ReadFile(path)
	if err != nil {
		return ""
	}
	var status statusDoc
	if json.Unmarshal(raw, &status) != nil {
		return ""
	}
	return status.PodSpecSHA256
}

func sha256Hex(data []byte) string {
	sum := sha256.Sum256(data)
	return hex.EncodeToString(sum[:])
}

func redactJSON(raw []byte) ([]string, []byte) {
	var value any
	if json.Unmarshal(raw, &value) != nil {
		return nil, raw
	}
	redactions := map[string]bool{}
	redacted := redactValue(value, "", redactions)
	out, err := json.Marshal(redacted)
	if err != nil {
		return nil, raw
	}
	keys := make([]string, 0, len(redactions))
	for key := range redactions {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	return keys, out
}

func redactValue(value any, path string, redactions map[string]bool) any {
	switch typed := value.(type) {
	case map[string]any:
		result := map[string]any{}
		for key, child := range typed {
			childPath := key
			if path != "" {
				childPath = path + "." + key
			}
			if secretKey(key) {
				result[key] = "<redacted>"
				redactions[childPath] = true
				continue
			}
			result[key] = redactValue(child, childPath, redactions)
		}
		return result
	case []any:
		result := make([]any, len(typed))
		for index, child := range typed {
			result[index] = redactValue(child, path, redactions)
		}
		return result
	default:
		return value
	}
}

func secretKey(key string) bool {
	lower := strings.ToLower(key)
	for _, needle := range []string{"password", "token", "secret", "private_key", "auth_key"} {
		if strings.Contains(lower, needle) {
			return true
		}
	}
	return false
}
