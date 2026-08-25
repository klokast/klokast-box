// Package authoritystate validates immutable authority-state evidence.
package authoritystate

import (
	"bytes"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"os"
	"regexp"
	"sort"
	"strings"

	"klokast-box/internal/strictjson"
)

const (
	Kind   = "klokast.authority-state.v1"
	KindV2 = "klokast.authority-state.v2"

	TailnetGroupID        = "tailnet-policy-inputs-v1"
	BoxConnectivityPrefix = "box-connectivity-v1:"

	LegacyRegistrySource = "legacy_platform_resources"
)

const (
	LegacyAuthority   = "legacy_deployment"
	InstanceAuthority = "instance_specification_v1"
)

var TailnetScopes = []string{
	"deployment.tailnet.groups.family",
	"deployment.tailnet.groups.operators",
	"deployment.tailnet.magicdns_suffix",
}

var boxIDPattern = regexp.MustCompile(`^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$`)

type Assignment struct {
	Scope     string `json:"scope"`
	Authority string `json:"authority"`
}

type State struct {
	SchemaVersion        int          `json:"schema_version"`
	Kind                 string       `json:"kind"`
	PriorStateSHA256     string       `json:"prior_state_sha256"`
	TransitionedScopes   []string     `json:"transitioned_scopes"`
	ResultingAuthorities []Assignment `json:"resulting_authorities"`
	SignedIntentSHA256   string       `json:"signed_intent_sha256"`
	TransitionID         string       `json:"transition_id"`
	AuthorityStateSHA256 string       `json:"authority_state_sha256"`
}

type SettingGroup struct {
	ID     string   `json:"id"`
	Scopes []string `json:"scopes"`
	Source string   `json:"source"`
}

type StateV2 struct {
	SchemaVersion        int            `json:"schema_version"`
	Kind                 string         `json:"kind"`
	PriorStateKind       string         `json:"prior_state_kind"`
	PriorStateSHA256     string         `json:"prior_state_sha256"`
	SettingGroups        []SettingGroup `json:"setting_groups"`
	SignedIntentSHA256   string         `json:"signed_intent_sha256"`
	TransitionID         string         `json:"transition_id"`
	AuthorityStateSHA256 string         `json:"authority_state_sha256"`
}

func Initial() (State, error) {
	state := State{
		SchemaVersion:        1,
		Kind:                 Kind,
		PriorStateSHA256:     "",
		TransitionedScopes:   []string{},
		ResultingAuthorities: []Assignment{},
		SignedIntentSHA256:   "",
		TransitionID:         "initial",
	}
	digest, err := Hash(state)
	state.AuthorityStateSHA256 = digest
	return state, err
}

func Load(path string) (State, error) {
	if path == "" {
		return State{}, fmt.Errorf("authority state path is required")
	}
	info, err := os.Lstat(path)
	if err != nil {
		return State{}, fmt.Errorf("inspect authority state: %w", err)
	}
	if !info.Mode().IsRegular() || info.Mode()&os.ModeSymlink != 0 || info.Size() <= 0 || info.Size() > 64*1024 {
		return State{}, fmt.Errorf("authority state must be a non-empty bounded regular file")
	}
	content, err := os.ReadFile(path)
	if err != nil {
		return State{}, fmt.Errorf("read authority state: %w", err)
	}
	var state State
	if err := strictjson.Decode(content, &state, true); err != nil {
		return State{}, fmt.Errorf("decode authority state: %w", err)
	}
	if err := Validate(state); err != nil {
		return State{}, err
	}
	return state, nil
}

func LoadV2(path string) (StateV2, error) {
	if path == "" {
		return StateV2{}, fmt.Errorf("authority state path is required")
	}
	info, err := os.Lstat(path)
	if err != nil {
		return StateV2{}, fmt.Errorf("inspect authority state: %w", err)
	}
	if !info.Mode().IsRegular() || info.Mode()&os.ModeSymlink != 0 || info.Size() <= 0 || info.Size() > 64*1024 {
		return StateV2{}, fmt.Errorf("authority state must be a non-empty bounded regular file")
	}
	content, err := os.ReadFile(path)
	if err != nil {
		return StateV2{}, fmt.Errorf("read authority state: %w", err)
	}
	var state StateV2
	if err := strictjson.Decode(content, &state, true); err != nil {
		return StateV2{}, fmt.Errorf("decode authority state: %w", err)
	}
	if err := ValidateV2(state); err != nil {
		return StateV2{}, err
	}
	return state, nil
}

func Validate(state State) error {
	if state.SchemaVersion != 1 || state.Kind != Kind {
		return fmt.Errorf("authority state kind or version is invalid")
	}
	if !sortedUnique(state.TransitionedScopes) || !assignmentsSortedUnique(state.ResultingAuthorities) {
		return fmt.Errorf("authority state scopes must be sorted, unique, and closed")
	}
	if len(state.TransitionedScopes) == 0 {
		if state.TransitionID != "initial" || state.PriorStateSHA256 != "" || state.SignedIntentSHA256 != "" || len(state.ResultingAuthorities) != 0 {
			return fmt.Errorf("initial authority state contains transition data")
		}
	} else {
		if !exactScopes(state.TransitionedScopes) || !digest(state.PriorStateSHA256) || !digest(state.SignedIntentSHA256) || state.TransitionID == "" || state.TransitionID == "initial" {
			return fmt.Errorf("authority transition binding is incomplete")
		}
		if len(state.ResultingAuthorities) != len(TailnetScopes) {
			return fmt.Errorf("authority transition must assign the complete Tailnet scope group")
		}
		for index, assignment := range state.ResultingAuthorities {
			if assignment.Scope != TailnetScopes[index] || (assignment.Authority != LegacyAuthority && assignment.Authority != InstanceAuthority) {
				return fmt.Errorf("authority transition contains an unknown scope or authority")
			}
		}
		first := state.ResultingAuthorities[0].Authority
		for _, assignment := range state.ResultingAuthorities[1:] {
			if assignment.Authority != first {
				return fmt.Errorf("Tailnet scope authority must change atomically")
			}
		}
	}
	want, err := Hash(state)
	if err != nil {
		return err
	}
	if state.AuthorityStateSHA256 != want {
		return fmt.Errorf("authority state hash does not match canonical content")
	}
	return nil
}

func Authority(state State) (string, error) {
	if err := Validate(state); err != nil {
		return "", err
	}
	if len(state.ResultingAuthorities) == 0 {
		return LegacyAuthority, nil
	}
	return state.ResultingAuthorities[0].Authority, nil
}

func Transition(prior State, authority, intentSHA256, transitionID string) (State, error) {
	if _, err := Authority(prior); err != nil {
		return State{}, err
	}
	assignments := make([]Assignment, 0, len(TailnetScopes))
	for _, scope := range TailnetScopes {
		assignments = append(assignments, Assignment{Scope: scope, Authority: authority})
	}
	state := State{
		SchemaVersion:        1,
		Kind:                 Kind,
		PriorStateSHA256:     prior.AuthorityStateSHA256,
		TransitionedScopes:   append([]string{}, TailnetScopes...),
		ResultingAuthorities: assignments,
		SignedIntentSHA256:   intentSHA256,
		TransitionID:         transitionID,
	}
	digestValue, err := Hash(state)
	if err != nil {
		return State{}, err
	}
	state.AuthorityStateSHA256 = digestValue
	if err := Validate(state); err != nil {
		return State{}, err
	}
	return state, nil
}

func ConvertV1(prior State, boxIDs []string, intentSHA256, transitionID string) (StateV2, error) {
	tailnetAuthority, err := Authority(prior)
	if err != nil {
		return StateV2{}, err
	}
	if !digest(intentSHA256) || transitionID == "" || transitionID == "initial" {
		return StateV2{}, fmt.Errorf("authority v1 conversion binding is incomplete")
	}
	boxes := append([]string{}, boxIDs...)
	sort.Strings(boxes)
	if len(boxes) == 0 {
		return StateV2{}, fmt.Errorf("authority v1 conversion requires at least one box")
	}
	groups := []SettingGroup{{
		ID: TailnetGroupID, Scopes: append([]string{}, TailnetScopes...),
		Source: tailnetAuthority,
	}}
	for index, box := range boxes {
		if !boxIDPattern.MatchString(box) || (index > 0 && boxes[index-1] == box) {
			return StateV2{}, fmt.Errorf("authority v1 conversion box list is invalid")
		}
		groups = append(groups, SettingGroup{
			ID: BoxConnectivityPrefix + box, Scopes: BoxConnectivityScopes(box),
			Source: LegacyRegistrySource,
		})
	}
	sort.Slice(groups, func(i, j int) bool { return groups[i].ID < groups[j].ID })
	state := StateV2{
		SchemaVersion: 2, Kind: KindV2, PriorStateKind: Kind,
		PriorStateSHA256: prior.AuthorityStateSHA256, SettingGroups: groups,
		SignedIntentSHA256: intentSHA256, TransitionID: transitionID,
	}
	state.AuthorityStateSHA256, err = HashV2(state)
	if err != nil {
		return StateV2{}, err
	}
	if err := ValidateV2(state); err != nil {
		return StateV2{}, err
	}
	return state, nil
}

func TransitionGroup(prior StateV2, groupID, source, intentSHA256, transitionID string) (StateV2, error) {
	if err := ValidateV2(prior); err != nil {
		return StateV2{}, err
	}
	if !digest(intentSHA256) || transitionID == "" || transitionID == "initial" {
		return StateV2{}, fmt.Errorf("authority v2 transition binding is incomplete")
	}
	groups := make([]SettingGroup, len(prior.SettingGroups))
	found := false
	for index, group := range prior.SettingGroups {
		groups[index] = SettingGroup{ID: group.ID, Scopes: append([]string{}, group.Scopes...), Source: group.Source}
		if group.ID == groupID {
			groups[index].Source = source
			found = true
		}
	}
	if !found {
		return StateV2{}, fmt.Errorf("authority v2 transition group is not approved")
	}
	state := StateV2{
		SchemaVersion: 2, Kind: KindV2, PriorStateKind: KindV2,
		PriorStateSHA256: prior.AuthorityStateSHA256, SettingGroups: groups,
		SignedIntentSHA256: intentSHA256, TransitionID: transitionID,
	}
	var err error
	state.AuthorityStateSHA256, err = HashV2(state)
	if err != nil {
		return StateV2{}, err
	}
	if err := ValidateV2(state); err != nil {
		return StateV2{}, err
	}
	return state, nil
}

func ValidateV2(state StateV2) error {
	if state.SchemaVersion != 2 || state.Kind != KindV2 {
		return fmt.Errorf("authority state v2 kind or version is invalid")
	}
	if (state.PriorStateKind != Kind && state.PriorStateKind != KindV2) || !digest(state.PriorStateSHA256) || !digest(state.SignedIntentSHA256) || state.TransitionID == "" || state.TransitionID == "initial" {
		return fmt.Errorf("authority state v2 transition binding is incomplete")
	}
	if len(state.SettingGroups) < 2 {
		return fmt.Errorf("authority state v2 setting groups are incomplete")
	}
	tailnetSeen := false
	boxCount := 0
	for index, group := range state.SettingGroups {
		if group.ID == "" || (index > 0 && state.SettingGroups[index-1].ID >= group.ID) {
			return fmt.Errorf("authority state v2 setting groups must be sorted and unique")
		}
		switch {
		case group.ID == TailnetGroupID:
			if tailnetSeen || !sameStrings(group.Scopes, TailnetScopes) || (group.Source != LegacyAuthority && group.Source != InstanceAuthority) {
				return fmt.Errorf("authority state v2 Tailnet group is incomplete or mixed")
			}
			tailnetSeen = true
		case strings.HasPrefix(group.ID, BoxConnectivityPrefix):
			box := strings.TrimPrefix(group.ID, BoxConnectivityPrefix)
			if !boxIDPattern.MatchString(box) || !sameStrings(group.Scopes, BoxConnectivityScopes(box)) || (group.Source != LegacyRegistrySource && group.Source != InstanceAuthority) {
				return fmt.Errorf("authority state v2 box group is unknown, incomplete, or mixed")
			}
			boxCount++
		default:
			return fmt.Errorf("authority state v2 contains an unknown setting group")
		}
	}
	if !tailnetSeen || boxCount == 0 {
		return fmt.Errorf("authority state v2 setting groups are incomplete")
	}
	want, err := HashV2(state)
	if err != nil {
		return err
	}
	if state.AuthorityStateSHA256 != want {
		return fmt.Errorf("authority state v2 hash does not match canonical content")
	}
	return nil
}

func GroupSource(state StateV2, groupID string) (string, error) {
	if err := ValidateV2(state); err != nil {
		return "", err
	}
	for _, group := range state.SettingGroups {
		if group.ID == groupID {
			return group.Source, nil
		}
	}
	return "", fmt.Errorf("authority state v2 does not contain setting group %q", groupID)
}

func BoxConnectivityScopes(box string) []string {
	base := "boxes." + box
	return []string{
		base,
		base + ".access.available_capabilities",
		base + ".access.enabled_capabilities",
		base + ".access.prohibited_capabilities",
		base + ".connectivity",
	}
}

func Hash(state State) (string, error) {
	state.AuthorityStateSHA256 = ""
	content, err := json.Marshal(state)
	if err != nil {
		return "", fmt.Errorf("encode authority state: %w", err)
	}
	var value any
	decoder := json.NewDecoder(bytes.NewReader(content))
	decoder.UseNumber()
	if err := decoder.Decode(&value); err != nil {
		return "", fmt.Errorf("canonicalize authority state: %w", err)
	}
	canonical, err := json.Marshal(value)
	if err != nil {
		return "", fmt.Errorf("canonicalize authority state: %w", err)
	}
	sum := sha256.Sum256(canonical)
	return fmt.Sprintf("%x", sum[:]), nil
}

func HashV2(state StateV2) (string, error) {
	state.AuthorityStateSHA256 = ""
	content, err := json.Marshal(state)
	if err != nil {
		return "", fmt.Errorf("encode authority state v2: %w", err)
	}
	var value any
	decoder := json.NewDecoder(bytes.NewReader(content))
	decoder.UseNumber()
	if err := decoder.Decode(&value); err != nil {
		return "", fmt.Errorf("canonicalize authority state v2: %w", err)
	}
	canonical, err := json.Marshal(value)
	if err != nil {
		return "", fmt.Errorf("canonicalize authority state v2: %w", err)
	}
	sum := sha256.Sum256(canonical)
	return fmt.Sprintf("%x", sum[:]), nil
}

func sameStrings(first, second []string) bool {
	if len(first) != len(second) {
		return false
	}
	for index := range first {
		if first[index] != second[index] {
			return false
		}
	}
	return true
}

func exactScopes(scopes []string) bool {
	if len(scopes) != len(TailnetScopes) {
		return false
	}
	for index := range TailnetScopes {
		if scopes[index] != TailnetScopes[index] {
			return false
		}
	}
	return true
}

func sortedUnique(values []string) bool {
	for index, value := range values {
		known := false
		for _, scope := range TailnetScopes {
			known = known || value == scope
		}
		if !known {
			return false
		}
		if index > 0 && values[index-1] >= value {
			return false
		}
	}
	return true
}

func assignmentsSortedUnique(values []Assignment) bool {
	for index, value := range values {
		if index > 0 && values[index-1].Scope >= value.Scope {
			return false
		}
	}
	return true
}

func digest(value string) bool {
	if len(value) != 64 {
		return false
	}
	for _, character := range value {
		if (character < '0' || character > '9') && (character < 'a' || character > 'f') {
			return false
		}
	}
	return true
}
