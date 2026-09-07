package contract

import (
	"fmt"
	"net/netip"
	"sort"
	"strings"
	"time"
)

// These optional inputs preserve disabled configuration for the registry
// migration. Acceptance by Check does not grant a compiler or executor authority.
type SubstrateDocument struct {
	BridgePorts      map[string][]string            `json:"bridge-ports,omitempty"`
	DHCPReservations []DeviceBindingDocument        `json:"dhcp-reservations,omitempty"`
	SharedGuests     map[string]SharedGuestDocument `json:"shared-guests,omitempty"`
}

type SharedGuestDocument struct {
	RuntimeState string `json:"runtime-state,omitempty"`
}

type DeviceBindingDocument struct {
	Hostname    string `json:"hostname"`
	IPv4Address string `json:"ipv4-address"`
	MAC         string `json:"mac"`
}

type InactivePlacementDocument struct {
	Primary   string   `json:"primary,omitempty"`
	Secondary string   `json:"secondary,omitempty"`
	Builder   string   `json:"builder,omitempty"`
	Boxes     []string `json:"boxes,omitempty"`
}

type VMBindingDocument struct {
	IPv4Address string `json:"vm-ipv4-address"`
}

type InactiveUserDocument struct {
	Slug           string `json:"slug"`
	SystemUser     string `json:"system-user,omitempty"`
	TailscaleLogin string `json:"tailscale-login"`
	IPv4Address    string `json:"vm-ipv4-address"`
}

type EphemeralDocument struct {
	PrivilegedApproval *bool   `json:"privileged-approval,omitempty"`
	CleanupRequired    *bool   `json:"cleanup-required,omitempty"`
	ExpiresAt          *string `json:"expires-at,omitempty"`
}

type InactiveAppDocument struct {
	Placement    *InactivePlacementDocument                  `json:"placement,omitempty"`
	Resources    map[string]bool                             `json:"resources,omitempty"`
	RuntimeState string                                      `json:"runtime-state,omitempty"`
	IngressMode  string                                      `json:"ingress-mode,omitempty"`
	Isolation    string                                      `json:"isolation,omitempty"`
	Devices      map[string]map[string]DeviceBindingDocument `json:"devices,omitempty"`
	AppVMs       map[string]map[string]VMBindingDocument     `json:"app-vms,omitempty"`
	Users        []InactiveUserDocument                      `json:"users,omitempty"`
	Ephemeral    *EphemeralDocument                          `json:"ephemeral,omitempty"`
}

func (p InactivePlacementDocument) BoxIDs() []string {
	values := append([]string{}, p.Boxes...)
	for _, box := range []string{p.Primary, p.Secondary, p.Builder} {
		if box != "" {
			values = append(values, box)
		}
	}
	return values
}

func registryKeys[T any](values map[string]T) []string {
	keys := make([]string, 0, len(values))
	for key := range values {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	return keys
}

func (c *checker) validateRegistrySettings(instance InstanceDocument) {
	// Collisions are local to a box. Two sites can use the same private address.
	seen := map[string]map[string]bool{}
	unique := func(box, kind, value, path string) {
		if seen[box] == nil {
			seen[box] = map[string]bool{}
		}
		key := kind + ":" + value
		if seen[box][key] {
			c.add(path, "binding.duplicate", "binding duplicates another "+kind+" on the same box")
		}
		seen[box][key] = true
	}
	reference := func(box, path string) {
		if _, ok := instance.Boxes[box]; !ok {
			c.add(path, "reference.box", "registry setting references an unknown box")
		}
	}
	ipv4 := func(value, path string) {
		address, err := netip.ParseAddr(value)
		if err != nil || !address.Is4() || !address.IsGlobalUnicast() || address.IsLoopback() {
			c.add(path, "binding.ipv4", "binding must use one unicast IPv4 address")
		}
	}
	device := func(box string, value DeviceBindingDocument, path string) {
		ipv4(value.IPv4Address, path+".ipv4-address")
		unique(box, "IPv4 address", value.IPv4Address, path+".ipv4-address")
		unique(box, "hostname", value.Hostname, path+".hostname")
		unique(box, "MAC address", strings.ToLower(value.MAC), path+".mac")
	}
	for _, box := range registryKeys(instance.Boxes) {
		value := instance.Boxes[box].Substrate
		if value == nil {
			continue
		}
		path := InstancePath + "$.boxes." + box + ".substrate"
		ports := map[string]string{}
		for _, bridge := range registryKeys(value.BridgePorts) {
			for _, port := range value.BridgePorts[bridge] {
				if prior, ok := ports[port]; ok {
					c.add(path+".bridge-ports."+bridge, "bridge.port-duplicate", "physical interface is already assigned to "+prior)
				}
				ports[port] = bridge
			}
		}
		if _, bak := value.BridgePorts["bak"]; bak {
			if _, alias := value.BridgePorts["backend"]; alias {
				c.add(path+".bridge-ports", "bridge.alias", "use only one name for the backend bridge")
			}
		}
		for index, reservation := range value.DHCPReservations {
			device(box, reservation, fmt.Sprintf("%s.dhcp-reservations[%d]", path, index))
		}
	}
	for _, appID := range registryKeys(instance.InactiveApps) {
		app := instance.InactiveApps[appID]
		path := InstancePath + "$.inactive-apps." + appID
		if active, ok := instance.Apps[appID]; ok && active.DesiredState == "present" {
			c.add(path, "app.inactive-conflict", "a present app cannot also have inactive configuration")
		}
		selected := []string{}
		if p := app.Placement; p != nil {
			selected = p.BoxIDs()
			modes := 0
			if p.Primary != "" || p.Secondary != "" {
				modes++
			}
			if p.Builder != "" {
				modes++
			}
			if len(p.Boxes) != 0 {
				modes++
			}
			if modes > 1 || (p.Secondary != "" && p.Primary == "") || (p.Primary != "" && p.Primary == p.Secondary) {
				c.add(path+".placement", "placement.conflict", "inactive placement must use distinct primary/secondary, builder, or multi-box targets")
			}
			for _, box := range selected {
				reference(box, path+".placement")
			}
		}
		boundBox := func(box, path string) {
			reference(box, path)
			for _, target := range selected {
				if target == box {
					return
				}
			}
			c.add(path, "binding.placement", "binding box is not selected by inactive placement")
		}
		for _, resource := range registryKeys(app.Devices) {
			for _, box := range registryKeys(app.Devices[resource]) {
				p := path + ".devices." + resource + "." + box
				boundBox(box, p)
				device(box, app.Devices[resource][box], p)
			}
		}
		for _, resource := range registryKeys(app.AppVMs) {
			for _, box := range registryKeys(app.AppVMs[resource]) {
				p := path + ".app-vms." + resource + "." + box
				boundBox(box, p)
				value := app.AppVMs[resource][box].IPv4Address
				ipv4(value, p+".vm-ipv4-address")
				unique(box, "IPv4 address", value, p+".vm-ipv4-address")
			}
		}
		users := map[string]bool{}
		for index, user := range app.Users {
			p := fmt.Sprintf("%s.users[%d]", path, index)
			if len(selected) == 0 {
				c.add(p, "binding.placement", "inactive users require a placement target")
			}
			ipv4(user.IPv4Address, p+".vm-ipv4-address")
			systemUser := user.SystemUser
			if systemUser == "" {
				systemUser = user.Slug
			}
			for _, value := range []string{"slug:" + user.Slug, "user:" + systemUser, "login:" + user.TailscaleLogin} {
				if users[value] {
					c.add(p, "binding.duplicate", "inactive user identity is duplicated")
				}
				users[value] = true
			}
			for _, box := range selected {
				unique(box, "IPv4 address", user.IPv4Address, p+".vm-ipv4-address")
			}
		}
		if e := app.Ephemeral; e != nil {
			if e.ExpiresAt != nil && *e.ExpiresAt != "" {
				if _, err := time.Parse("2006-01-02T15:04:05Z", *e.ExpiresAt); err != nil {
					c.add(path+".ephemeral.expires-at", "ephemeral.time", "saved expiry must be a valid UTC timestamp")
				}
			}
			// This is saved inactive evidence, not permission for a future launch.
			// Past timestamps and false controls are preserved without renewal.
		}
	}
}
