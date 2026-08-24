// Command compute-worker starts the training and research compute worker.
package main

import (
	"log"
	"os"

	"echo.erez.io/worker/internal/identity"
)

func main() {
	if err := identity.Print(os.Stdout, "echo-compute-controller"); err != nil {
		log.Print(err)
		os.Exit(1)
	}
}
