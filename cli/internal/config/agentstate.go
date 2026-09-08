package config

import (
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"

	"gopkg.in/yaml.v3"

	sdkconfig "github.com/jentic/jentic-one/cli/client/config"
)

// AgentStateName is the localagent state file under the XDG config dir
// (~/.config/jentic/agent-account.yaml). It is the OPERATOR-side record of the
// shared agent account: whether it exists, where its home lives, and which
// directories it has been granted — paths and names only, never secrets.
//
// It lives in the operator's XDG config tree (like the rest of the V2 store)
// rather than the legacy ~/.jentic root: localagent no longer reads or writes
// ~/.jentic for its own state. It is a SEPARATE file from config.yaml (the
// contexts/environments/identities store) because it is CLI-internal — not part
// of the public SDK schema — and has its own writers (`jentic run` grants) that
// must not contend with SDK config mutations. Crucially, both ~/.config/jentic
// and the legacy ~/.jentic are hard-banned grant targets and sandbox-denied, so
// the agent can never edit its own access record wherever it lives.
const AgentStateName = "agent-account.yaml"

// agentStateLockName is the flock sidecar for AgentStateName. A separate lock
// file (never the state file itself) so the atomic rename in saveAgentState can
// never replace the file the lock is held on.
const agentStateLockName = "agent-account.lock"

// AgentState is the on-disk agent-account record. It carries exactly the two
// pieces of localagent state that used to live in the legacy ~/.jentic
// config.yaml: the account record and the one-time same-user notice flag.
type AgentState struct {
	// Agent is the SINGLE dedicated Unix account every local coding agent runs
	// under (one per human). Nil until the operator has been through the
	// agent-user decision at least once.
	Agent *AgentAccount `yaml:"agent_account,omitempty"`
	// SameUserNoticeSeen records that the operator has already been shown the
	// one-time notice that `jentic run` is launching the agent same-user (no
	// dedicated account, no confinement). Set the first time that unconfined
	// path is taken so the security notice informs once, not on every launch.
	SameUserNoticeSeen bool `yaml:"same_user_notice_seen,omitempty"`

	// Path records where the state was (or would be) persisted.
	Path string `yaml:"-"`
	// Loaded reports whether the XDG state file was actually found and parsed.
	// False with non-zero fields means the values came from the legacy
	// ~/.jentic/config.yaml fallback and have not been adopted yet.
	Loaded bool `yaml:"-"`
}

// AgentStatePath resolves the agent-state file path under the XDG config dir.
func AgentStatePath() (string, error) {
	dir, err := sdkconfig.ConfigDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(dir, AgentStateName), nil
}

// LoadAgentState reads the agent-account record. The XDG file is authoritative;
// when it does not exist yet, the legacy ~/.jentic/config.yaml (via legacy
// Paths) is consulted READ-ONLY so accounts recorded by older releases keep
// working — listing grants, launching, and resetting all see the old record
// without requiring a migration first. The first MutateAgentState adopts the
// legacy values into the XDG file and clears them from the legacy config.
func LoadAgentState(legacy Paths) (*AgentState, error) {
	path, err := AgentStatePath()
	if err != nil {
		return nil, err
	}

	data, err := os.ReadFile(path) //nolint:gosec // path is the CLI's own XDG config dir, not user input.
	switch {
	case err == nil:
		st := &AgentState{}
		if uerr := yaml.Unmarshal(data, st); uerr != nil {
			return nil, fmt.Errorf("parse %s: %w", path, uerr)
		}
		st.Path = path
		st.Loaded = true
		return st, nil
	case errors.Is(err, fs.ErrNotExist):
		// Fall through to the legacy read below.
	default:
		return nil, fmt.Errorf("read %s: %w", path, err)
	}

	// No XDG state yet: project the legacy config's agent fields (read-only).
	cfg, err := Load(legacy)
	if err != nil {
		return nil, err
	}
	return &AgentState{
		Agent:              cfg.Agent,
		SameUserNoticeSeen: cfg.SameUserNoticeSeen,
		Path:               path,
	}, nil
}

// MutateAgentState performs a race-safe read-modify-write of the agent state:
// it takes an exclusive flock on the sidecar lock file, reloads the state from
// disk UNDER that lock (so it sees any change a concurrent writer committed —
// two concurrent `jentic run` grants must not drop each other's dir), applies
// fn, and saves atomically before releasing the lock.
//
// The first mutation on a machine with a legacy record ADOPTS it: the reload
// falls back to the legacy ~/.jentic/config.yaml, fn is applied on top, the
// result lands in the XDG file, and the agent fields are then cleared from the
// legacy config (best-effort) so exactly one copy of the record exists.
func MutateAgentState(legacy Paths, fn func(*AgentState) error) (*AgentState, error) {
	dir, err := sdkconfig.ConfigDir()
	if err != nil {
		return nil, err
	}
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return nil, fmt.Errorf("create config dir %s: %w", dir, err)
	}
	unlock, err := lockAgentState(dir)
	if err != nil {
		return nil, err
	}
	defer unlock()

	st, err := LoadAgentState(legacy)
	if err != nil {
		return nil, err
	}
	if err := fn(st); err != nil {
		return nil, err
	}
	if err := saveAgentState(st); err != nil {
		return nil, err
	}
	st.Loaded = true
	clearLegacyAgentState(legacy)
	return st, nil
}

// saveAgentState writes the state to its XDG path (0600) via the same
// atomic temp-file + rename dance as the legacy config writer, so a crash
// mid-write leaves either the old file or the new one, never a torn record.
func saveAgentState(st *AgentState) error {
	data, err := yaml.Marshal(st)
	if err != nil {
		return err
	}
	return writeFileAtomic(st.Path, data, 0o600)
}

// lockAgentState takes an exclusive advisory flock on the agent-state sidecar
// lock in dir, returning the release func. Same crash-safe semantics as
// lockConfig: the lock dies with the process.
func lockAgentState(dir string) (func(), error) {
	path := filepath.Join(dir, agentStateLockName)
	f, err := os.OpenFile(path, os.O_CREATE|os.O_RDWR, 0o600) //nolint:gosec // path is the CLI's own XDG config dir, not user input.
	if err != nil {
		return nil, fmt.Errorf("open agent-state lock %s: %w", path, err)
	}
	if err := lockFile(f); err != nil {
		_ = f.Close()
		return nil, fmt.Errorf("lock agent state %s: %w", path, err)
	}
	return func() {
		_ = unlockFile(f)
		_ = f.Close()
	}, nil
}

// clearLegacyAgentState drops the agent-account fields from the legacy
// ~/.jentic/config.yaml after they have been adopted into the XDG file, so a
// stale legacy copy can't drift from (or later shadow) the authoritative
// record. Best-effort by design: it only touches a legacy config that already
// exists AND still carries agent fields (it never creates ~/.jentic), and a
// failure here is invisible — the XDG file already holds the truth and wins on
// every future read.
func clearLegacyAgentState(legacy Paths) {
	cfg, err := Load(legacy)
	if err != nil || !cfg.Loaded || (cfg.Agent == nil && !cfg.SameUserNoticeSeen) {
		return
	}
	_, _ = Mutate(legacy, func(c *FileConfig) error {
		c.Agent = nil
		c.SameUserNoticeSeen = false
		return nil
	})
}

// AgentAccount returns the recorded agent account and whether one is recorded.
// A nil Agent (no account ever set up) returns a zero value and false.
func (s *AgentState) AgentAccount() (AgentAccount, bool) {
	if s.Agent == nil {
		return AgentAccount{}, false
	}
	return *s.Agent, true
}

// HasAgentUser reports whether a dedicated Unix agent account has been
// provisioned and is enabled. This is the single gate the rest of the CLI keys
// off: false means behave exactly as for an operator with no agent account.
func (s *AgentState) HasAgentUser() bool {
	return s.Agent != nil && s.Agent.AccountCreated && s.Agent.Enabled
}

// SetAgentAccount records (or replaces) the agent account entry. Call inside
// MutateAgentState so the write is race-safe and persisted.
func (s *AgentState) SetAgentAccount(acct AgentAccount) {
	a := acct
	s.Agent = &a
}

// ClearAgentAccount removes the agent-account record entirely — used when a full
// reset tears down the Unix account, its home, and every grant, so nothing in
// the state points at things that no longer exist.
func (s *AgentState) ClearAgentAccount() {
	s.Agent = nil
}

// AddGrantedDir records dir against the agent account (idempotently) and returns
// true if it was newly added. Returns false when no account exists.
func (s *AgentState) AddGrantedDir(dir string) bool {
	if s.Agent == nil {
		return false
	}
	for _, d := range s.Agent.GrantedDirs {
		if d == dir {
			return false
		}
	}
	s.Agent.GrantedDirs = append(s.Agent.GrantedDirs, dir)
	return true
}

// RemoveGrantedDir drops dir from the agent account's grant inventory and
// returns true if it was present.
func (s *AgentState) RemoveGrantedDir(dir string) bool {
	if s.Agent == nil {
		return false
	}
	for i, d := range s.Agent.GrantedDirs {
		if d == dir {
			s.Agent.GrantedDirs = append(s.Agent.GrantedDirs[:i], s.Agent.GrantedDirs[i+1:]...)
			return true
		}
	}
	return false
}
