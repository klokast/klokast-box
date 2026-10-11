package contract

import "testing"

func TestRoutingProfileAddresses(t *testing.T) {
	for _, address := range []string{"223.5.5.5", "119.29.29.29", "1.1.1.1", "8.8.8.8"} {
		if !publicRoutingIPv4(address) {
			t.Fatalf("public address rejected: %s", address)
		}
	}
	for _, address := range []string{"100.64.0.1", "127.0.0.1", "192.168.1.1", "192.0.2.1", "::1", "invalid", "999.1.1.1"} {
		if publicRoutingIPv4(address) {
			t.Fatalf("invalid address accepted: %s", address)
		}
	}
	for _, endpoint := range []string{"https://1.1.1.1/dns-query", "https://dns.example.org/custom-path", "https://raw.githubusercontent.com/owner/repo/main/list.yaml"} {
		if !publicRoutingHTTPS(endpoint) {
			t.Fatalf("public URL rejected: %s", endpoint)
		}
	}
	for _, endpoint := range []string{"http://8.8.8.8/dns-query", "https://127.0.0.1/dns-query", "https://user:password@example.org/list", "https://example.org/list#DIRECT", "https://box.ts.net/list", "https://999.1.1.1/list", "https://example.org:7890/list"} {
		if publicRoutingHTTPS(endpoint) {
			t.Fatalf("unsafe URL accepted: %s", endpoint)
		}
	}
}
