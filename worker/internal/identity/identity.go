// Package identity provides the shared startup identity output for worker binaries.
package identity

import (
	"fmt"
	"io"

	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/log/zap"
)

// Print identifies an otherwise-empty worker process during the foundation phase.
func Print(output io.Writer, name string) error {
	ctrl.SetLogger(zap.New(zap.UseDevMode(true)))
	_, err := fmt.Fprintln(output, name)

	return err
}
