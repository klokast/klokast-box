package main

import (
	"bytes"
	"encoding/json"
	"strings"
	"testing"
)

func TestVersionJSON(t *testing.T) {
	var stdout, stderr bytes.Buffer
	if got := run([]string{"version", "--json"}, &stdout, &stderr); got != 0 {
		t.Fatalf("run(version) = %d, stderr=%q", got, stderr.String())
	}
	var result versionResult
	if err := json.Unmarshal(stdout.Bytes(), &result); err != nil {
		t.Fatal(err)
	}
	if result.EngineCommit != engineCommit || result.Name != "klokast" {
		t.Fatalf("unexpected version result: %#v", result)
	}
}

func TestUnsupportedCommandIsValidationFailure(t *testing.T) {
	var stdout, stderr bytes.Buffer
	if got := run([]string{"init"}, &stdout, &stderr); got != 2 {
		t.Fatalf("run(init) = %d, want 2", got)
	}
	if strings.Contains(stderr.String(), "init --") {
		t.Fatalf("usage advertises deferred init command: %q", stderr.String())
	}
}

func TestCheckUsageIsValidationFailure(t *testing.T) {
	var stdout, stderr bytes.Buffer
	if got := run([]string{"check"}, &stdout, &stderr); got != 2 {
		t.Fatalf("run(check) = %d, want 2", got)
	}
}
