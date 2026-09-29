// Verify one exact Tailscale stable archive with Tailscale's own distsign code.
// This tool has no Platform credentials or authority to install the archive.
package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"syscall"
	"time"

	"tailscale.com/clientupdate/distsign"
)

var namePattern = regexp.MustCompile(`^tailscale_[0-9]+\.[0-9]+\.[0-9]+_amd64\.tgz$`)

type result struct {
	Kind      string `json:"kind"`
	Archive   string `json:"archive"`
	SHA256    string `json:"sha256"`
	Bytes     int64  `json:"bytes"`
	Verifier  string `json:"verifier"`
	Signed    bool   `json:"signed"`
}

func run(args []string, output io.Writer) error {
	flags := flag.NewFlagSet("tailscale-distsign", flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	name := flags.String("archive", "", "exact stable amd64 archive name")
	directory := flags.String("directory", "", "new private empty output directory")
	if err := flags.Parse(args); err != nil || flags.NArg() != 0 || !namePattern.MatchString(*name) || *directory == "" {
		return fmt.Errorf("require --archive tailscale_VERSION_amd64.tgz and --directory NEW_PRIVATE_DIRECTORY")
	}
	info, err := os.Lstat(*directory)
	if err != nil || !info.IsDir() || info.Mode().Perm() != 0700 {
		return fmt.Errorf("output directory must be an existing private directory")
	}
	owner, ok := info.Sys().(*syscall.Stat_t)
	if !ok || owner.Uid != uint32(os.Getuid()) {
		return fmt.Errorf("output directory must belong to the current user")
	}
	entries, err := os.ReadDir(*directory)
	if err != nil || len(entries) != 0 {
		return fmt.Errorf("output directory must be empty")
	}
	syscall.Umask(0077)
	http.DefaultClient.Timeout = 180 * time.Second
	client, err := distsign.NewClient(func(string, ...any) {}, "https://pkgs.tailscale.com/")
	if err != nil {
		return fmt.Errorf("cannot create upstream verifier: %w", err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 180*time.Second)
	defer cancel()
	path := filepath.Join(*directory, *name)
	if err := client.Download(ctx, "stable/"+*name, path); err != nil {
		_ = os.Remove(path)
		_ = os.Remove(path + ".unverified")
		return fmt.Errorf("upstream archive signature verification failed: %w", err)
	}
	file, err := os.Open(path)
	if err != nil {
		return err
	}
	defer file.Close()
	state, err := file.Stat()
	if err != nil || !state.Mode().IsRegular() || state.Size() <= 0 || state.Size() > 512*1024*1024 {
		return fmt.Errorf("verified upstream archive has an invalid size or type")
	}
	hash := sha256.New()
	if _, err := io.Copy(hash, file); err != nil {
		return err
	}
	return json.NewEncoder(output).Encode(result{
		Kind: "klokast.tailscale-upstream-archive.v1", Archive: *name,
		SHA256: hex.EncodeToString(hash.Sum(nil)), Bytes: state.Size(),
		Verifier: "tailscale.com/clientupdate/distsign@v1.102.4", Signed: true,
	})
}

func main() {
	if err := run(os.Args[1:], os.Stdout); err != nil {
		fmt.Fprintln(os.Stderr, "tailscale-distsign:", err)
		os.Exit(1)
	}
}
