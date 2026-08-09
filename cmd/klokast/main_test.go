package main

import "testing"

func TestUnsupportedCommandIsValidationFailure(t *testing.T) {
	if got := run([]string{"check"}); got != 2 {
		t.Fatalf("run(check) = %d, want 2", got)
	}
}
