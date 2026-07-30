package config

import (
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"time"
)

// DefaultRepo is the source repository the CLI and stack are built from. It is
// the manifest default when none was recorded and the target for update checks.
const DefaultRepo = "jentic/jentic-one"

// Deploy modes recorded in the manifest.
const (
	ModeLocal  = "local"
	ModeDocker = "docker"
)

// Manifest records what `jenticctl install` / the installer put on disk so
// `jenticctl update` knows what to track (which repo/ref) and how to refresh it
// (local venv vs Docker compose). It is written to ~/.jentic/install.json by
// both tools/install.sh (CLI fields) and `jenticctl install` (stack fields).
type Manifest struct {
	// Repo is the owner/name source repository (e.g. "jentic/jentic-one").
	Repo string `json:"repo,omitempty"`
	// Ref is the git branch, tag, or commit the install tracks.
	Ref string `json:"ref,omitempty"`
	// Commit is the short SHA that was built.
	Commit string `json:"commit,omitempty"`
	// CLIVersion is the version stamped into the installed binary.
	CLIVersion string `json:"cli_version,omitempty"`
	// BinaryPath is where the jentic binary was installed.
	BinaryPath string `json:"binary_path,omitempty"`
	// Mode is the stack deploy mode: "local" or "docker".
	Mode string `json:"mode,omitempty"`
	// DB is the configured database backend: "sqlite" or "postgres".
	DB string `json:"db,omitempty"`
	// BrokerPort is the port the broker service binds to. The broker runs as a
	// separate process/container (apps=broker) and lifecycle commands need its
	// port to (re)start it on the local path.
	BrokerPort string `json:"broker_port,omitempty"`
	// InstalledAt is the RFC3339 timestamp of the last write.
	InstalledAt string `json:"installed_at,omitempty"`
}

// ResolvedRepo returns the recorded repo or the built-in default.
func (m *Manifest) ResolvedRepo() string {
	if m.Repo != "" {
		return m.Repo
	}
	return DefaultRepo
}

// LoadManifest reads <paths>/install.json. A missing file is not an error: it
// returns a zero-value manifest and found=false so callers can fall back to
// build-time (ldflags) metadata.
func LoadManifest(paths Paths) (*Manifest, bool, error) {
	path := paths.ManifestPath()
	data, err := os.ReadFile(path) //nolint:gosec // path derived from the CLI's own JENTIC_HOME, not user input.
	if err != nil {
		if errors.Is(err, fs.ErrNotExist) {
			return &Manifest{}, false, nil
		}
		return nil, false, fmt.Errorf("read %s: %w", path, err)
	}
	m := &Manifest{}
	if err := json.Unmarshal(data, m); err != nil {
		return nil, false, fmt.Errorf("parse %s: %w", path, err)
	}
	return m, true, nil
}

// Save writes the manifest to <paths>/install.json (0600), stamping InstalledAt
// with the current time.
func (m *Manifest) Save(paths Paths) error {
	if _, err := paths.Ensure(paths.Dir()); err != nil {
		return err
	}
	m.InstalledAt = time.Now().UTC().Format(time.RFC3339)
	data, err := json.MarshalIndent(m, "", "  ")
	if err != nil {
		return err
	}
	path := paths.ManifestPath()
	if err := os.WriteFile(path, append(data, '\n'), 0o600); err != nil {
		return fmt.Errorf("write %s: %w", path, err)
	}
	return nil
}

// MergeStack updates the stack-related fields (mode, db, broker port) plus the
// CLI metadata known at runtime (ref, commit, version) on top of whatever the
// installer recorded, then saves. This is what `jenticctl install` calls so a
// re-install keeps any CLI fields written by tools/install.sh while refreshing
// the rest.
func (m *Manifest) MergeStack(paths Paths, mode, db, brokerPort, ref, commit, cliVersion string) error {
	m.Mode = mode
	m.DB = db
	if brokerPort != "" {
		m.BrokerPort = brokerPort
	}
	if ref != "" {
		m.Ref = ref
	}
	if commit != "" {
		m.Commit = commit
	}
	if cliVersion != "" {
		m.CLIVersion = cliVersion
	}
	if m.Repo == "" {
		m.Repo = DefaultRepo
	}
	return m.Save(paths)
}
