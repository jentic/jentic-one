package install

import (
	"context"
	"os"
	"strings"
	"testing"
	"time"
)

func TestMissing(t *testing.T) {
	results := []CheckResult{
		{Req: Requirement{Name: "uv"}, Found: true},
		{Req: Requirement{Name: "git"}, Found: false},
		{Req: Requirement{Name: "docker"}, Found: false},
	}
	missing := Missing(results)
	if len(missing) != 2 {
		t.Fatalf("Missing returned %d, want 2", len(missing))
	}
	if missing[0].Req.Name != "git" || missing[1].Req.Name != "docker" {
		t.Errorf("unexpected missing set: %+v", missing)
	}
}

func TestMissingError(t *testing.T) {
	err := MissingError([]CheckResult{
		{Req: Requirement{Name: "git", URL: "https://git-scm.com/downloads"}},
	})
	if err == nil {
		t.Fatal("expected error")
	}
	msg := err.Error()
	if !strings.Contains(msg, "git") || !strings.Contains(msg, "https://git-scm.com/downloads") {
		t.Errorf("error missing name/url: %q", msg)
	}
}

func TestPreflightProbesRequirements(t *testing.T) {
	// The docker path runs the stack via docker compose, so docker is the core
	// requirement; the probe should return one result per requirement.
	d := NewDraft()
	d.RuntimePath = RuntimeDocker
	results := Preflight(context.Background(), d)
	if len(results) == 0 {
		t.Fatal("expected at least one preflight result")
	}
	names := map[string]bool{}
	for _, r := range results {
		names[r.Req.Name] = true
	}
	if !names["docker"] {
		t.Errorf("docker preflight missing docker: %v", names)
	}
}

func TestRenderPreflight(t *testing.T) {
	out := RenderPreflight([]CheckResult{
		{Req: Requirement{Name: "uv"}, Found: true, Version: "uv 0.5.0"},
		{Req: Requirement{Name: "git", Why: "clone", URL: "https://x"}, Found: false},
	})
	if !strings.Contains(out, "uv") || !strings.Contains(out, "git") {
		t.Errorf("preflight render missing tools:\n%s", out)
	}
}

func TestRenderPreflightShowsDaemonStatus(t *testing.T) {
	healthy := RenderPreflight([]CheckResult{
		{Req: Requirement{Name: "docker"}, Found: true, Version: "Docker 27", DaemonChecked: true, Healthy: true},
	})
	if !strings.Contains(healthy, "docker daemon") || !strings.Contains(healthy, "responsive") {
		t.Errorf("healthy daemon line missing:\n%s", healthy)
	}
	down := RenderPreflight([]CheckResult{
		{Req: Requirement{Name: "docker"}, Found: true, Version: "Docker 27", DaemonChecked: true, Healthy: false, DaemonDetail: "Cannot connect to the Docker daemon"},
	})
	if !strings.Contains(down, "DOWN") || !strings.Contains(down, "Cannot connect") {
		t.Errorf("down daemon line missing:\n%s", down)
	}
}

func TestUnhealthyDaemon(t *testing.T) {
	// A present docker whose daemon answered → not flagged.
	ok := []CheckResult{{Req: Requirement{Name: "docker"}, Found: true, DaemonChecked: true, Healthy: true}}
	if _, down := UnhealthyDaemon(ok); down {
		t.Error("healthy daemon should not be flagged")
	}
	// A present docker whose daemon did NOT answer → flagged.
	bad := []CheckResult{{Req: Requirement{Name: "docker"}, Found: true, DaemonChecked: true, Healthy: false, DaemonDetail: "stopped"}}
	check, down := UnhealthyDaemon(bad)
	if !down || check.DaemonDetail != "stopped" {
		t.Errorf("unhealthy daemon not flagged: %+v down=%v", check, down)
	}
	// A missing docker binary is NOT a daemon problem (Missing handles it).
	absent := []CheckResult{{Req: Requirement{Name: "docker"}, Found: false}}
	if _, down := UnhealthyDaemon(absent); down {
		t.Error("absent binary should be a Missing case, not an UnhealthyDaemon case")
	}
}

func TestDaemonError(t *testing.T) {
	err := DaemonError(CheckResult{DaemonDetail: "Is the docker daemon running?"})
	if err == nil {
		t.Fatal("expected error")
	}
	msg := err.Error()
	if !strings.Contains(msg, "daemon is not responding") ||
		!strings.Contains(msg, "docker desktop start") ||
		!strings.Contains(msg, "Is the docker daemon running?") {
		t.Errorf("daemon error not actionable: %q", msg)
	}
}

func TestRequireDockerDaemon(t *testing.T) {
	orig := dockerDaemonProbe
	t.Cleanup(func() { dockerDaemonProbe = orig })

	// Healthy daemon → no error, so `start`/`stop` proceed.
	dockerDaemonProbe = func(context.Context) (string, bool) { return "", true }
	if err := RequireDockerDaemon(context.Background(), "jenticctl start"); err != nil {
		t.Errorf("healthy daemon should not error, got %v", err)
	}

	for _, tc := range []struct {
		name    string
		probe   func(context.Context) (string, bool)
		command string
		want    []string
	}{
		{
			name:    "down with detail names the caller and stays runtime-agnostic",
			probe:   func(context.Context) (string, bool) { return "Cannot connect to the Docker daemon", false },
			command: "jenticctl start",
			// Leads with the generic instruction, then Docker Desktop / Linux /
			// Colima specifics, and names the command the operator ran.
			want: []string{
				"daemon is not responding", "Cannot connect to the Docker daemon",
				"start your Docker daemon", "docker desktop start", "colima start", "jenticctl start",
			},
		},
		{
			name:    "down with empty detail falls back to a usable reason",
			probe:   func(context.Context) (string, bool) { return "", false },
			command: "jenticctl stop",
			want:    []string{"not reachable", "jenticctl stop"},
		},
	} {
		t.Run(tc.name, func(t *testing.T) {
			dockerDaemonProbe = tc.probe
			err := RequireDockerDaemon(context.Background(), tc.command)
			if err == nil {
				t.Fatal("stopped daemon should return an error")
			}
			msg := err.Error()
			for _, want := range tc.want {
				if !strings.Contains(msg, want) {
					t.Errorf("error missing %q: %q", want, msg)
				}
			}
		})
	}
}

func TestPreflightDaemonProbeSeam(t *testing.T) {
	// Stub a `docker` on PATH so Preflight's LookPath succeeds (and the daemon
	// branch is reached) regardless of whether the host has Docker installed,
	// then force the probe seam to report the daemon down. Without the PATH stub
	// this test would silently assert nothing on a Docker-less CI agent.
	dir := t.TempDir()
	docker := dir + string(os.PathSeparator) + "docker"
	if err := os.WriteFile(docker, []byte("#!/bin/sh\nexit 0\n"), 0o755); err != nil {
		t.Fatalf("write docker stub: %v", err)
	}
	t.Setenv("PATH", dir+string(os.PathListSeparator)+os.Getenv("PATH"))

	orig := dockerDaemonProbe
	t.Cleanup(func() { dockerDaemonProbe = orig })
	dockerDaemonProbe = func(context.Context) (string, bool) { return "daemon stopped", false }

	d := NewDraft()
	d.RuntimePath = RuntimeDocker
	results := Preflight(context.Background(), d)

	var sawDocker bool
	for _, r := range results {
		if r.Req.Name != "docker" {
			continue
		}
		sawDocker = true
		if !r.Found {
			t.Fatal("docker stub on PATH should make the docker requirement Found")
		}
		if !r.DaemonChecked {
			t.Error("docker result should carry a daemon probe")
		}
		if r.Healthy {
			t.Error("probe reported down, but result says healthy")
		}
		if r.DaemonDetail != "daemon stopped" {
			t.Errorf("daemon detail = %q, want %q", r.DaemonDetail, "daemon stopped")
		}
	}
	if !sawDocker {
		t.Fatal("expected a docker requirement in the docker-path preflight")
	}
}

// TestDefaultDockerDaemonProbeCancelable is the core #953 guarantee: an operator
// who hits Ctrl-C (a canceled context) doesn't have to sit through the full
// ~30s cold-start polling window. With an already-canceled ctx the real probe
// must give up promptly rather than sleeping out every attempt/backoff.
func TestDefaultDockerDaemonProbeCancelable(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel() // canceled before we even start probing

	done := make(chan struct{})
	var detail string
	var healthy bool
	start := time.Now()
	go func() {
		detail, healthy = defaultDockerDaemonProbe(ctx)
		close(done)
	}()

	select {
	case <-done:
	case <-time.After(15 * time.Second):
		t.Fatal("canceled probe did not return promptly — it appears to be waiting out the full polling window")
	}

	if elapsed := time.Since(start); elapsed >= 15*time.Second {
		t.Errorf("probe took %s; a canceled ctx should short-circuit the ~30s window", elapsed)
	}
	if healthy {
		t.Error("canceled probe reported the daemon healthy")
	}
	if detail == "" {
		t.Error("canceled probe should return a non-empty reason")
	}
}
