package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"html/template"
	"log"
	"net/http"
	"os"
	"os/exec"
	"regexp"
	"sort"
	"strings"
	"sync"
	"time"
)

const bootstrapTag = "tag:bootstrap"

var (
	dnsLabelPattern = regexp.MustCompile(`^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$`)
	authKeyPattern  = regexp.MustCompile(`^tskey-auth-[A-Za-z0-9._:-]+$`)
	secretPattern   = regexp.MustCompile(`tskey-auth-[A-Za-z0-9._:-]+`)
	errNameInUse    = errors.New("box name is already visible")
)

type commandRunner interface {
	Run(ctx context.Context, name string, args ...string) (string, string, error)
}

type osRunner struct{}

func (osRunner) Run(ctx context.Context, name string, args ...string) (string, string, error) {
	cmd := exec.CommandContext(ctx, name, args...)
	var stdout strings.Builder
	var stderr strings.Builder
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	err := cmd.Run()
	return stdout.String(), stderr.String(), err
}

type enrollmentState struct {
	Enrolled                bool
	Retry                   bool
	InProgress              bool
	Hostname                string
	TailscaleIP             string
	Status                  string
	Error                   string
	VisibleMachineNames     []string
	VisibleMachineNamesJSON template.JS
}

type tailscaleMachine struct {
	HostName     string   `json:"HostName"`
	DNSName      string   `json:"DNSName"`
	TailscaleIPs []string `json:"TailscaleIPs"`
	Tags         []string `json:"Tags"`
}

type tailscaleStatus struct {
	BackendState string                      `json:"BackendState"`
	Self         tailscaleMachine            `json:"Self"`
	Peer         map[string]tailscaleMachine `json:"Peer"`
}

type tailscaleSnapshot struct {
	BackendState        string
	SelfName            string
	SelfDNSName         string
	SelfTags            []string
	SelfTailscaleIPs    []string
	VisibleMachineNames []string
}

type portalServer struct {
	runner commandRunner
	mu     sync.Mutex
	state  enrollmentState
}

func newPortalServer(runner commandRunner) *portalServer {
	return &portalServer{runner: runner}
}

func main() {
	listen := flag.String("listen", ":80", "HTTP listen address")
	flag.Parse()

	server := newPortalServer(osRunner{})
	mux := http.NewServeMux()
	mux.HandleFunc("/", server.handleIndex)
	mux.HandleFunc("/enroll", server.handleEnroll)

	httpServer := &http.Server{
		Addr:              *listen,
		Handler:           mux,
		ReadHeaderTimeout: 10 * time.Second,
	}

	log.Printf("klokast onboarding portal listening on %s", *listen)
	if err := httpServer.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		log.Printf("portal failed: %v", err)
		os.Exit(1)
	}
}

func (s *portalServer) handleIndex(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path != "/" {
		http.NotFound(w, r)
		return
	}
	if r.Method != http.MethodGet {
		w.Header().Set("Allow", http.MethodGet)
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	s.render(w, http.StatusOK, "")
}

func (s *portalServer) handleEnroll(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		w.Header().Set("Allow", http.MethodPost)
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	if err := r.ParseForm(); err != nil {
		s.render(w, http.StatusBadRequest, "Invalid form submission.")
		return
	}

	node := strings.ToLower(strings.TrimSpace(r.FormValue("node")))
	authKey := strings.TrimSpace(r.FormValue("auth_key"))

	if err := validateNode(node); err != nil {
		s.render(w, http.StatusBadRequest, err.Error())
		return
	}

	if s.isEnrolled() {
		http.Redirect(w, r, "/", http.StatusSeeOther)
		return
	}

	var hostname string
	var message string
	var status int
	var err error
	if s.isRetrying() {
		hostname, message, status, err = s.rename(r.Context(), node)
	} else {
		if err := validateAuthKey(authKey); err != nil {
			s.render(w, http.StatusBadRequest, err.Error())
			return
		}
		hostname, message, status, err = s.enroll(r.Context(), node, authKey)
	}
	if err != nil {
		s.render(w, status, message)
		return
	}

	log.Printf("tailscale enrollment succeeded for %s", hostname)
	http.Redirect(w, r, "/", http.StatusSeeOther)
}

func (s *portalServer) isEnrolled() bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.state.Enrolled
}

func (s *portalServer) isRetrying() bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.state.Retry
}

func (s *portalServer) enroll(ctx context.Context, node string, authKey string) (string, string, int, error) {
	hostname := node + "-bootstrap"

	s.mu.Lock()
	if s.state.Enrolled {
		s.mu.Unlock()
		return s.state.Hostname, "", http.StatusOK, nil
	}
	if s.state.InProgress {
		s.mu.Unlock()
		return hostname, "Enrollment is already in progress.", http.StatusConflict, errors.New("enrollment already in progress")
	}
	s.state.InProgress = true
	s.state.Error = ""
	s.mu.Unlock()

	clearProgress := func() {
		s.mu.Lock()
		s.state.InProgress = false
		s.mu.Unlock()
	}
	defer clearProgress()

	if stdout, stderr, err := s.runner.Run(ctx, "systemctl", "start", "tailscaled.service"); err != nil {
		message := "Could not start tailscaled: " + redactSecrets(combineOutput(stdout, stderr), authKey)
		s.setError(message)
		return hostname, message, http.StatusInternalServerError, err
	}

	if stdout, stderr, err := s.runner.Run(ctx, "tailscale", buildTailscaleUpArgs(hostname, authKey)...); err != nil {
		message := strings.TrimSpace(redactSecrets(combineOutput(stdout, stderr), authKey))
		if message == "" {
			message = err.Error()
		}
		message = "Tailscale enrollment failed: " + message + " Use an auth key generated by a tailnet Owner/Admin/Network admin that can assign tag:bootstrap."
		s.setError(message)
		return hostname, message, http.StatusBadGateway, err
	}

	return s.finishEnrollmentAttempt(ctx, node, authKey)
}

func (s *portalServer) rename(ctx context.Context, node string) (string, string, int, error) {
	hostname := node + "-bootstrap"

	s.mu.Lock()
	if s.state.Enrolled {
		s.mu.Unlock()
		return s.state.Hostname, "", http.StatusOK, nil
	}
	if !s.state.Retry {
		s.mu.Unlock()
		return hostname, "Tailscale auth key is required.", http.StatusBadRequest, errors.New("auth key required")
	}
	if s.state.InProgress {
		s.mu.Unlock()
		return hostname, "Enrollment is already in progress.", http.StatusConflict, errors.New("enrollment already in progress")
	}
	s.state.InProgress = true
	s.state.Error = ""
	s.mu.Unlock()

	clearProgress := func() {
		s.mu.Lock()
		s.state.InProgress = false
		s.mu.Unlock()
	}
	defer clearProgress()

	snapshot, message, err := s.readTailscaleSnapshot(ctx, "")
	if err != nil {
		message = "Could not read Tailscale status: " + message
		s.setRetryState(node, hostname, snapshot, message)
		return hostname, message, http.StatusBadGateway, err
	}
	if conflict := visibleNameConflict(snapshot.VisibleMachineNames, node, ""); conflict != "" {
		message = nameInUseMessage(node, hostname, hostname, conflict)
		s.setRetryState(node, snapshot.SelfName, snapshot, message)
		return hostname, message, http.StatusConflict, errNameInUse
	}

	if stdout, stderr, err := s.runner.Run(ctx, "tailscale", buildTailscaleSetHostnameArgs(hostname)...); err != nil {
		message := strings.TrimSpace(redactSecrets(combineOutput(stdout, stderr)))
		if message == "" {
			message = err.Error()
		}
		message = "Tailscale rename failed: " + message
		s.setRetryState(node, snapshot.SelfName, snapshot, message)
		return hostname, message, http.StatusBadGateway, err
	}

	return s.finishEnrollmentAttempt(ctx, node, "")
}

func (s *portalServer) finishEnrollmentAttempt(ctx context.Context, node string, secret string) (string, string, int, error) {
	expectedHostname := node + "-bootstrap"
	snapshot, message, err := s.readTailscaleSnapshot(ctx, secret)
	if err != nil {
		message = "Tailscale is logged in, but status could not be read: " + message
		s.setRetryState(node, expectedHostname, snapshot, message)
		return expectedHostname, message, http.StatusBadGateway, err
	}

	state := enrollmentState{
		Hostname:            expectedHostname,
		TailscaleIP:         s.readTailscaleIP(ctx, secret, snapshot),
		Status:              summarizeSnapshot(snapshot),
		VisibleMachineNames: snapshot.VisibleMachineNames,
	}

	if snapshot.SelfName != expectedHostname {
		message := nameInUseMessage(node, expectedHostname, snapshot.SelfName, snapshot.SelfName)
		state.Retry = true
		state.Hostname = snapshot.SelfName
		state.Error = message
		s.setState(state)
		return snapshot.SelfName, message, http.StatusConflict, errNameInUse
	}

	if conflict := visibleNameConflict(snapshot.VisibleMachineNames, node, expectedHostname); conflict != "" {
		message := nameInUseMessage(node, expectedHostname, snapshot.SelfName, conflict)
		state.Retry = true
		state.Error = message
		s.setState(state)
		return expectedHostname, message, http.StatusConflict, errNameInUse
	}

	state.Enrolled = true
	s.setState(state)
	return expectedHostname, "", http.StatusOK, nil
}

func (s *portalServer) setError(message string) {
	s.mu.Lock()
	s.state.Error = message
	s.mu.Unlock()
}

func (s *portalServer) setRetryState(node string, fallbackHostname string, snapshot tailscaleSnapshot, message string) {
	hostname := snapshot.SelfName
	if hostname == "" {
		hostname = fallbackHostname
	}
	s.setState(enrollmentState{
		Retry:               true,
		Hostname:            hostname,
		Status:              summarizeSnapshot(snapshot),
		VisibleMachineNames: snapshot.VisibleMachineNames,
		Error:               message,
	})
}

func (s *portalServer) setState(state enrollmentState) {
	s.mu.Lock()
	s.state = state
	s.mu.Unlock()
}

func (s *portalServer) render(w http.ResponseWriter, status int, message string) {
	s.mu.Lock()
	state := s.state
	s.mu.Unlock()

	if message != "" {
		state.Error = message
	}
	state.VisibleMachineNamesJSON = jsonForTemplate(state.VisibleMachineNames)

	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.WriteHeader(status)
	if err := pageTemplate.Execute(w, state); err != nil {
		log.Printf("render failed: %v", err)
	}
}

func validateNode(node string) error {
	if node == "" {
		return errors.New("Node is required.")
	}
	if !dnsLabelPattern.MatchString(node) {
		return errors.New("Node must be a DNS label using lowercase letters, numbers, and interior hyphens.")
	}
	return nil
}

func validateAuthKey(authKey string) error {
	if authKey == "" {
		return errors.New("Tailscale auth key is required.")
	}
	if !authKeyPattern.MatchString(authKey) {
		return errors.New("Tailscale auth key must be a tskey-auth-* value.")
	}
	return nil
}

func buildTailscaleUpArgs(hostname string, authKey string) []string {
	return []string{
		"up",
		"--ssh",
		"--hostname=" + hostname,
		"--advertise-tags=" + bootstrapTag,
		"--auth-key=" + authKey,
	}
}

func buildTailscaleSetHostnameArgs(hostname string) []string {
	return []string{
		"set",
		"--hostname=" + hostname,
	}
}

func (s *portalServer) readTailscaleSnapshot(ctx context.Context, secret string) (tailscaleSnapshot, string, error) {
	stdout, stderr, err := s.runner.Run(ctx, "tailscale", "status", "--json")
	if err != nil {
		message := strings.TrimSpace(redactSecrets(combineOutput(stdout, stderr), secret))
		if message == "" {
			message = err.Error()
		}
		return tailscaleSnapshot{}, message, err
	}

	snapshot, err := parseTailscaleStatus(stdout)
	if err != nil {
		return tailscaleSnapshot{}, "Could not parse Tailscale status.", err
	}
	return snapshot, "", nil
}

func (s *portalServer) readTailscaleIP(ctx context.Context, secret string, snapshot tailscaleSnapshot) string {
	if len(snapshot.SelfTailscaleIPs) > 0 {
		return snapshot.SelfTailscaleIPs[0]
	}
	stdout, stderr, err := s.runner.Run(ctx, "tailscale", "ip")
	if err == nil {
		return firstLine(stdout)
	}
	return strings.TrimSpace(redactSecrets(stderr, secret))
}

func redactSecrets(value string, secrets ...string) string {
	redacted := value
	for _, secret := range secrets {
		if secret != "" {
			redacted = strings.ReplaceAll(redacted, secret, "[redacted]")
		}
	}
	return secretPattern.ReplaceAllString(redacted, "[redacted]")
}

func firstLine(value string) string {
	for _, line := range strings.Split(value, "\n") {
		line = strings.TrimSpace(line)
		if line != "" {
			return line
		}
	}
	return ""
}

func combineOutput(stdout string, stderr string) string {
	switch {
	case stdout == "":
		return stderr
	case stderr == "":
		return stdout
	default:
		return stdout + "\n" + stderr
	}
}

func summarizeStatus(statusJSON string) string {
	snapshot, err := parseTailscaleStatus(statusJSON)
	if err != nil {
		return firstLine(statusJSON)
	}
	return summarizeSnapshot(snapshot)
}

func summarizeSnapshot(snapshot tailscaleSnapshot) string {
	parts := []string{}
	if snapshot.BackendState != "" {
		parts = append(parts, snapshot.BackendState)
	}
	if snapshot.SelfDNSName != "" {
		parts = append(parts, snapshot.SelfDNSName)
	} else if snapshot.SelfName != "" {
		parts = append(parts, snapshot.SelfName)
	}
	if len(snapshot.SelfTags) > 0 {
		parts = append(parts, strings.Join(snapshot.SelfTags, ", "))
	}
	if len(parts) == 0 {
		return "Running"
	}
	return strings.Join(parts, " | ")
}

func parseTailscaleStatus(statusJSON string) (tailscaleSnapshot, error) {
	var parsed tailscaleStatus
	if err := json.Unmarshal([]byte(statusJSON), &parsed); err != nil {
		return tailscaleSnapshot{}, err
	}

	visible := map[string]struct{}{}
	selfName := normalizeMachineName(parsed.Self)
	if selfName != "" {
		visible[selfName] = struct{}{}
	}
	for _, peer := range parsed.Peer {
		name := normalizeMachineName(peer)
		if name != "" {
			visible[name] = struct{}{}
		}
	}

	visibleNames := make([]string, 0, len(visible))
	for name := range visible {
		visibleNames = append(visibleNames, name)
	}
	sort.Strings(visibleNames)

	return tailscaleSnapshot{
		BackendState:        parsed.BackendState,
		SelfName:            selfName,
		SelfDNSName:         normalizeDNSName(parsed.Self.DNSName),
		SelfTags:            append([]string{}, parsed.Self.Tags...),
		SelfTailscaleIPs:    append([]string{}, parsed.Self.TailscaleIPs...),
		VisibleMachineNames: visibleNames,
	}, nil
}

func normalizeMachineName(machine tailscaleMachine) string {
	if dnsName := normalizeDNSName(machine.DNSName); dnsName != "" {
		return firstDNSLabel(dnsName)
	}
	return firstDNSLabel(strings.ToLower(strings.TrimSpace(strings.TrimSuffix(machine.HostName, "."))))
}

func normalizeDNSName(name string) string {
	return strings.ToLower(strings.TrimSpace(strings.TrimSuffix(name, ".")))
}

func firstDNSLabel(name string) string {
	name = strings.TrimSpace(strings.TrimSuffix(name, "."))
	if name == "" {
		return ""
	}
	if index := strings.Index(name, "."); index >= 0 {
		return name[:index]
	}
	return name
}

func visibleNameConflict(visibleNames []string, node string, allowedSelf string) string {
	prefix := node + "-"
	for _, name := range visibleNames {
		name = strings.ToLower(strings.TrimSpace(name))
		if name == "" || name == allowedSelf {
			continue
		}
		if name == node || strings.HasPrefix(name, prefix) {
			return name
		}
	}
	return ""
}

func nameInUseMessage(node string, expected string, assigned string, conflict string) string {
	if assigned != "" && assigned != expected {
		return "Tailscale assigned " + assigned + " instead of " + expected + ". Choose another box name."
	}
	if conflict != "" {
		return "Box name " + node + " is already visible in Tailscale as " + conflict + ". Choose another box name."
	}
	return "Box name " + node + " is already visible in Tailscale. Choose another box name."
}

func jsonForTemplate(values []string) template.JS {
	data, err := json.Marshal(values)
	if err != nil {
		return template.JS("[]")
	}
	return template.JS(data)
}

var pageTemplate = template.Must(template.New("page").Parse(`<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Klokast Bootstrap</title>
  <style>
    :root { color-scheme: light dark; font-family: system-ui, sans-serif; }
    body { margin: 0; min-height: 100vh; display: grid; place-items: center; background: #f5f7f8; color: #172026; }
    main { width: min(92vw, 34rem); background: #fff; border: 1px solid #d8dee3; border-radius: 8px; padding: 1.5rem; box-shadow: 0 18px 50px rgba(23, 32, 38, 0.12); }
    h1 { margin: 0 0 1rem; font-size: 1.45rem; line-height: 1.2; }
    label { display: block; margin: 1rem 0 0.35rem; font-weight: 650; }
    input { box-sizing: border-box; width: 100%; padding: 0.7rem 0.75rem; border: 1px solid #bac4cc; border-radius: 6px; font: inherit; }
    button { margin-top: 1.2rem; width: 100%; padding: 0.75rem; border: 0; border-radius: 6px; background: #116149; color: white; font: inherit; font-weight: 700; cursor: pointer; }
    button[disabled], input[disabled] { opacity: 0.62; cursor: not-allowed; }
    .status { margin-top: 1rem; padding: 0.8rem; border-radius: 6px; background: #e9f4ef; border: 1px solid #b9d8ca; }
    .error { margin-top: 1rem; padding: 0.8rem; border-radius: 6px; background: #fff0ed; border: 1px solid #efb5aa; color: #7e2416; }
    .feedback { min-height: 1.2rem; margin-top: 0.35rem; color: #7e2416; font-size: 0.92rem; }
    .row { margin: 0.3rem 0; overflow-wrap: anywhere; }
    @media (prefers-color-scheme: dark) {
      body { background: #11181d; color: #edf2f4; }
      main { background: #172026; border-color: #34434c; box-shadow: none; }
      input { background: #0e1519; border-color: #4a5b65; color: #edf2f4; }
      .status { background: #16372d; border-color: #2a6b55; }
      .error { background: #351a17; border-color: #7e3328; color: #ffd7d0; }
      .feedback { color: #ffd7d0; }
    }
  </style>
</head>
<body>
  <main>
    <h1>Klokast Bootstrap</h1>
    {{if .Enrolled}}
      <div class="status">
        <div class="row"><strong>Machine:</strong> {{.Hostname}}</div>
        <div class="row"><strong>Tailscale IP:</strong> {{.TailscaleIP}}</div>
        <div class="row"><strong>Status:</strong> {{.Status}}</div>
      </div>
    {{else}}
      <form method="post" action="/enroll" autocomplete="off">
        <label for="node">Choose a box name:</label>
        <input id="node" name="node" inputmode="latin" pattern="[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?" required {{if .InProgress}}disabled{{end}}>
        <div id="node_feedback" class="feedback" aria-live="polite"></div>
        {{if not .Retry}}
        <label for="auth_key">Tailscale auth key</label>
        <input id="auth_key" name="auth_key" type="password" required {{if .InProgress}}disabled{{end}}>
        {{end}}
        <button type="submit" {{if .InProgress}}disabled{{end}}>{{if .Retry}}Change Box Name{{else}}Enroll Bootstrap Host{{end}}</button>
      </form>
    {{end}}
    {{if .Error}}<div class="error">{{.Error}}</div>{{end}}
  </main>
  <script>
    (() => {
      const visibleNames = {{.VisibleMachineNamesJSON}};
      const input = document.getElementById("node");
      const feedback = document.getElementById("node_feedback");
      if (!input || !feedback || !Array.isArray(visibleNames) || visibleNames.length === 0) {
        return;
      }
      const validate = () => {
        const value = input.value.trim().toLowerCase();
        const prefix = value + "-";
        const conflict = value === "" ? "" : visibleNames.find((name) => name === value || name.startsWith(prefix));
        const message = conflict ? "Box name already appears as " + conflict + "." : "";
        input.setCustomValidity(message);
        feedback.textContent = message;
      };
      input.addEventListener("input", validate);
      validate();
    })();
  </script>
</body>
</html>
`))
