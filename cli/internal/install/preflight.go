package install

import (
	"context"
	"fmt"
	"os/exec"
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
}

// CheckResult is the outcome of probing a single Requirement.
type CheckResult struct {
	Req     Requirement
	Found   bool
	Path    string
	Version string
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
	if _, inRepo := RepoRoot(); !inRepo {
		reqs = append(reqs, Requirement{
			Name: "git",
			Why:  "clones the source from GitHub",
			URL:  "https://git-scm.com/downloads",
		})
	}
	return reqs
}

// Preflight probes every Requirement for the chosen path.
func Preflight(d *Draft) []CheckResult {
	reqs := requirementsFor(d)
	results := make([]CheckResult, 0, len(reqs))
	for _, req := range reqs {
		res := CheckResult{Req: req}
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
				if detail, ok := dockerDaemonHealth(); ok {
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
// short reason (empty when healthy) and whether the daemon answered.
var dockerDaemonProbe = defaultDockerDaemonProbe

// dockerDaemonHealth reports whether the Docker daemon is up and responsive.
func dockerDaemonHealth() (detail string, healthy bool) {
	return dockerDaemonProbe()
}

// defaultDockerDaemonProbe checks whether the Docker daemon answers a
// server-side request. A LookPath-present client whose daemon is stopped/wedged
// returns a non-zero exit with "Cannot connect to the Docker daemon" / "Is the
// docker daemon running?" — exactly the case that otherwise fails the build
// halfway through (#653). A cold Docker Desktop can take 15–40s to answer after
// launch, so we poll a few times before declaring it down rather than failing
// on a single short timeout and sending the operator down a false "wedged" path.
func defaultDockerDaemonProbe() (string, bool) {
	// Up to ~24s total: a snappy daemon answers on the first try in well under a
	// second; a cold-starting one usually comes up within this window.
	const attempts = 4
	const perAttempt = 6 * time.Second
	var lastDetail string
	for i := range attempts {
		detail, ok := dockerInfoOnce(perAttempt)
		if ok {
			return "", true
		}
		lastDetail = detail
		if i < attempts-1 {
			time.Sleep(2 * time.Second)
		}
	}
	if lastDetail == "" {
		lastDetail = "the Docker daemon did not respond (is it starting up or wedged?)"
	}
	return lastDetail, false
}

// dockerInfoOnce runs a single `docker info` round-trip with the given timeout.
func dockerInfoOnce(timeout time.Duration) (string, bool) {
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()
	// `--format {{.ServerVersion}}` forces a round-trip to the daemon and keeps
	// the output to one line we can show; a stopped daemon errors here.
	out, err := exec.CommandContext(ctx, "docker", "info", "--format", "{{.ServerVersion}}").CombinedOutput()
	if ctx.Err() == context.DeadlineExceeded {
		return "the Docker daemon did not respond in time (is it starting up or wedged?)", false
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
	return "the Docker daemon is not reachable"
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

// DaemonError builds an actionable error for a present-but-unresponsive Docker
// daemon, so the install fails fast here instead of crashing mid-build.
func DaemonError(check CheckResult) error {
	detail := check.DaemonDetail
	if detail == "" {
		detail = "the Docker daemon is not reachable"
	}
	return fmt.Errorf("docker is installed but its daemon is not responding: %s — "+
		"start Docker Desktop (or your docker daemon), wait for it to report healthy, "+
		"then re-run `jenticctl install`", detail)
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
			b.WriteString("  " + errorStyle.Render("MISSING") + " " +
				r.Req.Name + "  " + mutedStyle.Render(r.Req.Why+" -> "+r.Req.URL) + "\n")
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
			fmt.Fprintf(&hints, "\n  %s: %s", r.Req.Name, r.Req.URL)
		} else if r.DaemonChecked && !r.Healthy {
			detail := r.DaemonDetail
			if detail == "" {
				detail = "the Docker daemon is not reachable"
			}
			fmt.Fprintf(&hints, "\n  docker daemon: %s — start Docker Desktop and re-run", detail)
		}
	}
	return fmt.Errorf("missing required tool(s) or daemons down: %s — install/start and re-run:%s",
		strings.Join(names, ", "), hints.String())
}
