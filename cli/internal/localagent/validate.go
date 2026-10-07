package localagent

import (
	"errors"
	"fmt"
	"path/filepath"
	"runtime"
	"strings"
)

// This file is the single choke point that keeps operator-editable inputs — the
// agent account name and its home directory — from reaching a shell command,
// sudoers line, ACL entry, or SBPL profile as anything other than a benign,
// well-formed token. Both are threaded through `sudo -u <name>`, `setfacl -m
// u:<name>`, macOS `chmod +a "user:<name> allow …"`, the sudoers rule
// `<operator> ALL=(<name>) …`, and `--home <homeDir>`; a name or path carrying a
// space, quote, newline, or shell metacharacter could break out of any of those.
// Rather than trust per-sink quoting, we constrain the inputs at the source to a
// shape that is safe in EVERY sink, and re-check in localagent before any command
// is built (defence in depth: the form validates, but a hand-edited config.yaml
// or a --agent-user flag bypasses the form).

// maxAgentUserLen bounds the account name. Linux useradd rejects names over 32
// chars; we keep the same ceiling on macOS for parity so a name that validates
// here creates on both platforms.
const maxAgentUserLen = 32

// ValidateAgentUser reports whether name is a safe, well-formed Unix account name
// for the agent user. The grammar is the portable-username shape
// (^[a-z_][a-z0-9_-]{0,31}$): it starts with a lowercase letter or underscore and
// contains only lowercase letters, digits, underscores, and hyphens — no spaces,
// quotes, slashes, dots, newlines, or shell metacharacters. That makes the name
// safe to interpolate into every sink it reaches (sudo runas spec, sudoers rule,
// setfacl/chmod ACL entries, `sudo -u`) without relying on downstream quoting.
func ValidateAgentUser(name string) error {
	if name == "" {
		return errors.New("agent account name is empty")
	}
	if len(name) > maxAgentUserLen {
		return fmt.Errorf("agent account name %q is too long (max %d characters)", name, maxAgentUserLen)
	}
	for i, r := range name {
		ok := r == '_' ||
			(r >= 'a' && r <= 'z') ||
			(i > 0 && (r >= '0' && r <= '9' || r == '-'))
		if !ok {
			return fmt.Errorf("agent account name %q is not a valid Unix username "+
				"(use lowercase letters, digits, '_' and '-'; must start with a letter or '_')", name)
		}
	}
	return nil
}

// AgentHomeRoot is the shared parent every agent home must live under: a
// world-traversable, root-owned directory OUTSIDE any human's home, so the
// operator can be granted into the agent home without widening their own home and
// the agent home can never alias a human home. macOS uses /Users/Shared, Linux
// uses /opt — the same roots DefaultHomeDir builds under.
func AgentHomeRoot() string {
	if runtime.GOOS == "darwin" {
		return "/Users/Shared"
	}
	return "/opt"
}

// ValidateHomeDir reports whether homeDir is a safe, well-formed home directory
// for the agent account. It must be an absolute, already-cleaned path (no "..",
// no "." segments, no trailing slash, no doubled separators) that is a strict
// descendant of AgentHomeRoot() — never the root itself, never a human home, never
// a system tree. It must also carry no control characters (which could inject a
// newline into an SBPL profile or a shell here-doc). Constraining the home to the
// shared root is what lets the confinement layer trust that re-opening the agent
// home never re-opens a human home.
func ValidateHomeDir(homeDir string) error {
	if homeDir == "" {
		return errors.New("agent home directory is empty")
	}
	if err := rejectControlChars("agent home directory", homeDir); err != nil {
		return err
	}
	if !filepath.IsAbs(homeDir) {
		return fmt.Errorf("agent home directory %q must be an absolute path", homeDir)
	}
	if filepath.Clean(homeDir) != homeDir {
		return fmt.Errorf("agent home directory %q must be a clean path "+
			"(no '..', '.', trailing '/', or repeated '/')", homeDir)
	}
	root := AgentHomeRoot()
	if homeDir == root || !strings.HasPrefix(homeDir, root+string(filepath.Separator)) {
		return fmt.Errorf("agent home directory %q must live under %s "+
			"(so it stays outside every human's home)", homeDir, root)
	}
	// A strict descendant only: reject a path that resolves back up to the root
	// via the leaf being empty (already excluded by Clean, but explicit here).
	if rest := strings.TrimPrefix(homeDir, root+string(filepath.Separator)); rest == "" {
		return fmt.Errorf("agent home directory %q must name a subdirectory under %s", homeDir, root)
	}
	return nil
}

// ValidateAccount validates the agent account name and home directory together —
// the single call every path builds on before it constructs a privileged command
// from either value. Returns the first problem found.
func ValidateAccount(name, homeDir string) error {
	if err := ValidateAgentUser(name); err != nil {
		return err
	}
	return ValidateHomeDir(homeDir)
}

// ValidateConfigDir was the guard for destructive ops on the recorded legacy
// config_dir (`rm -rf`/`chown -R` of the agent's <home>/.jentic). It is gone
// because nothing operates on a recorded config_dir any more: new accounts don't
// record one, and reset derives the identity dirs to remove from the (already
// ValidateHomeDir-validated) home via AgentIdentityDirs — fixed joins that can't
// be steered by a hand-edited record.

// rejectControlChars returns an error if s contains any ASCII control character
// (including newline, carriage return, and tab). These are the characters that
// let a value break out of a single line — into a second SBPL rule, a second
// sudoers line, or a new shell command — so they are refused before the value is
// ever formatted into one of those.
func rejectControlChars(label, s string) error {
	for _, r := range s {
		if r < 0x20 || r == 0x7f {
			return fmt.Errorf("%s contains a control character and cannot be used", label)
		}
	}
	return nil
}

// ValidateGrantPath reports whether dir is safe to interpolate into an ACL/shell
// grant command: an absolute, cleaned path free of control characters. Grant
// paths come from the operator (a working directory or a --grant flag) and from
// the recorded config, and they reach setfacl/chmod/bwrap/SBPL, so they are held
// to the same no-control-character bar as the home. (Sensitivity — which paths
// may be granted at all — is a separate check, Classify.)
func ValidateGrantPath(dir string) error {
	if dir == "" {
		return errors.New("grant path is empty")
	}
	if err := rejectControlChars("grant path", dir); err != nil {
		return err
	}
	if !filepath.IsAbs(dir) {
		return fmt.Errorf("grant path %q must be absolute", dir)
	}
	return nil
}
