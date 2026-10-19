//go:build unix

package skillgen

import (
	"errors"
	"os"
	"path/filepath"
	"syscall"
	"testing"
	"time"
)

// mkfifo creates a named pipe at path, skipping when the platform refuses.
func mkfifo(t *testing.T, path string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
		t.Fatal(err)
	}
	if err := syscall.Mkfifo(path, 0o600); err != nil {
		t.Skipf("cannot create a FIFO: %v", err)
	}
}

// returnsPromptly runs fn and fails if it does not return promptly: reading a
// FIFO with no writer blocks forever, which is the bug this guards.
func returnsPromptly(t *testing.T, what string, fn func() error) error {
	t.Helper()
	done := make(chan error, 1)
	go func() { done <- fn() }()
	select {
	case err := <-done:
		return err
	case <-time.After(5 * time.Second):
		t.Fatalf("%s blocked on a FIFO target", what)
		return nil
	}
}

// Pins the mode check: an AGENTS.md (direct or via an in-project symlink) or
// SKILL.md that is a FIFO is refused by apply and remove without blocking,
// and reads as not installed, at both scopes.
func TestFIFOTargetRefused(t *testing.T) {
	for _, scope := range []Scope{ScopeProject, ScopeUser} {
		for _, tc := range []struct {
			name, op string
			linked   bool
		}{
			{"agents", "codex", false},
			{"agents via link", "codex", true},
			{"skill", "claude", false},
		} {
			t.Run(string(scope)+"/"+tc.name, func(t *testing.T) {
				if tc.linked {
					skipNoSymlinks(t)
				}
				dir := t.TempDir()
				env := DetectEnv{Home: dir, Cwd: dir}
				ad, _ := DefaultRegistry().Resolve(tc.op)
				c := jenticContent(t)
				target := ad.Target(scope, c.Name, env)
				if tc.linked {
					fifo := filepath.Join(dir, "pipe")
					mkfifo(t, fifo)
					if err := os.MkdirAll(filepath.Dir(target), 0o750); err != nil {
						t.Fatal(err)
					}
					if err := os.Symlink(fifo, target); err != nil {
						t.Fatal(err)
					}
				} else {
					mkfifo(t, target)
				}

				err := returnsPromptly(t, "apply", func() error {
					_, err := Apply(ad, c, env, ApplyOptions{Scope: scope})
					return err
				})
				if !errors.Is(err, errNotRegularFile) {
					t.Errorf("apply must refuse a FIFO target, got %v", err)
				}
				err = returnsPromptly(t, "remove", func() error {
					_, err := Remove(ad, c, env, RemoveOptions{Scope: scope})
					return err
				})
				if !errors.Is(err, errNotRegularFile) {
					t.Errorf("remove must refuse a FIFO target, got %v", err)
				}
				_ = returnsPromptly(t, "install state", func() error {
					for _, st := range InstallStates(ad, c.Name, env) {
						if st.Installed {
							t.Errorf("a FIFO target must not read as installed: %+v", st)
						}
					}
					if Attributed(ad, target, c.Name) {
						t.Error("a FIFO target must not read as attributed")
					}
					return nil
				})
				if fi, err := os.Stat(target); err != nil || fi.Mode()&os.ModeNamedPipe == 0 {
					t.Errorf("the FIFO must be left in place, got %v (err %v)", fi, err)
				}
			})
		}
	}
}
