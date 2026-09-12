package toolchain

import (
	"fmt"
	"testing"
)

func validReceipt(t *testing.T) Receipt {
	t.Helper()
	commit := "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
	receipt := Receipt{SchemaVersion: 6, Kind: Kind, EngineCommit: commit, PublicCheckoutClean: true, PublicCheckoutCommit: commit}
	for index, name := range Components {
		digest := fmt.Sprintf("%064x", index+1)
		receipt.Components = append(receipt.Components, Component{Name: name, SourceSHA256: digest, InstalledSHA256: digest})
	}
	digest, err := Hash(receipt)
	if err != nil {
		t.Fatal(err)
	}
	receipt.ReceiptSHA256 = digest
	return receipt
}

func TestClosedReceipt(t *testing.T) {
	receipt := validReceipt(t)
	if err := Validate(receipt, receipt.EngineCommit); err != nil {
		t.Fatal(err)
	}
	receipt.Components[0].InstalledSHA256 = fmt.Sprintf("%064x", 99)
	digest, _ := Hash(receipt)
	receipt.ReceiptSHA256 = digest
	if err := Validate(receipt, receipt.EngineCommit); err == nil {
		t.Fatal("mismatched installed component was accepted")
	}
}

func TestHistoricalToolchainsRemainClosed(t *testing.T) {
	for version, kind := range map[int]string{3: KindV3, 4: KindV4, 5: KindV5} {
		receipt := validReceipt(t)
		receipt.SchemaVersion, receipt.Kind = version, kind
		receipt.ReceiptSHA256, _ = Hash(receipt)
		if Validate(receipt, receipt.EngineCommit) == nil {
			t.Fatal("old toolchain accepted current components")
		}
		components := []Component{}
		for _, component := range receipt.Components {
			if component.Name == "platform_inventory" || (version < 5 && component.Name == "platform_registry") || (version == 3 && component.Name == "controller_ha") {
				continue
			}
			components = append(components, component)
		}
		receipt.Components = components
		receipt.ReceiptSHA256, _ = Hash(receipt)
		if err := Validate(receipt, receipt.EngineCommit); err != nil {
			t.Fatal(err)
		}
	}
}

func TestToolchainV8UsesTheClosedCurrentComponents(t *testing.T) {
	receipt := validReceipt(t)
	receipt.SchemaVersion, receipt.Kind = 8, KindV8
	receipt.ReceiptSHA256, _ = Hash(receipt)
	if err := Validate(receipt, receipt.EngineCommit); err != nil {
		t.Fatal(err)
	}
}
