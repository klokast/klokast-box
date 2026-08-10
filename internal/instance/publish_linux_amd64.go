//go:build linux && amd64

package instance

import (
	"syscall"
	"unsafe"
)

const (
	linuxAMD64Renameat2 = 316
	renameNoReplace     = 1
	atCurrentWorkingDir = ^uintptr(99)
)

func publishNoReplace(source, destination string) error {
	sourcePointer, err := syscall.BytePtrFromString(source)
	if err != nil {
		return err
	}
	destinationPointer, err := syscall.BytePtrFromString(destination)
	if err != nil {
		return err
	}
	_, _, callErr := syscall.Syscall6(
		linuxAMD64Renameat2,
		atCurrentWorkingDir,
		uintptr(unsafe.Pointer(sourcePointer)),
		atCurrentWorkingDir,
		uintptr(unsafe.Pointer(destinationPointer)),
		renameNoReplace,
		0,
	)
	if callErr != 0 {
		return callErr
	}
	return nil
}
