package strictjson

import (
	"strings"
	"testing"
)

type closedDocument struct {
	A int `json:"a"`
}

func TestDecodeRejectsDuplicateTrailingUnknownAndNonCanonical(t *testing.T) {
	tests := []struct {
		name    string
		content string
		match   string
	}{
		{name: "duplicate", content: "{\"a\":1,\"a\":2}\n", match: "duplicate"},
		{name: "trailing", content: "{\"a\":1}\n{}\n", match: "trailing"},
		{name: "unknown", content: "{\"a\":1,\"b\":2}\n", match: "unknown field"},
		{name: "noncanonical", content: "{ \"a\": 1 }\n", match: "not canonical"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			var destination closedDocument
			err := Decode([]byte(test.content), &destination, true)
			if err == nil || !strings.Contains(err.Error(), test.match) {
				t.Fatalf("Decode error = %v, want %q", err, test.match)
			}
		})
	}
	var valid closedDocument
	if err := Decode([]byte("{\"a\":1}\n"), &valid, true); err != nil || valid.A != 1 {
		t.Fatalf("canonical document failed: %#v, %v", valid, err)
	}
}
