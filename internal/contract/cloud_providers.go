package contract

import (
	"fmt"
	"regexp"
	"strings"

	klokastbox "klokast-box"
)

var (
	cloudProviderIDPattern = regexp.MustCompile(`^[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?$`)
	dnsDomainPattern       = regexp.MustCompile(`^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$`)
)

type cloudProvider struct {
	Name    string
	Domain  string
	Comment string
}

func loadCloudProviders() (map[string]cloudProvider, error) {
	content, err := klokastbox.Assets.ReadFile("cloud-providers.json")
	if err != nil {
		return nil, err
	}
	return validateCloudProviders(content)
}

func validateCloudProviders(content []byte) (map[string]cloudProvider, error) {
	value, duplicatePath, err := decodeUniqueJSON(content)
	if err != nil {
		if duplicatePath != "" {
			return nil, fmt.Errorf("duplicate cloud-provider catalog key at %s", duplicatePath)
		}
		return nil, fmt.Errorf("decode cloud-provider catalog: %w", err)
	}
	root, ok := value.(map[string]any)
	if !ok || len(root) == 0 {
		return nil, fmt.Errorf("cloud-provider catalog must be a non-empty object")
	}
	providers := make(map[string]cloudProvider, len(root))
	for id, raw := range root {
		if !cloudProviderIDPattern.MatchString(id) {
			return nil, fmt.Errorf("cloud-provider ID %q is not normalized", id)
		}
		entry, ok := raw.(map[string]any)
		if !ok {
			return nil, fmt.Errorf("cloud-provider %q must be an object", id)
		}
		if len(entry) != 3 {
			return nil, fmt.Errorf("cloud-provider %q must contain only name, domain, and comment", id)
		}
		name, nameOK := entry["name"].(string)
		domain, domainOK := entry["domain"].(string)
		comment, commentOK := entry["comment"].(string)
		if !nameOK || !domainOK || !commentOK {
			return nil, fmt.Errorf("cloud-provider %q fields must be strings", id)
		}
		if name != id {
			return nil, fmt.Errorf("cloud-provider %q name must equal its ID", id)
		}
		if len(domain) > 253 || domain != strings.ToLower(domain) || !dnsDomainPattern.MatchString(domain) {
			return nil, fmt.Errorf("cloud-provider %q domain must be a lowercase DNS name", id)
		}
		if len(comment) > 500 {
			return nil, fmt.Errorf("cloud-provider %q comment exceeds 500 characters", id)
		}
		providers[id] = cloudProvider{Name: name, Domain: domain, Comment: comment}
	}
	return providers, nil
}
