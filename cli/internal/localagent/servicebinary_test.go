// The root-owned service-binary remove script is POSIX sh run through sudo —
// Unix-only.
//
//go:build !windows

package localagent

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

// --- Root-owned MCP service binary (servicebinary.go) -----------------------
// The shape test asserts the privileged recipe without sudo; the script test
// runs the REAL remove script unprivileged against a temp tree.

func TestRemoveServiceBinaryCmdShape(t *testing.T) {
	cmd := RemoveServiceBinaryCmd()
	if cmd.Args[0] != "sudo" || cmd.Args[1] != "sh" {
		t.Fatalf("expected sudo sh -c, got %v", cmd.Args)
	}
	script := cmd.Args[3]
	if !strings.Contains(script, "d='/usr/local/libexec/jentic'") {
		t.Errorf("remove script must target the managed dir:\n%s", script)
	}
	if strings.Contains(script, "rm -rf") || strings.Contains(script, "/usr/local/libexec'") {
		t.Errorf("remove script must only remove the copy and the managed dir, never recursively or above it:\n%s", script)
	}
}

// TestServiceBinaryRemoveScript runs the REAL remove script unprivileged
// against a temp tree laid out like an installed copy.
func TestServiceBinaryRemoveScript(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "libexec", "jentic")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "jentic"), []byte("binary"), 0o755); err != nil {
		t.Fatal(err)
	}
	// An interrupted install's temp file is swept too.
	if err := os.WriteFile(filepath.Join(dir, ".jentic.AbC123"), nil, 0o600); err != nil {
		t.Fatal(err)
	}
	if out, err := exec.Command("sh", "-c", serviceBinaryRemoveScript(dir)).CombinedOutput(); err != nil {
		t.Fatalf("remove failed: %v\n%s", err, out)
	}
	if _, err := os.Lstat(dir); !os.IsNotExist(err) {
		t.Fatalf("managed dir must be gone after removal (%v)", err)
	}
	if _, err := os.Lstat(filepath.Dir(dir)); err != nil {
		t.Fatalf("parent dirs must be left alone: %v", err)
	}
	// Absent: a clean no-op.
	if out, err := exec.Command("sh", "-c", serviceBinaryRemoveScript(dir)).CombinedOutput(); err != nil {
		t.Fatalf("remove of an absent copy must be a no-op: %v\n%s", err, out)
	}

	// A symlinked managed dir is never followed.
	elsewhere := t.TempDir()
	keep := filepath.Join(elsewhere, "jentic")
	if err := os.WriteFile(keep, []byte("keep"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(elsewhere, dir); err != nil {
		t.Fatal(err)
	}
	if out, err := exec.Command("sh", "-c", serviceBinaryRemoveScript(dir)).CombinedOutput(); err != nil {
		t.Fatalf("remove failed: %v\n%s", err, out)
	}
	if _, err := os.Stat(keep); err != nil {
		t.Fatalf("remove must not follow a symlinked managed dir: %v", err)
	}
}
