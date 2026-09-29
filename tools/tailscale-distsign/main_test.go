package main

import (
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestRejectsUnsafeInputsBeforeNetworkAccess(t *testing.T) {
	private := filepath.Join(t.TempDir(), "private")
	if err := os.Mkdir(private, 0700); err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{
		"../tailscale_1.102.4_amd64.tgz",
		"tailscale_1.102.4_arm64.tgz",
		"tailscale_1.102.4_amd64.tgz.sig",
	} {
		if err := run([]string{"--archive", name, "--directory", private}, io.Discard); err == nil {
			t.Errorf("accepted unsafe archive name %q", name)
		}
	}
	public := filepath.Join(t.TempDir(), "public")
	if err := os.Mkdir(public, 0755); err != nil {
		t.Fatal(err)
	}
	if err := run([]string{"--archive", "tailscale_1.102.4_amd64.tgz", "--directory", public}, io.Discard); err == nil || !strings.Contains(err.Error(), "private directory") {
		t.Fatalf("accepted public output directory: %v", err)
	}
	if err := os.WriteFile(filepath.Join(private, "existing"), nil, 0600); err != nil {
		t.Fatal(err)
	}
	if err := run([]string{"--archive", "tailscale_1.102.4_amd64.tgz", "--directory", private}, io.Discard); err == nil || !strings.Contains(err.Error(), "empty") {
		t.Fatalf("accepted nonempty output directory: %v", err)
	}
}
