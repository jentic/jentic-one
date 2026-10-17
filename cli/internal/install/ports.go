package install

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"regexp"
	"strconv"
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
// "<name>\t<compose project>\t<ports>" line per container, where <ports> is
// docker's published-ports column ("127.0.0.1:5432->5432/tcp, …"). A
// package-level seam so tests can simulate holders without a real Docker
// daemon.
var dockerPublishers = func(ctx context.Context, hostPort string) (string, error) {
	ctx, cancel := context.WithTimeout(ctx, dockerPublishLookupTimeout)
	defer cancel()
	out, err := exec.CommandContext(ctx, "docker", "ps", //nolint:gosec // hostPort is a digits-only port parsed from the CLI-generated compose file.
		"--filter", "publish="+hostPort,
		"--format", `{{.Names}}`+"\t"+`{{.Label "com.docker.compose.project"}}`+"\t"+`{{.Ports}}`).Output()
	return string(out), err
}

// ForeignPortHolders reports the running containers from OTHER compose
// projects (or bare containers) that already publish a host port the compose
// file at composePath publishes, on a host address that overlaps the stack's
// own bind address. The jentic project's own containers are excluded: the
// update recreates them, so their bindings are not a conflict.
//
// `docker ps --filter publish=N` matches port N on any host address, so each
// holder's published bindings are compared against the compose bind host: a
// wildcard address (0.0.0.0, ::, or none) on either side overlaps everything,
// otherwise only the same address conflicts. A holder whose bindings cannot be
// read is reported, since docker already said it publishes the port.
//
// It sees Docker-held ports only — a non-container host process bound to the
// port is not detected here and still surfaces when compose starts the stack
// (see DetectPortConflict). Best-effort by contract: an unreadable compose
// file or a failing `docker ps` returns the error so the caller can skip the
// check rather than block the update on it.
func ForeignPortHolders(ctx context.Context, composePath string) ([]PortHolder, error) {
	bindings, err := composeHostBindings(composePath)
	if err != nil {
		return nil, err
	}
	var holders []PortHolder
	for _, port := range distinctPorts(bindings) {
		out, err := dockerPublishers(ctx, port)
		if err != nil {
			return nil, fmt.Errorf("list containers publishing port %s: %w", port, err)
		}
		for _, line := range strings.Split(strings.TrimSpace(out), "\n") {
			fields := strings.SplitN(strings.TrimSpace(line), "\t", 3)
			name := fields[0]
			project, published := "", ""
			if len(fields) > 1 {
				project = fields[1]
			}
			if len(fields) > 2 {
				published = fields[2]
			}
			if name == "" || project == composeProjectName {
				continue
			}
			if !holderOverlaps(published, port, hostsFor(bindings, port)) {
				continue
			}
			holders = append(holders, PortHolder{Port: port, Container: name, Project: project})
		}
	}
	return holders, nil
}

// hostBinding is one published host address:port from the compose file.
type hostBinding struct {
	host string // "" when the mapping has no bind host (all interfaces)
	port string
}

// composeHostBindings returns the distinct host bindings published by the
// services in the compose file, in file order.
func composeHostBindings(composePath string) ([]hostBinding, error) {
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
	var bindings []hostBinding
	seen := map[hostBinding]bool{}
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
			if b, ok := hostBindingOf(mapping); ok && !seen[b] {
				seen[b] = true
				bindings = append(bindings, b)
			}
		}
	}
	return bindings, nil
}

// distinctPorts returns the distinct ports of bindings, in order.
func distinctPorts(bindings []hostBinding) []string {
	var ports []string
	seen := map[string]bool{}
	for _, b := range bindings {
		if !seen[b.port] {
			seen[b.port] = true
			ports = append(ports, b.port)
		}
	}
	return ports
}

// hostsFor returns the compose bind hosts that publish port.
func hostsFor(bindings []hostBinding, port string) []string {
	var hosts []string
	for _, b := range bindings {
		if b.port == port {
			hosts = append(hosts, b.host)
		}
	}
	return hosts
}

// digitsOnly matches a single numeric port (ranges are not used by the
// generated compose file and are skipped).
var digitsOnly = regexp.MustCompile(`^[0-9]+$`)

// hostBindingOf parses a short-syntax compose port mapping
// "[host:]hostPort:containerPort[/proto]". The host port is the second-to-last
// colon-separated field and the bind host is everything before it, which also
// holds for a bracketed IPv6 bind host ("[::1]:8000:8000"). A bare
// "containerPort" publishes nothing fixed and yields ok=false.
func hostBindingOf(mapping string) (hostBinding, bool) {
	mapping, _, _ = strings.Cut(strings.TrimSpace(mapping), "/")
	parts := strings.Split(mapping, ":")
	if len(parts) < 2 {
		return hostBinding{}, false
	}
	port := parts[len(parts)-2]
	if !digitsOnly.MatchString(port) {
		return hostBinding{}, false
	}
	host := strings.Join(parts[:len(parts)-2], ":")
	return hostBinding{host: strings.Trim(host, "[]"), port: port}, true
}

// holderOverlaps reports whether a container whose published-ports column is
// published binds port on an address overlapping any of the stack's hosts.
// An unparseable column (no binding for port found) counts as overlapping.
func holderOverlaps(published, port string, stackHosts []string) bool {
	holderHosts := publishedHosts(published, port)
	if len(holderHosts) == 0 {
		return true
	}
	for _, h := range holderHosts {
		for _, s := range stackHosts {
			if bindHostsOverlap(h, s) {
				return true
			}
		}
	}
	return false
}

// publishedHosts extracts the host addresses binding port from docker's
// published-ports column, e.g. "0.0.0.0:5432->5432/tcp, [::]:5432->5432/tcp".
// Entries without "->" (exposed, not published) are ignored.
func publishedHosts(published, port string) []string {
	var hosts []string
	for _, entry := range strings.Split(published, ",") {
		left, _, ok := strings.Cut(strings.TrimSpace(entry), "->")
		if !ok {
			continue
		}
		i := strings.LastIndex(left, ":")
		if i < 0 {
			continue
		}
		if portInSpec(port, left[i+1:]) {
			hosts = append(hosts, strings.Trim(left[:i], "[]"))
		}
	}
	return hosts
}

// portInSpec reports whether port equals spec or falls in a "lo-hi" range.
func portInSpec(port, spec string) bool {
	lo, hi, isRange := strings.Cut(spec, "-")
	if !isRange {
		return spec == port
	}
	p, errP := strconv.Atoi(port)
	l, errL := strconv.Atoi(lo)
	h, errH := strconv.Atoi(hi)
	return errP == nil && errL == nil && errH == nil && l <= p && p <= h
}

// bindHostsOverlap reports whether two host bind addresses can collide: a
// wildcard on either side covers every address, otherwise they must match.
func bindHostsOverlap(a, b string) bool {
	if isWildcardHost(a) || isWildcardHost(b) {
		return true
	}
	return a == b
}

func isWildcardHost(h string) bool {
	return h == "" || h == "0.0.0.0" || h == "::"
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
