package main

import (
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"

	"klokast-box/internal/contract"
	"klokast-box/internal/deploymentplan"
	"klokast-box/internal/doctor"
	"klokast-box/internal/instance"
	"klokast-box/internal/planner"
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

type initFailureResult struct {
	Created          bool                  `json:"created"`
	Diagnostics      []contract.Diagnostic `json:"diagnostics,omitempty"`
	OperationalError string                `json:"operational_error,omitempty"`
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
	if len(args) > 0 && args[0] == "init" {
		return runInit(args[1:], stdout, stderr)
	}
	if len(args) > 0 && args[0] == "plan" {
		return runPlan(args[1:], stdout, stderr)
	}
	if len(args) > 0 && args[0] == "doctor" {
		return runDoctor(args[1:], stdout, stderr)
	}
	fmt.Fprintln(stderr, "usage: klokast version --json | klokast init --instance PATH --values FILE [--json] | klokast check --instance PATH [--json] | klokast plan --instance PATH --compatibility-deployment FILE --compatibility-registry FILE --compatibility-controller-ha FILE [--observation FILE --instance-source-receipt FILE --authority-state FILE --controller-toolchain-receipt FILE] [--json] | klokast doctor --instance PATH --observation FILE [--json]")
	return 2
}

func runDoctor(args []string, stdout, stderr io.Writer) int {
	flags := flag.NewFlagSet("doctor", flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	instancePath := flags.String("instance", "", "path to a standalone instance repository")
	observationPath := flags.String("observation", "", "path to an Observation v1 JSON document")
	jsonOutput := flags.Bool("json", false, "write machine-readable output")
	if err := flags.Parse(args); err != nil || flags.NArg() != 0 || *instancePath == "" || *observationPath == "" {
		fmt.Fprintln(stderr, "usage: klokast doctor --instance PATH --observation FILE [--json]")
		return 2
	}
	result, err := doctor.Doctor(doctor.Options{
		InstancePath: *instancePath, ObservationPath: *observationPath,
	}, contract.Engine{Repository: engineRepository, Ref: engineRef, Commit: engineCommit})
	if err != nil {
		if *jsonOutput {
			if encodeErr := json.NewEncoder(stdout).Encode(operationalResult{Valid: false, OperationalError: err.Error()}); encodeErr != nil {
				fmt.Fprintln(stderr, "klokast: cannot write doctor result")
			}
		} else {
			fmt.Fprintf(stderr, "klokast doctor: operational failure: %v\n", err)
		}
		return 1
	}
	if *jsonOutput {
		if err := json.NewEncoder(stdout).Encode(result); err != nil {
			fmt.Fprintln(stderr, "klokast: cannot write doctor result")
			return 1
		}
	} else if !result.Valid {
		for _, diagnostic := range result.Diagnostics {
			fmt.Fprintf(stderr, "%s: %s: %s\n", diagnostic.Path, diagnostic.Code, diagnostic.Message)
		}
	} else if !result.Healthy {
		for _, finding := range result.Findings {
			fmt.Fprintf(stderr, "%s: %s: %s\n", finding.Path, finding.Code, finding.Message)
		}
	} else {
		fmt.Fprintln(stdout, "klokast doctor: healthy")
	}
	if !result.Valid || !result.Healthy {
		return 2
	}
	return 0
}

func runPlan(args []string, stdout, stderr io.Writer) int {
	flags := flag.NewFlagSet("plan", flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	instancePath := flags.String("instance", "", "path to a standalone instance repository")
	deploymentPath := flags.String("compatibility-deployment", "", "path to the transitional deployment document")
	registryPath := flags.String("compatibility-registry", "", "path to the transitional platform-resources registry")
	controllerPath := flags.String("compatibility-controller-ha", "", "path to the transitional controller HA document")
	observationPath := flags.String("observation", "", "path to an Observation v1 JSON document")
	instanceSourceReceipt := flags.String("instance-source-receipt", "", "path to an Instance Source Receipt v1 JSON document")
	authorityState := flags.String("authority-state", "", "path to an Authority State v1 JSON document")
	controllerToolchainReceipt := flags.String("controller-toolchain-receipt", "", "path to a Controller Toolchain v1 receipt")
	jsonOutput := flags.Bool("json", false, "write machine-readable output")
	if err := flags.Parse(args); err != nil || flags.NArg() != 0 || *instancePath == "" || *deploymentPath == "" || *registryPath == "" || *controllerPath == "" {
		fmt.Fprintln(stderr, "usage: klokast plan --instance PATH --compatibility-deployment FILE --compatibility-registry FILE --compatibility-controller-ha FILE [--observation FILE --instance-source-receipt FILE --authority-state FILE --controller-toolchain-receipt FILE] [--json]")
		return 2
	}
	engine := contract.Engine{Repository: engineRepository, Ref: engineRef, Commit: engineCommit}
	if *observationPath == "" {
		if *instanceSourceReceipt != "" || *authorityState != "" || *controllerToolchainReceipt != "" {
			fmt.Fprintln(stderr, "klokast plan: Plan v2 evidence flags require --observation")
			return 2
		}
		return runCompatibilityPlan(planner.Options{
			InstancePath: *instancePath, CompatibilityDeployment: *deploymentPath,
			CompatibilityRegistry: *registryPath, CompatibilityControllerHA: *controllerPath,
		}, engine, *jsonOutput, stdout, stderr)
	}
	if *instanceSourceReceipt == "" || *authorityState == "" || *controllerToolchainReceipt == "" {
		fmt.Fprintln(stderr, "klokast plan: --instance-source-receipt, --authority-state, and --controller-toolchain-receipt are required with --observation")
		return 2
	}
	result, err := deploymentplan.Build(deploymentplan.Options{
		InstancePath:               *instancePath,
		CompatibilityDeployment:    *deploymentPath,
		CompatibilityRegistry:      *registryPath,
		CompatibilityControllerHA:  *controllerPath,
		ObservationPath:            *observationPath,
		InstanceSourceReceipt:      *instanceSourceReceipt,
		AuthorityState:             *authorityState,
		ControllerToolchainReceipt: *controllerToolchainReceipt,
	}, engine)
	if err != nil {
		if *jsonOutput {
			if encodeErr := json.NewEncoder(stdout).Encode(operationalResult{Valid: false, OperationalError: err.Error()}); encodeErr != nil {
				fmt.Fprintln(stderr, "klokast: cannot write plan result")
			}
		} else {
			fmt.Fprintf(stderr, "klokast plan: operational failure: %v\n", err)
		}
		return 1
	}
	if *jsonOutput {
		if err := json.NewEncoder(stdout).Encode(result); err != nil {
			fmt.Fprintln(stderr, "klokast: cannot write plan result")
			return 1
		}
	} else if !result.Valid {
		for _, diagnostic := range result.Diagnostics {
			fmt.Fprintf(stderr, "%s: %s: %s\n", diagnostic.Path, diagnostic.Code, diagnostic.Message)
		}
	} else if !result.Deployable {
		for _, refusal := range result.Refusals {
			fmt.Fprintf(stderr, "%s: %s: %s\n", refusal.Scope, refusal.Code, refusal.Message)
		}
	} else {
		fmt.Fprintf(stdout, "klokast plan: deployable; authority-ready=%t; legacy-removal-ready=%t; sha256=%s\n", result.AuthorityReady, result.LegacyRemovalReady, result.PlanSHA256)
	}
	if !result.Valid || !result.Deployable {
		return 2
	}
	return 0
}

func runCompatibilityPlan(options planner.Options, engine contract.Engine, jsonOutput bool, stdout, stderr io.Writer) int {
	result, err := planner.Plan(options, engine)
	if err != nil {
		if jsonOutput {
			if encodeErr := json.NewEncoder(stdout).Encode(operationalResult{Valid: false, OperationalError: err.Error()}); encodeErr != nil {
				fmt.Fprintln(stderr, "klokast: cannot write compatibility result")
			}
		} else {
			fmt.Fprintf(stderr, "klokast plan: operational failure: %v\n", err)
		}
		return 1
	}
	if jsonOutput {
		if err := json.NewEncoder(stdout).Encode(result); err != nil {
			fmt.Fprintln(stderr, "klokast: cannot write compatibility result")
			return 1
		}
	} else if !result.Valid {
		for _, diagnostic := range result.Diagnostics {
			fmt.Fprintf(stderr, "%s: %s: %s\n", diagnostic.Path, diagnostic.Code, diagnostic.Message)
		}
	} else if !result.Compatible {
		for _, finding := range result.Compatibility.Findings {
			if finding.Class == "conflict" || finding.Class == "unsupported" {
				fmt.Fprintf(stderr, "%s: %s: %s\n", finding.Path, finding.Code, finding.Message)
			}
		}
	} else {
		fmt.Fprintf(stdout, "klokast plan: compatible; repository-deployable=%t\n", result.Deployable)
	}
	if !result.Valid || !result.Compatible {
		return 2
	}
	return 0
}

func runInit(args []string, stdout, stderr io.Writer) int {
	flags := flag.NewFlagSet("init", flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	instancePath := flags.String("instance", "", "path for the new standalone instance repository")
	valuesPath := flags.String("values", "", "path to the init values JSON document")
	jsonOutput := flags.Bool("json", false, "write machine-readable output")
	if err := flags.Parse(args); err != nil || flags.NArg() != 0 || *instancePath == "" || *valuesPath == "" {
		fmt.Fprintln(stderr, "usage: klokast init --instance PATH --values FILE [--json]")
		return 2
	}
	result, err := instance.Init(instance.Options{
		InstancePath: *instancePath,
		ValuesPath:   *valuesPath,
	}, contract.Engine{
		Repository: engineRepository,
		Ref:        engineRef,
		Commit:     engineCommit,
	})
	if err != nil {
		var validationError *instance.ValidationError
		if errors.As(err, &validationError) {
			if *jsonOutput {
				if encodeErr := json.NewEncoder(stdout).Encode(initFailureResult{Created: false, Diagnostics: validationError.Diagnostics}); encodeErr != nil {
					fmt.Fprintln(stderr, "klokast: cannot write init result")
					return 1
				}
			} else {
				for _, diagnostic := range validationError.Diagnostics {
					fmt.Fprintf(stderr, "%s: %s: %s\n", diagnostic.Path, diagnostic.Code, diagnostic.Message)
				}
			}
			return 2
		}
		if *jsonOutput {
			if encodeErr := json.NewEncoder(stdout).Encode(initFailureResult{Created: false, OperationalError: err.Error()}); encodeErr != nil {
				fmt.Fprintln(stderr, "klokast: cannot write init result")
			}
		} else {
			fmt.Fprintf(stderr, "klokast init: operational failure: %v\n", err)
		}
		return 1
	}
	if *jsonOutput {
		if err := json.NewEncoder(stdout).Encode(result); err != nil {
			fmt.Fprintln(stderr, "klokast: cannot write init result")
			return 1
		}
	} else {
		fmt.Fprintf(stdout, "klokast init: created %s\n", result.InstancePath)
	}
	return 0
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
