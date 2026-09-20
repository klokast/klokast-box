package contract

import "fmt"

// VMUpdatePolicy is standing intent, never a resolved image or package lock.
// An enabled policy still requires separate signed activation on the controller.
type VMUpdatePolicy struct {
	Enabled         bool                `json:"enabled"`
	Targets         map[string][]string `json:"targets"`
	Exclusions      []VMUpdateExclusion `json:"exclusions"`
	BranchPolicy    string              `json:"branch-policy"`
	Window          VMUpdateWindow      `json:"maintenance-window"`
	ReplaceMinutes  int                 `json:"replacement-minutes"`
	RecoveryMinutes int                 `json:"recovery-minutes"`
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
