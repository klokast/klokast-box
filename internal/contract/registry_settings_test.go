package contract

import (
	"encoding/json"
	"testing"
)

func registryExample(t *testing.T, value map[string]any) {
	t.Helper()
	var extensions map[string]any
	if err := json.Unmarshal([]byte(`{
  "substrate": {
    "bridge-ports": {"lan": ["eth1"], "iot": ["eth2", "eth3"]},
    "dhcp-reservations": [{"hostname":"sensor", "ipv4-address":"192.0.2.20", "mac":"02:00:00:00:00:20"}],
    "shared-guests": {"iot":{"runtime-state":"stopped"}, "bak":{}}
  },
  "inactive-apps": {
    "music": {
      "placement":{"boxes":["boxa"]}, "resources":{}, "runtime-state":"stopped",
      "devices":{"local-audio-endpoint":{"boxa":{"hostname":"streamer", "ipv4-address":"192.0.2.21", "mac":"02:00:00:00:00:AB"}}}
    },
    "retired-example": {
      "placement":{"primary":"boxa", "secondary":"boxb"},
      "resources":{"private-ingress":false}, "ingress-mode":"overlay", "isolation":"dedicated_vm",
      "users":[{"slug":"example", "system-user":"example", "tailscale-login":"example@example.invalid", "vm-ipv4-address":"192.0.2.22"}]
    },
    "torrent": {
      "placement":{"primary":"boxb"},
      "app-vms":{"torrent":{"boxb":{"vm-ipv4-address":"192.0.2.23"}}}
    },
    "bootstrap-iso-debian": {
      "placement":{"builder":"boxa"},
      "ephemeral":{"privileged-approval":false,"cleanup-required":true,"expires-at":"2000-01-01T00:00:00Z"}
    }
  }
}`), &extensions); err != nil {
		t.Fatal(err)
	}
	value["boxes"].(map[string]any)["boxa"].(map[string]any)["substrate"] = extensions["substrate"]
	value["inactive-apps"] = extensions["inactive-apps"]
}

func nested(value map[string]any, keys ...string) map[string]any {
	for _, key := range keys {
		value = value[key].(map[string]any)
	}
	return value
}

func TestRegistryCheckpointAcceptsSavedInactiveConfiguration(t *testing.T) {
	root := prepareInstance(t, "two", func(root string) {
		mutateInstanceJSON(t, root, func(value map[string]any) { registryExample(t, value) })
	})
	snapshot, report, err := Load(root, testEngine)
	if err != nil || !report.Valid {
		t.Fatalf("err=%v diagnostics=%#v", err, report.Diagnostics)
	}
	if snapshot.Instance.InactiveApps["bootstrap-iso-debian"].Ephemeral == nil ||
		snapshot.Instance.Apps["music"].Data["library"].Retention != "preserve" ||
		snapshot.Instance.Boxes["boxa"].Substrate.SharedGuests["iot"].RuntimeState != "stopped" {
		t.Fatal("saved controls, retained data, or stopped runtime was lost")
	}
}

func TestRegistryCheckpointClosedSchemaAndReferences(t *testing.T) {
	tests := []struct {
		name, code string
		mutate     func(map[string]any)
	}{
		{"root-null", "schema.invalid", func(v map[string]any) { v["inactive-apps"] = nil }},
		{"substrate-null", "schema.invalid", func(v map[string]any) { nested(v, "boxes", "boxa")["substrate"] = nil }},
		{"substrate-command", "schema.invalid", func(v map[string]any) { nested(v, "boxes", "boxa", "substrate")["command"] = "true" }},
		{"unknown-bridge", "schema.invalid", func(v map[string]any) {
			nested(v, "boxes", "boxa", "substrate", "bridge-ports")["unknown"] = []string{"eth4"}
		}},
		{"unsafe-interface", "schema.invalid", func(v map[string]any) {
			nested(v, "boxes", "boxa", "substrate", "bridge-ports")["lan"] = []string{"eth1;true"}
		}},
		{"duplicate-interface", "bridge.port-duplicate", func(v map[string]any) {
			nested(v, "boxes", "boxa", "substrate", "bridge-ports")["lan"] = []string{"eth2"}
		}},
		{"bridge-alias", "bridge.alias", func(v map[string]any) {
			p := nested(v, "boxes", "boxa", "substrate", "bridge-ports")
			p["bak"] = []string{"eth4"}
			p["backend"] = []string{"eth5"}
		}},
		{"unknown-guest", "schema.invalid", func(v map[string]any) {
			nested(v, "boxes", "boxa", "substrate", "shared-guests")["ops"] = map[string]any{}
		}},
		{"guest-null", "schema.invalid", func(v map[string]any) { nested(v, "boxes", "boxa", "substrate", "shared-guests")["bak"] = nil }},
		{"guest-unknown", "schema.invalid", func(v map[string]any) {
			nested(v, "boxes", "boxa", "substrate", "shared-guests", "bak")["command"] = "true"
		}},
		{"guest-state", "schema.invalid", func(v map[string]any) {
			nested(v, "boxes", "boxa", "substrate", "shared-guests", "iot")["runtime-state"] = "destroyed"
		}},
		{"inactive-enable", "schema.invalid", func(v map[string]any) { nested(v, "inactive-apps", "music")["enabled"] = true }},
		{"inactive-data", "schema.invalid", func(v map[string]any) { nested(v, "inactive-apps", "music")["data"] = map[string]any{} }},
		{"inactive-state", "schema.invalid", func(v map[string]any) { nested(v, "inactive-apps", "music")["runtime-state"] = "destroyed" }},
		{"resource-string", "schema.invalid", func(v map[string]any) { nested(v, "inactive-apps", "music", "resources")["feature"] = "true" }},
		{"binding-null", "schema.invalid", func(v map[string]any) {
			nested(v, "inactive-apps", "music", "devices", "local-audio-endpoint")["boxa"] = nil
		}},
		{"binding-unknown", "schema.invalid", func(v map[string]any) {
			nested(v, "inactive-apps", "music", "devices", "local-audio-endpoint", "boxa")["command"] = "true"
		}},
		{"binding-unknown-box", "reference.box", func(v map[string]any) {
			d := nested(v, "inactive-apps", "music", "devices", "local-audio-endpoint")
			d["missing"] = d["boxa"]
			delete(d, "boxa")
		}},
		{"binding-unplaced", "binding.placement", func(v map[string]any) {
			d := nested(v, "inactive-apps", "music", "devices", "local-audio-endpoint")
			d["boxb"] = d["boxa"]
			delete(d, "boxa")
		}},
		{"invalid-ip", "binding.ipv4", func(v map[string]any) {
			nested(v, "inactive-apps", "music", "devices", "local-audio-endpoint", "boxa")["ipv4-address"] = "192.0.2.999"
		}},
		{"ipv6", "schema.invalid", func(v map[string]any) {
			nested(v, "inactive-apps", "music", "devices", "local-audio-endpoint", "boxa")["ipv4-address"] = "::1"
		}},
		{"loopback", "binding.ipv4", func(v map[string]any) {
			nested(v, "inactive-apps", "music", "devices", "local-audio-endpoint", "boxa")["ipv4-address"] = "127.0.0.1"
		}},
		{"multicast", "binding.ipv4", func(v map[string]any) {
			nested(v, "inactive-apps", "music", "devices", "local-audio-endpoint", "boxa")["ipv4-address"] = "224.0.0.1"
		}},
		{"invalid-mac", "schema.invalid", func(v map[string]any) {
			nested(v, "inactive-apps", "music", "devices", "local-audio-endpoint", "boxa")["mac"] = "not-a-mac"
		}},
		{"duplicate-ip", "binding.duplicate", func(v map[string]any) {
			nested(v, "inactive-apps", "music", "devices", "local-audio-endpoint", "boxa")["ipv4-address"] = "192.0.2.20"
		}},
		{"duplicate-host", "binding.duplicate", func(v map[string]any) {
			nested(v, "inactive-apps", "music", "devices", "local-audio-endpoint", "boxa")["hostname"] = "sensor"
		}},
		{"duplicate-mac-case", "binding.duplicate", func(v map[string]any) {
			s := nested(v, "boxes", "boxa", "substrate")
			d := s["dhcp-reservations"].([]any)[0].(map[string]any)
			d["mac"] = "02:00:00:00:00:ab"
		}},
		{"placement-mix", "placement.conflict", func(v map[string]any) { nested(v, "inactive-apps", "music", "placement")["builder"] = "boxa" }},
		{"placement-unknown-box", "reference.box", func(v map[string]any) { nested(v, "inactive-apps", "torrent", "placement")["primary"] = "missing" }},
		{"placement-same-pair", "placement.conflict", func(v map[string]any) {
			nested(v, "inactive-apps", "retired-example", "placement")["secondary"] = "boxa"
		}},
		{"placement-duplicate", "schema.invalid", func(v map[string]any) {
			nested(v, "inactive-apps", "music", "placement")["boxes"] = []string{"boxa", "boxa"}
		}},
		{"vm-extra", "schema.invalid", func(v map[string]any) {
			nested(v, "inactive-apps", "torrent", "app-vms", "torrent", "boxb")["image"] = "arbitrary"
		}},
		{"vm-address", "binding.ipv4", func(v map[string]any) {
			nested(v, "inactive-apps", "torrent", "app-vms", "torrent", "boxb")["vm-ipv4-address"] = "0.0.0.0"
		}},
		{"ephemeral-null", "schema.invalid", func(v map[string]any) { nested(v, "inactive-apps", "bootstrap-iso-debian")["ephemeral"] = nil }},
		{"ephemeral-command", "schema.invalid", func(v map[string]any) {
			nested(v, "inactive-apps", "bootstrap-iso-debian", "ephemeral")["command"] = "true"
		}},
		{"ephemeral-bool", "schema.invalid", func(v map[string]any) {
			nested(v, "inactive-apps", "bootstrap-iso-debian", "ephemeral")["privileged-approval"] = "true"
		}},
		{"ephemeral-date", "ephemeral.time", func(v map[string]any) {
			nested(v, "inactive-apps", "bootstrap-iso-debian", "ephemeral")["expires-at"] = "2000-02-31T00:00:00Z"
		}},
		{"present-conflict", "app.inactive-conflict", func(v map[string]any) {
			nested(v, "apps")["music"] = map[string]any{"desired-state": "present", "placement": map[string]any{"mode": "multi-box", "boxes": []string{"boxa"}}}
		}},
		{"duplicate-user", "binding.duplicate", func(v map[string]any) {
			a := nested(v, "inactive-apps", "retired-example")
			u := a["users"].([]any)
			a["users"] = append(u, u[0])
		}},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			root := prepareInstance(t, "two", func(root string) {
				mutateInstanceJSON(t, root, func(v map[string]any) { registryExample(t, v); test.mutate(v) })
			})
			requireCode(t, root, test.code)
		})
	}
}

func TestRegistryCheckpointPreservesOmissionAndEmptyValues(t *testing.T) {
	root := prepareInstance(t, "two", func(root string) {
		mutateInstanceJSON(t, root, func(v map[string]any) {
			nested(v, "boxes", "boxa")["substrate"] = map[string]any{}
			v["inactive-apps"] = map[string]any{"retired-example": map[string]any{"resources": map[string]any{}, "ephemeral": map[string]any{"expires-at": ""}}}
			nested(v, "inactive-apps", "retired-example")["placement"] = map[string]any{"primary": "", "secondary": "", "builder": "", "boxes": []string{}}
		})
	})
	snapshot, report, err := Load(root, testEngine)
	if err != nil || !report.Valid {
		t.Fatalf("err=%v diagnostics=%#v", err, report.Diagnostics)
	}
	if snapshot.Instance.Boxes["boxa"].Substrate == nil || snapshot.Instance.Boxes["boxb"].Substrate != nil || snapshot.Instance.InactiveApps["retired-example"].Resources == nil {
		t.Fatal("omitted and explicit empty fields were conflated on input")
	}
	if p := snapshot.Instance.InactiveApps["retired-example"].Placement; p == nil || len(p.BoxIDs()) != 0 {
		t.Fatal("saved empty placement fields must remain unselected")
	}
}
