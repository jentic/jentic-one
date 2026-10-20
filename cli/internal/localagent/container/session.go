package container

import (
	"context"
	"errors"
	"fmt"
	"io"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

// HostRoute is how the relay container reaches the host's Jentic.
type HostRoute string

const (
	// RouteGateway reaches the host as host.docker.internal, through the
	// engine's host gateway. The engines that run in a VM (Docker Desktop,
	// OrbStack) forward it to the host's own loopback, so a Jentic listening
	// on 127.0.0.1 is reachable.
	RouteGateway HostRoute = "gateway"
	// RouteHostNetwork runs the relay in the host's network namespace and
	// dials 127.0.0.1. A native Linux engine needs it: there the host gateway
	// is the docker0 bridge address, where a Jentic bound to 127.0.0.1 does
	// not listen. The relay alone shares the host's network; the agent never
	// does.
	RouteHostNetwork HostRoute = "host-network"
)

// RouteFor picks the HostRoute for an engine.
func RouteFor(e Engine, clientGOOS string) HostRoute {
	if clientGOOS != "linux" || e.Desktop() {
		return RouteGateway
	}
	return RouteHostNetwork
}

// Options describe one session. Every field is required unless noted.
type Options struct {
	SessionID   string
	Owner       string
	AgentImage  string
	BridgeImage string
	// WorkDir is the canonical host path of the work folder. It is
	// bind-mounted read-write at the same path and is the agent's working
	// directory.
	WorkDir string
	// JenticPorts are the host loopback ports Jentic serves on (the context's
	// base and broker URL ports); the agent reaches each at 127.0.0.1:<port>.
	JenticPorts []int
	Route       HostRoute
	// AgentUser is the agent's uid:gid inside the container.
	AgentUser string
	// TTY allocates a terminal for an interactive session.
	TTY bool
	// Env is the agent's KEY=VALUE environment (besides HOME and the
	// egress's own).
	Env []string
	// Command is the agent's argv.
	Command []string
	// Egress is the session's route beyond Jentic; nil means NoEgress.
	Egress Egress
}

// Plan is a validated session: its object names and container specs.
type Plan struct {
	Options
	Network      string
	HomeVolume   string
	SocketVolume string
	Relay        Spec
	Loopback     Spec
	Agent        Spec
}

// NewPlan validates o and derives the session's objects from it.
func NewPlan(o Options) (*Plan, error) {
	if !ValidSessionID(o.SessionID) {
		return nil, fmt.Errorf("invalid session id %q", o.SessionID)
	}
	if o.Owner == "" || o.AgentImage == "" || o.BridgeImage == "" || len(o.Command) == 0 {
		return nil, errors.New("container session needs an owner, both images and an agent command")
	}
	if !filepath.IsAbs(o.WorkDir) || filepath.Clean(o.WorkDir) != o.WorkDir || o.WorkDir == "/" {
		return nil, fmt.Errorf("work folder %q must be a clean absolute path below /", o.WorkDir)
	}
	// The home volume is mounted at /home. A work folder below it (a native
	// Linux host's /home/<user>/…) is mounted on top of the volume, which
	// Docker supports; only the agent's own home would collide.
	if o.WorkDir == AgentHome || strings.HasPrefix(o.WorkDir, AgentHome+"/") {
		return nil, fmt.Errorf("work folder %q collides with the agent's home inside the container", o.WorkDir)
	}
	if len(o.JenticPorts) == 0 {
		return nil, errors.New("container session needs at least one Jentic port")
	}
	for _, port := range o.JenticPorts {
		if port < 1 || port > 65535 {
			return nil, fmt.Errorf("invalid Jentic port %d", port)
		}
	}
	if o.Route != RouteGateway && o.Route != RouteHostNetwork {
		return nil, fmt.Errorf("invalid host route %q", o.Route)
	}
	if o.Egress == nil {
		o.Egress = NoEgress{}
	}

	base := "jentic-run-" + o.SessionID
	p := &Plan{
		Options:      o,
		Network:      base,
		HomeVolume:   base + "-home",
		SocketVolume: base + "-sockets",
	}
	ports := make([]string, len(o.JenticPorts))
	for i, port := range o.JenticPorts {
		ports[i] = strconv.Itoa(port)
	}
	sockets := Mount{Type: "volume", Source: p.SocketVolume, Target: socketDir}

	p.Relay = Spec{
		Name:         base + "-relay",
		Image:        o.BridgeImage,
		Labels:       p.labels(RoleRelay),
		User:         bridgeUser,
		ReadOnlyRoot: true,
		Mounts:       []Mount{sockets},
		Detach:       true,
		Cmd:          append([]string{"relay", p.relayTarget()}, ports...),
	}
	if o.Route == RouteHostNetwork {
		p.Relay.Network = "host"
	} else {
		p.Relay.Network = "bridge"
		p.Relay.AddHosts = []string{"host.docker.internal:host-gateway"}
	}

	p.Loopback = Spec{
		Name:         base + "-loopback",
		Image:        o.BridgeImage,
		Labels:       p.labels(RoleLoopback),
		Network:      p.Network,
		User:         bridgeUser,
		ReadOnlyRoot: true,
		Mounts:       []Mount{sockets},
		Detach:       true,
		Cmd:          append([]string{"loopback"}, ports...),
	}

	env := append([]string{"HOME=" + AgentHome}, o.Env...)
	env = append(env, o.Egress.AgentEnv(p)...)
	mounts := []Mount{
		{Type: "volume", Source: p.HomeVolume, Target: filepath.Dir(AgentHome)},
		{Type: "bind", Source: o.WorkDir, Target: o.WorkDir},
	}
	for _, m := range o.Egress.AgentMounts(p) {
		if !m.ReadOnly {
			return nil, fmt.Errorf("egress %s mount %s must be read-only", o.Egress.Name(), m.Target)
		}
		mounts = append(mounts, m)
	}
	p.Agent = Spec{
		Name:         base + "-agent",
		Image:        o.AgentImage,
		Labels:       p.labels(RoleAgent),
		Network:      "container:" + p.Loopback.Name,
		User:         o.AgentUser,
		ReadOnlyRoot: true,
		Tmpfs:        []string{"/tmp", "/var/tmp"},
		Mounts:       mounts,
		Workdir:      o.WorkDir,
		Env:          env,
		TTY:          o.TTY,
		Init:         true,
		Cmd:          o.Command,
	}
	for _, spec := range append([]Spec{p.Relay, p.Loopback, p.Agent}, o.Egress.Sidecars(p)...) {
		if err := spec.Validate(); err != nil {
			return nil, err
		}
	}
	if v, ok := o.Egress.(interface{ Validate(*Plan) error }); ok {
		if err := v.Validate(p); err != nil {
			return nil, err
		}
	}
	return p, nil
}

func (p *Plan) relayTarget() string {
	if p.Route == RouteHostNetwork {
		return "127.0.0.1"
	}
	return "host.docker.internal"
}

// labels are the labels of one session object.
func (p *Plan) labels(role string) map[string]string {
	return map[string]string{LabelSession: p.SessionID, LabelOwner: p.Owner, LabelRole: role}
}

func (p *Plan) labelArgs(role string) []string {
	return []string{
		"--label", LabelSession + "=" + p.SessionID,
		"--label", LabelOwner + "=" + p.Owner,
		"--label", LabelRole + "=" + role,
	}
}

// Session drives one Plan.
type Session struct {
	Plan   *Plan
	Docker Docker
	// ReadyTimeout bounds each wait for a bridge or sidecar; zero means 10s.
	ReadyTimeout time.Duration
}

// Up creates the session's network and volumes, starts the relay, the
// loopback and the egress sidecars, waits until each accepts traffic, and
// creates (but does not start) the agent container. On error the caller
// still owns teardown: every object made so far carries the session label.
func (s *Session) Up(ctx context.Context) error {
	p, d := s.Plan, s.Docker
	netArgs := append([]string{"network", "create", "--internal"}, p.labelArgs(RoleNetwork)...)
	if _, err := d.Output(ctx, append(netArgs, p.Network)...); err != nil {
		return fmt.Errorf("create the session network: %w", err)
	}
	for _, name := range p.Egress.Networks(p) {
		args := append([]string{"network", "create"}, p.labelArgs(RoleEgress)...)
		if _, err := d.Output(ctx, append(args, name)...); err != nil {
			return fmt.Errorf("create the egress network %s: %w", name, err)
		}
	}
	volumes := []struct{ name, role string }{{p.HomeVolume, RoleHome}, {p.SocketVolume, RoleSockets}}
	for _, name := range p.Egress.Volumes(p) {
		volumes = append(volumes, struct{ name, role string }{name, RoleEgress})
	}
	for _, v := range volumes {
		args := append([]string{"volume", "create"}, p.labelArgs(v.role)...)
		if _, err := d.Output(ctx, append(args, v.name)...); err != nil {
			return fmt.Errorf("create volume %s: %w", v.name, err)
		}
	}
	if err := s.start(ctx, p.Relay); err != nil {
		return err
	}
	if err := s.waitReady(ctx, p.Relay.Name, "ready-relay"); err != nil {
		return err
	}
	if err := s.start(ctx, p.Loopback); err != nil {
		return err
	}
	if err := s.waitReady(ctx, p.Loopback.Name, "ready-loopback"); err != nil {
		return err
	}
	for _, sidecar := range p.Egress.Sidecars(p) {
		sidecar.Network = p.Network
		sidecar.Labels = p.labels(RoleEgress)
		if err := s.start(ctx, sidecar); err != nil {
			return err
		}
	}
	if err := p.Egress.Ready(ctx, d, p); err != nil {
		return fmt.Errorf("egress %s is not ready: %w", p.Egress.Name(), err)
	}
	args, err := p.Agent.CreateArgs()
	if err != nil {
		return err
	}
	if _, err := d.Output(ctx, args...); err != nil {
		return fmt.Errorf("create the agent container: %w", err)
	}
	return nil
}

// start runs a detached container. One with extra networks is created,
// connected to each, and only then started, so it never runs before it has
// all its routes.
func (s *Session) start(ctx context.Context, spec Spec) error {
	if len(spec.ExtraNetworks) == 0 {
		args, err := spec.RunArgs()
		if err != nil {
			return err
		}
		if _, err := s.Docker.Output(ctx, args...); err != nil {
			return fmt.Errorf("start %s: %w", spec.Name, err)
		}
		return nil
	}
	args, err := spec.CreateArgs()
	if err != nil {
		return err
	}
	if _, err := s.Docker.Output(ctx, args...); err != nil {
		return fmt.Errorf("create %s: %w", spec.Name, err)
	}
	for _, n := range spec.ExtraNetworks {
		if _, err := s.Docker.Output(ctx, "network", "connect", n, spec.Name); err != nil {
			return fmt.Errorf("connect %s to %s: %w", spec.Name, n, err)
		}
	}
	if _, err := s.Docker.Output(ctx, "start", spec.Name); err != nil {
		return fmt.Errorf("start %s: %w", spec.Name, err)
	}
	return nil
}

// waitReady polls the bridge image's readiness check for the session's ports.
func (s *Session) waitReady(ctx context.Context, name, check string) error {
	timeout := s.ReadyTimeout
	if timeout == 0 {
		timeout = 10 * time.Second
	}
	deadline := time.Now().Add(timeout)
	args := make([]string, 0, 4+len(s.Plan.JenticPorts))
	args = append(args, "exec", name, "jentic-bridge", check)
	for _, port := range s.Plan.JenticPorts {
		args = append(args, strconv.Itoa(port))
	}
	var last error
	for {
		if _, last = s.Docker.Output(ctx, args...); last == nil {
			return nil
		}
		if time.Now().After(deadline) {
			return fmt.Errorf("%s did not become ready within %s: %w", name, timeout, last)
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(100 * time.Millisecond):
		}
	}
}

// Seed copies a home archive (HomeArchive) into the created agent container's
// home volume. The archive's entries are under "agent/", so its root entry
// creates the agent's home with the archive's owner and mode.
func (s *Session) Seed(ctx context.Context, archive io.Reader) error {
	if err := s.Docker.Input(ctx, archive, "cp", "-", s.Plan.Agent.Name+":"+filepath.Dir(AgentHome)); err != nil {
		return fmt.Errorf("seed the agent's home: %w", err)
	}
	return nil
}

// AttachCmd is `docker start --attach --interactive <agent>`: it runs the
// created agent container in the foreground and exits with its exit code.
func (s *Session) AttachCmd(ctx context.Context) *exec.Cmd {
	return s.Docker.Command(ctx, "start", "--attach", "--interactive", s.Plan.Agent.Name)
}

// Stop asks the agent container to exit (SIGTERM, then SIGKILL after grace).
func (s *Session) Stop(ctx context.Context, grace time.Duration) error {
	secs := int(grace / time.Second)
	_, err := s.Docker.Output(ctx, "stop", "--time", strconv.Itoa(secs), s.Plan.Agent.Name)
	return err
}

// WatchBridges returns a channel that receives once if the relay or the
// loopback container stops before ctx ends. A bridge that stops frees the
// agent's Jentic ports, so the caller ends the session instead of letting the
// agent bind them.
func (s *Session) WatchBridges(ctx context.Context) <-chan string {
	stopped := make(chan string, 2)
	for _, name := range []string{s.Plan.Relay.Name, s.Plan.Loopback.Name} {
		go func(name string) {
			// A failed wait means the container is already gone: stopped too.
			_, _ = s.Docker.Output(ctx, "wait", name)
			if ctx.Err() == nil {
				stopped <- name
			}
		}(name)
	}
	return stopped
}

// Teardown removes every Docker object labelled with sessionID: containers,
// then networks, then volumes. It is idempotent and keeps going past a failed
// removal, returning the errors joined.
func Teardown(ctx context.Context, d Docker, sessionID string) error {
	if !ValidSessionID(sessionID) {
		return fmt.Errorf("invalid session id %q", sessionID)
	}
	filter := "label=" + LabelSession + "=" + sessionID
	var errs []error
	for _, kind := range []struct{ list, remove []string }{
		{[]string{"ps", "--all", "--quiet", "--filter", filter}, []string{"rm", "--force", "--volumes"}},
		{[]string{"network", "ls", "--quiet", "--filter", filter}, []string{"network", "rm"}},
		{[]string{"volume", "ls", "--quiet", "--filter", filter}, []string{"volume", "rm", "--force"}},
	} {
		out, err := d.Output(ctx, kind.list...)
		if err != nil {
			errs = append(errs, err)
			continue
		}
		ids := strings.Fields(out)
		if len(ids) == 0 {
			continue
		}
		if _, err := d.Output(ctx, append(kind.remove, ids...)...); err != nil {
			errs = append(errs, err)
		}
	}
	return errors.Join(errs...)
}
