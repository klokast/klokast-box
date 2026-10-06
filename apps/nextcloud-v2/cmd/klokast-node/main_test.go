package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func testBundle() []byte {
	raw := []byte(`{
  "schema_version": 1,
  "app": "nextcloud-v2",
  "operation": "install",
  "target_box": "boxa",
  "target_role": "backend",
  "site_role": "active",
  "placement": {"active_master": "boxa", "passive_backup": "boxb"},
  "resource_grant_sha256": "abc",
  "runtime": {
    "pod_name": "nextcloud-v2",
    "podman_user": "neo",
    "backend_http_bind": "192.168.100.10",
    "backend_http_port": 8080
  },
  "images": {
    "upstream_images": {
      "postgres": {"canonical": "docker.io/library/postgres", "digest": "sha256:111"},
      "redis": {"canonical": "docker.io/library/redis", "digest": "sha256:222"}
    },
    "built_images": {
      "nextcloud-v2-app": {"image": "localhost/app", "digest": "sha256:333"},
      "nextcloud-v2-web": {"image": "localhost/web", "digest": "sha256:444"},
      "nextcloud-v2-dmz-proxy": {"image": "localhost/proxy", "digest": "sha256:555"}
    }
  }
}`)
	return raw
}

func TestParseDesiredValidation(t *testing.T) {
	if _, err := parseDesired(testBundle(), nextcloudV2AppID); err != nil {
		t.Fatalf("parseDesired returned error: %v", err)
	}
	var value map[string]any
	if err := json.Unmarshal(testBundle(), &value); err != nil {
		t.Fatal(err)
	}
	value["app"] = "other"
	raw, _ := json.Marshal(value)
	if _, err := parseDesired(raw, nextcloudV2AppID); err == nil {
		t.Fatal("parseDesired accepted wrong app")
	}
	for field, invalid := range map[string]any{
		"schema_version":        2,
		"operation":             "",
		"runtime_state":         "unknown",
		"target_role":           "ops",
		"site_role":             "unknown",
		"placement":             map[string]string{"active_master": "boxa"},
		"resource_grant_sha256": "",
	} {
		t.Run(field, func(t *testing.T) {
			var value map[string]any
			if err := json.Unmarshal(testBundle(), &value); err != nil {
				t.Fatal(err)
			}
			value[field] = invalid
			raw, err := json.Marshal(value)
			if err != nil {
				t.Fatal(err)
			}
			if _, err := parseDesired(raw, nextcloudV2AppID); err == nil {
				t.Fatalf("accepted invalid %s", field)
			}
		})
	}
}

func TestRedactJSON(t *testing.T) {
	redactions, redacted := redactJSON([]byte(`{"password":"x","nested":{"token":"y"},"ok":"z"}`))
	if len(redactions) != 2 {
		t.Fatalf("expected two redactions, got %v", redactions)
	}
	var value map[string]any
	if err := json.Unmarshal(redacted, &value); err != nil {
		t.Fatal(err)
	}
	if value["password"] != "<redacted>" || value["nested"].(map[string]any)["token"] != "<redacted>" || value["ok"] != "z" {
		t.Fatalf("unexpected redacted values: %v", value)
	}
}

func TestOnlyNextcloudCanRun(t *testing.T) {
	root := t.TempDir()
	t.Setenv("KLOKAST_NODE_ROOT", root)
	for _, app := range []string{"openclaw", "music", "../nextcloud-v2"} {
		if err := run([]string{"apply", app}); err == nil || !strings.Contains(err.Error(), "unsupported app") {
			t.Fatalf("run accepted unsupported app %q: %v", app, err)
		}
	}
	entries, err := os.ReadDir(root)
	if err != nil || len(entries) != 0 {
		t.Fatalf("unsupported apps touched runtime state: %v, %v", entries, err)
	}
}

func TestRenderingMatchesBeforeMove(t *testing.T) {
	// Capture the outputs from the foundation runner before moving it here.
	for role, expected := range map[string]string{
		"backend": "c83db1232a0ca88c582ae3a959d58d89b9ac3c2e9ad0e1253785d87dd6e153ea",
		"dmz":     "2359ff332371b0189acf31e28adc5c1aa61b5b5cd19be106e28e607548ed4ef0",
	} {
		t.Run(role, func(t *testing.T) {
			var value map[string]any
			if err := json.Unmarshal(testBundle(), &value); err != nil {
				t.Fatal(err)
			}
			value["target_role"] = role
			raw, err := json.Marshal(value)
			if err != nil {
				t.Fatal(err)
			}
			bundle, err := parseDesired(raw, nextcloudV2AppID)
			if err != nil {
				t.Fatal(err)
			}
			pod, err := renderNextcloudV2Pod(bundle)
			if err != nil {
				t.Fatal(err)
			}
			if got := sha256Hex(pod); got != expected {
				t.Fatalf("rendered %s configuration changed: got %s, want %s", role, got, expected)
			}
		})
	}
}

func TestRuntimePathsAndStatusRemainCompatible(t *testing.T) {
	root := t.TempDir()
	t.Setenv("KLOKAST_NODE_ROOT", root)
	p := defaultPaths(nextcloudV2AppID)
	if err := atomicWrite(p.desired, testBundle(), 0o640); err != nil {
		t.Fatal(err)
	}
	// Stub the handlers so this test needs no Podman or infrastructure access.
	for _, command := range []string{"apply", "verify", "remove"} {
		if err := atomicWrite(filepath.Join(p.handlerDir, command), []byte("#!/bin/sh\nexit 0\n"), 0o750); err != nil {
			t.Fatal(err)
		}
		if err := run([]string{command, nextcloudV2AppID}); err != nil {
			t.Fatal(err)
		}
		raw, err := os.ReadFile(filepath.Join(root, "var/lib/klokast/status/nextcloud-v2.json"))
		if err != nil {
			t.Fatal(err)
		}
		var status statusDoc
		if err := json.Unmarshal(raw, &status); err != nil {
			t.Fatal(err)
		}
		if status.SchemaVersion != 1 || status.App != nextcloudV2AppID || status.Command != command || !status.OK || status.State != "ok" || status.DesiredSHA256 != sha256Hex(testBundle()) {
			t.Fatalf("incompatible status: %+v", status)
		}
	}
	pod, err := os.ReadFile(filepath.Join(root, "var/lib/klokast/rendered/nextcloud-v2/pod.yml"))
	if err != nil {
		t.Fatal(err)
	}
	if sha256Hex(pod) != "c83db1232a0ca88c582ae3a959d58d89b9ac3c2e9ad0e1253785d87dd6e153ea" {
		t.Fatal("verify or remove changed the rendered configuration")
	}
	if _, err := os.Stat(filepath.Join(root, "run/lock/klokast-node-nextcloud-v2.lock")); err != nil {
		t.Fatal(err)
	}
}

func TestInvalidDesiredRecordsFailureBeforeHandler(t *testing.T) {
	root := t.TempDir()
	t.Setenv("KLOKAST_NODE_ROOT", root)
	marker := filepath.Join(root, "handler-called")
	t.Setenv("KLOKAST_TEST_HANDLER_MARKER", marker)
	p := defaultPaths(nextcloudV2AppID)
	if err := atomicWrite(p.desired, []byte(`{"schema_version":1,"app":"music","operation":"install"}`), 0o640); err != nil {
		t.Fatal(err)
	}
	if err := atomicWrite(filepath.Join(p.handlerDir, "apply"), []byte("#!/bin/sh\nprintf called > \"$KLOKAST_TEST_HANDLER_MARKER\"\n"), 0o750); err != nil {
		t.Fatal(err)
	}
	if err := run([]string{"apply", nextcloudV2AppID}); err == nil {
		t.Fatal("accepted desired state for another app")
	}
	if _, err := os.Stat(marker); !os.IsNotExist(err) {
		t.Fatal("invalid desired state invoked the handler")
	}
	raw, err := os.ReadFile(p.status)
	if err != nil {
		t.Fatal(err)
	}
	var status statusDoc
	if err := json.Unmarshal(raw, &status); err != nil {
		t.Fatal(err)
	}
	if status.OK || status.State != "invalid-desired" || status.Handler.ExitCode != 0 || status.Handler.Path != "" {
		t.Fatalf("unexpected failure status: %+v", status)
	}
}

func TestRenderNextcloudV2BackendVolumes(t *testing.T) {
	bundle, err := parseDesired(testBundle(), nextcloudV2AppID)
	if err != nil {
		t.Fatal(err)
	}
	pod, err := renderNextcloudV2Pod(bundle)
	if err != nil {
		t.Fatal(err)
	}
	text := string(pod)
	for _, needle := range []string{
		"persistentVolumeClaim:",
		"claimName: klokast-nextcloud-v2-postgres",
		"claimName: klokast-nextcloud-v2-data",
		"mountPath: /var/www/html/data",
	} {
		if !strings.Contains(text, needle) {
			t.Fatalf("rendered backend pod is missing %q:\n%s", needle, text)
		}
	}
}

func TestRenderNextcloudV2DMZIngressSidecar(t *testing.T) {
	var value map[string]any
	if err := json.Unmarshal(testBundle(), &value); err != nil {
		t.Fatal(err)
	}
	value["target_role"] = "dmz"
	value["site_role"] = "passive"
	raw, _ := json.Marshal(value)
	bundle, err := parseDesired(raw, nextcloudV2AppID)
	if err != nil {
		t.Fatal(err)
	}
	pod, err := renderNextcloudV2Pod(bundle)
	if err != nil {
		t.Fatal(err)
	}
	text := string(pod)
	for _, needle := range []string{
		"name: tailscale",
		"NEXTCLOUD_SITE_ROLE",
		"value: passive",
		"claimName: klokast-nextcloud-v2-ingress-ts-state",
		"path: /etc/klokast/apps/nextcloud-v2/secrets/tailscale-authkey",
	} {
		if !strings.Contains(text, needle) {
			t.Fatalf("rendered dmz pod is missing %q:\n%s", needle, text)
		}
	}
}

func TestAtomicStatusWrite(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "status.json")
	status := baseStatus(nextcloudV2AppID, "verify", "hash", nowForTest(), true)
	status.State = "ok"
	if err := writeStatus(path, status); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(path); err != nil {
		t.Fatal(err)
	}
}

func nowForTest() time.Time {
	return time.Unix(0, 0).UTC()
}
