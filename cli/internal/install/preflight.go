package install

import (
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

// Requirement is an external tool the install needs on PATH. Extend
// requirementsFor to add new ones for a given install path.
type Requirement struct {
	// Name is the executable looked up on PATH.
	Name string
	// Why is a short reason the tool is needed.
	Why string
	// URL is an install hint shown when the tool is missing.
	URL string
	// Soft marks a requirement whose absence is a warning, not a failure:
	// RenderPreflight still shows it, but Missing() excludes it from the set
	// that fails the install (e.g. npm — the UI build is skipped, not fatal).
	Soft bool
	// Probe, when set, replaces the default exec.LookPath check with custom
	// logic (returns found, an optional path/version detail, and an optional
	// override for the "why it's missing" hint). Used for checks that aren't a
	// bare "is this binary on PATH" — e.g. "can uv resolve a Python 3.12
	// interpreter" or "is a source checkout / token available for the clone".
	Probe func() ProbeResult
}

// ProbeResult is what a custom Requirement.Probe returns.
type ProbeResult struct {
	Found bool
	// Detail is shown next to an OK row (a version/path); optional.
	Detail string
	// MissingWhy overrides Req.Why in the MISSING row when non-empty, so a
	// custom probe can give a more precise recovery hint than the static Why.
	MissingWhy string
}

// CheckResult is the outcome of probing a single Requirement.
type CheckResult struct {
	Req     Requirement
	Found   bool
	Path    string
	Version string
	// MissingWhy, when set by a custom probe, overrides Req.Why in the rendered
	// MISSING row (a probe-specific recovery hint).
	MissingWhy string
	// DaemonChecked is true when this requirement carries a daemon-health probe
	// (only `docker`). Healthy/DaemonDetail are meaningful only when true.
	DaemonChecked bool
	// Healthy reports whether the daemon answered (`docker info` succeeded).
	Healthy bool
	// DaemonDetail is a short human reason when the daemon is unhealthy.
	DaemonDetail string
}

// requirementsFor returns the tools needed to perform the install for the chosen
// path. Both paths are executed by the wizard: the local path builds a venv with
// uv, the Docker path builds the app image and runs it via docker compose.
func requirementsFor(d *Draft) []Requirement {
	if d.IsDocker() {
		reqs := []Requirement{
			{Name: "docker", Why: "builds the app image and runs the stack via docker compose", URL: "https://docs.docker.com/get-docker/"},
		}
		// Building the image needs the source tree; clone it when run outside a
		// checkout (mirrors the local path).
		if _, inRepo := RepoRoot(); !inRepo {
			reqs = append(reqs, Requirement{
				Name: "git",
				Why:  "clones the source to build the image",
				URL:  "https://git-scm.com/downloads",
			})
		}
		return reqs
	}

	reqs := []Requirement{
		{Name: "uv", Why: "creates the venv and installs from source", URL: "https://docs.astral.sh/uv/"},
	}
	// Python 3.12 is pinned by `uv venv --python 3.12` (build.go). Probe it now
	// (via uv, falling back to a python3.12 on PATH) so uv doesn't surprise-fetch
	// an interpreter or fail mid-build. Hard row.
	reqs = append(reqs, Requirement{
		Name:  "python3.12",
		Why:   "the venv is pinned to Python 3.12",
		URL:   "https://www.python.org/downloads/",
		Probe: probePython312,
	})
	// npm is only needed to build the SPA, and its absence is non-fatal — the
	// build logs "skipping UI build" and proceeds. Surface it as a SOFT row when
	// a UI tree is present, so the "SPA will not be available" consequence is
	// visible at preflight rather than scrolled past mid-build.
	if root, inRepo := RepoRoot(); inRepo {
		if _, err := os.Stat(filepath.Join(root, "ui", "package.json")); err == nil {
			reqs = append(reqs, Requirement{
				Name: "npm",
				Why:  "builds the UI (SPA); optional — the install proceeds without it",
				URL:  "https://nodejs.org/",
				Soft: true,
			})
		}
	}
	if _, inRepo := RepoRoot(); !inRepo {
		reqs = append(reqs, Requirement{
			Name: "git",
			Why:  "clones the source from GitHub",
			URL:  "https://git-scm.com/downloads",
		})
		// Cloning the (private) source needs either a local checkout via
		// $JENTIC_SRC or a GITHUB_TOKEN. Without either, the clone dead-ends
		// mid-build; fail at preflight instead, reusing the clone-failure
		// recovery wording so the guidance is identical.
		reqs = append(reqs, Requirement{
			Name:  "source-access",
			Why:   "cloning the private source needs a token or a local checkout",
			Probe: probeSourceAccess,
		})
	}
	return reqs
}

// probePython312 reports whether a Python 3.12 interpreter is resolvable for the
// pinned `uv venv --python 3.12` step: preferring uv's own resolver (which is
// what the build uses), then a bare `python3.12` on PATH. It never triggers a
// download — `uv python find` only reports an already-available interpreter.
func probePython312() ProbeResult {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if _, err := exec.LookPath("uv"); err == nil {
		out, err := exec.CommandContext(ctx, "uv", "python", "find", "3.12").CombinedOutput()
		if err == nil && strings.TrimSpace(string(out)) != "" {
			return ProbeResult{Found: true, Detail: "via uv: " + firstLine(string(out))}
		}
	}
	if path, err := exec.LookPath("python3.12"); err == nil {
		return ProbeResult{Found: true, Detail: path}
	}
	return ProbeResult{
		Found:      false,
		MissingWhy: "no Python 3.12 found; `uv venv --python 3.12` would try to fetch one mid-build",
	}
}

// probeSourceAccess reports whether the private-source clone will be able to
// authenticate before the build starts. Mirrors the clone-failure recovery text
// in build.go so the pre-build message reads identically.
func probeSourceAccess() ProbeResult {
	if os.Getenv(SrcEnv) != "" || os.Getenv("GITHUB_TOKEN") != "" {
		return ProbeResult{Found: true, Detail: "token or local checkout available"}
	}
	return ProbeResult{
		Found: false,
		MissingWhy: "set " + SrcEnv + "=/path/to/jentic-one to build from a local checkout, " +
			"or GITHUB_TOKEN=ghp_xxx (repo read scope) to clone",
	}
}

// Preflight probes every Requirement for the chosen path. ctx bounds the Docker
// daemon probe so a cold-start wait can be canceled (Ctrl-C).
func Preflight(ctx context.Context, d *Draft) []CheckResult {
	reqs := requirementsFor(d)
	results := make([]CheckResult, 0, len(reqs))
	for _, req := range reqs {
		res := CheckResult{Req: req}
		// A custom probe replaces the LookPath check (e.g. Python-3.12
		// resolution, source-access). Otherwise fall back to the binary lookup.
		if req.Probe != nil {
			pr := req.Probe()
			res.Found = pr.Found
			res.Version = pr.Detail
			res.MissingWhy = pr.MissingWhy
			results = append(results, res)
			continue
		}
		if path, err := exec.LookPath(req.Name); err == nil {
			res.Found = true
			res.Path = path
			res.Version = toolVersion(req.Name)
			// The docker binary being on PATH proves nothing about the daemon —
			// a stopped Docker Desktop / unhealthy daemon passes the LookPath
			// check but fails the build mid-way (see #653). Probe the daemon now
			// so we can fail fast with an actionable "start Docker" message.
			if req.Name == "docker" {
				res.DaemonChecked = true
				if detail, ok := dockerDaemonHealth(ctx); ok {
					res.Healthy = true
				} else {
					res.DaemonDetail = detail
				}
			}
		}
		results = append(results, res)
	}
	return results
}

// Missing returns the checks whose tool was not found, or whose daemon probe failed.
func Missing(results []CheckResult) []CheckResult {
	var missing []CheckResult
	for _, r := range results {
		// A soft requirement never fails the install — it's rendered as a
		// warning but excluded from the blocking set.
		if r.Req.Soft {
			continue
		}
		// A check is missing if the tool itself is absent, OR if it has a daemon
		// requirement that failed (i.e. the docker daemon is not responding).
		if !r.Found || (r.DaemonChecked && !r.Healthy) {
			missing = append(missing, r)
		}
	}
	return missing
}

// toolVersion best-effort reads the first line of `<name> --version`.
func toolVersion(name string) string {
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	out, err := exec.CommandContext(ctx, name, "--version").CombinedOutput()
	if err != nil {
		return ""
	}
	line := strings.TrimSpace(string(out))
	if i := strings.IndexByte(line, '\n'); i >= 0 {
		line = line[:i]
	}
	return line
}

// dockerDaemonProbe is the seam the daemon-health check runs through so tests
// can simulate a stopped/unhealthy daemon without a real Docker. It returns a
// short reason (empty when healthy) and whether the daemon answered. The ctx
// lets callers cancel the (up to ~30s) cold-start wait — e.g. an operator who
// hits Ctrl-C once they realize the daemon is down (#953).
var dockerDaemonProbe = defaultDockerDaemonProbe

// dockerDaemonHealth reports whether the Docker daemon is up and responsive.
func dockerDaemonHealth(ctx context.Context) (detail string, healthy bool) {
	return dockerDaemonProbe(ctx)
}

// daemonProbeBackoff is the pause between daemon probe attempts. Kept named so
// the "~30s total" arithmetic in defaultDockerDaemonProbe stays self-documenting
// (4 × 6s timeout + 3 × daemonProbeBackoff).
const daemonProbeBackoff = 2 * time.Second

// daemonUnreachableFallback is the generic reason used when a probe can't
// extract a more specific one from Docker's output. Shared so every daemon-down
// message reads the same when there's nothing more precise to show.
const daemonUnreachableFallback = "the Docker daemon is not reachable"

// defaultDockerDaemonProbe checks whether the Docker daemon answers a
// server-side request. A LookPath-present client whose daemon is stopped/wedged
// returns a non-zero exit with "Cannot connect to the Docker daemon" / "Is the
// docker daemon running?" — exactly the case that otherwise fails the build
// halfway through (#653). A cold Docker Desktop can take 15–40s to answer after
// launch, so we poll a few times before declaring it down rather than failing
// on a single short timeout and sending the operator down a false "wedged" path.
func defaultDockerDaemonProbe(ctx context.Context) (string, bool) {
	// Up to ~30s total (4 attempts × 6s timeout + 3 × 2s backoff): a snappy
	// daemon answers on the first try in well under a second; a cold-starting
	// one usually comes up within this window. Matches the "~30s" the callers
	// advertise before probing. A canceled ctx (Ctrl-C) short-circuits the wait.
	const attempts = 4
	const perAttempt = 6 * time.Second
	var lastDetail string
	for i := range attempts {
		detail, ok := dockerInfoOnce(ctx, perAttempt)
		if ok {
			return "", true
		}
		lastDetail = detail
		if i < attempts-1 {
			// Cancelable backoff: return promptly if the caller gave up.
			select {
			case <-ctx.Done():
				return "the Docker daemon check was canceled", false
			case <-time.After(daemonProbeBackoff):
			}
		}
	}
	if lastDetail == "" {
		lastDetail = "the Docker daemon did not respond (is it starting up or wedged?)"
	}
	return lastDetail, false
}

// dockerInfoOnce runs a single `docker info` round-trip, bounded by the smaller
// of the given timeout and the caller's ctx.
func dockerInfoOnce(ctx context.Context, timeout time.Duration) (string, bool) {
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	// `--format {{.ServerVersion}}` forces a round-trip to the daemon and keeps
	// the output to one line we can show; a stopped daemon errors here.
	out, err := exec.CommandContext(ctx, "docker", "info", "--format", "{{.ServerVersion}}").CombinedOutput()
	// A caller cancellation reads as a canceled context, not a dead daemon.
	if ctx.Err() == context.Canceled {
		return "the Docker daemon check was canceled", false
	}
	if ctx.Err() == context.DeadlineExceeded {
		return "the Docker daemon did not respond in time (is it starting up or wedged?)", false
	}
	// A missing `docker` binary is a different problem from a stopped daemon:
	// exec can't find the executable at all (empty output), so the generic
	// "daemon not reachable" reason would misdiagnose it. Flag it distinctly so
	// callers can point at the install docs instead of the "start Docker" hint
	// (#954). This only bites a user who uninstalled Docker after installing —
	// the runtime guards sit behind a compose-file check — but it should still
	// read correctly.
	if errors.Is(err, exec.ErrNotFound) {
		return dockerNotInstalledDetail, false
	}
	if err != nil {
		return firstLine(string(out)), false
	}
	if strings.TrimSpace(string(out)) == "" {
		return "the Docker daemon reported no server version", false
	}
	return "", true
}

// firstLine returns the first non-empty line of s, or a generic fallback.
func firstLine(s string) string {
	for _, line := range strings.Split(s, "\n") {
		if t := strings.TrimSpace(line); t != "" {
			return t
		}
	}
	return daemonUnreachableFallback
}

// UnhealthyDaemon returns the docker check whose daemon probe failed, if any.
// (The binary is present but the daemon did not answer.)
func UnhealthyDaemon(results []CheckResult) (CheckResult, bool) {
	for _, r := range results {
		if r.Found && r.DaemonChecked && !r.Healthy {
			return r, true
		}
	}
	return CheckResult{}, false
}

// dockerDaemonRecoveryHint is the one canonical "how to bring the daemon back"
// phrase, reused by every daemon-down message (install, start/stop, doctor) so
// the guidance can't drift between touch points. It leads with the
// runtime-agnostic instruction so Colima / Linux dockerd / Podman / Rancher
// users aren't sent down a Docker-Desktop-only path, then offers the
// Docker-Desktop convenience (the `docker desktop start` CLI needs Docker
// Desktop 4.37+, hence the version caveat inline).
const dockerDaemonRecoveryHint = "start your Docker daemon " +
	"(Docker Desktop users: open it, or run `docker desktop start` on 4.37+; " +
	"Linux: `sudo systemctl start docker`; Colima: `colima start`)"

// dockerNotInstalledDetail is the reason the probe reports when the `docker`
// binary itself is not on PATH (exec.ErrNotFound), as opposed to a present
// binary whose daemon is down. Kept as a package constant so callers can tell
// the two apart and swap the recovery guidance (#954).
const dockerNotInstalledDetail = "the docker command was not found on PATH"

// dockerNotInstalledHint is the "how to fix a missing Docker" guidance — it
// points at installation rather than starting a daemon, since there is nothing
// to start. Paired with dockerNotInstalledDetail.
const dockerNotInstalledHint = "install Docker (https://docs.docker.com/get-docker/)"

// dockerNotInstalled reports whether a probe detail means the binary is absent
// (vs. a present-but-unresponsive daemon), so callers render the install hint
// instead of the start-the-daemon hint.
func dockerNotInstalled(detail string) bool {
	return detail == dockerNotInstalledDetail
}

// DaemonError builds an actionable error for a present-but-unresponsive Docker
// daemon, so the install fails fast here instead of crashing mid-build.
func DaemonError(check CheckResult) error {
	detail := check.DaemonDetail
	if detail == "" {
		detail = daemonUnreachableFallback
	}
	return fmt.Errorf("docker is installed but its daemon is not responding: %s — %s, "+
		"wait until `docker info` succeeds, then re-run `jenticctl install`", detail, dockerDaemonRecoveryHint)
}

// RequireDockerDaemon fails fast with an actionable error when the Docker daemon
// is not responding, so runtime commands (`start`/`stop`) surface a clear
// recovery path when the daemon is down (e.g. Docker Desktop closed after a
// reboot) instead of a raw `docker compose` transport error. The referenced
// command names the caller (e.g. "jenticctl start") so the recovery path points
// back at what the operator ran. It returns nil when the daemon answers. See
// jentic-one#783 and jentic-api-scorecard#224.
//
// It only reports the problem; it deliberately does NOT start Docker itself.
// Client CLIs across the ecosystem (Testcontainers, act, Dagger) fail fast here
// rather than silently launching the daemon, since Docker Desktop is packaged as
// a user app, not a managed service. The recovery hint (dockerDaemonRecoveryHint)
// leads with a runtime-agnostic instruction so non-Docker-Desktop users aren't
// misled.
//
// The probe (dockerDaemonHealth) polls for up to ~30s to tolerate a
// cold-starting daemon, so callers should announce the check before invoking
// this — otherwise the command appears to hang. See the callers in
// internal/cli/ctlcmd/start.go and stop.go. The ctx (the command's context) lets an
// operator cancel that wait with Ctrl-C (#953).
func RequireDockerDaemon(ctx context.Context, command string) error {
	detail, healthy := dockerDaemonHealth(ctx)
	if healthy {
		return nil
	}
	// A missing binary needs install guidance, not "start the daemon".
	if dockerNotInstalled(detail) {
		return fmt.Errorf("docker is required but not installed: %s — %s, "+
			"then re-run `%s`", detail, dockerNotInstalledHint, command)
	}
	if detail == "" {
		detail = daemonUnreachableFallback
	}
	return fmt.Errorf("docker is installed but its daemon is not responding: %s — %s, "+
		"wait until `docker info` succeeds, then re-run `%s`", detail, dockerDaemonRecoveryHint, command)
}

// DockerDaemonRecoveryHint returns the canonical "how to start the Docker
// daemon" guidance so callers outside this package (e.g. the doctor deploy
// check) render the exact same advice as the fail-fast errors.
func DockerDaemonRecoveryHint() string { return dockerDaemonRecoveryHint }

// DockerNotInstalled reports whether a probe detail (from
// DockerDaemonResponsiveQuick) means the `docker` binary is absent rather than
// its daemon being down, so callers can pick DockerNotInstalledHint over
// DockerDaemonRecoveryHint (#954).
func DockerNotInstalled(detail string) bool { return dockerNotInstalled(detail) }

// DockerNotInstalledHint returns the "install Docker" guidance for the
// binary-absent case, mirroring DockerDaemonRecoveryHint for the daemon-down
// case.
func DockerNotInstalledHint() string { return dockerNotInstalledHint }

// DockerNotInstalledDetail returns the exact probe reason emitted when the
// `docker` binary is absent — the string DockerNotInstalled recognizes. Exposed
// so callers/tests can reason about the binary-absent case without duplicating
// the literal.
func DockerNotInstalledDetail() string { return dockerNotInstalledDetail }

// DockerDaemonResponsiveQuick is a single-round-trip daemon probe for callers
// that must stay fast and non-blocking — `doctor` is read-only and should not
// hang for the full cold-start polling window when the daemon is simply down.
// Unlike RequireDockerDaemon it does not tolerate a cold-starting Docker
// Desktop; it answers within `timeout` (or when ctx is canceled). It returns a
// short human reason (empty when healthy) and whether the daemon answered.
func DockerDaemonResponsiveQuick(ctx context.Context, timeout time.Duration) (detail string, healthy bool) {
	return dockerInfoOnce(ctx, timeout)
}

// RenderPreflight returns a styled checklist of the probe results.
func RenderPreflight(results []CheckResult) string {
	var b strings.Builder
	b.WriteString(headingStyle.Render("Preflight checks"))
	b.WriteString("\n")
	for _, r := range results {
		if r.Found {
			detail := r.Version
			if detail == "" {
				detail = r.Path
			}
			b.WriteString("  " + successStyle.Render("OK") + "      " +
				r.Req.Name + "  " + mutedStyle.Render(detail) + "\n")
			// Surface the daemon-health result on its own line for docker so a
			// present binary with a stopped daemon is visible before the build.
			if r.DaemonChecked {
				if r.Healthy {
					b.WriteString("  " + successStyle.Render("OK") + "      " +
						"docker daemon  " + mutedStyle.Render("responsive") + "\n")
				} else {
					b.WriteString("  " + errorStyle.Render("DOWN") + "    " +
						"docker daemon  " + mutedStyle.Render(r.DaemonDetail) + "\n")
				}
			}
		} else {
			// A soft requirement absent is a warning (the install proceeds),
			// not a MISSING failure. Prefer a probe-supplied MissingWhy over the
			// static Why for the recovery hint.
			why := r.Req.Why
			if r.MissingWhy != "" {
				why = r.MissingWhy
			}
			detail := why
			if r.Req.URL != "" {
				detail = why + " -> " + r.Req.URL
			}
			if r.Req.Soft {
				b.WriteString("  " + warnStyle.Render("SKIP") + "    " +
					r.Req.Name + "  " + mutedStyle.Render(detail) + "\n")
			} else {
				b.WriteString("  " + errorStyle.Render("MISSING") + " " +
					r.Req.Name + "  " + mutedStyle.Render(detail) + "\n")
			}
		}
	}
	return b.String()
}

// MissingError builds an actionable error for missing required tools or unhealthy daemons.
func MissingError(missing []CheckResult) error {
	names := make([]string, 0, len(missing))
	var hints strings.Builder
	for _, r := range missing {
		names = append(names, r.Req.Name)
		if !r.Found {
			hint := r.Req.URL
			if hint == "" {
				hint = r.MissingWhy
			}
			if r.MissingWhy != "" && r.Req.URL != "" {
				hint = r.MissingWhy + " (" + r.Req.URL + ")"
			}
			fmt.Fprintf(&hints, "\n  %s: %s", r.Req.Name, hint)
		} else if r.DaemonChecked && !r.Healthy {
			detail := r.DaemonDetail
			if detail == "" {
				detail = daemonUnreachableFallback
			}
			fmt.Fprintf(&hints, "\n  docker daemon: %s — %s, then re-run", detail, dockerDaemonRecoveryHint)
		}
	}
	return fmt.Errorf("missing required tool(s) or daemons down: %s — install/start and re-run:%s",
		strings.Join(names, ", "), hints.String())
}
