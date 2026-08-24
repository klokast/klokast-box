// Package authoritystate validates immutable authority-state evidence.
package authoritystate

import (
	"bytes"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"os"
)

const Kind = "klokast.authority-state.v1"

const (
	LegacyAuthority   = "legacy_deployment"
	InstanceAuthority = "instance_specification_v1"
)

var TailnetScopes = []string{
	"deployment.tailnet.groups.family",
	"deployment.tailnet.groups.operators",
	"deployment.tailnet.magicdns_suffix",
}

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
	decoder := json.NewDecoder(bytes.NewReader(content))
	decoder.DisallowUnknownFields()
	var state State
	if err := decoder.Decode(&state); err != nil {
		return State{}, fmt.Errorf("decode authority state: %w", err)
	}
	if decoder.Decode(&struct{}{}) == nil {
		return State{}, fmt.Errorf("authority state contains trailing JSON")
	}
	if err := Validate(state); err != nil {
		return State{}, err
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
