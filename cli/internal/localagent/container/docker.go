package container

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"os/exec"
	"strings"
)

// Docker runs `docker` CLI commands. The session code only ever talks to Docker
// through it, so tests substitute a recorder.
type Docker interface {
	// Output runs `docker <args>` and returns its trimmed stdout. A non-zero
	// exit is an error carrying the command's stderr.
	Output(ctx context.Context, args ...string) (string, error)
	// Input runs `docker <args>` with stdin as its standard input.
	Input(ctx context.Context, stdin io.Reader, args ...string) error
	// Command builds `docker <args>` for the caller to wire and run (the
	// attached agent container).
	Command(ctx context.Context, args ...string) *exec.Cmd
}

// CLI is the Docker implementation that runs the `docker` binary.
type CLI struct {
	// Bin is the docker executable; empty means "docker" on PATH.
	Bin string
}

func (c CLI) bin() string {
	if c.Bin == "" {
		return "docker"
	}
	return c.Bin
}

// Output implements Docker.
func (c CLI) Output(ctx context.Context, args ...string) (string, error) {
	cmd := exec.CommandContext(ctx, c.bin(), args...) //nolint:gosec // argv is built by this package from validated session data.
	var stdout, stderr bytes.Buffer
	cmd.Stdout, cmd.Stderr = &stdout, &stderr
	if err := cmd.Run(); err != nil {
		return "", commandError(args, stderr.String(), err)
	}
	return strings.TrimSpace(stdout.String()), nil
}

// Input implements Docker.
func (c CLI) Input(ctx context.Context, stdin io.Reader, args ...string) error {
	cmd := exec.CommandContext(ctx, c.bin(), args...) //nolint:gosec // argv is built by this package from validated session data.
	var stderr bytes.Buffer
	cmd.Stdin, cmd.Stderr = stdin, &stderr
	if err := cmd.Run(); err != nil {
		return commandError(args, stderr.String(), err)
	}
	return nil
}

// Command implements Docker.
func (c CLI) Command(ctx context.Context, args ...string) *exec.Cmd {
	return exec.CommandContext(ctx, c.bin(), args...) //nolint:gosec // argv is built by this package from validated session data.
}

// commandError names the docker subcommand and keeps the first line of stderr,
// which is where the daemon's own message is.
func commandError(args []string, stderr string, err error) error {
	sub := "docker"
	if len(args) > 0 {
		sub += " " + args[0]
		if len(args) > 1 && !strings.HasPrefix(args[1], "-") {
			sub += " " + args[1]
		}
	}
	msg := strings.TrimSpace(stderr)
	if i := strings.IndexByte(msg, '\n'); i >= 0 {
		msg = msg[:i]
	}
	if msg == "" {
		return fmt.Errorf("%s: %w", sub, err)
	}
	return fmt.Errorf("%s: %s: %w", sub, msg, err)
}

// Engine is what Preflight learns about the Docker engine.
type Engine struct {
	// OperatingSystem is the engine's `docker info` OperatingSystem, e.g.
	// "Docker Desktop" or "Ubuntu 24.04.1 LTS".
	OperatingSystem string
}

// Desktop reports whether the engine runs in a VM that publishes the host as
// host.docker.internal (Docker Desktop, OrbStack), rather than natively on the
// client's own kernel.
func (e Engine) Desktop() bool {
	osName := strings.ToLower(e.OperatingSystem)
	return strings.Contains(osName, "docker desktop") || strings.Contains(osName, "orbstack")
}

// PreflightError is a session that cannot start: the reason plus what to do
// about it.
type PreflightError struct {
	Reason string
	Hint   string
}

func (e *PreflightError) Error() string {
	if e.Hint == "" {
		return e.Reason
	}
	return e.Reason + " — " + e.Hint
}

// Preflight checks that the Docker engine answers and that every image is
// present, pulling a missing one once. It fails closed with a *PreflightError.
func Preflight(ctx context.Context, d Docker, images ...string) (Engine, error) {
	osName, err := d.Output(ctx, "info", "--format", "{{.OperatingSystem}}")
	if err != nil {
		return Engine{}, &PreflightError{
			Reason: fmt.Sprintf("the Docker engine is not reachable (%v)", err),
			Hint:   "start Docker Desktop (or the docker service) and re-run",
		}
	}
	for _, image := range images {
		if _, err := d.Output(ctx, "image", "inspect", "--format", "{{.Id}}", image); err == nil {
			continue
		}
		if _, err := d.Output(ctx, "pull", "--quiet", image); err != nil {
			return Engine{}, &PreflightError{
				Reason: fmt.Sprintf("image %s is not present and could not be pulled (%v)", image, err),
				Hint:   "build the images from a jentic-one checkout: " + BuildHint(),
			}
		}
	}
	return Engine{OperatingSystem: osName}, nil
}

// IsPreflight reports whether err is (or wraps) a *PreflightError.
func IsPreflight(err error) bool {
	var p *PreflightError
	return errors.As(err, &p)
}
