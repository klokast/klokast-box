package planner

import (
	"bytes"
	"crypto/sha256"
	"fmt"
	"os"
	"path/filepath"
	"sort"

	"klokast-box/internal/contract"
)

type compatibilityDocument struct {
	digest string
	root   map[string]any
}

func loadCompatibilityDocument(path, label string) (compatibilityDocument, []contract.Diagnostic, error) {
	if path == "" {
		return compatibilityDocument{}, nil, fmt.Errorf("%s path is required", label)
	}
	absolute, err := filepath.Abs(path)
	if err != nil {
		return compatibilityDocument{}, nil, fmt.Errorf("resolve %s: %w", label, err)
	}
	info, err := os.Lstat(absolute)
	if err != nil {
		return compatibilityDocument{}, nil, fmt.Errorf("inspect %s: %w", label, err)
	}
	if info.Mode()&os.ModeSymlink != 0 {
		return compatibilityDocument{}, []contract.Diagnostic{{Path: label, Code: "path.symlink", Message: label + " must not be a symbolic link"}}, nil
	}
	if !info.Mode().IsRegular() {
		return compatibilityDocument{}, []contract.Diagnostic{{Path: label, Code: "path.type", Message: label + " must be a regular file"}}, nil
	}
	if info.Size() <= 0 || info.Size() > maximumRegistryFile {
		return compatibilityDocument{}, []contract.Diagnostic{{Path: label, Code: "path.size", Message: label + " must be non-empty and no larger than one MiB"}}, nil
	}
	content, err := os.ReadFile(absolute)
	if err != nil {
		return compatibilityDocument{}, nil, fmt.Errorf("read %s: %w", label, err)
	}
	if bytes.IndexByte(content, 0) >= 0 {
		return compatibilityDocument{}, []contract.Diagnostic{{Path: label, Code: "yaml.binary", Message: label + " must be text"}}, nil
	}
	diagnostics := []contract.Diagnostic{}
	for _, line := range contract.RawSecretLines(content) {
		diagnostics = append(diagnostics, contract.Diagnostic{Path: fmt.Sprintf("%s:%d", label, line), Code: "secret.raw", Message: "possible raw secret value is present"})
	}
	value, yamlDiagnostics := contract.ParseSafeYAML(content)
	for _, diagnostic := range yamlDiagnostics {
		diagnostic.Path = label
		diagnostics = append(diagnostics, diagnostic)
	}
	root, ok := value.(map[string]any)
	if len(yamlDiagnostics) == 0 && !ok {
		diagnostics = append(diagnostics, contract.Diagnostic{Path: label, Code: "document.type", Message: label + " must be an object"})
	}
	if ok {
		version, versionOK := integer(root["schema_version"])
		if !versionOK || version != 1 {
			diagnostics = append(diagnostics, contract.Diagnostic{Path: label + "$.schema_version", Code: "document.version", Message: label + " schema_version must be 1"})
		}
	}
	sort.Slice(diagnostics, func(i, j int) bool {
		if diagnostics[i].Path != diagnostics[j].Path {
			return diagnostics[i].Path < diagnostics[j].Path
		}
		return diagnostics[i].Code < diagnostics[j].Code
	})
	digest := sha256.Sum256(content)
	return compatibilityDocument{digest: fmt.Sprintf("%x", digest[:]), root: root}, diagnostics, nil
}

func compareDeployment(projection Projection, legacy compatibilityDocument) Compatibility {
	findings := []Finding{}
	add := func(path, class, code, message string) {
		authority := ""
		if class == "compatibility_only" {
			authority = "legacy_deployment"
		}
		findings = append(findings, Finding{Path: "deployment." + path, Class: class, Code: code, Authority: authority, Message: message})
	}
	add("schema_version", "matched", "deployment.schema", "the legacy deployment uses the supported compatibility schema")
	for _, field := range sortedKeys(legacy.root) {
		if field != "schema_version" && field != "tailnet" && field != "boxes" {
			add(field, "compatibility_only", "deployment.field", "the legacy deployment root field remains under legacy deployment authority")
		}
	}

	tailnet, tailnetOK := asMap(legacy.root["tailnet"])
	if !tailnetOK {
		add("tailnet", "unsupported", "deployment.tailnet", "the legacy deployment tailnet field must be an object")
	} else {
		if equivalent(projectionTailnetSuffix(projection), tailnet["magicdns_suffix"]) {
			add("tailnet.magicdns_suffix", "matched", "tailnet.suffix", "the Tailnet DNS name matches Instance Specification v1")
		} else {
			add("tailnet.magicdns_suffix", "conflict", "tailnet.suffix", "the Tailnet DNS name does not match Instance Specification v1")
		}
		groups, groupsOK := asMap(tailnet["groups"])
		if !groupsOK {
			add("tailnet.groups", "unsupported", "tailnet.groups", "the legacy Tailnet groups field must be an object")
		} else {
			for _, name := range []string{"operators", "family"} {
				if equivalentStringSet(projectionTailnetGroup(projection, name), groups[name]) {
					add("tailnet.groups."+name, "matched", "tailnet.group", "the Tailnet group matches Instance Specification v1")
				} else {
					add("tailnet.groups."+name, "conflict", "tailnet.group", "the Tailnet group does not match Instance Specification v1")
				}
			}
			for _, name := range sortedKeys(groups) {
				if name != "operators" && name != "family" {
					add("tailnet.groups."+name, "compatibility_only", "tailnet.group", "the extra Tailnet group remains under legacy deployment authority")
				}
			}
		}
		for _, field := range sortedKeys(tailnet) {
			if field != "magicdns_suffix" && field != "groups" {
				add("tailnet."+field, "compatibility_only", "tailnet.field", "the legacy Tailnet field remains under legacy deployment authority")
			}
		}
	}

	boxes, boxesOK := asMap(legacy.root["boxes"])
	if !boxesOK {
		add("boxes", "unsupported", "deployment.boxes", "the legacy deployment boxes field must be an object")
	} else {
		expected := map[string]bool{}
		for _, box := range projection.Boxes {
			expected[box.HostnamePrefix] = true
			if len(boxes) == 0 {
				continue
			}
			path := "boxes." + box.HostnamePrefix
			legacyBox, ok := asMap(boxes[box.HostnamePrefix])
			if !ok {
				add(path, "conflict", "box.missing", "the legacy deployment has no object for the Instance Specification box")
				continue
			}
			if _, present := legacyBox["site"]; present {
				add(path+".site", "compatibility_only", "box.site", "legacy site labels remain under deployment compatibility authority")
			}
			if _, present := legacyBox["physical_location"]; present {
				add(path+".physical_location", "compatibility_only", "box.location", "legacy location text remains under deployment compatibility authority")
			}
			for _, field := range sortedKeys(legacyBox) {
				if field != "site" && field != "physical_location" {
					add(path+"."+field, "compatibility_only", "box.field", "the legacy deployment box field remains under legacy deployment authority")
				}
			}
		}
		for _, prefix := range sortedKeys(boxes) {
			if !expected[prefix] {
				add("boxes."+prefix, "conflict", "box.unrepresented", "the legacy deployment box is not represented by Instance Specification v1")
			}
		}
	}

	findings = append(findings, Finding{
		Path: "deployment.control_plane.controller", Class: "derived", Code: "controller.instance-specification",
		Authority: "instance_specification_v1", Message: "Instance Specification v1 owns the proposed controller placement",
	})
	for _, runner := range projection.ControlPlane.Airunners {
		findings = append(findings, Finding{
			Path: "deployment.control_plane.airunners." + runner, Class: "derived", Code: "airunner.instance-specification",
			Authority: "instance_specification_v1", Message: "Instance Specification v1 owns the ordered airunner runtime identity",
		})
	}
	result := Compatibility{Findings: findings}
	sortCompatibility(&result)
	return result
}

func compareControllerHA(projection Projection, legacy compatibilityDocument) Compatibility {
	findings := []Finding{}
	add := func(path, class, code, message string) {
		authority := ""
		if class == "compatibility_only" {
			authority = "legacy_controller_ha"
		}
		findings = append(findings, Finding{Path: "controller_ha." + path, Class: class, Code: code, Authority: authority, Message: message})
	}
	add("schema_version", "matched", "controller.schema", "the legacy controller set uses the supported compatibility schema")
	for _, field := range sortedKeys(legacy.root) {
		if field != "schema_version" && field != "controllers" {
			add(field, "compatibility_only", "controller.field", "the legacy controller setting remains under legacy controller HA authority")
		}
	}

	expected := []Controller{projection.ControlPlane.ActiveController}
	if projection.ControlPlane.StandbyController != nil {
		expected = append(expected, *projection.ControlPlane.StandbyController)
	}
	controllers, ok := legacy.root["controllers"].([]any)
	if !ok {
		add("controllers", "unsupported", "controller.list", "the legacy controllers field must be an array")
	} else {
		if len(controllers) != len(expected) {
			add("controllers", "conflict", "controller.cardinality", "the legacy and Instance Specification controller sets have different sizes")
		}
		for index, controller := range expected {
			path := fmt.Sprintf("controllers[%d]", index)
			if index >= len(controllers) {
				add(path, "conflict", "controller.missing", "the expected controller is absent from the legacy controller set")
				continue
			}
			entry, entryOK := asMap(controllers[index])
			if !entryOK {
				add(path, "unsupported", "controller.type", "the legacy controller entry must be an object")
				continue
			}
			prefix := prefixForProjectionBox(projection, controller.BoxID)
			for field, wanted := range map[string]string{"box": prefix, "hostname": controller.Hostname} {
				if equivalent(wanted, entry[field]) {
					add(path+"."+field, "matched", "controller.match", "the controller identity matches Instance Specification v1")
				} else {
					add(path+"."+field, "conflict", "controller.mismatch", "the controller identity does not match Instance Specification v1")
				}
			}
			for _, field := range sortedKeys(entry) {
				if field != "box" && field != "hostname" {
					add(path+"."+field, "compatibility_only", "controller.entry-field", "the controller entry field remains under legacy controller HA authority")
				}
			}
		}
		for index := len(expected); index < len(controllers); index++ {
			add(fmt.Sprintf("controllers[%d]", index), "conflict", "controller.unrepresented", "the legacy controller has no Instance Specification v1 representation")
		}
	}
	result := Compatibility{Findings: findings}
	sortCompatibility(&result)
	return result
}

func mergeCompatibility(target *Compatibility, source Compatibility) {
	target.Findings = append(target.Findings, source.Findings...)
}

func sortCompatibility(result *Compatibility) {
	sort.Slice(result.Findings, func(i, j int) bool {
		if result.Findings[i].Path != result.Findings[j].Path {
			return result.Findings[i].Path < result.Findings[j].Path
		}
		if result.Findings[i].Class != result.Findings[j].Class {
			return result.Findings[i].Class < result.Findings[j].Class
		}
		return result.Findings[i].Code < result.Findings[j].Code
	})
	result.Summary = FindingSummary{}
	for _, finding := range result.Findings {
		switch finding.Class {
		case "matched":
			result.Summary.Matched++
		case "derived":
			result.Summary.Derived++
		case "compatibility_only":
			result.Summary.CompatibilityOnly++
		case "conflict":
			result.Summary.Conflict++
		case "unsupported":
			result.Summary.Unsupported++
		}
	}
}

func projectionTailnetSuffix(projection Projection) string {
	return projection.Tailnet.MagicDNSSuffix
}

func projectionTailnetGroup(projection Projection, name string) []string {
	for _, group := range projection.Tailnet.Groups {
		if group.Name == name {
			return group.Members
		}
	}
	return []string{}
}

func siteLocation(projection Projection, siteID string) string {
	for _, site := range projection.Sites {
		if site.ID == siteID {
			return site.PhysicalLocation
		}
	}
	return ""
}

func prefixForProjectionBox(projection Projection, boxID string) string {
	for _, box := range projection.Boxes {
		if box.ID == boxID {
			return box.HostnamePrefix
		}
	}
	return ""
}
