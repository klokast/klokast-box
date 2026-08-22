package main

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

type commandCall struct {
	name string
	args []string
}

type fakeRunner struct {
	calls   []commandCall
	fail    map[string]error
	errs    map[string]string
	outs    map[string]string
	outSeqs map[string][]string
}

func (r *fakeRunner) Run(_ context.Context, name string, args ...string) (string, string, error) {
	copiedArgs := append([]string{}, args...)
	r.calls = append(r.calls, commandCall{name: name, args: copiedArgs})
	key := name + " " + strings.Join(args, " ")
	if err := r.fail[key]; err != nil {
		return "", r.errs[key], err
	}
	if seq := r.outSeqs[key]; len(seq) > 0 {
		out := seq[0]
		r.outSeqs[key] = seq[1:]
		return out, r.errs[key], nil
	}
	return r.outs[key], r.errs[key], nil
}

func TestValidateNode(t *testing.T) {
	valid := []string{"duh", "yii", "b01", "box-1"}
	for _, node := range valid {
		if err := validateNode(node); err != nil {
			t.Fatalf("expected %q to be valid: %v", node, err)
		}
	}

	invalid := []string{"", "-duh", "duh-", "duh_box", "Duh", strings.Repeat("a", 64)}
	for _, node := range invalid {
		if err := validateNode(node); err == nil {
			t.Fatalf("expected %q to be invalid", node)
		}
	}
}

func TestValidateAuthKey(t *testing.T) {
	if err := validateAuthKey("tskey-auth-abc123_XYZ:-."); err != nil {
		t.Fatalf("expected auth key to be valid: %v", err)
	}

	for _, key := range []string{"", "tskey-client-abc", " tskey-auth-abc", "tskey-auth-abc value"} {
		if err := validateAuthKey(key); err == nil {
			t.Fatalf("expected %q to be invalid", key)
		}
	}
}

func TestBuildTailscaleUpArgsAdvertisesBootstrapTag(t *testing.T) {
	args := buildTailscaleUpArgs("duh-bootstrap", "tskey-auth-secret")
	command := strings.Join(args, " ")

	for _, want := range []string{
		"up",
		"--ssh",
		"--hostname=duh-bootstrap",
		"--advertise-tags=tag:bootstrap",
		"--auth-key=tskey-auth-secret",
	} {
		if !strings.Contains(command, want) {
			t.Fatalf("expected %q in args %s", want, command)
		}
	}
}

func TestDuplicateSubmitDoesNotRunTailscaleTwice(t *testing.T) {
	runner := &fakeRunner{
		outs: map[string]string{
			"tailscale ip":            "100.64.0.10\nfd7a:115c:a1e0::10\n",
			"tailscale status --json": `{"BackendState":"Running","Self":{"HostName":"duh-bootstrap","DNSName":"duh-bootstrap.example.ts.net.","TailscaleIPs":["100.64.0.10"],"Tags":["tag:bootstrap"]}}`,
		},
	}
	server := newPortalServer(runner)

	if _, _, status, err := server.enroll(context.Background(), "duh", "tskey-auth-first"); err != nil || status != http.StatusOK {
		t.Fatalf("first enroll failed: status=%d err=%v", status, err)
	}
	if _, _, status, err := server.enroll(context.Background(), "duh", "tskey-auth-second"); err != nil || status != http.StatusOK {
		t.Fatalf("duplicate enroll should be accepted without rerun: status=%d err=%v", status, err)
	}

	var upCalls int
	for _, call := range runner.calls {
		if call.name == "tailscale" && len(call.args) > 0 && call.args[0] == "up" {
			upCalls++
		}
	}
	if upCalls != 1 {
		t.Fatalf("expected one tailscale up call, got %d", upCalls)
	}
}

func TestSuffixedSelfNameEntersRetryState(t *testing.T) {
	runner := &fakeRunner{
		outs: map[string]string{
			"tailscale status --json": `{"BackendState":"Running","Self":{"HostName":"boxa-bootstrap-1","DNSName":"boxa-bootstrap-1.example.ts.net.","TailscaleIPs":["100.64.0.10"],"Tags":["tag:bootstrap"]}}`,
		},
	}
	server := newPortalServer(runner)

	_, message, status, err := server.enroll(context.Background(), "boxa", "tskey-auth-first")
	if !errors.Is(err, errNameInUse) || status != http.StatusConflict {
		t.Fatalf("expected name conflict, status=%d err=%v", status, err)
	}
	if !strings.Contains(message, "boxa-bootstrap-1") {
		t.Fatalf("expected assigned suffix in message, got %q", message)
	}
	if !server.state.Retry || server.state.Enrolled {
		t.Fatalf("expected retry state without final enrollment: %+v", server.state)
	}
}

func TestVisiblePeerPrefixRejectsBoxName(t *testing.T) {
	runner := &fakeRunner{
		outs: map[string]string{
			"tailscale status --json": `{"BackendState":"Running","Self":{"HostName":"boxa-bootstrap","DNSName":"boxa-bootstrap.example.ts.net.","TailscaleIPs":["100.64.0.10"],"Tags":["tag:bootstrap"]},"Peer":{"nodekey:1":{"HostName":"boxa-dom0","DNSName":"boxa-dom0.example.ts.net."}}}`,
		},
	}
	server := newPortalServer(runner)

	_, message, status, err := server.enroll(context.Background(), "boxa", "tskey-auth-first")
	if !errors.Is(err, errNameInUse) || status != http.StatusConflict {
		t.Fatalf("expected visible peer conflict, status=%d err=%v", status, err)
	}
	if !strings.Contains(message, "boxa-dom0") {
		t.Fatalf("expected peer name in message, got %q", message)
	}
}

func TestRetryUsesSetHostnameWithoutAuthKey(t *testing.T) {
	runner := &fakeRunner{
		outSeqs: map[string][]string{
			"tailscale status --json": {
				`{"BackendState":"Running","Self":{"HostName":"boxa-bootstrap-1","DNSName":"boxa-bootstrap-1.example.ts.net.","TailscaleIPs":["100.64.0.10"],"Tags":["tag:bootstrap"]}}`,
				`{"BackendState":"Running","Self":{"HostName":"boxb-bootstrap","DNSName":"boxb-bootstrap.example.ts.net.","TailscaleIPs":["100.64.0.11"],"Tags":["tag:bootstrap"]}}`,
			},
		},
	}
	server := newPortalServer(runner)
	server.state = enrollmentState{Retry: true, Hostname: "boxa-bootstrap-1", VisibleMachineNames: []string{"boxa-bootstrap-1"}}

	_, _, status, err := server.rename(context.Background(), "boxb")
	if err != nil || status != http.StatusOK {
		t.Fatalf("retry rename failed: status=%d err=%v", status, err)
	}
	if !server.state.Enrolled || server.state.Hostname != "boxb-bootstrap" {
		t.Fatalf("expected final enrollment as boxb-bootstrap: %+v", server.state)
	}

	var upCalls int
	var setCalls int
	for _, call := range runner.calls {
		if call.name != "tailscale" || len(call.args) == 0 {
			continue
		}
		switch call.args[0] {
		case "up":
			upCalls++
		case "set":
			setCalls++
			command := strings.Join(call.args, " ")
			if strings.Contains(command, "tskey-auth-") {
				t.Fatalf("retry set command must not include auth key: %s", command)
			}
			if !strings.Contains(command, "--hostname=boxb-bootstrap") {
				t.Fatalf("expected retry set hostname, got %s", command)
			}
		}
	}
	if upCalls != 0 || setCalls != 1 {
		t.Fatalf("expected no tailscale up and one tailscale set, got up=%d set=%d", upCalls, setCalls)
	}
}

func TestRetryRejectsVisibleNameBeforeSet(t *testing.T) {
	runner := &fakeRunner{
		outs: map[string]string{
			"tailscale status --json": `{"BackendState":"Running","Self":{"HostName":"boxa-bootstrap-1","DNSName":"boxa-bootstrap-1.example.ts.net.","TailscaleIPs":["100.64.0.10"],"Tags":["tag:bootstrap"]},"Peer":{"nodekey:1":{"HostName":"boxb-dom0","DNSName":"boxb-dom0.example.ts.net."}}}`,
		},
	}
	server := newPortalServer(runner)
	server.state = enrollmentState{Retry: true, Hostname: "boxa-bootstrap-1", VisibleMachineNames: []string{"boxa-bootstrap-1", "boxb-dom0"}}

	_, message, status, err := server.rename(context.Background(), "boxb")
	if !errors.Is(err, errNameInUse) || status != http.StatusConflict {
		t.Fatalf("expected visible conflict before rename, status=%d err=%v", status, err)
	}
	if !strings.Contains(message, "boxb-dom0") {
		t.Fatalf("expected conflict name in message, got %q", message)
	}
	for _, call := range runner.calls {
		if call.name == "tailscale" && len(call.args) > 0 && call.args[0] == "set" {
			t.Fatal("tailscale set should not run for a visible conflict")
		}
	}
}

func TestRetryPageRendersClientValidationDataWithoutAuthKey(t *testing.T) {
	server := newPortalServer(&fakeRunner{})
	server.state = enrollmentState{
		Retry:               true,
		Hostname:            "boxa-bootstrap-1",
		VisibleMachineNames: []string{"boxa-bootstrap-1", "boxb-dom0"},
	}

	request := httptest.NewRequest(http.MethodGet, "/", nil)
	response := httptest.NewRecorder()
	server.handleIndex(response, request)

	body := response.Body.String()
	if strings.Contains(body, `name="auth_key"`) {
		t.Fatalf("retry page must not ask for auth key: %s", body)
	}
	if !strings.Contains(body, `const visibleNames = ["boxa-bootstrap-1","boxb-dom0"];`) {
		t.Fatalf("expected visible names in client validation script: %s", body)
	}
}

func TestSecretRedaction(t *testing.T) {
	secret := "tskey-auth-super-secret"
	output := redactSecrets("tailscale rejected tskey-auth-other and "+secret, secret)

	if strings.Contains(output, "tskey-auth-") || strings.Contains(output, secret) {
		t.Fatalf("expected auth keys to be redacted, got %q", output)
	}
}

func TestEnrollFailureRedactsAuthKey(t *testing.T) {
	secret := "tskey-auth-super-secret"
	key := "tailscale up --ssh --hostname=duh-bootstrap --advertise-tags=tag:bootstrap --auth-key=" + secret
	runner := &fakeRunner{
		fail: map[string]error{key: errors.New("tailscale failed")},
		errs: map[string]string{key: "tag assignment failed for " + secret},
	}
	server := newPortalServer(runner)

	_, message, _, err := server.enroll(context.Background(), "duh", secret)
	if err == nil {
		t.Fatal("expected enrollment failure")
	}
	if strings.Contains(message, secret) || strings.Contains(message, "tskey-auth-") {
		t.Fatalf("expected redacted failure, got %q", message)
	}
	if !strings.Contains(message, "tag:bootstrap") {
		t.Fatalf("expected tag assignment guidance, got %q", message)
	}
}
