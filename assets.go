package klokastbox

import "embed"

// Assets contains the public cloud-provider catalog, the complete Instance
// Specification v1 schemas and support template, and public application
// resource manifests used by klokast init, klokast check, and klokast plan.
//
//go:embed cloud-providers.json schemas/*.json all:templates/instance apps/*/platform-resources.yml
var Assets embed.FS
