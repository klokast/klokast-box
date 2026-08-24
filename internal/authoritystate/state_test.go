package authoritystate

import (
	"strings"
	"testing"
)

func TestInitialAndForwardTransitions(t *testing.T) {
	initial, err := Initial()
	if err != nil {
		t.Fatal(err)
	}
	if authority, err := Authority(initial); err != nil || authority != LegacyAuthority {
		t.Fatalf("initial authority: %q, %v", authority, err)
	}
	adopted, err := Transition(initial, InstanceAuthority, strings.Repeat("a", 64), "adopt-001")
	if err != nil {
		t.Fatal(err)
	}
	if authority, err := Authority(adopted); err != nil || authority != InstanceAuthority {
		t.Fatalf("adopted authority: %q, %v", authority, err)
	}
	rolledBack, err := Transition(adopted, LegacyAuthority, strings.Repeat("b", 64), "rollback-001")
	if err != nil {
		t.Fatal(err)
	}
	if rolledBack.PriorStateSHA256 != adopted.AuthorityStateSHA256 {
		t.Fatal("rollback did not create a forward link")
	}
	if authority, err := Authority(rolledBack); err != nil || authority != LegacyAuthority {
		t.Fatalf("rollback authority: %q, %v", authority, err)
	}
}

func TestRejectsPartialAndMixedTransitions(t *testing.T) {
	initial, _ := Initial()
	state, _ := Transition(initial, InstanceAuthority, strings.Repeat("a", 64), "adopt-001")
	state.TransitionedScopes = state.TransitionedScopes[:2]
	digest, _ := Hash(state)
	state.AuthorityStateSHA256 = digest
	if err := Validate(state); err == nil {
		t.Fatal("partial transition was accepted")
	}
	state, _ = Transition(initial, InstanceAuthority, strings.Repeat("a", 64), "adopt-002")
	state.ResultingAuthorities[1].Authority = LegacyAuthority
	digest, _ = Hash(state)
	state.AuthorityStateSHA256 = digest
	if err := Validate(state); err == nil {
		t.Fatal("mixed authority transition was accepted")
	}
}
