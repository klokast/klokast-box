package authoritystate

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
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

func TestConvertV1AndTransitionOneBoxGroup(t *testing.T) {
	initial, err := Initial()
	if err != nil {
		t.Fatal(err)
	}
	adopted, err := Transition(initial, InstanceAuthority, strings.Repeat("a", 64), "tailnet-adopt")
	if err != nil {
		t.Fatal(err)
	}
	converted, err := ConvertV1(
		adopted, []string{"boxb", "boxa"}, strings.Repeat("b", 64), "convert-v2",
	)
	if err != nil {
		t.Fatal(err)
	}
	if converted.PriorStateKind != Kind || converted.PriorStateSHA256 != adopted.AuthorityStateSHA256 {
		t.Fatal("v1 conversion did not preserve the historical link")
	}
	if source, err := GroupSource(converted, TailnetGroupID); err != nil || source != InstanceAuthority {
		t.Fatalf("converted Tailnet source = %q, %v", source, err)
	}
	groupID := BoxConnectivityPrefix + "boxa"
	if source, err := GroupSource(converted, groupID); err != nil || source != LegacyRegistrySource {
		t.Fatalf("initial box source = %q, %v", source, err)
	}
	transitioned, err := TransitionGroup(
		converted, groupID, InstanceAuthority, strings.Repeat("c", 64), "boxa-adopt",
	)
	if err != nil {
		t.Fatal(err)
	}
	if transitioned.PriorStateKind != KindV2 || transitioned.PriorStateSHA256 != converted.AuthorityStateSHA256 {
		t.Fatal("v2 transition did not create a forward link")
	}
	if source, err := GroupSource(transitioned, groupID); err != nil || source != InstanceAuthority {
		t.Fatalf("transitioned box source = %q, %v", source, err)
	}
	if source, err := GroupSource(transitioned, BoxConnectivityPrefix+"boxb"); err != nil || source != LegacyRegistrySource {
		t.Fatalf("unselected box source changed = %q, %v", source, err)
	}
}

func TestAuthorityV2RejectsUnknownIncompleteMixedAndChangedGroups(t *testing.T) {
	initial, _ := Initial()
	state, _ := ConvertV1(initial, []string{"boxa", "boxb"}, strings.Repeat("a", 64), "convert-v2")
	tests := []struct {
		name   string
		mutate func(*StateV2)
	}{
		{name: "unknown", mutate: func(value *StateV2) { value.SettingGroups[0].ID = "shell" }},
		{name: "incomplete", mutate: func(value *StateV2) { value.SettingGroups = value.SettingGroups[:1] }},
		{name: "mixed duplicate", mutate: func(value *StateV2) {
			value.SettingGroups = append(value.SettingGroups, value.SettingGroups[len(value.SettingGroups)-1])
			value.SettingGroups[len(value.SettingGroups)-1].Source = InstanceAuthority
		}},
		{name: "changed scopes", mutate: func(value *StateV2) { value.SettingGroups[0].Scopes = value.SettingGroups[0].Scopes[:4] }},
		{name: "wrong source", mutate: func(value *StateV2) { value.SettingGroups[0].Source = LegacyAuthority }},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			candidate := state
			candidate.SettingGroups = append([]SettingGroup{}, state.SettingGroups...)
			for index := range candidate.SettingGroups {
				candidate.SettingGroups[index].Scopes = append([]string{}, state.SettingGroups[index].Scopes...)
			}
			test.mutate(&candidate)
			candidate.AuthorityStateSHA256, _ = HashV2(candidate)
			if err := ValidateV2(candidate); err == nil {
				t.Fatal("invalid Authority State v2 was accepted")
			}
		})
	}
}

func TestCanonicalLoadingAndHistoricalV1Preservation(t *testing.T) {
	initial, _ := Initial()
	directory := t.TempDir()
	v1Path := filepath.Join(directory, "v1.json")
	if err := os.WriteFile(v1Path, canonicalStateBytes(t, initial), 0o600); err != nil {
		t.Fatal(err)
	}
	loadedV1, err := Load(v1Path)
	if err != nil || loadedV1.Kind != Kind {
		t.Fatalf("historical v1 state did not load: %#v, %v", loadedV1, err)
	}
	v2, _ := ConvertV1(initial, []string{"boxa"}, strings.Repeat("a", 64), "convert-v2")
	v2Path := filepath.Join(directory, "v2.json")
	if err := os.WriteFile(v2Path, canonicalStateBytes(t, v2), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := LoadV2(v2Path); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(v2Path, []byte("{\"kind\":\"first\",\"kind\":\"second\"}\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := LoadV2(v2Path); err == nil || !strings.Contains(err.Error(), "duplicate") {
		t.Fatalf("duplicate v2 JSON error = %v", err)
	}
	content := canonicalStateBytes(t, v2)
	var pretty bytes.Buffer
	if err := json.Indent(&pretty, bytes.TrimSpace(content), "", "  "); err != nil {
		t.Fatal(err)
	}
	pretty.WriteByte('\n')
	if err := os.WriteFile(v2Path, pretty.Bytes(), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := LoadV2(v2Path); err == nil || !strings.Contains(err.Error(), "not canonical") {
		t.Fatalf("non-canonical v2 JSON error = %v", err)
	}
}

func canonicalStateBytes(t *testing.T, value any) []byte {
	t.Helper()
	content, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	var generic any
	decoder := json.NewDecoder(bytes.NewReader(content))
	decoder.UseNumber()
	if err := decoder.Decode(&generic); err != nil {
		t.Fatal(err)
	}
	canonical, err := json.Marshal(generic)
	if err != nil {
		t.Fatal(err)
	}
	return append(canonical, '\n')
}
