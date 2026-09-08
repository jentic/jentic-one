package cmdcore

import (
	"context"
	"testing"
	"time"

	"github.com/spf13/cobra"
)

// newProbeRoot builds a minimal root with the interceptor installed and a "probe"
// subcommand that records the deadline (if any) of its command context. The root
// carries the persistent --mode/--context/--theme/--verbose/--yes flags the
// interceptor looks up (flagValue/boolFlag tolerate absent flags, but --mode must
// exist for the mode ladder to see it).
func newProbeRoot(t *testing.T, capture *func(ctx context.Context)) *cobra.Command {
	t.Helper()
	app := &App{}
	root := &cobra.Command{Use: "jentic", SilenceUsage: true, SilenceErrors: true}
	root.PersistentFlags().String("mode", "", "")
	root.PersistentFlags().String("context", "", "")
	root.PersistentFlags().String("theme", "", "")
	root.PersistentFlags().Bool("verbose", false, "")
	root.PersistentFlags().Bool("yes", false, "")
	installInterceptor(app, root)
	root.AddCommand(&cobra.Command{
		Use: "probe",
		RunE: func(c *cobra.Command, _ []string) error {
			(*capture)(c.Context())
			return nil
		},
	})
	// serve mirrors `jentic mcp`: a caller-owned long-running command that must
	// be exempt from the agent-mode wall-clock deadline.
	root.AddCommand(&cobra.Command{
		Use:         "serve",
		Annotations: map[string]string{LongRunningAnnotation: "true"},
		RunE: func(c *cobra.Command, _ []string) error {
			(*capture)(c.Context())
			return nil
		},
	})
	return root
}

// TestInterceptor_AgentModeSetsWallClockDeadline pins F3 (round-3 #7): in
// agent/service-account mode the interceptor derives a wall-clock deadline so a
// wedged Control Plane can't hang the CLI forever.
func TestInterceptor_AgentModeSetsWallClockDeadline(t *testing.T) {
	var seen context.Context
	capture := func(ctx context.Context) { seen = ctx }
	root := newProbeRoot(t, &capture)

	t.Setenv("JENTIC_MODE", "agent")
	root.SetArgs([]string{"probe"})
	if err := root.ExecuteContext(context.Background()); err != nil {
		t.Fatalf("execute: %v", err)
	}
	deadline, ok := seen.Deadline()
	if !ok {
		t.Fatal("agent-mode command context must carry a wall-clock deadline")
	}
	if until := time.Until(deadline); until <= 0 || until > 2*time.Minute {
		t.Errorf("deadline %s from now is out of the expected ~60s band", until)
	}
}

// TestInterceptor_HumanModeNoDeadline confirms human mode stays undeadlined so
// interactive prompts and paginators aren't aborted mid-flow.
func TestInterceptor_HumanModeNoDeadline(t *testing.T) {
	var seen context.Context
	capture := func(ctx context.Context) { seen = ctx }
	root := newProbeRoot(t, &capture)

	t.Setenv("JENTIC_MODE", "human")
	root.SetArgs([]string{"probe"})
	if err := root.ExecuteContext(context.Background()); err != nil {
		t.Fatalf("execute: %v", err)
	}
	if _, ok := seen.Deadline(); ok {
		t.Error("human-mode command context must NOT carry a deadline")
	}
}

// TestInterceptor_LongRunningExemptFromAgentDeadline pins the
// LongRunningAnnotation carve-out: a caller-owned server process (`jentic
// mcp`) must NOT inherit the agent-mode 60s wall-clock deadline — the
// interceptor would otherwise kill the stdio session mid-flight, silently,
// and only under JENTIC_MODE=agent entries. (That the mcp command actually
// carries the annotation is pinned in the api package's tests.)
func TestInterceptor_LongRunningExemptFromAgentDeadline(t *testing.T) {
	var seen context.Context
	capture := func(ctx context.Context) { seen = ctx }
	root := newProbeRoot(t, &capture)

	t.Setenv("JENTIC_MODE", "agent")
	root.SetArgs([]string{"serve"})
	if err := root.ExecuteContext(context.Background()); err != nil {
		t.Fatalf("execute: %v", err)
	}
	if _, ok := seen.Deadline(); ok {
		t.Error("a long-running command in agent mode must NOT carry the interceptor's wall-clock deadline")
	}
}
