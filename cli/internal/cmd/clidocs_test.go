package cmd

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

// committedCLIReference is the path to the generated, committed reference the
// docs SPA serves from ui/public.
const committedCLIReference = "../../../ui/public/cli-reference.json"

// TestCLIReferenceShape sanity-checks the generated model: both binaries,
// the expected top-level commands, groups, and that nested subcommands and
// inherited flags are captured.
func TestCLIReferenceShape(t *testing.T) {
	ref := BuildCLIReference()

	if ref.Schema != CLIReferenceSchema {
		t.Fatalf("schema = %q, want %q", ref.Schema, CLIReferenceSchema)
	}
	if len(ref.Binaries) != 2 {
		t.Fatalf("got %d binaries, want 2 (jentic, jenticctl)", len(ref.Binaries))
	}

	byName := map[string]BinaryDoc{}
	for _, b := range ref.Binaries {
		byName[b.Name] = b
	}

	jentic, ok := byName["jentic"]
	if !ok {
		t.Fatal("missing jentic binary")
	}
	if jentic.Tagline == "" || jentic.Short == "" {
		t.Error("jentic binary missing tagline/short")
	}

	// profile has subcommands; assert the tree recursed. Value-typed lookup
	// (not a *CommandDoc guarded by t.Fatal) — staticcheck's SA5011 in CI
	// doesn't always see t.Fatal as terminating and flags the derefs.
	profile, ok := findCommand(jentic.Commands, "profile")
	if !ok {
		t.Fatal("jentic missing profile command")
		return // unreachable; satisfies SA5011 when noreturn facts are cold
	}
	if len(profile.Subcommands) == 0 {
		t.Error("profile should expose subcommands (list/use/add-key)")
	}
	if profile.GroupTitle == "" {
		t.Error("profile should carry its group title")
	}

	// endpoints should inherit/carry the --profile flag.
	endpoints, ok := findCommand(jentic.Commands, "endpoints")
	if !ok {
		t.Fatal("jentic missing endpoints command")
		return // unreachable; satisfies SA5011 when noreturn facts are cold
	}
	if !hasFlag(endpoints.Flags, "profile") || !hasFlag(endpoints.Flags, "scope") {
		t.Errorf("endpoints flags = %+v, want profile + scope", endpoints.Flags)
	}

	if _, ok := byName["jenticctl"]; !ok {
		t.Fatal("missing jenticctl binary")
	}
}

func findCommand(commands []CommandDoc, name string) (CommandDoc, bool) {
	for _, c := range commands {
		if c.Name == name {
			return c, true
		}
	}
	return CommandDoc{}, false
}

func hasFlag(flags []FlagDoc, name string) bool {
	for _, f := range flags {
		if f.Name == name {
			return true
		}
	}
	return false
}

// TestCommittedCLIReferenceUpToDate fails if ui/public/cli-reference.json drifts
// from the cobra definitions. Regenerate with `make cli-reference`.
func TestCommittedCLIReferenceUpToDate(t *testing.T) {
	path := filepath.Clean(committedCLIReference)
	committed, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read committed reference: %v (run `make cli-reference`)", err)
	}

	want, err := json.MarshalIndent(BuildCLIReference(), "", "  ")
	if err != nil {
		t.Fatalf("marshal reference: %v", err)
	}
	want = append(want, '\n')

	if string(committed) != string(want) {
		t.Errorf("ui/public/cli-reference.json is out of date with the CLI command tree.\n" +
			"Regenerate with `make cli-reference` and commit the result.")
	}
}
