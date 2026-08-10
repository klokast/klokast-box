package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"os"

	"klokast-box/internal/contract"
)

var (
	engineRepository = "unverified"
	engineRef        = "unverified"
	engineCommit     = "0000000000000000000000000000000000000000"
)

type versionResult struct {
	Name             string `json:"name"`
	Version          string `json:"version"`
	EngineRepository string `json:"engine_repository"`
	EngineRef        string `json:"engine_ref"`
	EngineCommit     string `json:"engine_commit"`
}

type operationalResult struct {
	Valid            bool   `json:"valid"`
	OperationalError string `json:"operational_error"`
}

func main() {
	os.Exit(run(os.Args[1:], os.Stdout, os.Stderr))
}

func run(args []string, stdout, stderr io.Writer) int {
	if len(args) == 2 && args[0] == "version" && args[1] == "--json" {
		result := versionResult{
			Name:             "klokast",
			Version:          "0.1.0-dev",
			EngineRepository: engineRepository,
			EngineRef:        engineRef,
			EngineCommit:     engineCommit,
		}
		if err := json.NewEncoder(stdout).Encode(result); err != nil {
			fmt.Fprintln(stderr, "klokast: cannot write version result")
			return 1
		}
		return 0
	}
	if len(args) > 0 && args[0] == "check" {
		return runCheck(args[1:], stdout, stderr)
	}
	fmt.Fprintln(stderr, "usage: klokast version --json | klokast check --instance PATH [--json]")
	return 2
}

func runCheck(args []string, stdout, stderr io.Writer) int {
	flags := flag.NewFlagSet("check", flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	instance := flags.String("instance", "", "path to a standalone instance repository")
	jsonOutput := flags.Bool("json", false, "write machine-readable output")
	if err := flags.Parse(args); err != nil || flags.NArg() != 0 || *instance == "" {
		fmt.Fprintln(stderr, "usage: klokast check --instance PATH [--json]")
		return 2
	}
	report, err := contract.Check(*instance, contract.Engine{
		Repository: engineRepository,
		Ref:        engineRef,
		Commit:     engineCommit,
	})
	if err != nil {
		if *jsonOutput {
			if encodeErr := json.NewEncoder(stdout).Encode(operationalResult{Valid: false, OperationalError: err.Error()}); encodeErr != nil {
				fmt.Fprintln(stderr, "klokast: cannot write check result")
			}
		} else {
			fmt.Fprintf(stderr, "klokast check: operational failure: %v\n", err)
		}
		return 1
	}
	if *jsonOutput {
		if err := json.NewEncoder(stdout).Encode(report); err != nil {
			fmt.Fprintln(stderr, "klokast: cannot write check result")
			return 1
		}
	} else if report.Valid {
		fmt.Fprintln(stdout, "klokast check: valid")
	} else {
		for _, diagnostic := range report.Diagnostics {
			fmt.Fprintf(stderr, "%s: %s: %s\n", diagnostic.Path, diagnostic.Code, diagnostic.Message)
		}
	}
	if !report.Valid {
		return 2
	}
	return 0
}
