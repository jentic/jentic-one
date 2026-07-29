package cmd

import (
	"bytes"
	"strings"
	"testing"

	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/spf13/cobra"
)

func testApp(t *testing.T) *App {
	t.Helper()
	return &App{
		Paths: config.Paths{Root: t.TempDir()},
		Out:   new(bytes.Buffer),
		Err:   new(bytes.Buffer),
	}
}

func hasCommand(root *cobra.Command, name string) bool {
	for _, c := range root.Commands() {
		if c.Name() == name {
			return true
		}
	}
	return false
}

func TestCtlRootListsLifecycleCommands(t *testing.T) {
	root := newCtlRootCmd(testApp(t))
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
	for _, name := range []string{"bootstrap", "register", "profile", "logout", "catalog", "apis", "search", "inspect", "execute"} {
		if hasCommand(root, name) {
			t.Errorf("jenticctl root unexpectedly registers %q", name)
		}
	}
}

func TestAPIRootListsAPICommands(t *testing.T) {
	root := newAPIRootCmd(testApp(t))
	var out bytes.Buffer
	root.SetOut(&out)
	root.SetErr(&out)
	root.SetArgs([]string{"--help"})
	if err := root.Execute(); err != nil {
		t.Fatalf("help: %v", err)
	}
	got := out.String()
	for _, name := range []string{"bootstrap", "register", "profile", "logout", "catalog", "apis", "search", "inspect", "execute"} {
		if !strings.Contains(got, name) {
			t.Errorf("jentic help output missing command %q", name)
		}
		if !hasCommand(root, name) {
			t.Errorf("jentic root missing command %q", name)
		}
	}
	// Lifecycle commands must not be registered on the API CLI.
	for _, name := range []string{"install", "setup", "doctor", "status", "start", "stop", "logs", "update", "uninstall"} {
		if hasCommand(root, name) {
			t.Errorf("jentic root unexpectedly registers %q", name)
		}
	}
}

func TestRootVersion(t *testing.T) {
	for _, tc := range []struct {
		name  string
		build func(*App) *cobra.Command
		want  string
	}{
		{"jenticctl", newCtlRootCmd, "jenticctl "},
		{"jentic", newAPIRootCmd, "jentic "},
	} {
		t.Run(tc.name, func(t *testing.T) {
			root := tc.build(testApp(t))
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
	root := newCtlRootCmd(testApp(t))
	root.SetOut(new(bytes.Buffer))
	root.SetErr(new(bytes.Buffer))
	root.SetArgs([]string{"definitely-not-a-command"})
	if err := root.Execute(); err == nil {
		t.Fatalf("expected error for unknown command")
	}
}
