package identity

import (
	"bytes"
	"testing"
)

func TestPrint(t *testing.T) {
	t.Parallel()

	var output bytes.Buffer
	if err := Print(&output, "echo-compute-controller"); err != nil {
		t.Fatalf("Print() returned an error: %v", err)
	}

	if got, want := output.String(), "echo-compute-controller\n"; got != want {
		t.Fatalf("Print() = %q, want %q", got, want)
	}
}
