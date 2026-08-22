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
}

func TestParseOpenClawDesiredValidation(t *testing.T) {
	raw := []byte(`{
  "schema_version": 1,
  "app": "openclaw",
  "operation": "deploy",
  "target_box": "boxa",
  "target_role": "agent",
  "site_role": "active",
  "placement": {"active_master": "boxa"},
  "resource_grant_sha256": "abc",
  "runtime": {"openclaw_npm_version": "2026.5.27"}
}`)
	if _, err := parseDesired(raw, openclawAppID); err != nil {
		t.Fatalf("parseDesired returned error for OpenClaw: %v", err)
	}
	var value map[string]any
	if err := json.Unmarshal(raw, &value); err != nil {
		t.Fatal(err)
	}
	value["target_role"] = "backend"
	bad, _ := json.Marshal(value)
	if _, err := parseDesired(bad, openclawAppID); err == nil {
		t.Fatal("parseDesired accepted wrong OpenClaw target_role")
	}
}

func TestRedactJSON(t *testing.T) {
	redactions, redacted := redactJSON([]byte(`{"password":"x","nested":{"token":"y"},"ok":"z"}`))
	if len(redactions) != 2 {
		t.Fatalf("expected two redactions, got %v", redactions)
	}
	if string(redacted) == "" || string(redacted) == `{"password":"x"}` {
		t.Fatalf("redaction did not change JSON: %s", redacted)
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
