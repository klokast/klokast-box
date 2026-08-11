package instancesource

import (
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"klokast-box/internal/contract"
)

func TestLoadAcceptsCanonicalFreshPrivateSourceReceipt(t *testing.T) {
	path := writeReceipt(t, validReceipt(time.Now().UTC()))
	receipt, diagnostics, err := Load(path, time.Now().UTC())
	if err != nil {
		t.Fatal(err)
	}
	if len(diagnostics) != 0 {
		t.Fatalf("unexpected diagnostics: %#v", diagnostics)
	}
	if receipt.Reference().ReceiptSHA256 == "" || receipt.Reference().RepositorySHA256 == "" {
		t.Fatalf("receipt reference is incomplete: %#v", receipt.Reference())
	}
}

func TestLoadRejectsPublicStaleAndTamperedReceipts(t *testing.T) {
	tests := []struct {
		name   string
		change func(map[string]any)
		code   string
	}{
		{name: "public", change: func(value map[string]any) { value["anonymous_readable"] = true }, code: "repository.public"},
		{name: "stale", change: func(value map[string]any) { value["fetched_at"] = time.Now().UTC().Add(-time.Hour).Format(time.RFC3339) }, code: "fetched-at.stale"},
		{name: "tampered", change: func(value map[string]any) { value["repository_id"] = 99; value["receipt_sha256"] = strings.Repeat("0", 64) }, code: "receipt.hash"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			value := validReceipt(time.Now().UTC())
			test.change(value)
			_, diagnostics, err := Load(writeReceipt(t, value), time.Now().UTC())
			if err != nil {
				t.Fatal(err)
			}
			if !hasCode(diagnostics, test.code) {
				t.Fatalf("missing diagnostic %q: %#v", test.code, diagnostics)
			}
		})
	}
}

func TestLoadRejectsSymlinkAndUnknownField(t *testing.T) {
	path := writeReceipt(t, validReceipt(time.Now().UTC()))
	link := filepath.Join(t.TempDir(), "receipt.json")
	if err := os.Symlink(path, link); err != nil {
		t.Fatal(err)
	}
	_, diagnostics, err := Load(link, time.Now().UTC())
	if err != nil || !hasCode(diagnostics, "path.symlink") {
		t.Fatalf("symlink was not rejected: diagnostics=%#v err=%v", diagnostics, err)
	}
	value := validReceipt(time.Now().UTC())
	value["unexpected"] = true
	_, diagnostics, err = Load(writeReceipt(t, value), time.Now().UTC())
	if err != nil || !hasCode(diagnostics, "document.json") {
		t.Fatalf("unknown field was not rejected: diagnostics=%#v err=%v", diagnostics, err)
	}
}

func validReceipt(now time.Time) map[string]any {
	repository := "family/klokast"
	repositoryDigest := sha256.Sum256([]byte(repository))
	value := map[string]any{
		"schema_version": 1,
		"kind": "klokast.instance-source.v1",
		"repository": repository,
		"repository_sha256": fmt.Sprintf("%x", repositoryDigest[:]),
		"repository_id": 123456,
		"remote_ref": "refs/heads/main",
		"commit": strings.Repeat("a", 40),
		"fetched_at": now.Truncate(time.Second).Format(time.RFC3339),
		"deploy_key_fingerprint": "SHA256:abcdefghijklmnopqrstuvwxyzABCDEFGH123456",
		"anonymous_readable": false,
		"authenticated_readable": true,
	}
	canonical, _ := json.Marshal(value)
	digest := sha256.Sum256(canonical)
	value["receipt_sha256"] = fmt.Sprintf("%x", digest[:])
	return value
}

func writeReceipt(t *testing.T, value map[string]any) string {
	t.Helper()
	content, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(t.TempDir(), "receipt.json")
	if err := os.WriteFile(path, append(content, '\n'), 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

func hasCode(diagnostics []contract.Diagnostic, code string) bool {
	for _, diagnostic := range diagnostics {
		if diagnostic.Code == code {
			return true
		}
	}
	return false
}
