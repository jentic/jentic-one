package localagent

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"slices"
	"strings"
	"testing"
)

func TestKillAgentProcessesCmdShape(t *testing.T) {
	got := KillAgentProcessesCmd("alice-local-agent")
	if got == nil {
		t.Fatal("expected a process-termination command")
	}
	wantPrefix := []string{"/usr/bin/sudo", "/bin/sh", "-c"}
	if !slices.Equal(got.Args[:3], wantPrefix) {
		t.Errorf("KillAgentProcessesCmd prefix = %v, want %v", got.Args, wantPrefix)
	}
	script := got.Args[3]
	for _, want := range []string{"/usr/bin/pkill -KILL -u 'alice-local-agent'", `"$status" -eq 1`, `exit "$status"`} {
		if !strings.Contains(script, want) {
			t.Errorf("termination script missing %q: %s", want, script)
		}
	}
	assertFixedUtilityPATH(t, got)
	if cmd := KillAgentProcessesCmd("Invalid User"); cmd != nil {
		t.Errorf("invalid agent user must be rejected, got %v", cmd.Args)
	}
}

func TestKillAgentProcessesExitStatusContract(t *testing.T) {
	for _, tc := range []struct {
		name    string
		status  int
		wantErr bool
	}{
		{name: "processes terminated", status: 0},
		{name: "no matching processes", status: 1},
		{name: "unknown pkill failure", status: 2, wantErr: true},
		{name: "pkill unavailable", status: 127, wantErr: true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			err := replayProcessUtility(t, KillAgentProcessesCmd("alice-local-agent"), "pkill", tc.status)
			if (err != nil) != tc.wantErr {
				t.Errorf("pkill status %d returned %v, wantErr=%v", tc.status, err, tc.wantErr)
			}
		})
	}
}

func TestAssertNoAgentProcessesCmdShape(t *testing.T) {
	got := AssertNoAgentProcessesCmd("alice-local-agent")
	if got == nil {
		t.Fatal("expected a process-quiescence command")
	}
	wantPrefix := []string{"/usr/bin/sudo", "/bin/sh", "-c"}
	if !slices.Equal(got.Args[:3], wantPrefix) {
		t.Errorf("AssertNoAgentProcessesCmd prefix = %v, want %v", got.Args, wantPrefix)
	}
	script := got.Args[3]
	for _, want := range []string{"/usr/bin/pgrep -u 'alice-local-agent'", `"$status" -eq 1`, `"$status" -eq 0`, `exit "$status"`} {
		if !strings.Contains(script, want) {
			t.Errorf("quiescence script missing %q: %s", want, script)
		}
	}
	assertFixedUtilityPATH(t, got)
	if cmd := AssertNoAgentProcessesCmd("Invalid User"); cmd != nil {
		t.Errorf("invalid agent user must be rejected, got %v", cmd.Args)
	}
}

func TestAssertNoAgentProcessesExitStatusContract(t *testing.T) {
	for _, tc := range []struct {
		name    string
		status  int
		wantErr bool
	}{
		{name: "surviving processes", status: 0, wantErr: true},
		{name: "no matching processes", status: 1},
		{name: "unknown pgrep failure", status: 2, wantErr: true},
		{name: "pgrep unavailable", status: 127, wantErr: true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			err := replayProcessUtility(t, AssertNoAgentProcessesCmd("alice-local-agent"), "pgrep", tc.status)
			if (err != nil) != tc.wantErr {
				t.Errorf("pgrep status %d returned %v, wantErr=%v", tc.status, err, tc.wantErr)
			}
		})
	}
}

func TestProcessSafetySudoDenialIsFailure(t *testing.T) {
	for _, builder := range []struct {
		name string
		cmd  func(string) *exec.Cmd
	}{
		{name: "termination", cmd: KillAgentProcessesCmd},
		{name: "quiescence", cmd: AssertNoAgentProcessesCmd},
	} {
		t.Run(builder.name, func(t *testing.T) {
			bin := t.TempDir()
			sudo := filepath.Join(bin, "sudo")
			if err := os.WriteFile(sudo, []byte("#!/bin/sh\nexit 1\n"), 0o700); err != nil {
				t.Fatal(err)
			}
			t.Setenv("PATH", bin)
			cmd := builder.cmd("alice-local-agent")
			// Replay the denial through the fake after construction; running the
			// trusted absolute sudo here would cross the test host's privilege boundary.
			cmd.Path = sudo
			cmd.Args[0] = sudo
			cmd.Env = []string{"PATH=" + bin}
			if err := cmd.Run(); err == nil {
				t.Fatal("sudo denial must remain a non-zero process-safety result")
			}
		})
	}
}

func replayProcessUtility(t *testing.T, cmd *exec.Cmd, utility string, status int) error {
	t.Helper()
	bin := t.TempDir()
	utilityPath := filepath.Join(bin, utility)
	script := fmt.Sprintf("#!/bin/sh\nexit %d\n", status)
	if err := os.WriteFile(utilityPath, []byte(script), 0o700); err != nil {
		t.Fatal(err)
	}
	if len(cmd.Args) != 4 || cmd.Args[0] != trustedSudoPath || cmd.Args[1] != "/bin/sh" || cmd.Args[2] != "-c" {
		t.Fatalf("unexpected process command shape: %v", cmd.Args)
	}
	absoluteUtility := "/usr/bin/" + utility
	if !strings.Contains(cmd.Args[3], absoluteUtility) {
		t.Fatalf("process script lacks absolute utility %q: %s", absoluteUtility, cmd.Args[3])
	}
	replayed := strings.ReplaceAll(cmd.Args[3], absoluteUtility, shellQuote(utilityPath))
	probe := exec.Command("/bin/sh", "-c", replayed)
	probe.Env = cmd.Env
	return probe.Run()
}
