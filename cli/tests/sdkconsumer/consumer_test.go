// Package sdkconsumer holds a hermetic external-consumer smoke test for the
// public SDK (client/...). It proves the promise in impl/1.1 §1 and impl/7.0 §1
// concretely: a third-party Go module can `go get` this module and import
// client/... WITHOUT dragging in the CLI's cobra/charmbracelet/bubbletea
// dependency tree, and without touching any internal/ package.
//
// This is a stronger guarantee than tests/arch's import-list scan: it exercises
// the real Go build graph of an out-of-tree module, so a future `internal/` or
// cobra leak into client/ fails here at dependency-resolution time — the exact
// failure an external user would hit — not just as a source-import lint.
package sdkconsumer

import (
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

// sdkModulePath is the SDK's module import path. External consumers import
// subpackages under this (e.g. .../client, .../client/paginate).
const sdkModulePath = "github.com/jentic/jentic-one/cli"

// consumerMain is a minimal but real third-party program: construct a control
// client with an injected token (the file-less BYO-token path — no disk, no
// auth exchange) and reference the generic pagination helper. If the public
// surface these lines use ever changes incompatibly, this fails to compile —
// which is the point (it doubles as a compile-checked usage example that lives
// OUTSIDE the SDK module, unlike client/example_test.go).
const consumerMain = `package main

import (
	"fmt"

	client "` + sdkModulePath + `/client"
	"` + sdkModulePath + `/client/paginate"
)

func main() {
	c, err := client.NewControl(client.Config{
		ControlBaseURL:      "https://api.example.test",
		InjectedBearerToken: "byo-token",
	})
	if err != nil {
		panic(err)
	}
	_ = c
	_ = paginate.All[int]
	fmt.Println("ok")
}
`

// uiDeps are packages that are CLI-only and must NEVER appear in an external
// SDK consumer's build graph. Their presence would mean client/ (or something
// it imports) pulled in a UI/command dependency, bloating every downstream.
var uiDeps = []string{
	"github.com/spf13/cobra",
	"github.com/charmbracelet/",
	sdkModulePath + "/internal",
}

// sdkModuleDir returns the SDK module root (the cli/ dir), two levels up from
// this test file (cli/tests/sdkconsumer).
func sdkModuleDir(t *testing.T) string {
	t.Helper()
	_, thisFile, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("runtime.Caller failed")
	}
	// .../cli/tests/sdkconsumer/consumer_test.go -> .../cli
	return filepath.Clean(filepath.Join(filepath.Dir(thisFile), "..", ".."))
}

// goModVersion extracts the `go X.Y[.Z]` directive from the SDK go.mod so the
// synthetic consumer module declares a compatible version.
func goModVersion(t *testing.T, sdkDir string) string {
	t.Helper()
	data, err := os.ReadFile(filepath.Join(sdkDir, "go.mod"))
	if err != nil {
		t.Fatalf("read SDK go.mod: %v", err)
	}
	for _, line := range strings.Split(string(data), "\n") {
		if v, ok := strings.CutPrefix(strings.TrimSpace(line), "go "); ok {
			return strings.TrimSpace(v)
		}
	}
	t.Fatal("no `go` directive in SDK go.mod")
	return ""
}

// runGo runs a go subcommand in dir with GOWORK disabled (the consumer module
// must resolve on its own go.mod, never the workspace), returning combined output.
func runGo(t *testing.T, dir string, args ...string) (string, error) {
	t.Helper()
	cmd := exec.Command("go", args...)
	cmd.Dir = dir
	cmd.Env = append(os.Environ(), "GOWORK=off", "GOFLAGS=")
	out, err := cmd.CombinedOutput()
	return string(out), err
}

// TestExternalConsumerBuildsWithoutUIDeps materializes a throwaway external
// module that imports the SDK via a local `replace`, then asserts it (a) builds
// and (b) resolves a build graph free of cobra/charmbracelet/internal.
func TestExternalConsumerBuildsWithoutUIDeps(t *testing.T) {
	if testing.Short() {
		t.Skip("skipping external-consumer build in -short mode")
	}
	sdkDir := sdkModuleDir(t)
	if _, err := os.Stat(filepath.Join(sdkDir, "client", "client.go")); err != nil {
		t.Fatalf("SDK not found at %s: %v", sdkDir, err)
	}

	work := t.TempDir()
	goMod := "module sdkconsumersmoke\n\ngo " + goModVersion(t, sdkDir) + "\n\n" +
		"require " + sdkModulePath + " v0.0.0\n\n" +
		"replace " + sdkModulePath + " => " + sdkDir + "\n"
	if err := os.WriteFile(filepath.Join(work, "go.mod"), []byte(goMod), 0o600); err != nil {
		t.Fatalf("write consumer go.mod: %v", err)
	}
	if err := os.WriteFile(filepath.Join(work, "main.go"), []byte(consumerMain), 0o600); err != nil {
		t.Fatalf("write consumer main.go: %v", err)
	}

	// Resolve the dependency graph for the synthetic module.
	if out, err := runGo(t, work, "mod", "tidy"); err != nil {
		t.Fatalf("go mod tidy failed (SDK not standalone-consumable):\n%s", out)
	}

	// (a) It must build — the SDK's public surface is actually usable as written.
	if out, err := runGo(t, work, "build", "./..."); err != nil {
		t.Fatalf("external consumer failed to build against the SDK:\n%s", out)
	}

	// (b) Its build graph must be free of CLI-only dependencies. `go list -deps`
	// on the consumer main package enumerates everything actually compiled in.
	deps, err := runGo(t, work, "list", "-deps", ".")
	if err != nil {
		t.Fatalf("go list -deps failed:\n%s", deps)
	}
	for _, banned := range uiDeps {
		for _, line := range strings.Split(deps, "\n") {
			if strings.HasPrefix(strings.TrimSpace(line), banned) {
				t.Errorf("external SDK consumer pulled in a CLI-only dependency %q "+
					"(via %s) — client/ must not import cobra/charmbracelet/internal, "+
					"or downstream users inherit the whole CLI dependency tree",
					line, banned)
			}
		}
	}
}
