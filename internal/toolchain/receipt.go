// Package toolchain validates the closed controller toolchain receipt.
package toolchain

import (
	"bytes"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"os"

	"klokast-box/internal/strictjson"
)

const Kind = "klokast.controller-toolchain.v2"

var Components = []string{
	"controller_guard",
	"ksa_apply",
	"platform_resources",
	"policy_mutation_helper",
	"policy_renderer",
	"policy_template",
	"sealed_engine",
}

type Component struct {
	Name            string `json:"name"`
	SourceSHA256    string `json:"source_sha256"`
	InstalledSHA256 string `json:"installed_sha256"`
}

type Receipt struct {
	SchemaVersion        int         `json:"schema_version"`
	Kind                 string      `json:"kind"`
	EngineCommit         string      `json:"engine_commit"`
	PublicCheckoutClean  bool        `json:"public_checkout_clean"`
	PublicCheckoutCommit string      `json:"public_checkout_commit"`
	Components           []Component `json:"components"`
	ReceiptSHA256        string      `json:"receipt_sha256"`
}

func Load(path string, engineCommit string) (Receipt, error) {
	if path == "" {
		return Receipt{}, fmt.Errorf("controller toolchain receipt path is required")
	}
	info, err := os.Lstat(path)
	if err != nil {
		return Receipt{}, fmt.Errorf("inspect controller toolchain receipt: %w", err)
	}
	if !info.Mode().IsRegular() || info.Mode()&os.ModeSymlink != 0 || info.Size() <= 0 || info.Size() > 64*1024 {
		return Receipt{}, fmt.Errorf("controller toolchain receipt must be a non-empty bounded regular file")
	}
	content, err := os.ReadFile(path)
	if err != nil {
		return Receipt{}, fmt.Errorf("read controller toolchain receipt: %w", err)
	}
	var receipt Receipt
	if err := strictjson.Decode(content, &receipt, true); err != nil {
		return Receipt{}, fmt.Errorf("decode controller toolchain receipt: %w", err)
	}
	if err := Validate(receipt, engineCommit); err != nil {
		return Receipt{}, err
	}
	return receipt, nil
}

func Validate(receipt Receipt, engineCommit string) error {
	if receipt.SchemaVersion != 2 || receipt.Kind != Kind || !receipt.PublicCheckoutClean || receipt.EngineCommit != engineCommit || receipt.PublicCheckoutCommit != engineCommit {
		return fmt.Errorf("controller toolchain receipt identity does not match the selected clean engine commit")
	}
	if len(receipt.Components) != len(Components) {
		return fmt.Errorf("controller toolchain receipt does not contain the closed component set")
	}
	for index, component := range receipt.Components {
		if component.Name != Components[index] || !isDigest(component.SourceSHA256) || component.SourceSHA256 != component.InstalledSHA256 {
			return fmt.Errorf("controller toolchain component %q does not match its installed source", component.Name)
		}
	}
	want, err := Hash(receipt)
	if err != nil {
		return err
	}
	if receipt.ReceiptSHA256 != want {
		return fmt.Errorf("controller toolchain receipt hash does not match canonical content")
	}
	return nil
}

func Hash(receipt Receipt) (string, error) {
	content, err := json.Marshal(receipt)
	if err != nil {
		return "", fmt.Errorf("encode controller toolchain receipt: %w", err)
	}
	var value map[string]any
	decoder := json.NewDecoder(bytes.NewReader(content))
	decoder.UseNumber()
	if err := decoder.Decode(&value); err != nil {
		return "", fmt.Errorf("canonicalize controller toolchain receipt: %w", err)
	}
	delete(value, "receipt_sha256")
	canonical, err := json.Marshal(value)
	if err != nil {
		return "", fmt.Errorf("canonicalize controller toolchain receipt: %w", err)
	}
	sum := sha256.Sum256(canonical)
	return fmt.Sprintf("%x", sum[:]), nil
}

func isDigest(value string) bool {
	if len(value) != 64 {
		return false
	}
	for _, character := range value {
		if (character < '0' || character > '9') && (character < 'a' || character > 'f') {
			return false
		}
	}
	return true
}
