package localagent

import (
	"slices"
	"testing"
)

func TestKillAgentProcessesCmdShape(t *testing.T) {
	got := KillAgentProcessesCmd("alice-local-agent")
	if got == nil {
		t.Fatal("expected a process-termination command")
	}
	want := []string{"sudo", "pkill", "-KILL", "-u", "alice-local-agent"}
	if !slices.Equal(got.Args, want) {
		t.Errorf("KillAgentProcessesCmd = %v, want %v", got.Args, want)
	}
	if cmd := KillAgentProcessesCmd("Invalid User"); cmd != nil {
		t.Errorf("invalid agent user must be rejected, got %v", cmd.Args)
	}
}
