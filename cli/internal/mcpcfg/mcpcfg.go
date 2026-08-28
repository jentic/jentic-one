// Package mcpcfg writes the `jentic mcp` server entry into each supported
// agent runtime's native MCP configuration (local-MCP item 2-E3): Cursor's
// `~/.cursor/mcp.json`, Claude Desktop's `claude_desktop_config.json`, Claude
// Code via a `claude mcp add` exec plan, and Codex's `~/.codex/config.toml`.
//
// Writers are idempotent and edit-preserving: JSON targets are merged (we own
// exactly the "jentic" server key; every foreign key is preserved), the TOML
// target is spliced as a clearly-marked managed block, and the exec target is
// a remove-then-add plan so re-runs converge. The entry always names the
// binary by ABSOLUTE (stable) path — GUI runtimes spawn servers with a minimal
// PATH (master plan §3.7.3) — and always pins `--context <name>` so a later
// `jentic context use` can never silently re-point a runtime at a different
// agent/instance (§3.10).
package mcpcfg

import (
	"fmt"
	"path/filepath"
)

// Runtime identifies one MCP-capable agent runtime whose config we can write.
// It is a superset of the skillgen operator vocabulary: "claude" fans out to
// the two distinct Claude runtimes (Code and Desktop), which keep separate MCP
// configs.
type Runtime string

// Supported runtimes.
const (
	RuntimeCursor        Runtime = "cursor"
	RuntimeClaudeDesktop Runtime = "claude-desktop"
	RuntimeClaudeCode    Runtime = "claude-code"
	RuntimeCodex         Runtime = "codex"
)

// WireTag maps a runtime to the closed telemetry tag value the backend's
// McpConfigRuntime enum accepts ("cursor", "claude_desktop", "claude_code",
// "codex"). Anything unrecognised maps to "other" — the enum is closed on the
// wire, never a raw runtime string.
func (r Runtime) WireTag() string {
	switch r {
	case RuntimeCursor:
		return "cursor"
	case RuntimeClaudeDesktop:
		return "claude_desktop"
	case RuntimeClaudeCode:
		return "claude_code"
	case RuntimeCodex:
		return "codex"
	default:
		return "other"
	}
}

// ServerName is the key our entry is registered under in every runtime's MCP
// config. It is the managed unit for the JSON targets: we own exactly this
// key and never touch a sibling server entry.
const ServerName = "jentic"

// Entry is the runtime-agnostic MCP server entry: the command to spawn and
// its argv. Every writer renders this into its runtime's native shape.
type Entry struct {
	// Command is the absolute path of the executable the runtime spawns
	// (the stable jentic binary path, or "sudo"/"docker" for the isolated
	// variants).
	Command string
	// Args is the argv after Command. For the plain entry this is exactly
	// ["mcp", "--context", <name>].
	Args []string
}

// PlainEntry is the standard (non-isolated) entry: the stable absolute binary
// path running `mcp --context <name>`. contextName is expected to satisfy the
// config-name charset (client/config.ValidName); callers validate before
// building privileged variants.
func PlainEntry(binPath, contextName string) Entry {
	return Entry{Command: binPath, Args: []string{"mcp", "--context", contextName}}
}

// SudoShimEntry is the §3.7.5 rung-2 isolated entry: the runtime spawns
// `sudo -n -u <serviceUser> /abs/jentic mcp --context <name>`. sudo inherits
// stdin/stdout so the JSON-RPC pipe survives; `-n` because a GUI spawn can
// never answer a password prompt (the matching argv-pinned NOPASSWD sudoers
// line makes the prompt unnecessary). The argv after the service user is
// EXACTLY the command the sudoers rule pins, so the entry cannot be replayed
// with a different context.
func SudoShimEntry(serviceUser, binPath, contextName string) Entry {
	return Entry{
		Command: "sudo",
		Args:    append([]string{"-n", "-u", serviceUser}, McpArgv(binPath, contextName)...),
	}
}

// McpArgv is the exact argv (command first) the sudo shim runs and the
// sudoers rule pins: `/abs/jentic mcp --context <name>`. One source of truth
// so the entry and the rule can never drift apart.
func McpArgv(binPath, contextName string) []string {
	return []string{binPath, "mcp", "--context", contextName}
}

// ContainerEntry is the container-isolation variant (§3.7.5, the
// ecosystem-normal rung, offered first where Docker Desktop is present): a
// hardened `docker run -i --rm` with the standard flags and a named state
// volume holding the runtime's own jentic config, running `jentic mcp
// --context <name>` inside. The desktop user's side holds only this spawn
// line; key material lives in the volume, not in any desktop-user file.
func ContainerEntry(image, contextName string) Entry {
	return Entry{
		Command: "docker",
		Args: []string{
			"run", "-i", "--rm",
			"--read-only",
			"--cap-drop=ALL",
			"--security-opt", "no-new-privileges",
			"--volume", ContainerStateVolume(contextName) + ":/home/jentic",
			image,
			"jentic", "mcp", "--context", contextName,
		},
	}
}

// ContainerStateVolume is the named Docker volume that holds a containerized
// entry's jentic state (config, key, tokens), one per pinned context so
// runtimes never share key material.
func ContainerStateVolume(contextName string) string {
	return "jentic-mcp-" + contextName
}

// Env carries the probes the writers need, injected so detection and target
// resolution are deterministic in tests (mirrors skillgen.DetectEnv).
type Env struct {
	Home     string                       // user home directory
	GOOS     string                       // runtime.GOOS ("darwin", "linux", ...)
	Stat     func(string) bool            // reports whether a path exists
	LookPath func(string) (string, error) // resolves a binary on PATH
}

func (e Env) exists(p string) bool {
	return e.Stat != nil && e.Stat(p)
}

func (e Env) has(name string) bool {
	if e.LookPath == nil {
		return false
	}
	_, err := e.LookPath(name)
	return err == nil
}

// CursorConfigPath is Cursor's user-scope MCP config.
func CursorConfigPath(home string) string {
	return filepath.Join(home, ".cursor", "mcp.json")
}

// ClaudeDesktopConfigPath is Claude Desktop's config file for the given OS.
// Desktop keeps it under the platform application-support dir, not a dotdir.
// Returns "" on platforms where Claude Desktop has no known location we
// support writing (we only automate macOS/Linux; Windows would need %APPDATA%).
func ClaudeDesktopConfigPath(home, goos string) string {
	switch goos {
	case "darwin":
		return filepath.Join(home, "Library", "Application Support", "Claude", "claude_desktop_config.json")
	case "linux":
		return filepath.Join(home, ".config", "Claude", "claude_desktop_config.json")
	default:
		return ""
	}
}

// CodexConfigPath is Codex's TOML config.
func CodexConfigPath(home string) string {
	return filepath.Join(home, ".codex", "config.toml")
}

// Detect reports whether the given runtime looks present in env. Detection is
// aligned with skillgen's operator probes where a probe exists, plus the
// runtime-specific artifacts MCP registration itself needs (Claude Desktop's
// config dir; the `claude` binary for the exec route).
func Detect(r Runtime, env Env) bool {
	switch r {
	case RuntimeCursor:
		return env.exists(filepath.Join(env.Home, ".cursor")) ||
			env.has("cursor") || env.has("cursor-agent")
	case RuntimeClaudeDesktop:
		p := ClaudeDesktopConfigPath(env.Home, env.GOOS)
		// The parent dir existing is the desktop-app-installed signal; the
		// config file itself may not exist until first run.
		return p != "" && (env.exists(p) || env.exists(filepath.Dir(p)))
	case RuntimeClaudeCode:
		// The exec route needs the claude CLI itself.
		return env.has("claude")
	case RuntimeCodex:
		return env.exists(filepath.Join(env.Home, ".codex")) || env.has("codex")
	default:
		return false
	}
}

// Outcome reports what applying one runtime's entry did.
type Outcome struct {
	Runtime Runtime
	// Path is the config file written, or a human description of the exec
	// route for claude-code.
	Path string
	// Changed reports whether anything was (or would be) written.
	Changed bool
	// Created reports the target file did not exist before.
	Created bool
}

// String renders the outcome target for display.
func (o Outcome) String() string {
	return fmt.Sprintf("%s -> %s", o.Runtime, o.Path)
}
