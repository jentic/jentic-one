package ctlcmd

import (
	"fmt"
	"io"

	"github.com/jentic/jentic-one/cli/internal/install"
	"github.com/jentic/jentic-one/cli/internal/theme"
)

// requireDockerDaemon guards the Docker-backed commands (`start`, `stop`,
// `update`, `setup`, `reset-password`) against a stopped daemon, surfacing
// actionable recovery guidance instead of a raw `docker compose` transport
// error. It is a package-level seam so tests can simulate an up/down daemon
// without a real Docker (#783, jentic-api-scorecard#224).
//
// It can block up to ~30s while the underlying probe waits out a cold-starting
// daemon, so callers must announce the check first (via announceDaemonCheck)
// or the command looks like it hung. The ctx (the command's context) lets an
// operator cancel that wait with Ctrl-C (#953). Tests that swap this global
// must not run with t.Parallel().
var requireDockerDaemon = install.RequireDockerDaemon

// composeUp / composeDown / composeDownVolumes are package-level seams over the
// install package's docker-compose helpers so the Docker-backed commands can be
// tested (guard pass-through, output) without shelling out to a real Docker.
// Tests that swap these globals must not run with t.Parallel().
var (
	composeUp          = install.ComposeUp
	composeDown        = install.ComposeDown
	composeDownVolumes = install.ComposeDownVolumes
)

// daemonCheckMsg is the single announce line every Docker-backed command prints
// before requireDockerDaemon, so the ~30s cold-start wait doesn't look like a
// hang. Kept in one place so the wording (and the advertised window) can't
// drift between commands.
const daemonCheckMsg = "Checking the Docker daemon (waiting up to ~30s if it is still starting) ..."

// announceDaemonCheck prints daemonCheckMsg. Call it immediately before
// requireDockerDaemon on any command that probes the daemon.
func announceDaemonCheck(w io.Writer) {
	fmt.Fprintln(w, theme.Infof(daemonCheckMsg))
}
