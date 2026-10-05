package contract

import "testing"

func TestRetiredVMUpdatePolicyIsRejected(t *testing.T) {
	root := prepareInstance(t, "two", func(root string) {
		mutateInstanceJSON(t, root, func(v map[string]any) {
			v["vm-updates"] = map[string]any{"enabled": true}
		})
	})
	_, report, err := Load(root, testEngine)
	if err != nil || report.Valid {
		t.Fatalf("retired automatic update policy was accepted: %v %#v", err, report)
	}
}
