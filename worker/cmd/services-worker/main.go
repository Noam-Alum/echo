// Command services-worker starts the inference services worker.
package main

import (
	"log"
	"os"

	"echo.erez.io/worker/internal/identity"
)

func main() {
	if err := identity.Print(os.Stdout, "echo-services-controller"); err != nil {
		log.Print(err)
		os.Exit(1)
	}
}
