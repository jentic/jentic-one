package clitree_test

import (
	"bytes"
	"strings"
	"testing"

	"github.com/jentic/jentic-one/cli/internal/cli/api"
	"github.com/jentic/jentic-one/cli/internal/cli/ctlcmd"
	"github.com/spf13/cobra"
)

func hasCommand(root *cobra.Command, name string) bool {
	for _, c := range root.Commands() {
		if c.Name() == name {
			return true
		}
	}
	return false
}

func TestCtlRootListsLifecycleCommands(t *testing.T) {
	root := ctlcmd.NewDocsRoot()
	var out bytes.Buffer
	root.SetOut(&out)
	root.SetErr(&out)
	root.SetArgs([]string{"--help"})
	if err := root.Execute(); err != nil {
		t.Fatalf("help: %v", err)
	}
	got := out.String()
	for _, name := range []string{"install", "setup", "doctor", "status", "start", "stop", "logs", "update", "uninstall"} {
		if !strings.Contains(got, name) {
			t.Errorf("jenticctl help output missing command %q", name)
		}
		if !hasCommand(root, name) {
			t.Errorf("jenticctl root missing command %q", name)
		}
	}
	// API-only commands must not be registered on the lifecycle CLI.
	for _, name := range []string{"register", "logout", "catalog", "apis", "search", "inspect", "execute"} {
		if hasCommand(root, name) {
			t.Errorf("jenticctl root unexpectedly registers %q", name)
		}
	}
}

func TestAPIRootListsAPICommands(t *testing.T) {
	root := api.NewDocsRoot()
	var out bytes.Buffer
	root.SetOut(&out)
	root.SetErr(&out)
	root.SetArgs([]string{"--help"})
	if err := root.Execute(); err != nil {
		t.Fatalf("help: %v", err)
	}
	got := out.String()
	for _, name := range []string{"setup", "register", "logout", "catalog", "apis", "search", "inspect", "execute"} {
		if !strings.Contains(got, name) {
			t.Errorf("jentic help output missing command %q", name)
		}
		if !hasCommand(root, name) {
			t.Errorf("jentic root missing command %q", name)
		}
	}
	// The pre-rename `bootstrap` name must keep working as a hidden alias:
	// registered on the tree, but absent from help output.
	if !hasCommand(root, "bootstrap") {
		t.Errorf("jentic root missing the hidden bootstrap alias for setup")
	}
	if strings.Contains(got, "bootstrap") {
		t.Errorf("jentic help output must not mention the hidden bootstrap alias")
	}
	// Lifecycle commands must not be registered on the API CLI. NOTE: `doctor` is
	// intentionally NOT in this list — F8-4 ships an agent-side, read-only
	// `jentic doctor` self-check (distinct from the operator `jenticctl doctor`),
	// per impl/5.1 §3c. `setup` is not in it either: `jentic setup` (local-agent
	// onboarding) and `jenticctl setup` (first admin) are distinct commands that
	// legitimately share a name across the two binaries.
	for _, name := range []string{"install", "status", "start", "stop", "logs", "update", "uninstall"} {
		if hasCommand(root, name) {
			t.Errorf("jentic root unexpectedly registers %q", name)
		}
	}
}

func TestRootVersion(t *testing.T) {
	for _, tc := range []struct {
		name  string
		build func() *cobra.Command
		want  string
	}{
		{"jenticctl", ctlcmd.NewDocsRoot, "jenticctl "},
		{"jentic", api.NewDocsRoot, "jentic "},
	} {
		t.Run(tc.name, func(t *testing.T) {
			root := tc.build()
			var out bytes.Buffer
			root.SetOut(&out)
			root.SetArgs([]string{"--version"})
			if err := root.Execute(); err != nil {
				t.Fatalf("version: %v", err)
			}
			if !strings.Contains(out.String(), tc.want) {
				t.Errorf("version output = %q, want prefix %q", out.String(), tc.want)
			}
		})
	}
}

func TestRootUnknownCommand(t *testing.T) {
	root := ctlcmd.NewDocsRoot()
	root.SetOut(new(bytes.Buffer))
	root.SetErr(new(bytes.Buffer))
	root.SetArgs([]string{"definitely-not-a-command"})
	if err := root.Execute(); err == nil {
		t.Fatalf("expected error for unknown command")
	}
}
