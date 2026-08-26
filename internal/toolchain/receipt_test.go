package toolchain

import (
	"fmt"
	"testing"
)

func validReceipt(t *testing.T) Receipt {
	t.Helper()
	commit := "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
	receipt := Receipt{SchemaVersion: 3, Kind: Kind, EngineCommit: commit, PublicCheckoutClean: true, PublicCheckoutCommit: commit}
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
