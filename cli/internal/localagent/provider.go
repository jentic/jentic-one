package localagent

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
)

// ProviderConfig describes the LLM provider an operator's Claude Code setup
// authenticates against, and the local config paths that hold that provider's
// settings. It is derived from the env block of ~/.claude/settings.json.
type ProviderConfig struct {
	// Name is the human provider label ("aws", "vertex", "anthropic").
	Name string
	// ConfigPaths are tilde-relative provider config paths under the operator's
	// home to seed into the agent's home (e.g. "~/.aws"). Empty for the default
	// Anthropic API, where the credential travels in the agent config itself and
	// there is no separate provider config to copy.
	ConfigPaths []string
}

// claudeSettings is the subset of ~/.claude/settings.json we read: the env block
// carries the provider selection (CLAUDE_CODE_USE_BEDROCK / _USE_VERTEX) that
// tells Claude Code which cloud provider's credentials to authenticate with.
type claudeSettings struct {
	Env map[string]string `json:"env"`
}

// DetectProvider reads the operator's ~/.claude/settings.json env block and
// returns the LLM provider that Claude Code will authenticate against, plus the
// provider config paths (under the operator's home) that back it.
//
//   - CLAUDE_CODE_USE_BEDROCK=1 → AWS Bedrock; config lives in ~/.aws (profiles,
//     SSO session). Only the *config* is seeded — Claude Code performs the SSO
//     login programmatically, so the cached SSO and CLI credential caches are
//     excluded from the copy (seedTokenCaches).
//   - CLAUDE_CODE_USE_VERTEX=1 → Google Vertex; config lives in ~/.config/gcloud,
//     plus any explicit GOOGLE_APPLICATION_CREDENTIALS file. gcloud's credential
//     and token stores are excluded the same way, so the agent runs its own
//     `gcloud auth` login.
//   - otherwise → the default Anthropic API, whose key (if any) is already in the
//     agent config we seed separately, so there is no extra provider config.
//
// A missing/unparseable settings.json returns the Anthropic default (no extra
// paths) rather than an error: seeding is best-effort and always opt-in.
func DetectProvider(operatorHome string) ProviderConfig {
	env := readClaudeEnv(operatorHome)
	switch {
	case isTruthy(env["CLAUDE_CODE_USE_BEDROCK"]):
		return ProviderConfig{Name: "aws", ConfigPaths: []string{"~/.aws"}}
	case isTruthy(env["CLAUDE_CODE_USE_VERTEX"]):
		paths := []string{"~/.config/gcloud"}
		if creds := env["GOOGLE_APPLICATION_CREDENTIALS"]; creds != "" {
			paths = append(paths, creds)
		}
		return ProviderConfig{Name: "vertex", ConfigPaths: paths}
	default:
		return ProviderConfig{Name: "anthropic"}
	}
}

// readClaudeEnv parses the env map from ~/.claude/settings.json, returning an
// empty map when the file is absent or malformed.
func readClaudeEnv(operatorHome string) map[string]string {
	if operatorHome == "" {
		return nil
	}
	data, err := os.ReadFile(filepath.Join(operatorHome, ".claude", "settings.json")) //nolint:gosec // operatorHome is the current user's resolved home dir, not user input.
	if err != nil {
		return nil
	}
	var s claudeSettings
	if err := json.Unmarshal(data, &s); err != nil {
		return nil
	}
	return s.Env
}

// isTruthy reports whether a settings env value enables a boolean flag. Claude
// Code treats "1"/"true" as on; anything else (incl. "0", "") is off.
func isTruthy(v string) bool {
	switch strings.ToLower(strings.TrimSpace(v)) {
	case "1", "true":
		return true
	default:
		return false
	}
}

// ProviderConfigPaths expands a provider's tilde-relative ConfigPaths against
// operatorHome and returns those that actually exist on disk, so the caller only
// offers to copy what's there.
func ProviderConfigPaths(operatorHome string, pc ProviderConfig) []string {
	var found []string
	for _, p := range pc.ConfigPaths {
		abs := expandTilde(p, operatorHome)
		if abs == "" {
			continue
		}
		if _, err := os.Stat(abs); err == nil {
			found = append(found, abs)
		}
	}
	return found
}

// AgentHasPaths reports whether the agent user already has any of the given
// tilde-relative-or-absolute paths in its own home, so provider config is only
// seeded once (a re-run won't clobber the agent's evolved config).
func AgentHasPaths(ctx context.Context, agentUser string, paths []string) bool {
	for _, p := range paths {
		if runAsAgent(ctx, agentUser, "test -e "+quoteProbePath(agentRelPath(p))) == nil {
			return true
		}
	}
	return false
}

// agentRelPath maps an operator-side absolute provider path back to a
// tilde-relative path so AgentHasPaths can probe the equivalent location in the
// agent's home. A path already under "~/" is returned unchanged; an absolute
// path under a home is re-rooted to "~/…"; anything else is returned as-is.
func agentRelPath(p string) string {
	if strings.HasPrefix(p, "~/") || p == "~" {
		return p
	}
	// Absolute paths from ProviderConfigPaths live under the operator's home;
	// re-root the last home-relative segment onto "~/". We only know the operator
	// home here via OperatorHome(); fall back to the basename if it's not a child.
	home := OperatorHome()
	if home != "" {
		if rel, ok := strings.CutPrefix(p, filepath.Clean(home)+string(filepath.Separator)); ok {
			return "~/" + rel
		}
	}
	return p
}

// expandTilde resolves a leading "~/" (or bare "~") in p against home.
func expandTilde(p, home string) string {
	if home == "" {
		return ""
	}
	if p == "~" {
		return home
	}
	if rest, ok := strings.CutPrefix(p, "~/"); ok {
		return filepath.Join(home, rest)
	}
	return p
}
