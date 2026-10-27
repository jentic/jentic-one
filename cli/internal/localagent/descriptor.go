package localagent

// Descriptor describes one known coding agent so adding an agent is data, not
// code. Keyed in Registry by the identifier the operator types (e.g. "claude").
type Descriptor struct {
	// ID is the identifier the operator types: `jentic run <ID>`.
	ID string
	// Binary is the executable name to probe with `command -v` and to exec.
	Binary string
	// ProbePaths are well-known install locations, used to tell "missing"
	// apart from "installed but not on PATH". Tilde is expanded per user.
	ProbePaths []string
	// Install is the documented fresh-install command, run as the agent user.
	Install string
	// SingleBinary reports whether the agent is a self-contained single file,
	// so copying the operator's binary is offered as the default provision route.
	SingleBinary bool
	// ConfigPaths are the agent's non-secret configuration files/dirs under the
	// operator's home (tilde-relative, e.g. "~/.claude.json"), which `jentic
	// run` can seed into the agent's home so the agent inherits the operator's
	// settings. They may include provider-specific credentials the operator has
	// stored locally — see CopyConfigCmd's caveat.
	ConfigPaths []string
	// SecretConfigPaths are discrete credential files that live INSIDE a seeded
	// ConfigPaths tree but hold nothing but secrets (e.g. Codex's
	// "~/.codex/auth.json", Hermes's "~/.hermes/.env"). Unlike Claude — where the
	// API key is embedded in the very config the agent needs — these are separable
	// credential files, so after seeding they are scrubbed from the agent's home
	// by default (ScrubSecretsCmd): the agent inherits the operator's SETTINGS but
	// authenticates as itself, keeping the operator's raw keys out of the agent
	// account. Tilde-relative, expanded per user.
	SecretConfigPaths []string
}

// Registry is the set of known RUNNABLE agents — the ones `jentic run <id>` can
// launch as a confined agent user. New runnable agents are added as rows here.
// It deliberately does NOT include skill-only operators (see SkillOnly): those
// are targets `jentic skill` writes onboarding docs for but that `jentic run`
// cannot launch (e.g. "generic", which has no binary).
var Registry = map[string]Descriptor{
	"claude": {
		ID:           "claude",
		Binary:       "claude",
		ProbePaths:   []string{"~/.local/bin/claude"},
		Install:      "curl -fsSL https://claude.ai/install.sh | bash",
		SingleBinary: true,
		// Claude Code keeps its user settings in ~/.claude/ (settings, agents,
		// commands) and ~/.claude.json (the top-level config). Seeding these
		// gives the agent account the operator's Claude Code setup; the operator
		// still authenticates separately on first launch.
		ConfigPaths: []string{"~/.claude", "~/.claude.json"},
	},
	"codex": {
		ID:         "codex",
		Binary:     "codex",
		ProbePaths: []string{"~/.local/bin/codex", "~/.codex/bin/codex"},
		Install:    "curl -fsSL https://chatgpt.com/codex/install.sh | sh",
		// The Codex CLI ships as a self-contained Rust binary, so copying the
		// operator's copy is offered as the default provision route.
		SingleBinary: true,
		// Codex keeps its config, auth, and MCP settings under ~/.codex/. Seeding
		// it hands the agent the operator's Codex setup; note ~/.codex/auth.json
		// carries an API key — scrubbed by ScrubSecretsCmd before the agent runs.
		ConfigPaths:       []string{"~/.codex"},
		SecretConfigPaths: []string{"~/.codex/auth.json"},
	},
	"cursor": {
		ID:         "cursor",
		Binary:     "cursor-agent",
		ProbePaths: []string{"~/.local/bin/cursor-agent"},
		Install:    "curl https://cursor.com/install -fsS | bash",
		// cursor-agent installs via its own script into ~/.local/bin (it is not a
		// single copyable file — the installer lays down a versioned tree), so the
		// install route is the only provision path. This targets the HEADLESS
		// cursor-agent CLI, not the Cursor GUI, which cannot run under a separate
		// confined Unix account.
		SingleBinary: false,
		// cursor-agent keeps its config/session state under ~/.cursor/.
		ConfigPaths: []string{"~/.cursor"},
	},
	"hermes": {
		ID:         "hermes",
		Binary:     "hermes",
		ProbePaths: []string{"~/.local/bin/hermes"},
		Install:    "curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash",
		// The Hermes installer builds a Python venv + git checkout under
		// ~/.hermes/hermes-agent and symlinks the launcher into ~/.local/bin — not
		// a single copyable file, so provisioning uses the install route only.
		SingleBinary: false,
		// Hermes keeps config and sessions under ~/.hermes/ (config.yaml + .env).
		// ~/.hermes/.env holds provider API keys — scrubbed by ScrubSecretsCmd
		// before the agent runs.
		ConfigPaths:       []string{"~/.hermes"},
		SecretConfigPaths: []string{"~/.hermes/.env"},
	},
}

// SkillOnly is the set of skillgen operators that `jentic run` intentionally
// cannot launch: they name a skill/instruction target rather than a runnable
// coding-agent binary. Kept alongside Registry so the unknown-agent error can
// tell "skill-only operator" apart from a genuine typo, and so a registry-parity
// test can assert every skillgen operator is either runnable or listed here.
var SkillOnly = map[string]struct{}{
	"generic": {}, // AGENTS.md placement only — no binary to run
}

// IsSkillOnly reports whether id names a known skill-only operator (a valid
// operator that `jentic run` cannot launch because it has no runnable binary).
func IsSkillOnly(id string) bool {
	_, ok := SkillOnly[id]
	return ok
}

// Known returns the sorted list of known agent identifiers, for error messages.
func Known() []string {
	ids := make([]string, 0, len(Registry))
	for id := range Registry {
		ids = append(ids, id)
	}
	// Small, stable ordering without importing sort for one call site.
	for i := 1; i < len(ids); i++ {
		for j := i; j > 0 && ids[j-1] > ids[j]; j-- {
			ids[j-1], ids[j] = ids[j], ids[j-1]
		}
	}
	return ids
}

// Lookup returns the descriptor for id and whether it is known.
func Lookup(id string) (Descriptor, bool) {
	d, ok := Registry[id]
	return d, ok
}
