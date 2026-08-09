package klokastbox

import "embed"

// Assets contains the complete Contract v1 schemas, canonical instance
// template, and public application resource manifests used by klokast check.
//
//go:embed schemas/*.json all:templates/instance apps/*/platform-resources.yml
var Assets embed.FS
