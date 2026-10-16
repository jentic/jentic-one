package install

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"regexp"
	"strings"
	"time"

	"gopkg.in/yaml.v3"
)

// PortHolder is a running container, outside the jentic compose project, that
// already publishes a host port the jentic stack needs.
type PortHolder struct {
	// Port is the contended host port.
	Port string
	// Container is the holding container's name.
	Container string
	// Project is the holder's compose project label ("" for a bare container).
	Project string
}

// String renders the holder for an operator-facing message.
func (h PortHolder) String() string {
	if h.Project != "" {
		return fmt.Sprintf("host port %s is already allocated by container %s (compose project %q)", h.Port, h.Container, h.Project)
	}
	return fmt.Sprintf("host port %s is already allocated by container %s", h.Port, h.Container)
}

// dockerPublishLookupTimeout bounds each `docker ps --filter publish=` call so
// a wedged daemon cannot stall the update preflight.
const dockerPublishLookupTimeout = 10 * time.Second

// dockerPublishers lists the running containers publishing hostPort, one
// "<name>\t<compose project>" line per container. A package-level seam so
// tests can simulate holders without a real Docker daemon.
var dockerPublishers = func(ctx context.Context, hostPort string) (string, error) {
	ctx, cancel := context.WithTimeout(ctx, dockerPublishLookupTimeout)
	defer cancel()
	out, err := exec.CommandContext(ctx, "docker", "ps", //nolint:gosec // hostPort is a digits-only port parsed from the CLI-generated compose file.
		"--filter", "publish="+hostPort,
		"--format", `{{.Names}}`+"\t"+`{{.Label "com.docker.compose.project"}}`).Output()
	return string(out), err
}

// ForeignPortHolders reports the running containers from OTHER compose
// projects (or bare containers) that already publish a host port the compose
// file at composePath publishes. The jentic project's own containers are
// excluded: the update recreates them, so their bindings are not a conflict.
//
// It sees Docker-held ports only — a non-container host process bound to the
// port is not detected here and still surfaces when compose starts the stack
// (see DetectPortConflict). Best-effort by contract: an unreadable compose
// file or a failing `docker ps` returns the error so the caller can skip the
// check rather than block the update on it.
func ForeignPortHolders(ctx context.Context, composePath string) ([]PortHolder, error) {
	ports, err := composeHostPorts(composePath)
	if err != nil {
		return nil, err
	}
	var holders []PortHolder
	for _, port := range ports {
		out, err := dockerPublishers(ctx, port)
		if err != nil {
			return nil, fmt.Errorf("list containers publishing port %s: %w", port, err)
		}
		for _, line := range strings.Split(strings.TrimSpace(out), "\n") {
			name, project, _ := strings.Cut(strings.TrimSpace(line), "\t")
			if name == "" || project == composeProjectName {
				continue
			}
			holders = append(holders, PortHolder{Port: port, Container: name, Project: project})
		}
	}
	return holders, nil
}

// composeHostPorts returns the distinct host ports published by the services
// in the compose file, in file order.
func composeHostPorts(composePath string) ([]string, error) {
	raw, err := os.ReadFile(composePath) //nolint:gosec // composePath is the CLI-managed ~/.jentic compose file.
	if err != nil {
		return nil, fmt.Errorf("read compose file: %w", err)
	}
	var doc struct {
		Services yaml.Node `yaml:"services"`
	}
	if err := yaml.Unmarshal(raw, &doc); err != nil {
		return nil, fmt.Errorf("parse compose file: %w", err)
	}
	var ports []string
	seen := map[string]bool{}
	// Walk the mapping node rather than decoding into a Go map so the order is
	// the file's (deterministic messages and tests).
	svcs := doc.Services.Content
	for i := 1; i < len(svcs); i += 2 {
		var svc struct {
			Ports []string `yaml:"ports"`
		}
		if err := svcs[i].Decode(&svc); err != nil {
			return nil, fmt.Errorf("parse compose service ports: %w", err)
		}
		for _, mapping := range svc.Ports {
			if p := hostPortOf(mapping); p != "" && !seen[p] {
				seen[p] = true
				ports = append(ports, p)
			}
		}
	}
	return ports, nil
}

// digitsOnly matches a single numeric port (ranges are not used by the
// generated compose file and are skipped).
var digitsOnly = regexp.MustCompile(`^[0-9]+$`)

// hostPortOf extracts the published host port from a short-syntax compose
// port mapping: "[host:]hostPort:containerPort[/proto]". The host port is the
// second-to-last colon-separated field, which also holds for a bracketed IPv6
// bind host ("[::1]:8000:8000"). A bare "containerPort" publishes nothing
// fixed and yields "".
func hostPortOf(mapping string) string {
	mapping, _, _ = strings.Cut(strings.TrimSpace(mapping), "/")
	parts := strings.Split(mapping, ":")
	if len(parts) < 2 {
		return ""
	}
	port := parts[len(parts)-2]
	if !digitsOnly.MatchString(port) {
		return ""
	}
	return port
}

// portConflictPatterns match the Docker daemon / Docker Desktop errors for a
// host port that is already bound, capturing the port.
var portConflictPatterns = []*regexp.Regexp{
	// Linux daemon: "Bind for 0.0.0.0:5432 failed: port is already allocated".
	regexp.MustCompile(`(?i)bind for \S*:([0-9]+) failed: port is already allocated`),
	// Docker Desktop: "Ports are not available: exposing port TCP 0.0.0.0:5432 -> ...".
	regexp.MustCompile(`(?i)ports are not available: exposing port \w+ \S*:([0-9]+)`),
	// A host process holding the port: "listen tcp4 0.0.0.0:5432: bind: address already in use".
	regexp.MustCompile(`(?i)listen \w+ \S*:([0-9]+): bind: address already in use`),
}

// DetectPortConflict scans docker compose output for a host-port-in-use
// failure and returns the contended port, so the caller can replace a raw
// daemon error with an actionable message.
func DetectPortConflict(output string) (string, bool) {
	for _, re := range portConflictPatterns {
		if m := re.FindStringSubmatch(output); m != nil {
			return m[1], true
		}
	}
	return "", false
}
