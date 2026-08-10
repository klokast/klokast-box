//go:build !linux || !amd64

package instance

import "errors"

func publishNoReplace(_, _ string) error {
	return errors.New("atomic no-replace publication is not supported on this platform")
}
