package contract

import "testing"

func updateExample() map[string]any {
	return map[string]any{
		"enabled":    true,
		"targets":    map[string]any{"boxa": []string{"bak", "dmz", "iot"}},
		"exclusions": []any{}, "branch-policy": "tested-stable",
		"maintenance-window": map[string]any{"start": "02:00", "end": "04:00", "last-start": "03:00"},
		"check-frequency":    "daily", "check-time": "00:10", "branch-delay-days": 21, "report-max-age-hours": 30,
		"replacement-minutes": 30, "recovery-minutes": 30,
	}
}

func TestVMUpdatePolicy(t *testing.T) {
	root := prepareInstance(t, "two", func(root string) {
		mutateInstanceJSON(t, root, func(v map[string]any) { v["vm-updates"] = updateExample() })
	})
	snapshot, report, err := Load(root, testEngine)
	if err != nil || !report.Valid || snapshot.Instance.VMUpdates == nil || !snapshot.Instance.VMUpdates.Enabled {
		t.Fatalf("policy lost or rejected: %v %#v", err, report)
	}
}

func TestVMUpdateConfigurableTiming(t *testing.T) {
	root := prepareInstance(t, "two", func(root string) {
		mutateInstanceJSON(t, root, func(v map[string]any) {
			p := updateExample()
			p["check-time"] = "01:10"
			p["branch-delay-days"] = 7
			p["report-max-age-hours"] = 48
			p["replacement-minutes"] = 15
			p["recovery-minutes"] = 45
			v["vm-updates"] = p
		})
	})
	snapshot, report, err := Load(root, testEngine)
	if err != nil || !report.Valid || snapshot.Instance.VMUpdates.BranchDelayDays != 7 || snapshot.Instance.VMUpdates.ReportMaxAgeHours != 48 {
		t.Fatalf("configured timing lost or rejected: %v %#v", err, report)
	}
}

func TestVMUpdatePolicyRejectsExpandedAuthority(t *testing.T) {
	cases := []struct {
		name, code string
		mutate     func(map[string]any)
	}{
		{"check-in-window", "update.window", func(p map[string]any) { p["check-time"] = "02:10" }},
		{"invalid-clock", "schema.invalid", func(p map[string]any) { p["check-time"] = "24:00" }},
		{"negative-delay", "schema.invalid", func(p map[string]any) { p["branch-delay-days"] = -1 }},
		{"short-report-age", "schema.invalid", func(p map[string]any) { p["report-max-age-hours"] = 2 }},
		{"unknown-box", "reference.box", func(p map[string]any) { p["targets"] = map[string]any{"missing": []string{"bak"}} }},
		{"router", "schema.invalid", func(p map[string]any) { p["targets"] = map[string]any{"boxa": []string{"router"}} }},
		{"controller", "schema.invalid", func(p map[string]any) { p["targets"] = map[string]any{"boxa": []string{"ops"}} }},
		{"duplicate-role", "schema.invalid", func(p map[string]any) { p["targets"] = map[string]any{"boxa": []string{"bak", "bak"}} }},
		{"obsolete-canary-field", "schema.invalid", func(p map[string]any) { p["canary-hours"] = 24 }},
		{"moving-branch", "schema.invalid", func(p map[string]any) { p["branch-policy"] = "latest-stable" }},
		{"late-start", "update.window", func(p map[string]any) { p["maintenance-window"].(map[string]any)["last-start"] = "03:30" }},
		{"long-replacement", "schema.invalid", func(p map[string]any) { p["replacement-minutes"] = 0 }},
		{"command", "schema.invalid", func(p map[string]any) { p["command"] = "true" }},
		{"resolved-release", "schema.invalid", func(p map[string]any) { p["release-sha256"] = "a" }},
		{"physical-storage", "schema.invalid", func(p map[string]any) { p["data-lv"] = "/dev/vg0/data" }},
		{"excluded-undeclared", "update.exclusion-target", func(p map[string]any) {
			p["exclusions"] = []any{map[string]any{"box": "boxb", "role": "bak", "reason": "maintenance"}}
		}},
		{"duplicate-exclusion", "update.exclusion-duplicate", func(p map[string]any) {
			p["exclusions"] = []any{map[string]any{"box": "boxa", "role": "bak", "reason": "maintenance"}, map[string]any{"box": "boxa", "role": "bak", "reason": "repair"}}
		}},
	}
	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			root := prepareInstance(t, "two", func(root string) {
				mutateInstanceJSON(t, root, func(v map[string]any) { p := updateExample(); test.mutate(p); v["vm-updates"] = p })
			})
			requireCode(t, root, test.code)
		})
	}
}
