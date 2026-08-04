package install

import (
	"os"
	"strings"
	"testing"
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
	results := Preflight(d)
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
		!strings.Contains(msg, "start Docker Desktop") ||
		!strings.Contains(msg, "Is the docker daemon running?") {
		t.Errorf("daemon error not actionable: %q", msg)
	}
}

func TestRequireDockerDaemon(t *testing.T) {
	orig := dockerDaemonProbe
	t.Cleanup(func() { dockerDaemonProbe = orig })

	// Healthy daemon → no error, so `start`/`stop` proceed.
	dockerDaemonProbe = func() (string, bool) { return "", true }
	if err := RequireDockerDaemon("jenticctl start"); err != nil {
		t.Errorf("healthy daemon should not error, got %v", err)
	}

	// Stopped daemon → actionable error naming the caller's command.
	dockerDaemonProbe = func() (string, bool) { return "Cannot connect to the Docker daemon", false }
	err := RequireDockerDaemon("jenticctl start")
	if err == nil {
		t.Fatal("stopped daemon should return an error")
	}
	msg := err.Error()
	for _, want := range []string{"daemon is not responding", "Cannot connect to the Docker daemon", "start Docker Desktop", "jenticctl start"} {
		if !strings.Contains(msg, want) {
			t.Errorf("error missing %q: %q", want, msg)
		}
	}

	// A down daemon with no detail still yields a usable fallback reason.
	dockerDaemonProbe = func() (string, bool) { return "", false }
	if err := RequireDockerDaemon("jenticctl stop"); err == nil ||
		!strings.Contains(err.Error(), "not reachable") ||
		!strings.Contains(err.Error(), "jenticctl stop") {
		t.Errorf("empty-detail down daemon: %v", err)
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
	dockerDaemonProbe = func() (string, bool) { return "daemon stopped", false }

	d := NewDraft()
	d.RuntimePath = RuntimeDocker
	results := Preflight(d)

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
