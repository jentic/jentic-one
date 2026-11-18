// Command clidocs writes the CLI reference generated from the cobra command
// tree of both binaries (jentic and jenticctl): machine-readable JSON consumed
// by the docs SPA, and a Markdown rendering of the same model for readers on
// GitHub.
//
// Regenerate with `make cli-reference` (writes ui/public/cli-reference.json
// and docs/reference/cli.md). Both outputs are committed so neither the SPA nor
// GitHub readers need a Go toolchain, and drift tests keep them honest.
//
// Usage:
//
//	go run ./cmd/clidocs                        # print JSON to stdout
//	go run ./cmd/clidocs -o out.json            # write JSON to a file
//	go run ./cmd/clidocs -o out.json -md cli.md # also write the Markdown
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
	md := flag.String("md", "", "also write the Markdown reference to this file")
	flag.Parse()

	ref := clitree.BuildCLIReference()
	data, err := json.MarshalIndent(ref, "", "  ")
	if err != nil {
		fail(err)
	}
	data = append(data, '\n')

	if *out == "" {
		if _, err := os.Stdout.Write(data); err != nil {
			fail(err)
		}
	} else {
		writeFile(*out, data)
	}
	if *md != "" {
		writeFile(*md, clitree.RenderMarkdown(ref))
	}
}

func writeFile(path string, data []byte) {
	if err := os.WriteFile(path, data, 0o600); err != nil {
		fail(err)
	}
	fmt.Fprintf(os.Stderr, "Wrote %s\n", path)
}

func fail(err error) {
	fmt.Fprintln(os.Stderr, "error:", err)
	os.Exit(1)
}
