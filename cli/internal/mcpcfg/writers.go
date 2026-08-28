package mcpcfg

// writers.go applies one runtime's entry to disk (JSON/TOML targets) or
// assembles the exec plan (claude-code). File writes go through a read →
// merge → compare → write cycle so an unchanged config is never rewritten
// (mtime/watchers stay quiet) and a re-run is a no-op.

import (
	"fmt"
	"os"
	"path/filepath"
)

// WriteJSONEntry merges entry into the JSON MCP config at path (creating the
// file and its parent directory when absent) and reports what happened.
func WriteJSONEntry(runtime Runtime, path string, entry Entry) (Outcome, error) {
	out := Outcome{Runtime: runtime, Path: path}
	existing, err := os.ReadFile(path) //nolint:gosec // path is a fixed per-runtime config location under the user's home.
	switch {
	case os.IsNotExist(err):
		out.Created = true
		existing = nil
	case err != nil:
		return out, err
	}

	next, changed, err := MergeJSON(existing, entry)
	if err != nil {
		return out, fmt.Errorf("%s: %w", path, err)
	}
	if !changed {
		return out, nil
	}
	if err := writeFilePreserving(path, next, existing != nil); err != nil {
		return out, err
	}
	out.Changed = true
	return out, nil
}

// WriteCodexEntry splices the managed block into the Codex TOML config at
// path (creating it when absent) and reports what happened.
func WriteCodexEntry(path string, entry Entry) (Outcome, error) {
	out := Outcome{Runtime: RuntimeCodex, Path: path}
	existing, err := os.ReadFile(path) //nolint:gosec // path is the fixed ~/.codex/config.toml location.
	switch {
	case os.IsNotExist(err):
		out.Created = true
		existing = nil
	case err != nil:
		return out, err
	}

	next, changed, err := MergeCodexTOML(existing, entry)
	if err != nil {
		return out, fmt.Errorf("%s: %w", path, err)
	}
	if !changed {
		return out, nil
	}
	if err := writeFilePreserving(path, next, existing != nil); err != nil {
		return out, err
	}
	out.Changed = true
	return out, nil
}

// writeFilePreserving writes data to path, creating the parent directory for
// a new file and preserving the existing file's mode on rewrite. New files
// are 0600: an MCP config commonly carries sibling servers' env secrets, so
// err on the tight side (the runtimes read their own user's file only).
func writeFilePreserving(path string, data []byte, existed bool) error {
	mode := os.FileMode(0o600)
	if existed {
		if info, err := os.Stat(path); err == nil {
			mode = info.Mode().Perm()
		}
	} else if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
		return err
	}
	return os.WriteFile(path, data, mode) //nolint:gosec // path is a fixed, well-known config location under the user's home, not caller input.
}

// ExecStep is one step of an exec-route plan (claude-code), enumerated rather
// than run so the plan is assertable in tests without spawning anything —
// mirroring localagent.AccountStep.
type ExecStep struct {
	// What describes the step for progress/error messages.
	What string
	// Argv is the full command line (argv[0] first).
	Argv []string
	// BestEffort marks a step whose failure should be reported but not abort
	// the plan (the remove of a not-yet-registered entry).
	BestEffort bool
}

// ClaudeCodeSteps assembles the `claude mcp` exec plan that converges Claude
// Code's user-scope config on our entry: remove any previous "jentic" server
// (best-effort — absent on first run), then add the current one. claudePath
// is the resolved claude binary. User scope (not project/local) matches the
// skill placement policy: the Jentic capability is not tied to one repo.
func ClaudeCodeSteps(claudePath string, entry Entry) []ExecStep {
	add := append([]string{claudePath, "mcp", "add", "--scope", "user", ServerName, "--", entry.Command}, entry.Args...)
	return []ExecStep{
		{
			What:       "remove any previous jentic MCP entry",
			Argv:       []string{claudePath, "mcp", "remove", "--scope", "user", ServerName},
			BestEffort: true,
		},
		{
			What: "register the jentic MCP entry",
			Argv: add,
		},
	}
}
