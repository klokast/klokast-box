package contract

import (
	"net/netip"
	"net/url"
	"strings"
)

func publicRoutingIPv4(value string) bool {
	address, err := netip.ParseAddr(value)
	if err != nil || !address.Is4() || !address.IsGlobalUnicast() || address.IsPrivate() {
		return false
	}
	for _, reserved := range []string{"0.0.0.0/8", "100.64.0.0/10", "192.0.0.0/24", "192.0.2.0/24", "198.18.0.0/15", "198.51.100.0/24", "203.0.113.0/24", "240.0.0.0/4"} {
		if netip.MustParsePrefix(reserved).Contains(address) {
			return false
		}
	}
	return true
}

func publicRoutingHTTPS(value string) bool {
	u, err := url.Parse(value)
	if err != nil || u.Scheme != "https" || u.User != nil || u.Fragment != "" || strings.ContainsAny(value, "#\r\n\t ") || (u.Port() != "" && u.Port() != "443") {
		return false
	}
	host := strings.ToLower(u.Hostname())
	if address, err := netip.ParseAddr(host); err == nil {
		return address.Is4() && publicRoutingIPv4(host)
	}
	if !strings.Contains(host, ".") || strings.HasSuffix(host, ".") {
		return false
	}
	for _, suffix := range []string{"local", "lan", "localhost", "ts.net", "tailscale.com", "internal", "home.arpa"} {
		if host == suffix || strings.HasSuffix(host, "."+suffix) {
			return false
		}
	}
	for _, label := range strings.Split(host, ".") {
		if len(label) == 0 || len(label) > 63 || label[0] == '-' || label[len(label)-1] == '-' {
			return false
		}
		for _, char := range label {
			if !(char >= 'a' && char <= 'z' || char >= '0' && char <= '9' || char == '-') {
				return false
			}
		}
	}
	// Malformed numeric IPv4 names must not be treated as DNS names.
	return len(host) <= 253 && strings.Trim(host, "0123456789.") != ""
}

func validRoutingProfile(p *RoutingProfileDocument) bool {
	if p == nil {
		return false
	}
	if len(p.BootstrapDNS) != 2 || p.BootstrapDNS[0] == p.BootstrapDNS[1] || len(p.VPNDNS) != 2 || p.VPNDNS[0] == p.VPNDNS[1] {
		return false
	}
	for _, address := range p.BootstrapDNS {
		if !publicRoutingIPv4(address) {
			return false
		}
	}
	for _, endpoint := range p.VPNDNS {
		if !publicRoutingHTTPS(endpoint) {
			return false
		}
	}
	return p.DomainListURL == nil || publicRoutingHTTPS(*p.DomainListURL)
}
