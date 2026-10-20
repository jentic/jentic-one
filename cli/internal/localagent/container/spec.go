package container

import (
	"errors"
	"fmt"
	"path"
	"sort"
	"strings"
)

// Mount is one --mount of a container.
type Mount struct {
	// Type is "bind" or "volume".
	Type string
	// Source is the host path (bind) or the volume name (volume).
	Source string
	// Target is the path inside the container.
	Target   string
	ReadOnly bool
}

func (m Mount) arg() string {
	s := "type=" + m.Type + ",src=" + m.Source + ",dst=" + m.Target
	if m.ReadOnly {
		s += ",readonly"
	}
	return s
}

// Spec is one container of a session, rendered to `docker run` argv by Args.
// The hardening every session container shares is not optional here: Args
// always drops every capability, sets no-new-privileges and refuses a Docker
// socket mount.
type Spec struct {
	Name   string
	Image  string
	Labels map[string]string
	// Network is the --network value: a network name, "host", "none" or
	// "container:<name>".
	Network string
	// AddHosts are --add-host entries ("host.docker.internal:host-gateway").
	AddHosts []string
	// User is the --user value (uid:gid). Required: no session container runs
	// as root.
	User    string
	Mounts  []Mount
	Workdir string
	// Env holds KEY=VALUE entries.
	Env []string
	// ReadOnlyRoot mounts the image's root filesystem read-only.
	ReadOnlyRoot bool
	// Tmpfs are writable tmpfs mount points (with ReadOnlyRoot).
	Tmpfs []string
	// Detach starts the container in the background (-d); otherwise it runs
	// attached with stdin open (-i).
	Detach bool
	// TTY allocates a terminal (-t), for an interactive agent.
	TTY bool
	// Init runs Docker's init as PID 1 (--init), so the command gets default
	// signal handling (a Ctrl-C on the terminal ends it) and zombies are
	// reaped.
	Init bool
	// Entrypoint overrides the image's entrypoint; empty keeps it.
	Entrypoint string
	// Cmd is the command and its arguments after the image.
	Cmd []string
}

// dockerSocketPaths are paths that would hand the container the Docker engine.
var dockerSocketPaths = []string{
	"/var/run/docker.sock",
	"/run/docker.sock",
	"docker.sock",
	"/var/run/docker",
	"/run/docker",
}

// Validate refuses a spec a session must never run: no user, root, a Docker
// socket mount, a relative or parent-escaping mount target, or a mount field
// that would smuggle another --mount option.
func (s Spec) Validate() error {
	if s.Name == "" || s.Image == "" {
		return errors.New("container spec needs a name and an image")
	}
	if s.User == "" || s.User == "0" || strings.HasPrefix(s.User, "0:") || s.User == "root" {
		return fmt.Errorf("container %s must run as an unprivileged user, not %q", s.Name, s.User)
	}
	for _, m := range s.Mounts {
		if m.Type != "bind" && m.Type != "volume" {
			return fmt.Errorf("container %s: unsupported mount type %q", s.Name, m.Type)
		}
		for _, field := range []string{m.Source, m.Target} {
			if field == "" || strings.ContainsAny(field, ",\n\r\x00") {
				return fmt.Errorf("container %s: invalid mount field %q", s.Name, field)
			}
		}
		if !path.IsAbs(m.Target) || path.Clean(m.Target) != m.Target {
			return fmt.Errorf("container %s: mount target %q must be a clean absolute path", s.Name, m.Target)
		}
		if mountsDockerSocket(m) {
			return fmt.Errorf("container %s: refusing to mount the Docker socket (%s)", s.Name, m.Source)
		}
	}
	return nil
}

func mountsDockerSocket(m Mount) bool {
	for _, p := range []string{m.Source, m.Target} {
		clean := path.Clean(p)
		for _, sock := range dockerSocketPaths {
			if clean == sock || strings.HasSuffix(clean, "/docker.sock") ||
				strings.HasPrefix(sock, "/") && strings.HasPrefix(clean+"/", sock+"/") {
				return true
			}
		}
	}
	return false
}

// RunArgs renders the spec as `docker run` arguments (without "docker").
func (s Spec) RunArgs() ([]string, error) { return s.args("run") }

// CreateArgs renders the spec as `docker create` arguments, for a container
// that is seeded (`docker cp`) before `docker start` attaches to it.
func (s Spec) CreateArgs() ([]string, error) { return s.args("create") }

func (s Spec) args(verb string) ([]string, error) {
	if err := s.Validate(); err != nil {
		return nil, err
	}
	args := []string{verb, "--rm", "--name", s.Name}
	switch {
	case s.Detach && verb == "run":
		args = append(args, "-d")
	case !s.Detach:
		args = append(args, "-i")
	}
	if s.TTY {
		args = append(args, "-t")
	}
	if s.Init {
		args = append(args, "--init")
	}
	keys := make([]string, 0, len(s.Labels))
	for k := range s.Labels {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	for _, k := range keys {
		args = append(args, "--label", k+"="+s.Labels[k])
	}
	if s.Network != "" {
		args = append(args, "--network", s.Network)
	}
	for _, h := range s.AddHosts {
		args = append(args, "--add-host", h)
	}
	args = append(args,
		"--user", s.User,
		"--cap-drop", "ALL",
		"--security-opt", "no-new-privileges",
	)
	if s.ReadOnlyRoot {
		args = append(args, "--read-only")
	}
	for _, t := range s.Tmpfs {
		args = append(args, "--tmpfs", t)
	}
	for _, m := range s.Mounts {
		args = append(args, "--mount", m.arg())
	}
	if s.Workdir != "" {
		args = append(args, "--workdir", s.Workdir)
	}
	for _, e := range s.Env {
		args = append(args, "--env", e)
	}
	if s.Entrypoint != "" {
		args = append(args, "--entrypoint", s.Entrypoint)
	}
	args = append(args, s.Image)
	return append(args, s.Cmd...), nil
}
