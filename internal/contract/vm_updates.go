package contract

import (
	"fmt"
	"time"
)

// VMUpdatePolicy is standing intent, never a resolved image or package lock.
// An enabled policy still requires separate signed activation on the controller.
type VMUpdatePolicy struct {
	CheckFrequency    string              `json:"check-frequency"`
	CheckTime         string              `json:"check-time"`
	BranchDelayDays   int                 `json:"branch-delay-days"`
	ReportMaxAgeHours int                 `json:"report-max-age-hours"`
	Enabled           bool                `json:"enabled"`
	Targets           map[string][]string `json:"targets"`
	Exclusions        []VMUpdateExclusion `json:"exclusions"`
	BranchPolicy      string              `json:"branch-policy"`
	Window            VMUpdateWindow      `json:"maintenance-window"`
	ReplaceMinutes    int                 `json:"replacement-minutes"`
	RecoveryMinutes   int                 `json:"recovery-minutes"`
}

type VMUpdateWindow struct {
	Start     string `json:"start"`
	End       string `json:"end"`
	LastStart string `json:"last-start"`
}

type VMUpdateExclusion struct {
	Box    string `json:"box"`
	Role   string `json:"role"`
	Reason string `json:"reason"`
}

func (c *checker) validateVMUpdates(instance InstanceDocument) {
	p := instance.VMUpdates
	if p == nil {
		return
	}
	start, e1 := time.Parse("15:04", p.Window.Start)
	end, e2 := time.Parse("15:04", p.Window.End)
	cutoff, e3 := time.Parse("15:04", p.Window.LastStart)
	check, e4 := time.Parse("15:04", p.CheckTime)
	budget := time.Duration(p.ReplaceMinutes+p.RecoveryMinutes) * time.Minute
	if e1 != nil || e2 != nil || e3 != nil || e4 != nil || !check.Before(start) || cutoff.Before(start) || !cutoff.Before(end) || cutoff.Add(budget).After(end) {
		c.add("vm-updates.maintenance-window", "update.window", "check must precede the same-day window; last start must leave replacement and recovery budgets before its end")
	}
	for box := range p.Targets {
		if _, ok := instance.Boxes[box]; !ok {
			c.add("vm-updates.targets."+box, "reference.box", "update target must name a declared box")
		}
	}
	seen := map[string]bool{}
	for i, exclusion := range p.Exclusions {
		path := fmt.Sprintf("vm-updates.exclusions[%d]", i)
		found := false
		for _, role := range p.Targets[exclusion.Box] {
			if role == exclusion.Role {
				found = true
			}
		}
		if !found {
			c.add(path, "update.exclusion-target", "exclusion must refer to a declared update target")
		}
		key := exclusion.Box + "/" + exclusion.Role
		if seen[key] {
			c.add(path, "update.exclusion-duplicate", "each target can have only one durable exclusion")
		}
		seen[key] = true
	}
}
