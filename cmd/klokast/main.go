package main

import (
	"encoding/json"
	"fmt"
	"os"
)

var engineCommit = "0000000000000000000000000000000000000000"

type versionResult struct {
	Name         string `json:"name"`
	Version      string `json:"version"`
	EngineCommit string `json:"engine_commit"`
}

func main() {
	os.Exit(run(os.Args[1:]))
}

func run(args []string) int {
	if len(args) == 2 && args[0] == "version" && args[1] == "--json" {
		result := versionResult{
			Name:         "klokast",
			Version:      "0.1.0-dev",
			EngineCommit: engineCommit,
		}
		if err := json.NewEncoder(os.Stdout).Encode(result); err != nil {
			fmt.Fprintln(os.Stderr, "klokast: cannot write version result")
			return 1
		}
		return 0
	}
	fmt.Fprintln(os.Stderr, "usage: klokast version --json")
	return 2
}
