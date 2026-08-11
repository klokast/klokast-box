// Package instancesource validates controller-produced private instance source receipts.
package instancesource

import (
	"bytes"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"

	"klokast-box/internal/contract"
)

const (
	Kind              = "klokast.instance-source.v1"
	MaximumReceipt    = 64 * 1024
	MaximumAge        = 30 * time.Minute
	MaximumFutureSkew = 5 * time.Minute
)

var (
	commitPattern      = regexp.MustCompile(`^[0-9a-f]{40}$`)
	digestPattern      = regexp.MustCompile(`^[0-9a-f]{64}$`)
	repositoryPattern  = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}$`)
	fingerprintPattern = regexp.MustCompile(`^SHA256:[A-Za-z0-9+/]{20,80}$`)
)

// Receipt proves which private Git remote commit the active controller fetched.
type Receipt struct {
	SchemaVersion         int    `json:"schema_version"`
	Kind                  string `json:"kind"`
	Repository            string `json:"repository"`
	RepositorySHA256      string `json:"repository_sha256"`
	RepositoryID          int64  `json:"repository_id"`
	RemoteRef             string `json:"remote_ref"`
	Commit                string `json:"commit"`
	FetchedAt             string `json:"fetched_at"`
	DeployKeyFingerprint  string `json:"deploy_key_fingerprint"`
	AnonymousReadable     bool   `json:"anonymous_readable"`
	AuthenticatedReadable bool   `json:"authenticated_readable"`
	ReceiptSHA256         string `json:"receipt_sha256"`
}

// Reference is the non-path provenance copied into Plan v1.
type Reference struct {
	ReceiptSHA256        string `json:"receipt_sha256"`
	RepositorySHA256     string `json:"repository_sha256"`
	RepositoryID         int64  `json:"repository_id"`
	RemoteRef            string `json:"remote_ref"`
	Commit               string `json:"commit"`
	FetchedAt            string `json:"fetched_at"`
	DeployKeyFingerprint string `json:"deploy_key_fingerprint"`
}

func (receipt Receipt) Reference() Reference {
	return Reference{
		ReceiptSHA256: receipt.ReceiptSHA256, RepositorySHA256: receipt.RepositorySHA256,
		RepositoryID: receipt.RepositoryID, RemoteRef: receipt.RemoteRef, Commit: receipt.Commit,
		FetchedAt: receipt.FetchedAt, DeployKeyFingerprint: receipt.DeployKeyFingerprint,
	}
}

// Load validates one bounded, regular, non-symlink receipt and its canonical hash.
func Load(path string, now time.Time) (Receipt, []contract.Diagnostic, error) {
	abs, err := filepath.Abs(path)
	if err != nil {
		return Receipt{}, nil, fmt.Errorf("resolve instance source receipt: %w", err)
	}
	info, err := os.Lstat(abs)
	if err != nil {
		return Receipt{}, nil, fmt.Errorf("inspect instance source receipt: %w", err)
	}
	if info.Mode()&os.ModeSymlink != 0 {
		return Receipt{}, one("path.symlink", "instance source receipt must not be a symbolic link"), nil
	}
	if !info.Mode().IsRegular() {
		return Receipt{}, one("path.type", "instance source receipt must be a regular file"), nil
	}
	if info.Size() <= 0 || info.Size() > MaximumReceipt {
		return Receipt{}, one("path.size", "instance source receipt must be non-empty and no larger than 64 KiB"), nil
	}
	file, err := os.Open(abs)
	if err != nil {
		return Receipt{}, nil, fmt.Errorf("open instance source receipt: %w", err)
	}
	defer file.Close()
	opened, err := file.Stat()
	if err != nil {
		return Receipt{}, nil, fmt.Errorf("inspect open instance source receipt: %w", err)
	}
	if !os.SameFile(info, opened) || !opened.Mode().IsRegular() {
		return Receipt{}, one("path.changed", "instance source receipt changed during inspection"), nil
	}
	content, err := io.ReadAll(io.LimitReader(file, MaximumReceipt+1))
	if err != nil {
		return Receipt{}, nil, fmt.Errorf("read instance source receipt: %w", err)
	}
	if len(content) > MaximumReceipt || bytes.IndexByte(content, 0) >= 0 {
		return Receipt{}, one("document.binary", "instance source receipt must be bounded JSON text"), nil
	}

	decoder := json.NewDecoder(bytes.NewReader(content))
	decoder.DisallowUnknownFields()
	var receipt Receipt
	if err := decoder.Decode(&receipt); err != nil {
		return Receipt{}, one("document.json", "instance source receipt must be one closed JSON object"), nil
	}
	var extra any
	if err := decoder.Decode(&extra); err != io.EOF {
		return Receipt{}, one("document.trailing", "instance source receipt must contain one JSON value"), nil
	}
	diagnostics := validate(receipt, content, now.UTC())
	return receipt, diagnostics, nil
}

func validate(receipt Receipt, content []byte, now time.Time) []contract.Diagnostic {
	diagnostics := []contract.Diagnostic{}
	add := func(code, message string) {
		diagnostics = append(diagnostics, contract.Diagnostic{Path: "instance-source-receipt", Code: code, Message: message})
	}
	if receipt.SchemaVersion != 1 || receipt.Kind != Kind {
		add("document.version", "instance source receipt kind or schema version is unsupported")
	}
	if !repositoryPattern.MatchString(receipt.Repository) {
		add("repository.name", "instance source repository must be one explicit GitHub owner/name")
	}
	wantRepository := sha256.Sum256([]byte(receipt.Repository))
	if !digestPattern.MatchString(receipt.RepositorySHA256) || receipt.RepositorySHA256 != fmt.Sprintf("%x", wantRepository[:]) {
		add("repository.hash", "instance source repository hash does not match its canonical owner/name")
	}
	if receipt.RepositoryID <= 0 {
		add("repository.id", "instance source repository ID must be positive")
	}
	if receipt.RemoteRef != "refs/heads/main" {
		add("repository.ref", "instance source remote ref must be refs/heads/main")
	}
	if !commitPattern.MatchString(receipt.Commit) {
		add("repository.commit", "instance source commit must be a full lowercase Git object ID")
	}
	if !fingerprintPattern.MatchString(receipt.DeployKeyFingerprint) {
		add("deploy-key.fingerprint", "instance source deploy-key fingerprint is invalid")
	}
	if receipt.AnonymousReadable {
		add("repository.public", "instance source repository must not permit anonymous Git reads")
	}
	if !receipt.AuthenticatedReadable {
		add("repository.unreadable", "instance source deploy key did not authenticate a Git read")
	}
	fetched, err := time.Parse(time.RFC3339, receipt.FetchedAt)
	if err != nil || !strings.HasSuffix(receipt.FetchedAt, "Z") {
		add("fetched-at.format", "instance source fetch time must be UTC RFC3339 with Z")
	} else {
		if fetched.After(now.Add(MaximumFutureSkew)) {
			add("fetched-at.future", "instance source receipt is more than five minutes in the future")
		}
		if fetched.Before(now.Add(-MaximumAge)) {
			add("fetched-at.stale", "instance source receipt is more than 30 minutes old")
		}
	}
	actual, err := hashContent(content)
	if err != nil || !digestPattern.MatchString(receipt.ReceiptSHA256) || receipt.ReceiptSHA256 != actual {
		add("receipt.hash", "instance source receipt hash does not match its canonical content")
	}
	sort.Slice(diagnostics, func(i, j int) bool { return diagnostics[i].Code < diagnostics[j].Code })
	return diagnostics
}

func hashContent(content []byte) (string, error) {
	decoder := json.NewDecoder(bytes.NewReader(content))
	decoder.UseNumber()
	var value map[string]any
	if err := decoder.Decode(&value); err != nil {
		return "", err
	}
	delete(value, "receipt_sha256")
	canonical, err := json.Marshal(value)
	if err != nil {
		return "", err
	}
	digest := sha256.Sum256(canonical)
	return fmt.Sprintf("%x", digest[:]), nil
}

func one(code, message string) []contract.Diagnostic {
	return []contract.Diagnostic{{Path: "instance-source-receipt", Code: code, Message: message}}
}
