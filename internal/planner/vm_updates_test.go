package planner

import (
	"klokast-box/internal/contract"
	"testing"
)

func TestUpdateIntentChangesProjectionWithoutChangingEngineLock(t *testing.T) {
	// The real fixture also exercises all existing registry projection inputs.
	root := prepareInstance(t, nil)
	snapshot, report, err := contract.Load(root, testEngine)
	if err != nil || !report.Valid {
		t.Fatalf("%v %#v", err, report)
	}
	before := Resolve(snapshot)
	p := &contract.VMUpdatePolicy{Enabled: true, Targets: map[string][]string{"boxa": {"iot", "bak"}}, Exclusions: []contract.VMUpdateExclusion{}}
	snapshot.Instance.VMUpdates = p
	a := Resolve(snapshot)
	p.Targets["boxa"] = []string{"bak", "iot"}
	b := Resolve(snapshot)
	ha, _ := ProjectionHash(a)
	hb, _ := ProjectionHash(b)
	old, _ := ProjectionHash(before)
	if ha != hb || ha == old || a.Engine != before.Engine {
		t.Fatal("policy must change only its deterministic desired-state projection")
	}
	p.Enabled = false
	c := Resolve(snapshot)
	hc, _ := ProjectionHash(c)
	if hc == ha || !a.VMUpdates.Enabled {
		t.Fatal("revocation did not change projection, or resolver changed its input")
	}
}
