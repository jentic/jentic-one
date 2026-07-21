// Package config holds filesystem paths and default settings for the Jentic CLI.
package config

import (
	"os"
	"path/filepath"
	"strings"
)

const (
	// DefaultBrokerScheme is the scheme of the broker target used by execute.
	DefaultBrokerScheme = "https"
	// DefaultBrokerHost is the host of the broker target used by execute. It is a
	// bare host[:port] with no scheme — the scheme lives in DefaultBrokerScheme
	// (and broker.scheme in config.yaml). Callers assemble the URL as
	// scheme + "://" + host, so embedding a scheme here would double it.
	DefaultBrokerHost = "127.0.0.1:8100"

	// DefaultBaseURL is the Jentic control-plane (auth surface) base URL used for
	// agent registration and token minting.
	DefaultBaseURL = "http://127.0.0.1:8000"

	// DefaultProfile is the profile name used when none is specified.
	DefaultProfile = "default"

	// SPAMountPath is the URL prefix the web console (SPA) is served under. The
	// server mounts the React app at /app (see shared/web/static.py), so every
	// client-side route (/login, /setup, /agents, …) lives under this prefix in
	// the browser. Centralised here so CLI surfaces that point users at the
	// console build correct deep links instead of hand-concatenating /login etc.
	// onto the bare base URL (which 404s).
	SPAMountPath = "/app"

	// dirName is the per-user Jentic state directory under $HOME. Everything the
	// CLI owns — config, data, logs — is rooted here.
	dirName = ".jentic"

	// HomeEnv overrides the root directory (mainly for tests and unusual setups).
	HomeEnv = "JENTIC_HOME"

	// ProfileEnv overrides the active profile for the current shell (AWS_PROFILE
	// style). It sits between the --profile flag and config.yaml default_profile
	// in precedence.
	ProfileEnv = "JENTIC_PROFILE"

	dataDirName     = "data"
	logsDirName     = "logs"
	venvDirName     = "venv"
	srcDirName      = "src"
	profilesDirName = "profiles"

	// InstallConfigName is the generated app config file written by
	// `jentic install`.
	InstallConfigName = "jentic-one.yaml"

	// ManifestName records what was installed (repo, ref, commit, deploy mode)
	// so `jentic update` knows what to track and how to refresh it.
	ManifestName = "install.json"

	caCertName = "ca.pem"
	caKeyName  = "ca.key"

	// caTrustedName marks that we successfully added the CA to the OS trust
	// store (and/or Java keystore). The cert file (ca.pem) is generated
	// unconditionally at install, but trusting it system-wide is opt-in, so its
	// presence on disk does NOT imply a trust entry exists. Gating untrust on
	// this marker (rather than on ca.pem) keeps `uninstall`/`trust --remove`
	// from issuing removals for a CA that was never trusted — which surface as
	// alarming "item could not be found" / keychain errors. See issue #650.
	caTrustedName = "ca.trusted"

	// appPidName holds the PID of the app started in the background by
	// `jentic install`.
	appPidName = "app.pid"

	// brokerPidName holds the PID of the broker service started in the
	// background by `jenticctl install`/`jenticctl start` on the local path.
	brokerPidName = "broker.pid"

	// composeName is the generated docker-compose file written by the Docker
	// install path. Its presence marks the install as containerized so the
	// lifecycle commands (start/stop) drive compose instead of a local process.
	composeName = "docker-compose.yaml"
)

// Paths resolves every filesystem location the CLI owns, rooted at a single
// directory (default ~/.jentic, overridable via $JENTIC_HOME). Construct it once
// with NewPaths and thread it through; tests use Paths{Root: t.TempDir()}.
//
// The location getters are pure string joins (no side effects); use Ensure to
// create a directory before writing into it.
type Paths struct {
	// Root is the per-user Jentic state directory.
	Root string
}

// NewPaths resolves the root directory: $JENTIC_HOME if set, else ~/.jentic.
func NewPaths() (Paths, error) {
	if root := os.Getenv(HomeEnv); root != "" {
		return Paths{Root: root}, nil
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return Paths{}, err
	}
	return Paths{Root: filepath.Join(home, dirName)}, nil
}

// Dir returns the root directory (~/.jentic).
func (p Paths) Dir() string { return p.Root }

// DataDir returns ~/.jentic/data, the base for local databases (SQLite files).
func (p Paths) DataDir() string { return filepath.Join(p.Root, dataDirName) }

// LogsDir returns ~/.jentic/logs.
func (p Paths) LogsDir() string { return filepath.Join(p.Root, logsDirName) }

// VenvPath returns ~/.jentic/venv (created by the venv tool, not here).
func (p Paths) VenvPath() string { return filepath.Join(p.Root, venvDirName) }

// SrcPath returns ~/.jentic/src (created by git on clone).
func (p Paths) SrcPath() string { return filepath.Join(p.Root, srcDirName) }

// CACertPath returns the root CA certificate path (~/.jentic/ca.pem).
func (p Paths) CACertPath() string { return filepath.Join(p.Root, caCertName) }

// CATrustedMarkerPath returns the path of the marker written when the CA is
// added to the OS trust store, so untrust only runs when trust actually ran.
func (p Paths) CATrustedMarkerPath() string { return filepath.Join(p.Root, caTrustedName) }

// CAKeyPath returns the root CA private key path (~/.jentic/ca.key).
func (p Paths) CAKeyPath() string { return filepath.Join(p.Root, caKeyName) }

// ConfigPath returns the CLI settings file path (~/.jentic/config.yaml).
func (p Paths) ConfigPath() string { return filepath.Join(p.Root, ConfigName) }

// InstallConfigPath returns the generated app config path (~/.jentic/jentic-one.yaml).
func (p Paths) InstallConfigPath() string { return filepath.Join(p.Root, InstallConfigName) }

// ManifestPath returns the install manifest path (~/.jentic/install.json).
func (p Paths) ManifestPath() string { return filepath.Join(p.Root, ManifestName) }

// AppPIDPath returns the background app PID file path (~/.jentic/app.pid).
func (p Paths) AppPIDPath() string { return filepath.Join(p.Root, appPidName) }

// BrokerPIDPath returns the background broker PID file path
// (~/.jentic/broker.pid). The broker runs as its own process on the local path.
func (p Paths) BrokerPIDPath() string { return filepath.Join(p.Root, brokerPidName) }

// ComposePath returns the generated docker-compose file path
// (~/.jentic/docker-compose.yaml). Its existence marks a Docker install.
func (p Paths) ComposePath() string { return filepath.Join(p.Root, composeName) }

// ProfilesDir returns the directory holding per-agent profiles.
func (p Paths) ProfilesDir() string { return filepath.Join(p.Root, profilesDirName) }

// Ensure creates dir (and parents) with 0700 perms and returns it. Pass any of
// the location getters, e.g. paths.Ensure(paths.DataDir()).
func (p Paths) Ensure(dir string) (string, error) {
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return "", err
	}
	return dir, nil
}

// AppURL builds an absolute URL to a client-side SPA route, joining baseURL
// with the /app mount prefix and the given route. Use this for every CLI
// surface that points a human at the web console (sign-in, setup, agents) so
// deep links resolve instead of 404ing on the bare base URL. route may be given
// with or without a leading slash; an empty route yields the SPA root (/app).
func AppURL(baseURL, route string) string {
	base := strings.TrimRight(strings.TrimSpace(baseURL), "/")
	route = strings.Trim(strings.TrimSpace(route), "/")
	if route == "" {
		return base + SPAMountPath
	}
	return base + SPAMountPath + "/" + route
}
