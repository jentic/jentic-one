// Command clidocs writes the machine-readable CLI reference consumed by the
// docs SPA. It serialises the cobra command tree of both binaries (jentic and
// jenticctl) to JSON.
//
// Regenerate with `make cli-reference` (writes ui/public/cli-reference.json).
// The output is committed so the SPA can render the CLI docs without a Go
// toolchain at build time, and a drift test keeps it honest.
//
// Usage:
//
//	go run ./cmd/clidocs            # print JSON to stdout
//	go run ./cmd/clidocs -o out.json
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"

	"github.com/jentic/jentic-one/cli/pkg/clitree"
)

func main() {
	out := flag.String("o", "", "write JSON to this file instead of stdout")
	flag.Parse()

	ref := clitree.BuildCLIReference()
	data, err := json.MarshalIndent(ref, "", "  ")
	if err != nil {
		fmt.Fprintln(os.Stderr, "error:", err)
		os.Exit(1)
	}
	data = append(data, '\n')

	if *out == "" {
		if _, err := os.Stdout.Write(data); err != nil {
			fmt.Fprintln(os.Stderr, "error:", err)
			os.Exit(1)
		}
		return
	}
	if err := os.WriteFile(*out, data, 0o600); err != nil {
		fmt.Fprintln(os.Stderr, "error:", err)
		os.Exit(1)
	}
	fmt.Fprintf(os.Stderr, "Wrote %s\n", *out)
}
