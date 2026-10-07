package mcpcfg

// writers.go applies one runtime's entry to disk (JSON/TOML targets) or
// assembles the exec plan (claude-code). File writes go through a read →
// merge → compare → write cycle so an unchanged config is never rewritten
// (mtime/watchers stay quiet) and a re-run is a no-op.

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
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

// writeFilePreserving writes data to path ATOMICALLY (temp file in the target
// dir, write+fsync, chmod, rename — the skillgen writeFileAtomic pattern):
// these are FOREIGN runtimes' config files carrying sibling servers' entries
// (often env secrets), so a crash/kill/disk-full mid-write must never leave a
// truncated file behind. The parent directory is created for a new file, and
// an existing file's mode is preserved on rewrite. New files are 0600: an MCP
// config commonly carries sibling servers' env secrets, so err on the tight
// side (the runtimes read their own user's file only).
func writeFilePreserving(path string, data []byte, existed bool) error {
	mode := os.FileMode(0o600)
	if existed {
		if info, err := os.Stat(path); err == nil {
			mode = info.Mode().Perm()
		}
	} else if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
		return err
	}

	tmp, err := os.CreateTemp(filepath.Dir(path), ".jentic-mcp-*.tmp")
	if err != nil {
		return err
	}
	tmpName := tmp.Name()
	renamed := false
	defer func() {
		if !renamed {
			_ = os.Remove(tmpName)
		}
	}()

	if _, err := tmp.Write(data); err != nil {
		_ = tmp.Close()
		return err
	}
	if err := tmp.Sync(); err != nil {
		_ = tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	if err := os.Chmod(tmpName, mode); err != nil {
		return err
	}
	if err := os.Rename(tmpName, path); err != nil {
		return err
	}
	renamed = true
	return nil
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

// ClaudeCodeProbeArgv is the read-only `claude mcp get` probe run BEFORE any
// rewrite: when its output already shows our exact entry the plan is skipped
// entirely (re-runs report "already up to date" and never open the
// remove-succeeded/add-failed destructive window).
func ClaudeCodeProbeArgv(claudePath string) []string {
	return []string{claudePath, "mcp", "get", ServerName}
}

// ClaudeCodeConverged reports whether the `claude mcp get jentic` output
// describes exactly entry: the command path and the full argv must all
// appear. The check is deliberately tolerant of claude's output framing
// (labels, ordering, whitespace) — a false negative merely re-runs the
// rewrite, while a false positive would strand a stale entry, so every
// component must be present.
func ClaudeCodeConverged(probeOutput []byte, entry Entry) bool {
	out := string(probeOutput)
	if !strings.Contains(out, entry.Command) {
		return false
	}
	// The args are rendered space-joined by claude's get output; require the
	// exact joined sequence so a reordered/partial argv never passes.
	return strings.Contains(out, strings.Join(entry.Args, " "))
}

// ClaudeCodeSteps assembles the `claude mcp` exec plan that converges Claude
// Code's user-scope config on our entry. entryExists reports whether the
// probe found a (stale) jentic entry: only then is the best-effort remove
// included — a first run goes straight to add, so there is no window in which
// a working entry has been removed but not yet re-added unless a change is
// genuinely needed. claudePath is the resolved claude binary. User scope (not
// project/local) matches the skill placement policy: the Jentic capability is
// not tied to one repo.
func ClaudeCodeSteps(claudePath string, entry Entry, entryExists bool) []ExecStep {
	var steps []ExecStep
	if entryExists {
		steps = append(steps, ExecStep{
			What:       "remove the previous jentic MCP entry",
			Argv:       []string{claudePath, "mcp", "remove", "--scope", "user", ServerName},
			BestEffort: true,
		})
	}
	return append(steps, ExecStep{
		What: "register the jentic MCP entry",
		Argv: append([]string{claudePath, "mcp", "add", "--scope", "user", ServerName, "--", entry.Command}, entry.Args...),
	})
}
