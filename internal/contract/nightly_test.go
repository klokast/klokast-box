package contract

import "testing"

func TestNightlyTime(t *testing.T) {
	for _, item := range []struct {
		value any
		valid bool
	}{{"00:00", true}, {"04:30", true}, {"23:59", true}, {"24:00", false}, {"3:00", false}, {"03:60", false}, {"03:00\n", false}, {nil, false}, {3, false}} {
		root := prepareInstance(t, "single", func(root string) {
			mutateInstanceJSON(t, root, func(v map[string]any) { v["nightly-update-time"] = item.value })
		})
		report, err := Check(root, testEngine)
		if err != nil || report.Valid != item.valid {
			t.Fatalf("%v: %v %#v", item.value, err, report.Diagnostics)
		}
	}
}
